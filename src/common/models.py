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

    # Computed Real-Time Features
    velocity_1m: int = 0
    velocity_5m: int = 0
    amount_1h: float = 0.0

    # Risk Scoring Results
    risk_score: int = 0  # 0 to 100
    is_fraud: bool = False
    reasons: List[str] = Field(default_factory=list)


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
    reasons: List[str]
    alert_status: str = "NEW"
