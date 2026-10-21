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

    def record_and_get_velocity(self, account_no: str, amount: float, tx_id: str = "") -> Tuple[int, int, float]:
        """
        Records the transaction and returns:
        - velocity_1m (count in last 60 seconds)
        - velocity_5m (count in last 300 seconds)
        - amount_1h (sum in last 3600 seconds)
        """
        if not self.r or not account_no:
            return 1, 1, amount

        now = time.time()
        key = f"vel:acc:{account_no}"
        # Use tx_id to ensure exact deduplication if duplicate logs are replayed
        member = f"{tx_id}:{amount}" if tx_id else f"{now}:{amount}"

        try:
            pipe = self.r.pipeline()
            # Add transaction
            pipe.zadd(key, {member: now})
            pipe.expire(key, 3600)  # Keep key alive for 1 hour

            # Remove transactions older than 1 hour (3600s)
            pipe.zremrangebyscore(key, 0, now - 3600)

            # Count in 1 min (60s)
            pipe.zcount(key, now - 60, now)

            # Count in 5 min (300s)
            pipe.zcount(key, now - 300, now)

            # Fetch all items in last 1 hour to sum amount
            pipe.zrangebyscore(key, now - 3600, now)

            results = pipe.execute()
            vel_1m = results[3]
            vel_5m = results[4]
            items_1h = results[5]

            amt_1h = 0.0
            for item in items_1h:
                try:
                    amt_1h += float(item.split(":")[1])
                except Exception:
                    pass

            return vel_1m, vel_5m, amt_1h
        except Exception as e:
            logger.error(f"Redis velocity error for account {account_no}: {e}")
            return 1, 1, amount

    def record_and_get_dest_velocity(self, dest_account: str, source_account: str, amount: float, tx_id: str = "") -> Tuple[int, int, int]:
        """
        Tracks inbound velocity and distinct senders for destination/beneficiary account (Mule Detection).
        Returns:
        - dest_vel_1m (inbound transfer count in last 60s)
        - dest_vel_5m (inbound transfer count in last 300s)
        - dest_unique_senders_5m (number of different senders depositing into this account in last 300s)
        """
        if not self.r or not dest_account:
            return 1, 1, 1

        now = time.time()
        key_dst = f"vel:dst:{dest_account}"
        key_senders = f"vel:dst_senders:{dest_account}"
        member = f"{tx_id}:{amount}" if tx_id else f"{now}:{amount}"

        try:
            pipe = self.r.pipeline()
            # 1. Inbound velocity tracking
            pipe.zadd(key_dst, {member: now})
            pipe.expire(key_dst, 3600)
            pipe.zremrangebyscore(key_dst, 0, now - 3600)
            pipe.zcount(key_dst, now - 60, now)
            pipe.zcount(key_dst, now - 300, now)

            # 2. Distinct senders tracking for Fan-In (Mule Account) detection
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
