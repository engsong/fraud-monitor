from typing import List, Tuple
from src.common.models import Transaction

class RuleEngine:
    """
    High-speed deterministic rule engine.
    Returns (added_score, triggered_reasons)
    """

    def __init__(self, max_vel_1m: int = 5, max_single_amt: float = 50000000.0):
        self.max_vel_1m = max_vel_1m
        self.max_single_amt = max_single_amt

    def evaluate(self, tx: Transaction, is_blacklisted: bool) -> Tuple[int, List[str]]:
        score = 0
        reasons = []

        # 1. Blacklist check (Immediate Critical)
        if is_blacklisted:
            score += 100
            reasons.append(f"Account {tx.source_account} is on Fraud Blacklist")
            return min(100, score), reasons

        # 2. Excessive velocity in 1 minute
        if tx.velocity_1m > self.max_vel_1m:
            score += 45
            reasons.append(f"High Velocity: {tx.velocity_1m} transfers in 60 seconds (Limit: {self.max_vel_1m})")

        # 3. Sudden large amount
        if tx.amount >= self.max_single_amt:
            score += 35
            reasons.append(f"Large Transfer: Amount {tx.amount:,.0f} {tx.currency} exceeds threshold")

        # 4. Off-hours activity (01:00 AM - 04:30 AM) with significant amount
        hour = tx.timestamp.hour
        if 1 <= hour <= 4 and tx.amount >= 10000000:
            score += 25
            reasons.append(f"Off-Hours Activity: High-value transfer at {tx.timestamp.strftime('%H:%M')} UTC")

        # 5. Rapid small micro-transfers (Card / Account probing)
        if tx.amount < 10000 and tx.velocity_1m >= 3:
            score += 35
            reasons.append(f"Probing Pattern: Rapid small transfers ({tx.velocity_1m} times in 1m)")

        return min(100, score), reasons
