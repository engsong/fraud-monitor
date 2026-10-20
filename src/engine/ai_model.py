import numpy as np
from sklearn.ensemble import IsolationForest
from typing import Tuple
from src.common.models import Transaction
from src.common.logger import get_logger

logger = get_logger("AIEngine")

# Approximate conversion multipliers to normalize all currencies to LAK baseline
CURRENCY_RATES_TO_LAK = {
    "LAK": 1.0,
    "THB": 650.0,
    "USD": 22000.0,
    "CNY": 3100.0,
}

class AIAnomalyScorer:
    """
    Unsupervised Anomaly Detection using Isolation Forest.
    Evaluates multi-dimensional behavioral deviations without requiring labeled fraud data.
    Supports multi-currency normalization (LAK, USD, THB, CNY).
    """

    def __init__(self, contamination: float = 0.015):
        self.model = IsolationForest(
            n_estimators=100,
            contamination=contamination,
            random_state=42,
            n_jobs=-1
        )
        self.is_fitted = False
        self._initialize_baseline_model()

    def _initialize_baseline_model(self):
        """
        Synthesizes a realistic baseline normal distribution of switch transactions
        to enable zero-day scoring immediately upon startup.
        Features: [amount (LAK equivalent), hour, velocity_1m, velocity_5m, amount_1h (LAK equivalent)]
        """
        np.random.seed(42)
        n_samples = 2500

        # Normal typical distribution in base LAK scale
        amounts = np.random.exponential(scale=500000, size=n_samples) + 20000
        hours = np.random.normal(loc=14, scale=4, size=n_samples) % 24
        vel_1m = np.random.choice([1, 1, 1, 2], size=n_samples)
        vel_5m = np.random.choice([1, 2, 2, 3], size=n_samples)
        amt_1h = amounts * vel_5m

        X_train = np.column_stack([amounts, hours, vel_1m, vel_5m, amt_1h])
        self.model.fit(X_train)
        self.is_fitted = True
        logger.info("AI Anomaly Baseline Model successfully trained and ready (multi-currency normalized).")

    def score(self, tx: Transaction) -> Tuple[int, str]:
        """
        Returns (ai_risk_score: 0-100, reason_if_anomaly)
        """
        if not self.is_fitted:
            return 0, ""

        ccy = (tx.currency or "LAK").upper()
        rate = CURRENCY_RATES_TO_LAK.get(ccy, 1.0)
        norm_amt = tx.amount * rate
        norm_amt_1h = tx.amount_1h * rate

        feature_vector = np.array([[
            norm_amt,
            tx.timestamp.hour,
            tx.velocity_1m,
            tx.velocity_5m,
            norm_amt_1h
        ]])

        try:
            # decision_function: lower means more abnormal
            raw_score = self.model.decision_function(feature_vector)[0]

            # Normalize raw_score (typically ranges from -0.3 to +0.2) to 0-100
            # raw_score < 0 is considered anomalous
            normalized = int(max(0, min(100, (0.15 - raw_score) * 200)))

            reason = ""
            if normalized >= 65:
                reason = f"AI Statistical Outlier: Behavioral anomaly detected ({tx.amount:,.2f} {ccy}, Confidence: {normalized}%)"

            return normalized, reason
        except Exception as e:
            logger.error(f"AI scoring failed: {e}")
            return 0, ""

