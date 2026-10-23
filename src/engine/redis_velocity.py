import time
import os
import redis
from typing import Tuple, Set
from src.common.logger import get_logger

logger = get_logger("RedisVelocity")

class RedisVelocityTracker:
    """
    Maintains real-time sliding-window velocity metrics for accounts in Redis.
    Uses Redis Sorted Sets (ZSET) where score = timestamp, member = unique event.
    """

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, password: str = None):
        try:
            self.r = redis.Redis(
                host=host, 
                port=port, 
                db=db, 
                password=password if password else None,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=2.0
            )
            self.r.ping()
            logger.info(f"Connected to Redis at {host}:{port}")
        except Exception as e:
            logger.warning(f"Could not connect to Redis ({e}). Running in fallback mode.")
            self.r = None

    def enrich_features(self, tx):
        """
        Extracts and records multi-window sliding metrics for Sender, Receiver, and Pair.
        Covers sliding windows: 1m, 5m, 10m, 60m (1h), and 24h as defined in specification.
        """
        if not self.r or not tx.source_account or not tx.dest_account:
            return tx

        now = time.time()
        tx_id = tx.tx_id or str(now)
        amt = float(tx.amount or 0.0)
        amt_str = f"{amt:.2f}"
        src_acc = tx.source_account
        dst_acc = tx.dest_account
        src_bank = tx.source_bank or "UNKNOWN"
        dst_bank = tx.dest_bank or "UNKNOWN"
        member = f"{tx_id}:{amt_str}"

        try:
            pipe = self.r.pipeline()
            slots = {}

            # 1. SENDER OUTBOUND METRICS
            k_src = f"vel:acc:{src_acc}"
            pipe.zadd(k_src, {member: now})
            pipe.expire(k_src, 86400)
            pipe.zremrangebyscore(k_src, 0, now - 86400)
            slots["vel_1m"] = len(pipe)
            pipe.zcount(k_src, now - 60, now)          # S002 velocity_1m
            slots["vel_5m"] = len(pipe)
            pipe.zcount(k_src, now - 300, now)         # velocity_5m
            slots["vel_10m"] = len(pipe)
            pipe.zcount(k_src, now - 600, now)         # S008 velocity_10m
            slots["amt_1h_items"] = len(pipe)
            pipe.zrangebyscore(k_src, now - 3600, now)  # amount_1h items
            slots["amt_24h_items"] = len(pipe)
            pipe.zrangebyscore(k_src, now - 86400, now) # S005 amount_24h items

            # 1b. Sender Distinct Receivers (10m) - S003, SR002
            k_src_recv = f"vel:src_recv:{src_acc}"
            pipe.zadd(k_src_recv, {dst_acc: now})
            pipe.expire(k_src_recv, 600)
            pipe.zremrangebyscore(k_src_recv, 0, now - 600)
            slots["src_unique_recv_10m"] = len(pipe)
            pipe.zcount(k_src_recv, now - 600, now)

            # 1c. Sender Distinct Destination Banks (5m) - S014
            k_src_banks = f"vel:src_banks:{src_acc}"
            pipe.zadd(k_src_banks, {dst_bank: now})
            pipe.expire(k_src_banks, 300)
            pipe.zremrangebyscore(k_src_banks, 0, now - 300)
            slots["src_unique_banks_5m"] = len(pipe)
            pipe.zcount(k_src_banks, now - 300, now)

            # 1d. Sender Identical Amount Repetition (5m) - S004
            k_src_amt = f"vel:src_amt:{src_acc}:{amt_str}"
            pipe.zadd(k_src_amt, {tx_id: now})
            pipe.expire(k_src_amt, 300)
            pipe.zremrangebyscore(k_src_amt, 0, now - 300)
            slots["src_same_amt_5m"] = len(pipe)
            pipe.zcount(k_src_amt, now - 300, now)

            # 1e. Sender Failed Attempts (5m) - S011
            k_src_fail = f"vel:src_fail:{src_acc}"
            if tx.status in ["FAILED", "TIMEOUT"] or (tx.response_code and tx.response_code != "00"):
                pipe.zadd(k_src_fail, {tx_id: now})
                pipe.expire(k_src_fail, 300)
            pipe.zremrangebyscore(k_src_fail, 0, now - 300)
            slots["failed_5m"] = len(pipe)
            pipe.zcount(k_src_fail, now - 300, now)

            # 2. RECEIVER INBOUND METRICS
            k_dst = f"vel:dst:{dst_acc}"
            pipe.zadd(k_dst, {member: now})
            pipe.expire(k_dst, 86400)
            pipe.zremrangebyscore(k_dst, 0, now - 86400)
            slots["dst_vel_1m"] = len(pipe)
            pipe.zcount(k_dst, now - 60, now)
            slots["dst_vel_5m"] = len(pipe)
            pipe.zcount(k_dst, now - 300, now)
            slots["dst_amt_1h_items"] = len(pipe)
            pipe.zrangebyscore(k_dst, now - 3600, now)

            # 2b. Receiver Distinct Senders (5m) - R001, SR003 (Fan-In Mule)
            k_dst_senders = f"vel:dst_senders:{dst_acc}"
            pipe.zadd(k_dst_senders, {src_acc: now})
            pipe.expire(k_dst_senders, 600)
            pipe.zremrangebyscore(k_dst_senders, 0, now - 300)
            slots["dst_unique_senders_5m"] = len(pipe)
            pipe.zcount(k_dst_senders, now - 300, now)

            # 2c. Receiver Multiple Originating Banks (10m) - R005
            k_dst_banks = f"vel:dst_banks:{dst_acc}"
            pipe.zadd(k_dst_banks, {src_bank: now})
            pipe.expire(k_dst_banks, 600)
            pipe.zremrangebyscore(k_dst_banks, 0, now - 600)
            slots["dst_unique_banks_10m"] = len(pipe)
            pipe.zcount(k_dst_banks, now - 600, now)

            # 2d. Receiver Identical Incoming Amounts (10m) - R004
            k_dst_amt = f"vel:dst_amt:{dst_acc}:{amt_str}"
            pipe.zadd(k_dst_amt, {tx_id: now})
            pipe.expire(k_dst_amt, 600)
            pipe.zremrangebyscore(k_dst_amt, 0, now - 600)
            slots["dst_same_amt_10m"] = len(pipe)
            pipe.zcount(k_dst_amt, now - 600, now)

            # 3. SENDER-RECEIVER RELATIONSHIP PAIR METRICS
            # 3a. Same Pair Transfer Velocity (5m) - SR001
            k_pair = f"vel:pair:{src_acc}:{dst_acc}"
            pipe.zadd(k_pair, {tx_id: now})
            pipe.expire(k_pair, 300)
            pipe.zremrangebyscore(k_pair, 0, now - 300)
            slots["pair_vel_5m"] = len(pipe)
            pipe.zcount(k_pair, now - 300, now)

            # 3b. Fixed-Value Pair Repetition (5m) - SR009
            k_pair_amt = f"vel:pair_amt:{src_acc}:{dst_acc}:{amt_str}"
            pipe.zadd(k_pair_amt, {tx_id: now})
            pipe.expire(k_pair_amt, 300)
            pipe.zremrangebyscore(k_pair_amt, 0, now - 300)
            slots["pair_same_amt_5m"] = len(pipe)
            pipe.zcount(k_pair_amt, now - 300, now)

            res = pipe.execute()

            # Assign sender features
            tx.velocity_1m = int(res[slots["vel_1m"]] or 1)
            tx.velocity_5m = int(res[slots["vel_5m"]] or 1)
            tx.velocity_10m = int(res[slots["vel_10m"]] or 1)

            # Sum 1h and 24h amounts
            amt_1h = 0.0
            raw_1h = res[slots["amt_1h_items"]]
            if isinstance(raw_1h, (list, tuple, set)):
                for item in raw_1h:
                    try:
                        str_item = item.decode('utf-8') if isinstance(item, bytes) else str(item)
                        if ":" in str_item:
                            amt_1h += float(str_item.split(":")[1])
                    except Exception: pass
            tx.amount_1h = amt_1h

            amt_24h = 0.0
            raw_24h = res[slots["amt_24h_items"]]
            if isinstance(raw_24h, (list, tuple, set)):
                for item in raw_24h:
                    try:
                        str_item = item.decode('utf-8') if isinstance(item, bytes) else str(item)
                        if ":" in str_item:
                            amt_24h += float(str_item.split(":")[1])
                    except Exception: pass
            tx.amount_24h = amt_24h

            tx.sender_unique_receivers_10m = int(res[slots["src_unique_recv_10m"]] or 1)
            tx.sender_unique_banks_5m = int(res[slots["src_unique_banks_5m"]] or 1)
            tx.sender_identical_amt_count_5m = int(res[slots["src_same_amt_5m"]] or 1)
            tx.failed_attempts_5m = int(res[slots["failed_5m"]] or 0)

            # Assign receiver features
            tx.dest_velocity_1m = int(res[slots["dst_vel_1m"]] or 1)
            tx.dest_velocity_5m = int(res[slots["dst_vel_5m"]] or 1)

            dst_amt_1h = 0.0
            raw_dst_1h = res[slots["dst_amt_1h_items"]]
            if isinstance(raw_dst_1h, (list, tuple, set)):
                for item in raw_dst_1h:
                    try:
                        str_item = item.decode('utf-8') if isinstance(item, bytes) else str(item)
                        if ":" in str_item:
                            dst_amt_1h += float(str_item.split(":")[1])
                    except Exception: pass
            tx.dest_amount_1h = dst_amt_1h

            tx.dest_unique_senders_5m = int(res[slots["dst_unique_senders_5m"]] or 1)
            tx.dest_unique_banks_10m = int(res[slots["dst_unique_banks_10m"]] or 1)
            tx.dest_identical_amt_count_10m = int(res[slots["dst_same_amt_10m"]] or 1)

            # Assign pair features
            tx.pair_count_5m = int(res[slots["pair_vel_5m"]] or 1)
            tx.pair_identical_amt_5m = int(res[slots["pair_same_amt_5m"]] or 1)

            return tx
        except Exception as e:
            logger.error(f"Redis multi-window enrichment error for TX {tx_id}: {e}")
            return tx

    def record_and_get_velocity(self, account_no: str, amount: float, tx_id: str = "") -> Tuple[int, int, float]:
        """Backward-compatible helper for sender velocity"""
        if not self.r or not account_no:
            return 1, 1, amount
        now = time.time()
        key = f"vel:acc:{account_no}"
        member = f"{tx_id}:{amount}" if tx_id else f"{now}:{amount}"
        try:
            pipe = self.r.pipeline()
            pipe.zadd(key, {member: now})
            pipe.expire(key, 86400)
            pipe.zremrangebyscore(key, 0, now - 86400)
            pipe.zcount(key, now - 60, now)
            pipe.zcount(key, now - 300, now)
            pipe.zrangebyscore(key, now - 3600, now)
            results = pipe.execute()
            vel_1m = results[3]
            vel_5m = results[4]
            amt_1h = sum([float(item.split(":")[1]) for item in results[5] if ":" in item])
            return vel_1m, vel_5m, amt_1h
        except Exception as e:
            logger.error(f"Redis velocity error for account {account_no}: {e}")
            return 1, 1, amount

    def record_and_get_dest_velocity(self, dest_account: str, source_account: str, amount: float, tx_id: str = "") -> Tuple[int, int, int]:
        """Backward-compatible helper for destination velocity & distinct senders"""
        if not self.r or not dest_account:
            return 1, 1, 1
        now = time.time()
        key_dst = f"vel:dst:{dest_account}"
        key_senders = f"vel:dst_senders:{dest_account}"
        member = f"{tx_id}:{amount}" if tx_id else f"{now}:{amount}"
        try:
            pipe = self.r.pipeline()
            pipe.zadd(key_dst, {member: now})
            pipe.expire(key_dst, 86400)
            pipe.zremrangebyscore(key_dst, 0, now - 86400)
            pipe.zcount(key_dst, now - 60, now)
            pipe.zcount(key_dst, now - 300, now)
            if source_account:
                pipe.zadd(key_senders, {source_account: now})
                pipe.expire(key_senders, 600)
                pipe.zremrangebyscore(key_senders, 0, now - 300)
                pipe.zcount(key_senders, now - 300, now)
            results = pipe.execute()
            dest_vel_1m = results[3]
            dest_vel_5m = results[4]
            dest_unique_senders_5m = results[8] if source_account else 1
            return dest_vel_1m, dest_vel_5m, dest_unique_senders_5m
        except Exception as e:
            logger.error(f"Redis dest velocity error for {dest_account}: {e}")
            return 1, 1, 1

    def is_blacklisted(self, account_no: str) -> bool:
        if not self.r or not account_no:
            return False
        try:
            return self.r.sismember("fraud:blacklist:accounts", account_no)
        except Exception:
            return False

    def add_to_blacklist(self, account_no: str):
        if self.r and account_no:
            try:
                self.r.sadd("fraud:blacklist:accounts", account_no)
                logger.info(f"Added account {account_no} to blacklist")
            except Exception as e:
                logger.error(f"Failed to add to blacklist: {e}")
