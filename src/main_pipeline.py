import os
import time
from dotenv import load_dotenv

load_dotenv()

from src.common.models import Transaction
from src.common.logger import get_logger
from src.ingestion.parser import TransactionParser
from src.engine.redis_velocity import RedisVelocityTracker
from src.engine.rules import RuleEngine
from src.engine.ai_model import AIAnomalyScorer
from src.engine.postgres_writer import PostgresWriter
from src.alert.telegram_alert import TelegramAlertDispatcher

logger = get_logger("MainPipeline")

class FraudDetectionPipeline:
    """
    Main Real-Time Fraud Detection Pipeline.
    Orchestrates: Parsing -> Velocity -> Rules -> AI Scoring -> Alerting -> Storage (PostgreSQL & ClickHouse).
    """

    def __init__(self):
        # 1. Configuration
        self.alert_threshold = int(os.getenv("RISK_ALERT_THRESHOLD", 80))
        max_vel = int(os.getenv("MAX_VELOCITY_1MIN", 5))
        max_amt = float(os.getenv("MAX_SINGLE_AMOUNT", 50000000.0))
        self.db_engine = os.getenv("DB_ENGINE", "postgres").lower()

        # 2. Components
        self.parser = TransactionParser()
        self.velocity_tracker = RedisVelocityTracker(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            password=os.getenv("REDIS_PASSWORD") or None
        )
        self.rule_engine = RuleEngine(max_vel_1m=max_vel, max_single_amt=max_amt)
        self.ai_engine = AIAnomalyScorer()

        # Database: PostgreSQL (Primary as requested)
        self.pg_writer = PostgresWriter(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            user=os.getenv("POSTGRES_USER", "lev"),
            password=os.getenv("POSTGRES_PASSWORD", "LEV@2026"),
            dbname=os.getenv("POSTGRES_DB", "lapnet_eslip_db"),
            schema=os.getenv("POSTGRES_SCHEMA", "fraud_monitoring")
        )

        # Database: ClickHouse (Secondary/Optional)
        if self.db_engine in ["clickhouse", "both"]:
            try:
                from src.engine.clickhouse_writer import ClickHouseWriter
                self.ch_writer = ClickHouseWriter(
                    host=os.getenv("CLICKHOUSE_HOST", "localhost"),
                    port=int(os.getenv("CLICKHOUSE_PORT", 8123)),
                    user=os.getenv("CLICKHOUSE_USER", "default"),
                    password=os.getenv("CLICKHOUSE_PASSWORD", "lapnet2026"),
                    database=os.getenv("CLICKHOUSE_DB", "fraud_monitoring")
                )
            except Exception as e:
                logger.warning(f"ClickHouse initialization skipped: {e}")
                self.ch_writer = None
        else:
            self.ch_writer = None

        # Telegram Alerting
        self.alert_dispatcher = TelegramAlertDispatcher(
            token=os.getenv("TELEGRAM_BOT_TOKEN"),
            chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            enabled=os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
        )

        logger.info(f"Pipeline initialized. DB Engine: {self.db_engine} | Alert Threshold: {self.alert_threshold}")

    def process_raw_message(self, raw_message: str, node: str = "LMPS") -> Transaction:
        """
        Process a single raw transaction (LMPS log line, XML or JSON).
        Target latency: < 5ms.
        """
        start_time = time.time()

        # Step 1: Parse
        tx = self.parser.parse(raw_message)
        if not tx:
            return None

        # Step 2: Velocity & Blacklist (Both Sender & Receiver / Mule Detection)
        is_src_bl = self.velocity_tracker.is_blacklisted(tx.source_account)
        is_dst_bl = self.velocity_tracker.is_blacklisted(tx.dest_account)

        # 2a. Outbound Sender Velocity
        vel_1m, vel_5m, amt_1h = self.velocity_tracker.record_and_get_velocity(
            tx.source_account, tx.amount, tx_id=tx.tx_id
        )
        tx.velocity_1m = vel_1m
        tx.velocity_5m = vel_5m
        tx.amount_1h = amt_1h

        # 2b. Inbound Receiver Velocity & Fan-In (Mule Account Detection)
        dst_vel_1m, dst_vel_5m, dst_senders = self.velocity_tracker.record_and_get_dest_velocity(
            tx.dest_account, tx.source_account, tx.amount, tx_id=tx.tx_id
        )
        tx.dest_velocity_1m = dst_vel_1m
        tx.dest_velocity_5m = dst_vel_5m
        tx.dest_unique_senders_5m = dst_senders

        # Step 3: Rules (Evaluating both Sender and Beneficiary)
        rule_score, rule_reasons = self.rule_engine.evaluate(
            tx, is_blacklisted=is_src_bl, is_dest_blacklisted=is_dst_bl
        )

        # Step 4: AI Model
        ai_score, ai_reason = self.ai_engine.score(tx)

        # Step 5: Score Synthesis
        total_score = min(100, rule_score + int(ai_score * 0.5))
        tx.risk_score = total_score

        all_reasons = list(rule_reasons)
        if ai_reason and total_score >= 60:
            all_reasons.append(ai_reason)
        tx.reasons = all_reasons

        # Step 6: Alert Dispatch
        if tx.risk_score >= self.alert_threshold:
            tx.is_fraud = True
            logger.warning(
                f"🚨 [ALERT {node}] TX {tx.tx_id} | Score: {tx.risk_score}/100 | "
                f"{tx.source_bank}:{tx.source_account} -> {tx.dest_bank}:{tx.dest_account} | "
                f"Amount: {tx.amount:,.0f} {tx.currency} | Triggers: {', '.join(tx.reasons)}"
            )
            self.alert_dispatcher.send_alert(tx)
            self.pg_writer.insert_alert(tx, node=node)
            if self.ch_writer:
                self.ch_writer.insert_alert(tx)
        else:
            logger.info(
                f"✅ [NORMAL {node}] TX {tx.tx_id} | Score: {tx.risk_score}/100 | "
                f"{tx.source_bank} -> {tx.dest_bank} | Amount: {tx.amount:,.0f} {tx.currency}"
            )

        # Step 7: Persist All Transactions to PostgreSQL
        self.pg_writer.insert_transaction(tx, node=node)
        if self.ch_writer:
            self.ch_writer.insert_transaction(tx)

        elapsed_ms = (time.time() - start_time) * 1000
        logger.debug(f"TX {tx.tx_id} processed in {elapsed_ms:.2f} ms")

        return tx
