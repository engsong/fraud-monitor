from typing import List, Tuple
from src.common.models import Transaction

# Currency-specific thresholds
LARGE_AMOUNT_THRESHOLDS = {
    "LAK": 50000000.0,   # 50 Million LAK (~$2,300)
    "THB": 80000.0,      # 80,000 THB (~$2,400)
    "USD": 2500.0,       # $2,500 USD
    "CNY": 15000.0,      # 15,000 CNY
}

OFF_HOURS_THRESHOLDS = {
    "LAK": 10000000.0,   # 10 Million LAK
    "THB": 15000.0,      # 15,000 THB
    "USD": 500.0,        # $500 USD
    "CNY": 3000.0,       # 3,000 CNY
}

PROBING_MICRO_THRESHOLDS = {
    "LAK": 10000.0,      # 10,000 LAK
    "THB": 20.0,         # 20 THB
    "USD": 1.0,          # $1 USD
    "CNY": 5.0,          # 5 CNY
}

class RuleEngine:
    """
    High-speed deterministic rule engine supporting multi-currency (LAK, USD, THB, CNY).
    """

    def __init__(self, max_vel_1m: int = 5):
        self.max_vel_1m = max_vel_1m

    def evaluate(self, tx: Transaction, is_blacklisted: bool) -> Tuple[int, List[str]]:
        score = 0
        reasons = []
        ccy = (tx.currency or "LAK").upper()

        # 1. Blacklist check (Immediate Critical)
        if is_blacklisted:
            score += 100
            reasons.append(f"Account {tx.source_account} is on Fraud Blacklist")
            return min(100, score), reasons

        # 2. Excessive velocity in 1 minute
        if tx.velocity_1m > self.max_vel_1m:
            score += 45
            reasons.append(f"High Velocity: {tx.velocity_1m} transfers in 60s (Limit: {self.max_vel_1m})")

        # 3. Sudden large transfer based on currency
        large_thresh = LARGE_AMOUNT_THRESHOLDS.get(ccy, 50000000.0)
        if tx.amount >= large_thresh:
            score += 35
            reasons.append(f"Large Transfer: {tx.amount:,.2f} {ccy} exceeds limit ({large_thresh:,.0f} {ccy})")

        # 4. Off-hours activity (01:00 AM - 04:30 AM) with significant amount
        hour = tx.timestamp.hour
        off_hour_thresh = OFF_HOURS_THRESHOLDS.get(ccy, 10000000.0)
        if 1 <= hour <= 4 and tx.amount >= off_hour_thresh:
            score += 25
            reasons.append(f"Off-Hours Activity: High-value {ccy} transfer at {tx.timestamp.strftime('%H:%M')} UTC")

        # 5. Rapid micro-transfers (Card / Account probing)
        micro_thresh = PROBING_MICRO_THRESHOLDS.get(ccy, 10000.0)
        if tx.amount <= micro_thresh and tx.velocity_1m >= 3:
            score += 35
            reasons.append(f"Probing Pattern: Rapid micro-transfers (<= {micro_thresh} {ccy}) {tx.velocity_1m} times in 1m")

        return min(100, score), reasons
