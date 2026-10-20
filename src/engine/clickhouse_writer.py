import os
import clickhouse_connect
from src.common.models import Transaction, FraudAlert
from src.common.logger import get_logger

logger = get_logger("ClickHouseWriter")

class ClickHouseWriter:
    """
    Inserts transactions and alerts into ClickHouse.
    """

    def __init__(self, host: str = "localhost", port: int = 8123, user: str = "default", password: str = "", database: str = "fraud_monitoring"):
        try:
            self.client = clickhouse_connect.get_client(
                host=host,
                port=port,
                username=user,
                password=password,
                database=database
            )
            logger.info(f"Connected to ClickHouse at {host}:{port}/{database}")
        except Exception as e:
            logger.warning(f"ClickHouse connection failed ({e}). Database logging disabled.")
            self.client = None

    def insert_transaction(self, tx: Transaction):
        if not self.client:
            return

        row = [
            tx.tx_id,
            tx.timestamp,
            tx.channel,
            tx.source_bank,
            tx.source_account,
            tx.dest_bank,
            tx.dest_account,
            tx.amount,
            tx.currency,
            tx.status,
            tx.response_code,
            tx.velocity_1m,
            tx.velocity_5m,
            tx.amount_1h,
            tx.risk_score,
            1 if tx.is_fraud else 0,
            tx.reasons
        ]

        try:
            self.client.insert(
                'transactions',
                [row],
                column_names=[
                    'tx_id', 'timestamp', 'channel', 'source_bank', 'source_account',
                    'dest_bank', 'dest_account', 'amount', 'currency', 'status',
                    'response_code', 'velocity_1m', 'velocity_5m', 'amount_1h',
                    'risk_score', 'is_fraud', 'reasons'
                ]
            )
        except Exception as e:
            logger.error(f"Failed to insert transaction into ClickHouse: {e}")

    def insert_alert(self, tx: Transaction):
        if not self.client:
            return

        row = [
            tx.tx_id,
            tx.timestamp,
            tx.source_bank,
            tx.source_account,
            tx.dest_bank,
            tx.dest_account,
            tx.amount,
            tx.currency,
            tx.risk_score,
            tx.reasons,
            'NEW',
            1,
            0
        ]

        try:
            self.client.insert(
                'fraud_alerts',
                [row],
                column_names=[
                    'tx_id', 'timestamp', 'source_bank', 'source_account',
                    'dest_bank', 'dest_account', 'amount', 'currency',
                    'risk_score', 'reasons', 'alert_status', 'notified_telegram',
                    'notified_webhook'
                ]
            )
        except Exception as e:
            logger.error(f"Failed to insert fraud alert into ClickHouse: {e}")
