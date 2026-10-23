import os
import yaml
from datetime import datetime
from typing import List, Tuple, Dict, Any
from src.common.models import Transaction
from src.common.logger import get_logger

logger = get_logger("RuleEngine")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "rules_config.yaml")

DEFAULT_CURRENCIES = {
    "LAK": {
        "high_value_threshold": 50000000.0,
        "daily_volume_limit": 200000000.0,
        "structuring_floor": 40000000.0,
        "new_beneficiary_threshold": 20000000.0,
        "inbound_high_value_1h": 100000000.0,
        "off_hours_threshold": 10000000.0,
        "probing_micro_limit": 10000.0
    },
    "THB": {
        "high_value_threshold": 80000.0,
        "daily_volume_limit": 320000.0,
        "structuring_floor": 65000.0,
        "new_beneficiary_threshold": 32000.0,
        "inbound_high_value_1h": 160000.0,
        "off_hours_threshold": 16000.0,
        "probing_micro_limit": 20.0
    },
    "USD": {
        "high_value_threshold": 2500.0,
        "daily_volume_limit": 10000.0,
        "structuring_floor": 2000.0,
        "new_beneficiary_threshold": 1000.0,
        "inbound_high_value_1h": 5000.0,
        "off_hours_threshold": 500.0,
        "probing_micro_limit": 1.0
    },
    "CNY": {
        "high_value_threshold": 18000.0,
        "daily_volume_limit": 72000.0,
        "structuring_floor": 14000.0,
        "new_beneficiary_threshold": 7200.0,
        "inbound_high_value_1h": 36000.0,
        "off_hours_threshold": 3600.0,
        "probing_micro_limit": 5.0
    }
}

CROSS_BORDER_CORRIDORS = {"ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"}


class RuleEngine:
    """
    3-Tier Declarative Fraud Detection Rules Engine for Payment Switch Transactions.
    Implements all 42 rules defined in Functional Rules Specification Version 1.0:
      - Part 1: Sender-Side Rules (S001 - S015)
      - Part 2: Receiver-Side Rules (R001 - R015)
      - Part 3: Sender-Receiver Relationship Rules (SR001 - SR012)
    Rules and thresholds can be enabled/disabled and reconfigured in config/rules_config.yaml
    without modifying codebase.
    """

    def __init__(self, config_path: str = CONFIG_PATH, *args, **kwargs):
        self.config_path = config_path or CONFIG_PATH
        self.config: Dict[str, Any] = {}
        self.load_config()

        # Support optional backwards-compatibility keyword arguments
        if "max_single_amt" in kwargs and kwargs["max_single_amt"]:
            val = float(kwargs["max_single_amt"])
            for curr_cfg in self.config.get("currencies", {}).values():
                if isinstance(curr_cfg, dict):
                    curr_cfg["high_value_threshold"] = val
        if "max_vel_1m" in kwargs and kwargs["max_vel_1m"]:
            val = int(kwargs["max_vel_1m"])
            s002 = self.config.get("sender_rules", {}).get("S002")
            if isinstance(s002, dict):
                s002["threshold"] = val

    def load_config(self):
        """Loads or reloads config/rules_config.yaml"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
                logger.info(f"Loaded rules configuration from {self.config_path}")
                return
            except Exception as e:
                logger.error(f"Error loading {self.config_path}: {e}. Using fallback configuration.")
        else:
            logger.warning(f"Config file not found at {self.config_path}. Using fallback configuration.")

        self.config = {
            "currencies": DEFAULT_CURRENCIES,
            "sender_rules": {},
            "receiver_rules": {},
            "relationship_rules": {},
            "alert_policies": {"critical_score_threshold": 85, "high_score_threshold": 70, "medium_score_threshold": 40}
        }

    def _is_rule_enabled(self, rule_group: str, rule_id: str) -> bool:
        rules = self.config.get(rule_group, {})
        rule_def = rules.get(rule_id, {})
        return rule_def.get("enabled", True)

    def _get_rule_cfg(self, rule_group: str, rule_id: str) -> Dict[str, Any]:
        return self.config.get(rule_group, {}).get(rule_id, {})

    def evaluate(
        self, 
        tx: Transaction, 
        is_blacklisted: bool = False, 
        is_dest_blacklisted: bool = False,
        chain_context: Dict[str, Any] = None
    ) -> Tuple[int, List[str]]:
        """
        Evaluates a transaction across Sender, Receiver, and Relationship tiers.
        Updates tx.risk_score, tx.risk_level, tx.rule_ids, tx.reasons, and tx.evidence.
        Returns (risk_score, reasons).
        """
        chain = chain_context or {}
        ccy = (tx.currency or "LAK").upper()
        ccy_cfg = self.config.get("currencies", {}).get(ccy, DEFAULT_CURRENCIES.get(ccy, DEFAULT_CURRENCIES["LAK"]))
        
        score = 0
        reasons: List[str] = []
        rule_hits: List[str] = []
        evidence: Dict[str, Any] = {}
        amt = float(tx.amount or 0.0)

        # ======================================================================
        # PART 1: SENDER SIDE - FRAUD DETECTION RULES (S001 - S015)
        # ======================================================================

        # S013: Flagged receiver / sender blacklist (Immediate Critical)
        if is_blacklisted and self._is_rule_enabled("sender_rules", "S013"):
            score += 100
            rule_hits.append("S013")
            reasons.append(f"[S013] Sender Fraud Watchlist: Account {tx.source_account} ({tx.source_bank}) is on Verified Blacklist")
            evidence["S013"] = {"source_account": tx.source_account, "source_bank": tx.source_bank, "watchlist": True}

        # S001: High-value outgoing transfer
        s001_limit = ccy_cfg.get("high_value_threshold", 50000000.0)
        if amt >= s001_limit and self._is_rule_enabled("sender_rules", "S001"):
            cfg = self._get_rule_cfg("sender_rules", "S001")
            score += cfg.get("score", 40)
            rule_hits.append("S001")
            reasons.append(f"[S001] High-Value Transfer: {amt:,.2f} {ccy} exceeds reporting limit ({s001_limit:,.0f} {ccy})")
            evidence["S001"] = {"amount": amt, "currency": ccy, "threshold": s001_limit}

        # S002: Outgoing velocity (>= 10 in 1 minute)
        cfg_s002 = self._get_rule_cfg("sender_rules", "S002")
        s002_limit = cfg_s002.get("max_count", 10)
        if tx.velocity_1m >= s002_limit and self._is_rule_enabled("sender_rules", "S002"):
            score += cfg_s002.get("score", 85)
            rule_hits.append("S002")
            reasons.append(f"[S002] Critical Outgoing Velocity: {tx.velocity_1m} transfers in 60s (Threshold >= {s002_limit})")
            evidence["S002"] = {"velocity_1m": tx.velocity_1m, "limit": s002_limit}

        # S003: Many beneficiaries (>= 10 distinct receivers in 10 minutes)
        cfg_s003 = self._get_rule_cfg("sender_rules", "S003")
        s003_limit = cfg_s003.get("max_distinct_receivers", 10)
        if tx.sender_unique_receivers_10m >= s003_limit and self._is_rule_enabled("sender_rules", "S003"):
            score += cfg_s003.get("score", 65)
            rule_hits.append("S003")
            reasons.append(f"[S003] Rapid Dispersion to Multiple Beneficiaries: {tx.sender_unique_receivers_10m} distinct receivers in 10m")
            evidence["S003"] = {"unique_receivers": tx.sender_unique_receivers_10m, "limit": s003_limit}

        # S004: Repeated identical amounts (>= 5 in 5 minutes)
        cfg_s004 = self._get_rule_cfg("sender_rules", "S004")
        s004_limit = cfg_s004.get("max_count", 5)
        if tx.sender_identical_amt_count_5m >= s004_limit and self._is_rule_enabled("sender_rules", "S004"):
            score += cfg_s004.get("score", 60)
            rule_hits.append("S004")
            reasons.append(f"[S004] Repeated Identical Amounts: {tx.sender_identical_amt_count_5m} transfers of {amt:,.2f} {ccy} in 5m")
            evidence["S004"] = {"repeated_count": tx.sender_identical_amt_count_5m, "amount": amt}

        # S005: Daily outgoing volume (>= 200,000,000 LAK/day)
        s005_limit = ccy_cfg.get("daily_volume_limit", 200000000.0)
        if tx.amount_24h >= s005_limit and self._is_rule_enabled("sender_rules", "S005"):
            cfg_s005 = self._get_rule_cfg("sender_rules", "S005")
            score += cfg_s005.get("score", 65)
            rule_hits.append("S005")
            reasons.append(f"[S005] Daily Volume Cap Exceeded: {tx.amount_24h:,.2f} {ccy} in 24h (Cap: {s005_limit:,.0f} {ccy})")
            evidence["S005"] = {"amount_24h": tx.amount_24h, "limit": s005_limit}

        # S008: Potential structuring / Smurfing (Transfers just below reporting limit)
        s008_floor = ccy_cfg.get("structuring_floor", s001_limit * 0.80)
        if s008_floor <= amt < s001_limit and tx.velocity_10m >= 3 and self._is_rule_enabled("sender_rules", "S008"):
            cfg_s008 = self._get_rule_cfg("sender_rules", "S008")
            score += cfg_s008.get("score", 70)
            rule_hits.append("S008")
            reasons.append(f"[S008] Structuring / Smurfing Pattern: Repeated transfer of {amt:,.2f} {ccy} just below {s001_limit:,.0f} {ccy}")
            evidence["S008"] = {"amount": amt, "range": f"{s008_floor:,.0f} - {s001_limit:,.0f}"}

        # S009: New beneficiary / large transfer
        s009_limit = ccy_cfg.get("new_beneficiary_threshold", 20000000.0)
        if tx.pair_count_5m <= 1 and amt >= s009_limit and self._is_rule_enabled("sender_rules", "S009"):
            cfg_s009 = self._get_rule_cfg("sender_rules", "S009")
            score += cfg_s009.get("score", 60)
            rule_hits.append("S009")
            reasons.append(f"[S009] First-Time High-Value Pair: Initial transfer of {amt:,.2f} {ccy} (Threshold: {s009_limit:,.0f} {ccy})")
            evidence["S009"] = {"pair_transfers": tx.pair_count_5m, "amount": amt}

        # S010: Unusual hours with anomaly (01:00 AM - 05:00 AM Lao local time / 18:00 - 22:00 UTC)
        hour_utc = tx.timestamp.hour
        s010_limit = ccy_cfg.get("off_hours_threshold", 10000000.0)
        is_night = (18 <= hour_utc <= 22) or (1 <= hour_utc <= 4)
        if is_night and (amt >= s010_limit or tx.velocity_1m >= 3) and self._is_rule_enabled("sender_rules", "S010"):
            cfg_s010 = self._get_rule_cfg("sender_rules", "S010")
            score += cfg_s010.get("score", 35)
            rule_hits.append("S010")
            reasons.append(f"[S010] Off-Hours Anomaly: {amt:,.2f} {ccy} initiated during night hours ({tx.timestamp.strftime('%H:%M')} UTC)")
            evidence["S010"] = {"hour_utc": hour_utc, "amount": amt}

        # S011: Rapid failed attempts (>= 5 in 5m)
        cfg_s011 = self._get_rule_cfg("sender_rules", "S011")
        if tx.failed_attempts_5m >= cfg_s011.get("max_failed_count", 5) and self._is_rule_enabled("sender_rules", "S011"):
            score += cfg_s011.get("score", 40)
            rule_hits.append("S011")
            reasons.append(f"[S011] Rapid Failed Attempts: {tx.failed_attempts_5m} failed transactions/retries in 5m")
            evidence["S011"] = {"failed_attempts": tx.failed_attempts_5m}

        # S014: Cross-bank fan-out (>= 10 transfers to >= 3 banks in 5m)
        cfg_s014 = self._get_rule_cfg("sender_rules", "S014")
        if tx.velocity_5m >= 8 and tx.sender_unique_banks_5m >= 3 and self._is_rule_enabled("sender_rules", "S014"):
            score += cfg_s014.get("score", 70)
            rule_hits.append("S014")
            reasons.append(f"[S014] Cross-Bank Dispersion: Outgoing transfers fan-out to {tx.sender_unique_banks_5m} different banks in 5m")
            evidence["S014"] = {"velocity_5m": tx.velocity_5m, "banks_count": tx.sender_unique_banks_5m}

        # S015: Round-trip funds (From graph chain analysis)
        if chain.get("ring_detected") and self._is_rule_enabled("sender_rules", "S015"):
            cfg_s015 = self._get_rule_cfg("sender_rules", "S015")
            score += cfg_s015.get("score", 95)
            rule_hits.append("S015")
            reasons.append(f"[S015] Round-Trip Funds: Sender receives funds back through intermediate routing accounts")
            evidence["S015"] = {"ring_path": chain.get("ring_path", [])}


        # ======================================================================
        # PART 2: RECEIVER SIDE - FRAUD DETECTION RULES (R001 - R015)
        # ======================================================================

        # R010: Flagged receiver blacklist check (Immediate Critical)
        if is_dest_blacklisted and self._is_rule_enabled("receiver_rules", "R010"):
            score += 100
            rule_hits.append("R010")
            reasons.append(f"[R010] Mule / Fraud Watchlist: Beneficiary Account {tx.dest_account} ({tx.dest_bank}) on Verified Blacklist")
            evidence["R010"] = {"dest_account": tx.dest_account, "dest_bank": tx.dest_bank}

        # R001: Many senders to one receiver (Mule Collector Fan-In)
        cfg_r001 = self._get_rule_cfg("receiver_rules", "R001")
        r001_limit = cfg_r001.get("max_distinct_senders", 10)
        if tx.dest_unique_senders_5m >= r001_limit and self._is_rule_enabled("receiver_rules", "R001"):
            score += cfg_r001.get("score", 90)
            rule_hits.append("R001")
            reasons.append(f"[R001] Mule Collector Anomaly: {tx.dest_unique_senders_5m} distinct senders deposited into {tx.dest_bank} ({tx.dest_account}) in 5m")
            evidence["R001"] = {"distinct_senders": tx.dest_unique_senders_5m, "limit": r001_limit}
        elif tx.dest_unique_senders_5m >= 4:
            score += 55
            rule_hits.append("R001")
            reasons.append(f"[R001] Elevated Mule Convergence: {tx.dest_unique_senders_5m} distinct senders deposited into beneficiary in 5m")

        # R002: Incoming velocity (>= 20 in 5m)
        cfg_r002 = self._get_rule_cfg("receiver_rules", "R002")
        r002_limit = cfg_r002.get("max_count", 20)
        if tx.dest_velocity_5m >= r002_limit and self._is_rule_enabled("receiver_rules", "R002"):
            score += cfg_r002.get("score", 65)
            rule_hits.append("R002")
            reasons.append(f"[R002] Inbound Surge Velocity: Beneficiary received {tx.dest_velocity_5m} deposits in 5m (Threshold >= {r002_limit})")
            evidence["R002"] = {"dest_velocity_5m": tx.dest_velocity_5m}

        # R003: High incoming value (sum >= 100M LAK in 1h)
        r003_limit = ccy_cfg.get("inbound_high_value_1h", 100000000.0)
        if tx.dest_amount_1h >= r003_limit and self._is_rule_enabled("receiver_rules", "R003"):
            cfg_r003 = self._get_rule_cfg("receiver_rules", "R003")
            score += cfg_r003.get("score", 60)
            rule_hits.append("R003")
            reasons.append(f"[R003] Inbound Volume Spike: {tx.dest_amount_1h:,.2f} {ccy} received in 1h (Threshold: {r003_limit:,.0f} {ccy})")
            evidence["R003"] = {"dest_amount_1h": tx.dest_amount_1h, "limit": r003_limit}

        # R004: Identical incoming amounts (>= 10 same-amount in 10m)
        cfg_r004 = self._get_rule_cfg("receiver_rules", "R004")
        if tx.dest_identical_amt_count_10m >= cfg_r004.get("max_count", 10) and self._is_rule_enabled("receiver_rules", "R004"):
            score += cfg_r004.get("score", 60)
            rule_hits.append("R004")
            reasons.append(f"[R004] Identical Incoming Inflows: {tx.dest_identical_amt_count_10m} deposits of {amt:,.2f} {ccy} in 10m")
            evidence["R004"] = {"identical_inflows": tx.dest_identical_amt_count_10m}

        # R005: Multiple originating banks (>= 5 banks in 10m)
        cfg_r005 = self._get_rule_cfg("receiver_rules", "R005")
        if tx.dest_unique_banks_10m >= cfg_r005.get("max_distinct_banks", 5) and self._is_rule_enabled("receiver_rules", "R005"):
            score += cfg_r005.get("score", 65)
            rule_hits.append("R005")
            reasons.append(f"[R005] Inflow from Multiple Member Banks: Beneficiary received transfers from {tx.dest_unique_banks_10m} different banks in 10m")
            evidence["R005"] = {"originating_banks": tx.dest_unique_banks_10m}

        # R008: Rapid cash-out / pass-through
        if chain.get("is_rapid_cashout") and self._is_rule_enabled("receiver_rules", "R008"):
            cfg_r008 = self._get_rule_cfg("receiver_rules", "R008")
            score += cfg_r008.get("score", 90)
            rule_hits.append("R008")
            reasons.append(f"[R008] Rapid Pass-Through Cashout: >= 80% of received funds forwarded onward within 5m")
            evidence["R008"] = {"pass_through": True}

        # R012: Cross-border QR corridor anomaly
        is_cross_border = (tx.source_bank in CROSS_BORDER_CORRIDORS) or (tx.dest_bank in CROSS_BORDER_CORRIDORS) or ccy in ["THB", "USD", "CNY"]
        if is_cross_border and amt >= s009_limit and self._is_rule_enabled("receiver_rules", "R012"):
            cfg_r012 = self._get_rule_cfg("receiver_rules", "R012")
            score += cfg_r012.get("score", 60)
            rule_hits.append("R012")
            reasons.append(f"[R012] Cross-Border QR Inflow Anomaly: Out-of-profile {ccy} transaction ({amt:,.2f}) on international gateway")
            evidence["R012"] = {"channel": tx.channel, "corridor": tx.dest_bank or tx.source_bank}


        # ======================================================================
        # PART 3: SENDER-RECEIVER RELATIONSHIP RULES (SR001 - SR012)
        # ======================================================================

        # SR001: Repeated pair (>= 10 transfers in 5m)
        cfg_sr001 = self._get_rule_cfg("relationship_rules", "SR001")
        if tx.pair_count_5m >= cfg_sr001.get("max_count", 10) and self._is_rule_enabled("relationship_rules", "SR001"):
            score += cfg_sr001.get("score", 60)
            rule_hits.append("SR001")
            reasons.append(f"[SR001] Repeated Sender-Receiver Pair: {tx.pair_count_5m} transfers between same accounts in 5m")
            evidence["SR001"] = {"pair_transfers_5m": tx.pair_count_5m}

        # SR002: One-to-many (1 sender pays >= 10 receivers in 10m)
        cfg_sr002 = self._get_rule_cfg("relationship_rules", "SR002")
        if tx.sender_unique_receivers_10m >= cfg_sr002.get("max_distinct_receivers", 10) and self._is_rule_enabled("relationship_rules", "SR002"):
            score += cfg_sr002.get("score", 65)
            rule_hits.append("SR002")
            reasons.append(f"[SR002] One-to-Many Fan-Out Network: Single payer distributed funds to {tx.sender_unique_receivers_10m} receivers in 10m")
            evidence["SR002"] = {"receivers_10m": tx.sender_unique_receivers_10m}

        # SR003: Many-to-one (>= 10 senders pay 1 receiver in 5m)
        cfg_sr003 = self._get_rule_cfg("relationship_rules", "SR003")
        if tx.dest_unique_senders_5m >= cfg_sr003.get("max_distinct_senders", 10) and self._is_rule_enabled("relationship_rules", "SR003"):
            score += cfg_sr003.get("score", 90)
            rule_hits.append("SR003")
            reasons.append(f"[SR003] Many-to-One Mule Aggregation: {tx.dest_unique_senders_5m} distinct accounts pay same beneficiary in 5m")
            evidence["SR003"] = {"senders_5m": tx.dest_unique_senders_5m}

        # SR005: Circular flow (Observed A -> B -> C -> A Closed Fraud Ring)
        if chain.get("ring_detected") and self._is_rule_enabled("relationship_rules", "SR005"):
            cfg_sr005 = self._get_rule_cfg("relationship_rules", "SR005")
            score += cfg_sr005.get("score", 100)
            rule_hits.append("SR005")
            ring_path_str = " ➔ ".join(chain.get("ring_path", []))
            reasons.append(f"[SR005] Closed Circular Fraud Ring: Confirmed cycle {ring_path_str} returning to origin")
            evidence["SR005"] = {"cycle": chain.get("ring_path", []), "ring_type": chain.get("ring_type")}

        # SR006: Rapid pass-through (Linked movement within 2 minutes)
        if chain.get("rapid_pass_through_2m") and self._is_rule_enabled("relationship_rules", "SR006"):
            cfg_sr006 = self._get_rule_cfg("relationship_rules", "SR006")
            score += cfg_sr006.get("score", 90)
            rule_hits.append("SR006")
            reasons.append(f"[SR006] Rapid Pass-Through Movement: Multi-hop hop traversal executed in < 120 seconds")
            evidence["SR006"] = {"rapid_hop": True}

        # SR009: Fixed-value pair (Repeated identical amounts for same pair)
        cfg_sr009 = self._get_rule_cfg("relationship_rules", "SR009")
        if tx.pair_identical_amt_5m >= cfg_sr009.get("max_count", 4) and self._is_rule_enabled("relationship_rules", "SR009"):
            score += cfg_sr009.get("score", 40)
            rule_hits.append("SR009")
            reasons.append(f"[SR009] Fixed-Value Pair Repetition: {tx.pair_identical_amt_5m} identical {amt:,.2f} {ccy} transfers between same pair in 5m")
            evidence["SR009"] = {"identical_pair_transfers": tx.pair_identical_amt_5m}

        # SR010: Cross-bank mule network (Intermediaries routing across switches)
        if chain.get("is_cross_bank_mule") and self._is_rule_enabled("relationship_rules", "SR010"):
            cfg_sr010 = self._get_rule_cfg("relationship_rules", "SR010")
            score += cfg_sr010.get("score", 90)
            rule_hits.append("SR010")
            reasons.append(f"[SR010] Cross-Bank Mule Network: Intermediary accounts laundering funds across member switches")
            evidence["SR010"] = {"cross_bank_hops": chain.get("total_hops", 0)}

        # SR011: Round-trip pair (A -> B -> A within 10 minutes)
        if chain.get("ring_type") in ["ACCOUNT_CIRCULAR_RING", "DIRECT_2_HOP_RING"] and self._is_rule_enabled("relationship_rules", "SR011"):
            cfg_sr011 = self._get_rule_cfg("relationship_rules", "SR011")
            score += cfg_sr011.get("score", 80)
            rule_hits.append("SR011")
            reasons.append(f"[SR011] Direct Round-Trip Pair: A ➔ B ➔ A loopback completed within 10m")
            evidence["SR011"] = {"round_trip": True}

        # SR012: Fan-in / Fan-out (Many senders -> hub -> many receivers)
        if (tx.dest_unique_senders_5m >= 4 or chain.get("unique_senders", 0) >= 4) and (tx.sender_unique_receivers_10m >= 3) and self._is_rule_enabled("relationship_rules", "SR012"):
            cfg_sr012 = self._get_rule_cfg("relationship_rules", "SR012")
            score += cfg_sr012.get("score", 95)
            rule_hits.append("SR012")
            reasons.append(f"[SR012] Hub Fan-In / Fan-Out Dispersion: Aggregation from multiple senders followed by multi-account cashout")
            evidence["SR012"] = {"hub_aggregation": True}

        # ======================================================================
        # SYNTHESIS & SEVERITY CLASSIFICATION
        # ======================================================================
        final_score = min(100, score)
        policies = self.config.get("alert_policies", {})
        crit_thresh = policies.get("critical_score_threshold", 85)
        high_thresh = policies.get("high_score_threshold", 70)
        med_thresh = policies.get("medium_score_threshold", 40)

        if final_score >= crit_thresh or any(r in ["S002", "S013", "S015", "R001", "R010", "SR005", "SR012"] for r in rule_hits):
            risk_level = "Critical"
        elif final_score >= high_thresh:
            risk_level = "High"
        elif final_score >= med_thresh:
            risk_level = "Medium"
        else:
            risk_level = "Low"

        # Update transaction model
        tx.risk_score = final_score
        tx.risk_level = risk_level
        tx.rule_ids = rule_hits
        tx.reasons = reasons
        tx.evidence = evidence
        tx.is_fraud = final_score >= med_thresh

        return final_score, reasons
