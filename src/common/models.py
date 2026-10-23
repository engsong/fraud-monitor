from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class Transaction(BaseModel):
    tx_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    channel: str = "REST_API"  # ISO20022, REST_API, QR
    source_bank: str
    source_account: str
    dest_bank: str
    dest_account: str
    amount: float
    currency: str = "LAK"
    status: str = "SUCCESS"  # SUCCESS, FAILED, TIMEOUT
    response_code: str = "00"

    # Computed Real-Time Features (Sender / Outbound)
    velocity_1m: int = 0
    velocity_5m: int = 0
    velocity_10m: int = 0
    amount_1h: float = 0.0
    amount_24h: float = 0.0
    sender_unique_receivers_10m: int = 0
    sender_unique_banks_5m: int = 0
    sender_identical_amt_count_5m: int = 0
    failed_attempts_5m: int = 0

    # Computed Real-Time Features (Receiver / Inbound Mule Detection)
    dest_velocity_1m: int = 0
    dest_velocity_5m: int = 0
    dest_amount_1h: float = 0.0
    dest_unique_senders_5m: int = 0
    dest_unique_banks_10m: int = 0
    dest_identical_amt_count_10m: int = 0

    # Computed Real-Time Features (Pair & Relationship)
    pair_count_5m: int = 0
    pair_identical_amt_5m: int = 0

    # Risk Scoring & Rule Hits
    risk_score: int = 0  # 0 to 100
    risk_level: str = "Low"  # Critical, High, Medium, Low
    is_fraud: bool = False
    rule_ids: List[str] = Field(default_factory=list)  # E.g. ['S001', 'SR005']
    reasons: List[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)


class FraudAlert(BaseModel):
    tx_id: str
    timestamp: datetime
    source_bank: str
    source_account: str
    dest_bank: str
    dest_account: str
    amount: float
    currency: str
    risk_score: int
    risk_level: str = "High"
    rule_ids: List[str] = Field(default_factory=list)
    reasons: List[str]
    alert_status: str = "NEW"
