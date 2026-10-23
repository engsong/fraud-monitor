import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

def trace_transaction_chain(cur, tx_id=None, source_acc=None, dest_acc=None):
    # 1. Fetch the target / seed transaction
    if tx_id:
        cur.execute("""
            SELECT id, tx_id, node, source_bank, source_account, dest_bank, dest_account,
                   amount, currency, risk_score, array_to_string(reasons, ', ') as reasons,
                   timestamp, created_at
            FROM fraud_monitoring.transactions
            WHERE tx_id = %s OR id::text = %s
            LIMIT 1;
        """, (str(tx_id), str(tx_id)))
        target_tx = cur.fetchone()
    else:
        cur.execute("""
            SELECT id, tx_id, node, source_bank, source_account, dest_bank, dest_account,
                   amount, currency, risk_score, array_to_string(reasons, ', ') as reasons,
                   timestamp, created_at
            FROM fraud_monitoring.transactions
            WHERE (source_account = %s AND dest_account = %s)
               OR dest_account = %s OR source_account = %s
            ORDER BY id DESC LIMIT 1;
        """, (source_acc, dest_acc, dest_acc, source_acc))
        target_tx = cur.fetchone()

    if not target_tx:
        return {"error": "Transaction not found", "chain": [], "ring_detected": False}

    src_acc = target_tx["source_account"]
    dst_acc = target_tx["dest_account"]
    tx_time = target_tx["timestamp"] or target_tx["created_at"] or datetime.utcnow()

    chain = []
    visited_accounts = {src_acc: 1}

    # 2. Trace Preceding Inflows into Source Account (up to 2 hops backwards within 24h before)
    if src_acc and src_acc != 'N/A':
        cur.execute("""
            SELECT id, tx_id, node, source_bank, source_account, dest_bank, dest_account,
                   amount, currency, risk_score, array_to_string(reasons, ', ') as reasons,
                   timestamp, created_at
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s 
              AND (timestamp <= %s OR created_at <= %s)
              AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour')
              AND id != %s
            ORDER BY COALESCE(timestamp, created_at) DESC
            LIMIT 3;
        """, (src_acc, tx_time, tx_time, tx_time, tx_time, target_tx["id"]))
        inflows = cur.fetchall()
        for inf in reversed(inflows):
            inf_time = inf["timestamp"] or inf["created_at"]
            chain.append({
                "hop_id": inf["id"],
                "role": "INFLOW_PRECURSOR",
                "label": "1. Prior Inflow Layering",
                "source_bank": inf["source_bank"],
                "source_account": inf["source_account"],
                "dest_bank": inf["dest_bank"],
                "dest_account": inf["dest_account"],
                "amount": float(inf["amount"]),
                "currency": inf["currency"],
                "time": inf_time.strftime("%H:%M:%S") if inf_time else "N/A",
                "risk_score": inf["risk_score"] or 0,
                "reasons": inf["reasons"] or "Precursor Transfer"
            })
            visited_accounts[inf["source_account"]] = 1

    # 3. Add Primary Inspected Transaction
    chain.append({
        "hop_id": target_tx["id"],
        "role": "PRIMARY_INSPECTED",
        "label": "2. Inspected Transfer (Alert Trigger)",
        "source_bank": target_tx["source_bank"],
        "source_account": target_tx["source_account"],
        "dest_bank": target_tx["dest_bank"],
        "dest_account": target_tx["dest_account"],
        "amount": float(target_tx["amount"]),
        "currency": target_tx["currency"],
        "time": tx_time.strftime("%H:%M:%S") if tx_time else "N/A",
        "risk_score": target_tx["risk_score"] or 0,
        "reasons": target_tx["reasons"] or "Target Transaction"
    })
    visited_accounts[dst_acc] = 2

    # 4. Trace Downstream Outflows from Destination Account (Forward hops within 24h after)
    ring_detected = False
    ring_type = None
    ring_path = []

    if dst_acc and dst_acc != 'N/A':
        cur.execute("""
            SELECT id, tx_id, node, source_bank, source_account, dest_bank, dest_account,
                   amount, currency, risk_score, array_to_string(reasons, ', ') as reasons,
                   timestamp, created_at
            FROM fraud_monitoring.transactions
            WHERE source_account = %s 
              AND (timestamp >= %s OR created_at >= %s)
              AND (timestamp <= %s + INTERVAL '24 hour' OR created_at <= %s + INTERVAL '24 hour')
              AND id != %s
            ORDER BY COALESCE(timestamp, created_at) ASC
            LIMIT 4;
        """, (dst_acc, tx_time, tx_time, tx_time, tx_time, target_tx["id"]))
        outflows_hop1 = cur.fetchall()

        for out1 in outflows_hop1:
            out1_time = out1["timestamp"] or out1["created_at"]
            out1_dst = out1["dest_account"]

            # Check if out1 closes a ring back to src_acc or any predecessor!
            is_circle = out1_dst in visited_accounts or out1["dest_bank"] == target_tx["source_bank"]
            if is_circle and not ring_detected:
                ring_detected = True
                ring_type = "DIRECT_2_HOP_RING" if out1_dst == src_acc else "BANK_CIRCULAR_LOOP"
                ring_path = [target_tx["source_bank"], target_tx["dest_bank"], out1["dest_bank"]]

            chain.append({
                "hop_id": out1["id"],
                "role": "RING_CLOSURE" if is_circle else "OUTFLOW_DISPERSION",
                "label": "3. Mule Cashout / Loop Return" if is_circle else "3. Mule Dispersion Outflow",
                "source_bank": out1["source_bank"],
                "source_account": out1["source_account"],
                "dest_bank": out1["dest_bank"],
                "dest_account": out1["dest_account"],
                "amount": float(out1["amount"]),
                "currency": out1["currency"],
                "time": out1_time.strftime("%H:%M:%S") if out1_time else "N/A",
                "risk_score": out1["risk_score"] or 0,
                "reasons": out1["reasons"] or ("Circular Loop Return" if is_circle else "Downstream Transfer"),
                "is_ring_closure": is_circle
            })

            # 5. Check Hop 3 (if not already a ring)
            if not ring_detected and out1_dst and out1_dst != 'N/A' and len(chain) < 6:
                visited_accounts[out1_dst] = 3
                cur.execute("""
                    SELECT id, tx_id, node, source_bank, source_account, dest_bank, dest_account,
                           amount, currency, risk_score, array_to_string(reasons, ', ') as reasons,
                           timestamp, created_at
                    FROM fraud_monitoring.transactions
                    WHERE source_account = %s 
                      AND (timestamp >= %s OR created_at >= %s)
                      AND (timestamp <= %s + INTERVAL '24 hour' OR created_at <= %s + INTERVAL '24 hour')
                      AND id != %s
                    ORDER BY COALESCE(timestamp, created_at) ASC
                    LIMIT 2;
                """, (out1_dst, out1_time, out1_time, out1_time, out1_time, out1["id"]))
                outflows_hop2 = cur.fetchall()
                for out2 in outflows_hop2:
                    out2_time = out2["timestamp"] or out2["created_at"]
                    out2_dst = out2["dest_account"]
                    is_circle2 = out2_dst in visited_accounts or out2["dest_bank"] == target_tx["source_bank"]
                    if is_circle2 and not ring_detected:
                        ring_detected = True
                        ring_type = "MULTI_HOP_RING_3_HOP"
                        ring_path = [target_tx["source_bank"], target_tx["dest_bank"], out1["dest_bank"], out2["dest_bank"]]

                    chain.append({
                        "hop_id": out2["id"],
                        "role": "RING_CLOSURE" if is_circle2 else "OUTFLOW_HOP3",
                        "label": "4. Multi-Hop Ring Closure" if is_circle2 else "4. Secondary Dispersion",
                        "source_bank": out2["source_bank"],
                        "source_account": out2["source_account"],
                        "dest_bank": out2["dest_bank"],
                        "dest_account": out2["dest_account"],
                        "amount": float(out2["amount"]),
                        "currency": out2["currency"],
                        "time": out2_time.strftime("%H:%M:%S") if out2_time else "N/A",
                        "risk_score": out2["risk_score"] or 0,
                        "reasons": out2["reasons"] or ("Circular Ring Closure" if is_circle2 else "Tertiary Transfer"),
                        "is_ring_closure": is_circle2
                    })

    # 6. Gather Mule Statistics on the destination account
    fan_in_count = 0
    fan_out_count = 0
    if dst_acc and dst_acc != 'N/A':
        cur.execute("""
            SELECT count(DISTINCT source_account) AS senders, count(*) AS total_in
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour');
        """, (dst_acc, tx_time, tx_time))
        fi = cur.fetchone()
        fan_in_count = fi["senders"] if fi else 0

        cur.execute("""
            SELECT count(DISTINCT dest_account) AS recipients, count(*) AS total_out
            FROM fraud_monitoring.transactions
            WHERE source_account = %s AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour');
        """, (dst_acc, tx_time, tx_time))
        fo = cur.fetchone()
        fan_out_count = fo["recipients"] if fo else 0

    return {
        "target_tx": {
            "id": target_tx["id"],
            "tx_id": target_tx["tx_id"],
            "source_bank": target_tx["source_bank"],
            "source_account": target_tx["source_account"],
            "dest_bank": target_tx["dest_bank"],
            "dest_account": target_tx["dest_account"],
            "amount": float(target_tx["amount"]),
            "currency": target_tx["currency"],
            "time": tx_time.strftime("%H:%M:%S") if tx_time else "N/A"
        },
        "chain": chain,
        "total_hops": len(chain),
        "ring_detected": ring_detected,
        "ring_type": ring_type,
        "ring_path": ring_path,
        "mule_stats": {
            "unique_senders": fan_in_count,
            "unique_recipients": fan_out_count,
            "is_mule": fan_in_count >= 3 or (fan_in_count >= 2 and fan_out_count >= 1)
        }
    }

def test():
    conn = psycopg2.connect(host='127.0.0.1', user='lev', password='LEV@2026', dbname='lapnet_fraud_db')
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Test with a known high-risk transaction or mule alert
    cur.execute("""
        SELECT tx_id, source_account, dest_account, array_to_string(reasons, ', ') AS triggers, risk_score
        FROM fraud_monitoring.transactions
        WHERE array_to_string(reasons, ', ') ILIKE '%Mule%' OR risk_score >= 80
        ORDER BY id DESC LIMIT 3;
    """)
    alerts = cur.fetchall()
    for a in alerts:
        print("\n-----------------------------------------------------------")
        print(f"Testing Alert TX ID: {a['tx_id']} | Risk: {a['risk_score']}")
        res = trace_transaction_chain(cur, tx_id=a['tx_id'])
        print(f"Hops in Chain: {res['total_hops']}")
        print(f"Ring Detected: {res['ring_detected']} ({res['ring_type']}) Path: {res['ring_path']}")
        print(f"Mule Stats: Senders={res['mule_stats']['unique_senders']}, Recipients={res['mule_stats']['unique_recipients']}")
        for h in res['chain']:
            closure_tag = " [!!! RING CLOSURE !!!]" if h.get("is_ring_closure") else ""
            print(f"  Hop: {h['source_bank']}:{h['source_account'][:12]}... ➔ {h['dest_bank']}:{h['dest_account'][:12]}... | {h['amount']:,.2f} {h['currency']} | {h['label']}{closure_tag}")

if __name__ == '__main__':
    test()
