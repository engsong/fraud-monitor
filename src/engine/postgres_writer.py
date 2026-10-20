import os
import psycopg2
from psycopg2.extras import execute_values
from src.common.models import Transaction
from src.common.logger import get_logger

logger = get_logger("PostgresWriter")

class PostgresWriter:
    """
    Persists transactions and fraud alerts into PostgreSQL.
    Target: lapnet_eslip_db on 127.0.0.1:5432 (schema: fraud_monitoring)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5432, 
                 user: str = "lev", password: str = "LEV@2026", 
                 dbname: str = "lapnet_eslip_db", schema: str = "fraud_monitoring"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.dbname = dbname
        self.schema = schema
        self.conn = None
        self._connect()

    def _connect(self):
        try:
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                dbname=self.dbname,
                connect_timeout=3
            )
            self.conn.autocommit = True
            logger.info(f"Connected to PostgreSQL at {self.host}:{self.port}/{self.dbname} (schema: {self.schema})")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            self.conn = None

    def _ensure_connection(self):
        if self.conn is None or self.conn.closed != 0:
            self._connect()

    def insert_transaction(self, tx: Transaction, node: str = "LMPS"):
        self._ensure_connection()
        if not self.conn:
            return

        query = f"""
        INSERT INTO {self.schema}.transactions 
        (tx_id, timestamp, channel, source_bank, source_account, dest_bank, dest_account, 
         amount, currency, status, response_code, velocity_1m, velocity_5m, amount_1h, 
         risk_score, is_fraud, reasons, raw_reference, node)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """

        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (
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
                    tx.reasons,
                    tx.tx_id,
                    node
                ))
        except Exception as e:
            logger.error(f"Failed to insert transaction into PostgreSQL: {e}")

    def insert_alert(self, tx: Transaction, node: str = "LMPS"):
        self._ensure_connection()
        if not self.conn:
            return

        query = f"""
        INSERT INTO {self.schema}.fraud_alerts 
        (tx_id, timestamp, source_bank, source_account, dest_bank, dest_account, 
         amount, currency, risk_score, reasons, alert_status, node)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """

        try:
            with self.conn.cursor() as cur:
                cur.execute(query, (
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
                    node
                ))
        except Exception as e:
            logger.error(f"Failed to insert fraud alert into PostgreSQL: {e}")
