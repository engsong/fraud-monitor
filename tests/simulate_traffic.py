import sys
import os
import time
import random
import json
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.main_pipeline import FraudDetectionPipeline
from src.common.logger import get_logger

logger = get_logger("Simulator")

BANKS = ["BCELVNVX", "LDBLVNVX", "JDBLVNVX", "APBLVNVX", "LVBLVNVX", "M_MONEY", "U_MONEY"]

def generate_normal_transaction(idx: int) -> str:
    """Generates a realistic normal everyday transaction"""
    return json.dumps({
        "tx_id": f"TXN-NORM-{int(time.time())}-{idx:04d}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": random.choice(["QR_PAYMENT", "MOBILE_APP", "REST_API"]),
        "source_bank": random.choice(BANKS),
        "source_account": f"010{random.randint(1000000, 9999999)}",
        "dest_bank": random.choice(BANKS),
        "dest_account": f"020{random.randint(1000000, 9999999)}",
        "amount": float(random.choice([50000, 100000, 250000, 500000, 1200000])),
        "currency": "LAK",
        "status": "SUCCESS",
        "response_code": "00"
    })

def generate_high_velocity_burst(target_account: str, burst_count: int = 7) -> list:
    """Simulates an account draining burst (fraud syndicate transferring repeatedly in seconds)"""
    txs = []
    for i in range(burst_count):
        txs.append(json.dumps({
            "tx_id": f"TXN-BURST-{int(time.time())}-{i:02d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": "MOBILE_APP",
            "source_bank": "BCELVNVX",
            "source_account": target_account,
            "dest_bank": "M_MONEY",
            "dest_account": f"02088{random.randint(10000, 99999)}",
            "amount": 8000000.0,
            "currency": "LAK",
            "status": "SUCCESS",
            "response_code": "00"
        }))
    return txs

def generate_large_transfer() -> str:
    """Simulates a sudden high amount transaction exceeding limits"""
    return json.dumps({
        "tx_id": f"TXN-LARGE-{int(time.time())}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": "REST_API",
        "source_bank": "APBLVNVX",
        "source_account": "01099887766",
        "dest_bank": "U_MONEY",
        "dest_account": "02055667788",
        "amount": 95000000.0,  # 95 Million LAK
        "currency": "LAK",
        "status": "SUCCESS",
        "response_code": "00"
    })

def generate_off_hours_transfer() -> str:
    """Simulates an abnormal off-hours transfer at 03:15 AM"""
    off_hour_time = datetime.now(timezone.utc).replace(hour=3, minute=15, second=0)
    return json.dumps({
        "tx_id": f"TXN-NIGHT-{int(time.time())}",
        "timestamp": off_hour_time.isoformat(),
        "channel": "REST_API",
        "source_bank": "JDBLVNVX",
        "source_account": "01077665544",
        "dest_bank": "BCELVNVX",
        "dest_account": "02033445566",
        "amount": 18000000.0,  # 18 Million LAK at 3:15 AM
        "currency": "LAK",
        "status": "SUCCESS",
        "response_code": "00"
    })

def run_simulation():
    print("=" * 70)
    print("   STARTING REAL-TIME FRAUD MONITORING SIMULATION TEST")
    print("=" * 70)

    pipeline = FraudDetectionPipeline()

    # Pre-populate a blacklisted account in Redis
    blacklisted_acc = "01099999999"
    pipeline.velocity_tracker.add_to_blacklist(blacklisted_acc)

    print("\n--- [Step 1: Normal Everyday Transactions] ---")
    for i in range(5):
        msg = generate_normal_transaction(i + 1)
        pipeline.process_raw_message(msg)
        time.sleep(0.1)

    print("\n--- [Step 2: ISO 20022 XML Message Test] ---")
    iso_sample_path = os.path.join(os.path.dirname(__file__), "sample_pacs008.xml")
    if os.path.exists(iso_sample_path):
        with open(iso_sample_path, "r", encoding="utf-8") as f:
            xml_content = f.read()
        pipeline.process_raw_message(xml_content)

    print("\n--- [Step 3: INJECTING FRAUD - High Velocity Account Draining] ---")
    victim_account = "01055443322"
    burst_txs = generate_high_velocity_burst(victim_account, burst_count=7)
    for b_tx in burst_txs:
        pipeline.process_raw_message(b_tx)
        time.sleep(0.05)

    print("\n--- [Step 4: INJECTING FRAUD - Large Single Amount Transfer] ---")
    large_tx = generate_large_transfer()
    pipeline.process_raw_message(large_tx)

    print("\n--- [Step 5: INJECTING FRAUD - Blacklisted Account Transfer] ---")
    bl_tx = json.dumps({
        "tx_id": f"TXN-BL-{int(time.time())}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": "MOBILE_APP",
        "source_bank": "BCELVNVX",
        "source_account": blacklisted_acc,
        "dest_bank": "LDBLVNVX",
        "dest_account": "02011223344",
        "amount": 500000.0,
        "currency": "LAK",
        "status": "SUCCESS",
        "response_code": "00"
    })
    pipeline.process_raw_message(bl_tx)

    print("\n--- [Step 6: INJECTING FRAUD - High Amount at 03:15 AM] ---")
    night_tx = generate_off_hours_transfer()
    pipeline.process_raw_message(night_tx)

    print("\n" + "=" * 70)
    print("   SIMULATION COMPLETE! Check above for [NORMAL] vs [HIGH RISK] alerts.")
    print("=" * 70)

if __name__ == "__main__":
    run_simulation()
