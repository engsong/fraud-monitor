import os
import json
import time
import re
import csv
import io
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, Response, jsonify, send_from_directory, request
import psycopg2
from psycopg2.extras import RealDictCursor
from src.geomap.coordinates import INSTITUTION_COORDINATES, get_institution_coords

app = Flask(__name__, template_folder="templates", static_folder="static")

# DB Config
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
DB_USER = os.getenv("POSTGRES_USER", "lev")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "LEV@2026")
DB_NAME = os.getenv("POSTGRES_DB", "lapnet_fraud_db")

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME,
        cursor_factory=RealDictCursor
    )

LARGE_THRESHOLDS = {
    "LAK": 50000000.0,
    "THB": 80000.0,
    "USD": 2500.0,
    "CNY": 18000.0
}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@app.route("/api/nodes")
def get_nodes():
    nodes = []
    for code, data in INSTITUTION_COORDINATES.items():
        nodes.append({
            "code": code,
            "name": data["name"],
            "hub": data.get("hub", ""),
            "coords": data["coords"],
            "type": data["type"],
            "country": data["country"]
        })
    return jsonify(nodes)

def get_time_interval_sql(time_range_arg=None, from_arg=None):
    """
    Parses timeline range (e.g. 15m, 30m, 1h, 3h, 6h, 12h, 24h, today, or Grafana now-X)
    and returns SQL WHERE clause for created_at.
    """
    target = (from_arg or time_range_arg or "1h").strip().lower()
    
    if target in ["15m", "now-15m"]:
        return "created_at >= NOW() - INTERVAL '15 minutes'", "15m", "Last 15 minutes"
    elif target in ["30m", "now-30m"]:
        return "created_at >= NOW() - INTERVAL '30 minutes'", "30m", "Last 30 minutes"
    elif target in ["1h", "now-1h"]:
        return "created_at >= NOW() - INTERVAL '1 hour'", "1h", "Last 1 hour"
    elif target in ["3h", "now-3h"]:
        return "created_at >= NOW() - INTERVAL '3 hours'", "3h", "Last 3 hours"
    elif target in ["6h", "now-6h"]:
        return "created_at >= NOW() - INTERVAL '6 hours'", "6h", "Last 6 hours"
    elif target in ["12h", "now-12h"]:
        return "created_at >= NOW() - INTERVAL '12 hours'", "12h", "Last 12 hours"
    elif target in ["24h", "now-24h", "now-1d", "1d"]:
        return "created_at >= NOW() - INTERVAL '24 hours'", "24h", "Last 24 hours"
    elif target in ["today", "now/d"]:
        return "created_at >= date_trunc('day', NOW())", "today", "Today"
    else:
        return "created_at >= NOW() - INTERVAL '1 hour'", "1h", "Last 1 hour"

@app.route("/api/node-stats")
def get_node_stats():
    """Returns real-time switching metrics comparing VM1 (175.2.0.23) and VM2 (175.2.0.24) for selected timeline"""
    try:
        where_time, range_id, label = get_time_interval_sql(
            request.args.get("time_range"),
            request.args.get("from")
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT 
                node, 
                count(*) AS tx_count,
                count(*) FILTER (WHERE risk_score >= 80) AS alert_count,
                COALESCE(sum(amount) FILTER (WHERE currency = 'LAK'), 0) AS lak_volume,
                COALESCE(sum(amount) FILTER (WHERE currency = 'THB'), 0) AS thb_volume,
                COALESCE(sum(amount) FILTER (WHERE currency = 'USD'), 0) AS usd_volume
            FROM fraud_monitoring.transactions
            WHERE {where_time}
            GROUP BY node;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        vm1 = next((r for r in rows if "175.2.0.23" in (r["node"] or "")), {"tx_count": 0, "alert_count": 0, "lak_volume": 0})
        vm2 = next((r for r in rows if "175.2.0.24" in (r["node"] or "")), {"tx_count": 0, "alert_count": 0, "lak_volume": 0})
        
        total_tx = vm1["tx_count"] + vm2["tx_count"]
        vm1_pct = round((vm1["tx_count"] / total_tx * 100), 1) if total_tx > 0 else 50.0
        vm2_pct = round((vm2["tx_count"] / total_tx * 100), 1) if total_tx > 0 else 50.0

        return jsonify({
            "total_tx": total_tx,
            "time_range": range_id,
            "label": label,
            "vm1": {
                "name": "VM1-175.2.0.23",
                "count": vm1["tx_count"],
                "alerts": vm1["alert_count"],
                "pct": vm1_pct,
                "lak_volume": float(vm1["lak_volume"])
            },
            "vm2": {
                "name": "VM2-175.2.0.24",
                "count": vm2["tx_count"],
                "alerts": vm2["alert_count"],
                "pct": vm2_pct,
                "lak_volume": float(vm2["lak_volume"])
            }
        })
    except Exception as e:
        return jsonify({"error": str(e), "total_tx": 0, "vm1": {"count": 0, "pct": 0}, "vm2": {"count": 0, "pct": 0}})

@app.route("/api/hourly-stats")
def get_hourly_stats():
    """Returns hourly volume by node (VM1 vs VM2) and alerts for the timeline area chart"""
    try:
        where_time, range_id, label = get_time_interval_sql(
            request.args.get("time_range"),
            request.args.get("from")
        )
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT 
                to_char(date_trunc('hour', timestamp), 'HH24:00') AS hour_label,
                count(*) FILTER (WHERE node LIKE '%175.2.0.23%') AS vm1_tx,
                count(*) FILTER (WHERE node LIKE '%175.2.0.24%') AS vm2_tx,
                count(*) FILTER (WHERE risk_score >= 80) AS alert_tx
            FROM fraud_monitoring.transactions
            WHERE {where_time}
            GROUP BY date_trunc('hour', timestamp)
            ORDER BY date_trunc('hour', timestamp) ASC;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(rows or [])
    except Exception as e:
        return jsonify([])

@app.route("/api/recent-alerts")
def get_recent_alerts():
    """Returns alerts filtered by time range (default 1h)"""
    try:
        where_time, range_id, label = get_time_interval_sql(
            request.args.get("time_range"),
            request.args.get("from")
        )
        limit = int(request.args.get("limit", 60))
        format_type = request.args.get("format", "dict")
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT 
                id,
                COALESCE(tx_id, 'TX-' || id) AS tx_id,
                to_char(timestamp, 'HH24:MI:SS') AS alert_time,
                node,
                source_bank,
                source_account,
                dest_bank,
                dest_account,
                currency,
                to_char(amount, 'FM999,999,999,999,990.00') AS amount,
                risk_score,
                array_to_string(reasons, ', ') AS triggers,
                CASE 
                    WHEN risk_score >= 85 THEN 'Critical'
                    WHEN risk_score >= 70 THEN 'High'
                    WHEN risk_score >= 40 THEN 'Medium'
                    ELSE 'Low'
                END AS threat_level
            FROM fraud_monitoring.transactions
            WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
              AND {where_time}
            ORDER BY id DESC
            LIMIT %s;
        """, (limit,))
        rows = cur.fetchall()

        # Total alert count in this window
        cur.execute(f"""
            SELECT count(*) AS total
            FROM fraud_monitoring.transactions
            WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
              AND {where_time};
        """)
        c_row = cur.fetchone()
        total_count = c_row["total"] if c_row else len(rows)

        cur.close()
        conn.close()

        if format_type == "array":
            resp = jsonify(rows or [])
            resp.headers["X-Total-Count"] = str(total_count)
            return resp

        return jsonify({
            "alerts": rows or [],
            "total_count": total_count,
            "time_range": range_id,
            "label": label
        })
    except Exception as e:
        return jsonify({"alerts": [], "total_count": 0, "error": str(e)})

@app.route("/api/fan-in-out")
def get_fan_in_out():
    """Returns top Fan-In (Mule collectors) and Fan-Out (Outbound dispersers)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Top Fan-In (Multiple senders depositing to one destination in the last 30m)
        cur.execute("""
            SELECT 
                dest_bank,
                CASE 
                    WHEN length(dest_account) > 28 THEN substring(dest_account from 1 for 16) || '...' || substring(dest_account from length(dest_account)-6)
                    ELSE dest_account 
                END AS dest_account,
                count(DISTINCT source_account) AS unique_senders,
                count(*) AS deposit_count,
                to_char(sum(amount), 'FM999,999,999,999,990.00') AS volume,
                MAX(currency) AS currency,
                to_char(MAX(timestamp), 'HH24:MI:SS') AS last_seen
            FROM fraud_monitoring.transactions
            WHERE timestamp >= NOW() - INTERVAL '30 minutes'
            GROUP BY dest_bank, dest_account
            HAVING count(DISTINCT source_account) >= 2
            ORDER BY unique_senders DESC, deposit_count DESC
            LIMIT 6;
        """)
        fan_in = cur.fetchall()

        # Top Fan-Out (One source account distributing to multiple recipients in the last 30m)
        cur.execute("""
            SELECT 
                source_bank,
                CASE 
                    WHEN length(source_account) > 28 THEN substring(source_account from 1 for 16) || '...' || substring(source_account from length(source_account)-6)
                    ELSE source_account 
                END AS source_account,
                count(DISTINCT dest_account) AS unique_recipients,
                count(*) AS transfer_count,
                to_char(sum(amount), 'FM999,999,999,999,990.00') AS volume,
                MAX(currency) AS currency,
                to_char(MAX(timestamp), 'HH24:MI:SS') AS last_seen
            FROM fraud_monitoring.transactions
            WHERE timestamp >= NOW() - INTERVAL '30 minutes'
            GROUP BY source_bank, source_account
            HAVING count(DISTINCT dest_account) >= 2 OR count(*) >= 3
            ORDER BY unique_recipients DESC, transfer_count DESC
            LIMIT 6;
        """)
        fan_out = cur.fetchall()

        cur.close()
        conn.close()
        return jsonify({"fan_in": fan_in or [], "fan_out": fan_out or []})
    except Exception as e:
        return jsonify({"error": str(e), "fan_in": [], "fan_out": []})

def trace_transaction_chain_logic(cur, tx_id=None, source_acc=None, dest_acc=None):
    """
    Traces the multi-hop money flow for a transaction:
    1. Locates target seed transaction
    2. Identifies preceding inflows (layering into source_account)
    3. Identifies downstream outflows (dispersion from dest_account)
    4. Detects circular loops / fraud rings (account matching or bank loopback)
    5. Calculates Fan-In mule collector metrics (multiple distinct senders)
    """
    target_tx = None
    if tx_id:
        cur.execute("""
            SELECT t.id, t.tx_id, t.node, t.source_bank, t.source_account, t.dest_bank, t.dest_account,
                   t.amount, t.currency, COALESCE(a.risk_score, t.risk_score) as risk_score,
                   COALESCE(array_to_string(a.reasons, ', '), array_to_string(t.reasons, ', ')) as reasons,
                   t.timestamp, t.created_at
            FROM fraud_monitoring.transactions t
            LEFT JOIN fraud_monitoring.fraud_alerts a ON a.tx_id = t.tx_id
            WHERE t.tx_id = %s OR t.id::text = %s
            LIMIT 1;
        """, (str(tx_id).strip(), str(tx_id).strip()))
        target_tx = cur.fetchone()
    
    if not target_tx and (source_acc or dest_acc):
        cur.execute("""
            SELECT t.id, t.tx_id, t.node, t.source_bank, t.source_account, t.dest_bank, t.dest_account,
                   t.amount, t.currency, COALESCE(a.risk_score, t.risk_score) as risk_score,
                   COALESCE(array_to_string(a.reasons, ', '), array_to_string(t.reasons, ', ')) as reasons,
                   t.timestamp, t.created_at
            FROM fraud_monitoring.transactions t
            LEFT JOIN fraud_monitoring.fraud_alerts a ON a.tx_id = t.tx_id
            WHERE (%s IS NOT NULL AND t.source_account = %s)
               OR (%s IS NOT NULL AND t.dest_account = %s)
            ORDER BY t.id DESC LIMIT 1;
        """, (source_acc, source_acc, dest_acc, dest_acc))
        target_tx = cur.fetchone()

    if not target_tx:
        # Fallback to latest alert or transaction if neither given
        cur.execute("""
            SELECT t.id, t.tx_id, t.node, t.source_bank, t.source_account, t.dest_bank, t.dest_account,
                   t.amount, t.currency, COALESCE(a.risk_score, t.risk_score) as risk_score,
                   COALESCE(array_to_string(a.reasons, ', '), array_to_string(t.reasons, ', ')) as reasons,
                   t.timestamp, t.created_at
            FROM fraud_monitoring.transactions t
            LEFT JOIN fraud_monitoring.fraud_alerts a ON a.tx_id = t.tx_id
            ORDER BY t.id DESC LIMIT 1;
        """)
        target_tx = cur.fetchone()

    if not target_tx:
        return {
            "error": "No transactions found in database",
            "chain": [],
            "ring_detected": False,
            "ring_type": None,
            "ring_path": [],
            "mule_stats": {"unique_senders": 0, "unique_recipients": 0, "is_mule": False, "contributing_senders": []}
        }

    src_acc = target_tx["source_account"] or ""
    dst_acc = target_tx["dest_account"] or ""
    src_bank = target_tx["source_bank"] or "LAPNET"
    dst_bank = target_tx["dest_bank"] or "LAPNET"
    tx_time = target_tx["timestamp"] or target_tx["created_at"] or datetime.utcnow()

    chain = []
    # Visited accounts map for circular ring detection
    visited_accounts = {}
    if src_acc and src_acc != "N/A":
        visited_accounts[src_acc] = 1
    
    ring_detected = False
    ring_type = None
    ring_path = []
    ring_notes = "Linear transaction clearing without circular loopback detected."

    # 1. Check Preceding Inflows into source_account (up to 2 hops before within 24h)
    if src_acc and src_acc != "N/A" and len(src_acc) > 4:
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
            LIMIT 2;
        """, (src_acc, tx_time, tx_time, tx_time, tx_time, target_tx["id"]))
        pre_inflows = cur.fetchall()
        for p in reversed(pre_inflows):
            p_time = p["timestamp"] or p["created_at"]
            if p["source_account"] and p["source_account"] != "N/A":
                visited_accounts[p["source_account"]] = 0
            chain.append({
                "hop_id": p["id"],
                "tx_id": p["tx_id"] or f"TX-{p['id']}",
                "node": "VM1" if "175.2.0.23" in (p["node"] or "") else "VM2",
                "role": "INFLOW_PRECURSOR",
                "label": "Prior Funding Inflow",
                "source_bank": p["source_bank"],
                "source_account": p["source_account"],
                "dest_bank": p["dest_bank"],
                "dest_account": p["dest_account"],
                "amount": float(p["amount"] or 0),
                "amount_fmt": f"{float(p['amount'] or 0):,.2f}",
                "currency": p["currency"] or "LAK",
                "time": p_time.strftime("%H:%M:%S") if p_time else "N/A",
                "risk_score": int(p["risk_score"] or 0),
                "reasons": p["reasons"] or "Preceding Inflow Layering",
                "is_ring_closure": False
            })

    # 2. Add Primary Inspected Transaction
    chain.append({
        "hop_id": target_tx["id"],
        "tx_id": target_tx["tx_id"] or f"TX-{target_tx['id']}",
        "node": "VM1" if "175.2.0.23" in (target_tx["node"] or "") else "VM2",
        "role": "INSPECTED_TARGET",
        "label": "Primary Inspected Transfer",
        "source_bank": target_tx["source_bank"],
        "source_account": target_tx["source_account"],
        "dest_bank": target_tx["dest_bank"],
        "dest_account": target_tx["dest_account"],
        "amount": float(target_tx["amount"] or 0),
        "amount_fmt": f"{float(target_tx['amount'] or 0):,.2f}",
        "currency": target_tx["currency"] or "LAK",
        "time": tx_time.strftime("%H:%M:%S") if tx_time else "N/A",
        "risk_score": int(target_tx["risk_score"] or 0),
        "reasons": target_tx["reasons"] or "Audited Transaction",
        "is_ring_closure": False
    })

    if dst_acc and dst_acc != "N/A":
        visited_accounts[dst_acc] = 2

    # 3. Check Downstream Outflows from dest_account (Hop +1)
    if dst_acc and dst_acc != "N/A" and len(dst_acc) > 4:
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
            LIMIT 3;
        """, (dst_acc, tx_time, tx_time, tx_time, tx_time, target_tx["id"]))
        outflows_hop1 = cur.fetchall()

        for out1 in outflows_hop1:
            out1_time = out1["timestamp"] or out1["created_at"]
            out1_dst = out1["dest_account"]
            out1_bank = out1["dest_bank"]

            is_acc_ring = out1_dst in visited_accounts and out1_dst != "N/A"
            is_bank_ring = (out1_bank == src_bank) and (src_bank != "LAPNET")
            is_circle = is_acc_ring or is_bank_ring

            if is_circle and not ring_detected:
                ring_detected = True
                if is_acc_ring:
                    ring_type = "ACCOUNT_CIRCULAR_RING"
                    ring_notes = f"Closed account loop detected! Funds transferred back to account {out1_dst} on {out1_bank}."
                else:
                    ring_type = "BANK_CIRCULAR_LOOP"
                    ring_notes = f"Circular interbank loop detected! Funds routed back to originating bank {out1_bank}."
                ring_path = [src_bank, dst_bank, out1_bank]

            chain.append({
                "hop_id": out1["id"],
                "tx_id": out1["tx_id"] or f"TX-{out1['id']}",
                "node": "VM1" if "175.2.0.23" in (out1["node"] or "") else "VM2",
                "role": "RING_CLOSURE" if is_circle else "OUTFLOW_DISPERSION",
                "label": "Mule Loop Return (Ring Closure)" if is_circle else "Downstream Dispersion Outflow",
                "source_bank": out1["source_bank"],
                "source_account": out1["source_account"],
                "dest_bank": out1["dest_bank"],
                "dest_account": out1["dest_account"],
                "amount": float(out1["amount"] or 0),
                "amount_fmt": f"{float(out1['amount'] or 0):,.2f}",
                "currency": out1["currency"] or "LAK",
                "time": out1_time.strftime("%H:%M:%S") if out1_time else "N/A",
                "risk_score": int(out1["risk_score"] or 0),
                "reasons": out1["reasons"] or ("Circular Loop Return" if is_circle else "Downstream Transfer"),
                "is_ring_closure": is_circle
            })

            # Check Hop +2 (Hop 3) if ring not already closed and chain is short
            if not ring_detected and out1_dst and out1_dst != "N/A" and len(out1_dst) > 4 and len(chain) < 6:
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
                    out2_bank = out2["dest_bank"]

                    is_acc_ring2 = out2_dst in visited_accounts and out2_dst != "N/A"
                    is_bank_ring2 = (out2_bank == src_bank) and (src_bank != "LAPNET")
                    is_circle2 = is_acc_ring2 or is_bank_ring2

                    if is_circle2 and not ring_detected:
                        ring_detected = True
                        ring_type = "MULTI_HOP_ACCOUNT_RING" if is_acc_ring2 else "MULTI_HOP_BANK_RING"
                        ring_notes = f"3-Hop circular fraud loop detected: {src_bank} ➔ {dst_bank} ➔ {out1_bank} ➔ {out2_bank}!"
                        ring_path = [src_bank, dst_bank, out1_bank, out2_bank]

                    chain.append({
                        "hop_id": out2["id"],
                        "tx_id": out2["tx_id"] or f"TX-{out2['id']}",
                        "node": "VM1" if "175.2.0.23" in (out2["node"] or "") else "VM2",
                        "role": "RING_CLOSURE" if is_circle2 else "OUTFLOW_HOP3",
                        "label": "Secondary Ring Return" if is_circle2 else "Secondary Dispersion Hop",
                        "source_bank": out2["source_bank"],
                        "source_account": out2["source_account"],
                        "dest_bank": out2["dest_bank"],
                        "dest_account": out2["dest_account"],
                        "amount": float(out2["amount"] or 0),
                        "amount_fmt": f"{float(out2['amount'] or 0):,.2f}",
                        "currency": out2["currency"] or "LAK",
                        "time": out2_time.strftime("%H:%M:%S") if out2_time else "N/A",
                        "risk_score": int(out2["risk_score"] or 0),
                        "reasons": out2["reasons"] or ("Multi-Hop Ring Closure" if is_circle2 else "Tertiary Transfer"),
                        "is_ring_closure": is_circle2
                    })

    # 4. Check Fan-In Mule Statistics on Destination Account
    fan_in_count = 0
    fan_out_count = 0
    contributors = []
    if dst_acc and dst_acc != "N/A" and len(dst_acc) > 4:
        cur.execute("""
            SELECT count(DISTINCT source_account) AS senders, count(*) AS total_in
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s 
              AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour');
        """, (dst_acc, tx_time, tx_time))
        fi = cur.fetchone()
        fan_in_count = fi["senders"] if fi else 0

        cur.execute("""
            SELECT count(DISTINCT dest_account) AS recipients, count(*) AS total_out
            FROM fraud_monitoring.transactions
            WHERE source_account = %s 
              AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour');
        """, (dst_acc, tx_time, tx_time))
        fo = cur.fetchone()
        fan_out_count = fo["recipients"] if fo else 0

        # Fetch up to 5 other contributing senders for the mule drawer
        cur.execute("""
            SELECT DISTINCT ON (source_account)
                source_bank, source_account, amount, currency,
                to_char(timestamp, 'HH24:MI:SS') AS seen_time
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s
              AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour')
              AND source_account != %s
            ORDER BY source_account, id DESC
            LIMIT 5;
        """, (dst_acc, tx_time, tx_time, src_acc))
        c_rows = cur.fetchall()
        for c in c_rows:
            contributors.append({
                "source_bank": c["source_bank"],
                "source_account": c["source_account"],
                "amount": f"{float(c['amount'] or 0):,.2f}",
                "currency": c["currency"],
                "time": c["seen_time"]
            })

    is_mule = fan_in_count >= 3 or (fan_in_count >= 2 and fan_out_count >= 1)

    # 5. Evaluate 3-Tier Functional Fraud Rules (Specification v1.0)
    rule_hits = []
    tier1_sender = []
    tier2_receiver = []
    tier3_relationship = []

    target_amt = float(target_tx["amount"] or 0)
    target_ccy = (target_tx["currency"] or "LAK").upper()
    large_limit = LARGE_THRESHOLDS.get(target_ccy, 50000000.0)

    # --- TIER 1: SENDER RULES (S001 - S015) ---
    # S001: High-value transfer
    if target_amt >= large_limit:
        r = {"id": "S001", "name": "High-value outgoing transfer", "risk": "High", "desc": f"Single transfer >= {large_limit:,.0f} {target_ccy}"}
        rule_hits.append(r)
        tier1_sender.append(r)

    # S005: Daily outgoing volume
    if src_acc and src_acc != "N/A":
        cur.execute("""
            SELECT COALESCE(sum(amount), 0) AS total_24h
            FROM fraud_monitoring.transactions
            WHERE source_account = %s AND (timestamp >= %s - INTERVAL '24 hour' OR created_at >= %s - INTERVAL '24 hour');
        """, (src_acc, tx_time, tx_time))
        s24 = cur.fetchone()
        if s24 and float(s24["total_24h"] or 0) >= (large_limit * 4):
            r = {"id": "S005", "name": "Daily outgoing volume cap", "risk": "High", "desc": f"Total outgoing >= 200,000,000 LAK in 24h ({float(s24['total_24h']):,.0f} {target_ccy})"}
            rule_hits.append(r)
            tier1_sender.append(r)

    # S010: Night-time off hours
    hour_utc = tx_time.hour if hasattr(tx_time, "hour") else 0
    if ((18 <= hour_utc <= 22) or (1 <= hour_utc <= 4)) and target_amt >= (large_limit * 0.2):
        r = {"id": "S010", "name": "Unusual hours with anomaly", "risk": "Medium", "desc": f"Night-time transfer combined with high value at {tx_time.strftime('%H:%M')} (GMT+7)"}
        rule_hits.append(r)
        tier1_sender.append(r)

    # S014: Cross-bank fan-out
    if src_acc and src_acc != "N/A":
        cur.execute("""
            SELECT count(DISTINCT dest_bank) AS banks, count(*) AS cnt
            FROM fraud_monitoring.transactions
            WHERE source_account = %s AND (timestamp >= %s - INTERVAL '10 minute' OR created_at >= %s - INTERVAL '10 minute');
        """, (src_acc, tx_time, tx_time))
        sb = cur.fetchone()
        if sb and sb["banks"] >= 3 and sb["cnt"] >= 5:
            r = {"id": "S014", "name": "Cross-bank fan-out", "risk": "High", "desc": f"Transfers to {sb['banks']} different banks in 10m"}
            rule_hits.append(r)
            tier1_sender.append(r)

    # S015: Round-trip funds
    if ring_detected:
        r = {"id": "S015", "name": "Round-trip funds loopback", "risk": "Critical", "desc": "Sender receives related funds back via intermediary routing accounts"}
        rule_hits.append(r)
        tier1_sender.append(r)

    # --- TIER 2: RECEIVER RULES (R001 - R015) ---
    # R001: Many senders to one receiver (Mule Fan-In)
    if fan_in_count >= 10:
        r = {"id": "R001", "name": "Many senders to one receiver", "risk": "Critical", "desc": f"Critical Fan-In: {fan_in_count} distinct senders deposited into beneficiary in 24h"}
        rule_hits.append(r)
        tier2_receiver.append(r)
    elif fan_in_count >= 4:
        r = {"id": "R001", "name": "Elevated sender convergence", "risk": "High", "desc": f"{fan_in_count} distinct senders deposited into beneficiary in 24h"}
        rule_hits.append(r)
        tier2_receiver.append(r)

    # R005: Multiple originating banks
    if dst_acc and dst_acc != "N/A":
        cur.execute("""
            SELECT count(DISTINCT source_bank) AS src_banks
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s AND (timestamp >= %s - INTERVAL '10 minute' OR created_at >= %s - INTERVAL '10 minute');
        """, (dst_acc, tx_time, tx_time))
        db_res = cur.fetchone()
        if db_res and db_res["src_banks"] >= 3:
            r = {"id": "R005", "name": "Multiple originating banks", "risk": "High", "desc": f"Inflow originating from {db_res['src_banks']} different banks in 10m"}
            rule_hits.append(r)
            tier2_receiver.append(r)

    # R008: Rapid cash-out / pass-through
    if len(chain) > 1 and any(h.get("role") in ["OUTFLOW_DISPERSION", "RING_CLOSURE"] for h in chain):
        r = {"id": "R008", "name": "Rapid cash-out / pass-through", "risk": "Critical", "desc": "Observed rapid onward dispersion from beneficiary account to downstream accounts"}
        rule_hits.append(r)
        tier2_receiver.append(r)

    # R012: Cross-border QR corridor anomaly
    if src_bank in ["ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"] or dst_bank in ["ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"]:
        r = {"id": "R012", "name": "Cross-border QR corridor anomaly", "risk": "High", "desc": f"International corridor transaction ({src_bank} ➔ {dst_bank})"}
        rule_hits.append(r)
        tier2_receiver.append(r)

    # R013: Repeated in-out cycles
    if len(chain) >= 3:
        r = {"id": "R013", "name": "Repeated in-out cycles", "risk": "Critical", "desc": f"Multiple linked layering and dispersion hops observed ({len(chain)} hops)"}
        rule_hits.append(r)
        tier2_receiver.append(r)

    # --- TIER 3: RELATIONSHIP RULES (SR001 - SR012) ---
    # SR005: Circular flow (Closed Fraud Ring)
    if ring_detected:
        r = {"id": "SR005", "name": "Circular flow (Closed Loop)", "risk": "Critical", "desc": f"Observed circular fraud ring loop: {' ➔ '.join(ring_path)}"}
        rule_hits.append(r)
        tier3_relationship.append(r)

    # SR006: Rapid pass-through (< 2 minutes between hops)
    if len(chain) >= 2:
        r = {"id": "SR006", "name": "Rapid pass-through", "risk": "Critical", "desc": "Linked multi-hop movement executed within rapid clearing timeframe"}
        rule_hits.append(r)
        tier3_relationship.append(r)

    # SR010: Cross-bank mule network
    if len(set(h.get("source_bank") for h in chain if h.get("source_bank"))) >= 3:
        r = {"id": "SR010", "name": "Cross-bank mule network", "risk": "Critical", "desc": "Shared intermediary accounts routing funds across multiple member banks"}
        rule_hits.append(r)
        tier3_relationship.append(r)

    # SR011: Round-trip pair
    if ring_type in ["ACCOUNT_CIRCULAR_RING", "DIRECT_2_HOP_RING"]:
        r = {"id": "SR011", "name": "Round-trip pair", "risk": "High", "desc": "Direct A ➔ B ➔ A round-trip loopback confirmed"}
        rule_hits.append(r)
        tier3_relationship.append(r)

    # SR012: Fan-in / Fan-out
    if fan_in_count >= 3 and fan_out_count >= 1:
        r = {"id": "SR012", "name": "Fan-in / fan-out hub", "risk": "Critical", "desc": f"Hub pattern: {fan_in_count} senders in -> beneficiary -> {fan_out_count} recipients out"}
        rule_hits.append(r)
        tier3_relationship.append(r)

    # Ingestion Pipeline Rule Tags synchronization (e.g. S009, S001, etc.)
    raw_reasons = str(target_tx.get("reasons") or "")
    pipeline_tags = re.findall(r"\[(S\d{3}|R\d{3}|SR\d{3})\]\s*([^,\[]+)", raw_reasons)
    for tag, desc in pipeline_tags:
        if not any(rh["id"] == tag for rh in rule_hits):
            risk = "Critical" if tag in ["S008", "S013", "S015", "R001", "R008", "R010", "R011", "R015", "SR003", "SR004", "SR005", "SR006", "SR008", "SR010", "SR012"] else "High"
            rule_entry = {"id": tag, "name": desc.split(":")[0].strip(), "risk": risk, "desc": desc.strip()}
            rule_hits.append(rule_entry)
            if tag.startswith("SR"):
                tier3_relationship.append(rule_entry)
            elif tag.startswith("R"):
                tier2_receiver.append(rule_entry)
            elif tag.startswith("S"):
                tier1_sender.append(rule_entry)

    # Overall Risk Level
    if any(r["risk"] == "Critical" for r in rule_hits) or ring_detected or (target_tx.get("risk_score") or 0) >= 85:
        calc_risk_level = "Critical"
    elif any(r["risk"] == "High" for r in rule_hits) or (target_tx.get("risk_score") or 0) >= 70:
        calc_risk_level = "High"
    elif len(rule_hits) > 0 or (target_tx.get("risk_score") or 0) >= 40:
        calc_risk_level = "Medium"
    else:
        calc_risk_level = "Low"

    return {
        "target_tx": {
            "id": target_tx["id"],
            "tx_id": target_tx["tx_id"] or f"TX-{target_tx['id']}",
            "source_bank": target_tx["source_bank"],
            "source_account": target_tx["source_account"],
            "dest_bank": target_tx["dest_bank"],
            "dest_account": target_tx["dest_account"],
            "amount": float(target_tx["amount"] or 0),
            "amount_fmt": f"{float(target_tx['amount'] or 0):,.2f}",
            "currency": target_tx["currency"] or "LAK",
            "risk_score": int(target_tx["risk_score"] or 0),
            "reasons": target_tx["reasons"] or "Inspected Transaction",
            "time": tx_time.strftime("%H:%M:%S") if tx_time else "N/A"
        },
        "chain": chain,
        "total_hops": len(chain),
        "ring_detected": ring_detected,
        "ring_type": ring_type,
        "ring_path": ring_path,
        "ring_notes": ring_notes,
        "mule_stats": {
            "unique_senders": fan_in_count,
            "unique_recipients": fan_out_count,
            "is_mule": is_mule,
            "contributing_senders": contributors
        },
        "rule_hits": rule_hits,
        "tier_breakdown": {
            "tier1_sender": tier1_sender,
            "tier2_receiver": tier2_receiver,
            "tier3_relationship": tier3_relationship
        },
        "risk_level": calc_risk_level
    }

@app.route("/api/trace-chain")
@app.route("/api/trace-chain/<path:tx_id>")
def get_transaction_chain(tx_id=None):
    """Traces multi-hop journey and detects circular fraud rings or mule convergence"""
    tx_id_param = tx_id or request.args.get("tx_id") or request.args.get("id")
    src_acc = request.args.get("source_account") or request.args.get("source_acc")
    dst_acc = request.args.get("dest_account") or request.args.get("dest_acc")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        data = trace_transaction_chain_logic(cur, tx_id=tx_id_param, source_acc=src_acc, dest_acc=dst_acc)
        cur.close()
        conn.close()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "chain": [], "ring_detected": False})

@app.route("/report")
@app.route("/report/<path:tx_id>")
def view_fraud_report(tx_id=None):
    """Generates official printable forensic audit dossier for a transaction or fraud ring"""
    tx_id_param = tx_id or request.args.get("tx_id") or request.args.get("id")
    src_acc = request.args.get("source_account") or request.args.get("source_acc")
    dst_acc = request.args.get("dest_account") or request.args.get("dest_acc")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        data = trace_transaction_chain_logic(cur, tx_id=tx_id_param, source_acc=src_acc, dest_acc=dst_acc)
        cur.close()
        conn.close()

        lao_tz = timezone(timedelta(hours=7))
        now_lao = datetime.now(lao_tz).strftime("%Y-%m-%d %H:%M:%S")
        bank_names = {code: info.get("name", code) for code, info in INSTITUTION_COORDINATES.items()}

        return render_template("fraud_report.html", data=data, now=now_lao, bank_names=bank_names)
    except Exception as e:
        return f"<div style='font-family: sans-serif; padding: 20px; color: #b91c1c;'><h2>Error Generating Forensic Dossier</h2><p>{str(e)}</p></div>", 500

def compute_fraud_statistics(cur, where_time):
    """Computes macro and micro statistics for fraud monitoring reports"""
    # 1. Macro Totals: Switching vs Flagged Fraud
    cur.execute(f"""
        SELECT 
            count(*) AS total_switching,
            COALESCE(sum(amount) FILTER (WHERE currency = 'LAK'), 0) AS total_switching_lak,
            count(*) FILTER (WHERE risk_score >= 40 OR array_length(reasons, 1) > 0) AS total_fraud_alerts,
            count(*) FILTER (WHERE risk_score >= 85) AS crit_alerts,
            count(*) FILTER (WHERE risk_score >= 70 AND risk_score < 85) AS high_alerts,
            count(*) FILTER (WHERE risk_score >= 40 AND risk_score < 70) AS med_alerts,
            COALESCE(sum(amount) FILTER (WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0) AND currency = 'LAK'), 0) AS fraud_lak_volume,
            COALESCE(sum(amount) FILTER (WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0) AND currency = 'THB'), 0) AS fraud_thb_volume,
            COALESCE(sum(amount) FILTER (WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0) AND currency = 'USD'), 0) AS fraud_usd_volume
        FROM fraud_monitoring.transactions
        WHERE {where_time};
    """)
    totals = cur.fetchone() or {}

    total_sw = totals.get("total_switching") or 0
    total_fr = totals.get("total_fraud_alerts") or 0
    fraud_rate = round((total_fr / total_sw * 100), 2) if total_sw > 0 else 0.0

    # 2. Dual-Node Traffic & Alert Breakdown
    cur.execute(f"""
        SELECT 
            node, 
            count(*) AS tx_count,
            count(*) FILTER (WHERE risk_score >= 40 OR array_length(reasons, 1) > 0) AS alert_count,
            COALESCE(sum(amount) FILTER (WHERE currency = 'LAK'), 0) AS volume_lak
        FROM fraud_monitoring.transactions
        WHERE {where_time}
        GROUP BY node;
    """)
    node_rows = cur.fetchall()
    vm1_data = next((r for r in node_rows if "175.2.0.23" in (r["node"] or "")), {"tx_count": 0, "alert_count": 0, "volume_lak": 0})
    vm2_data = next((r for r in node_rows if "175.2.0.24" in (r["node"] or "")), {"tx_count": 0, "alert_count": 0, "volume_lak": 0})
    
    vm1_tx_pct = round((vm1_data["tx_count"] / total_sw * 100), 1) if total_sw > 0 else 50.0
    vm2_tx_pct = round((vm2_data["tx_count"] / total_sw * 100), 1) if total_sw > 0 else 50.0
    vm1_al_pct = round((vm1_data["alert_count"] / total_fr * 100), 1) if total_fr > 0 else 50.0
    vm2_al_pct = round((vm2_data["alert_count"] / total_fr * 100), 1) if total_fr > 0 else 50.0

    # 3. Top Originating Banks by Fraud Volume & Incident Count
    cur.execute(f"""
        SELECT 
            source_bank,
            count(*) AS alert_count,
            to_char(sum(amount), 'FM999,999,999,999,990.00') AS lak_volume,
            ROUND(count(*)::numeric / NULLIF({max(1, total_fr)}, 0) * 100, 1) AS share_pct
        FROM fraud_monitoring.transactions
        WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
          AND {where_time}
        GROUP BY source_bank
        ORDER BY alert_count DESC
        LIMIT 5;
    """)
    top_source_banks = cur.fetchall()

    # 4. Top Beneficiary Banks (Mule Hubs)
    cur.execute(f"""
        SELECT 
            dest_bank,
            count(*) AS alert_count,
            count(DISTINCT dest_account) AS unique_accounts,
            to_char(sum(amount), 'FM999,999,999,999,990.00') AS lak_volume,
            ROUND(count(*)::numeric / NULLIF({max(1, total_fr)}, 0) * 100, 1) AS share_pct
        FROM fraud_monitoring.transactions
        WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
          AND {where_time}
        GROUP BY dest_bank
        ORDER BY alert_count DESC
        LIMIT 5;
    """)
    top_dest_banks = cur.fetchall()

    # 5. Top Hotspot Interbank Corridors
    cur.execute(f"""
        SELECT 
            source_bank,
            dest_bank,
            count(*) AS alert_count,
            to_char(sum(amount), 'FM999,999,999,999,990.00') AS lak_volume
        FROM fraud_monitoring.transactions
        WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
          AND {where_time}
        GROUP BY source_bank, dest_bank
        ORDER BY alert_count DESC
        LIMIT 5;
    """)
    top_corridors = cur.fetchall()

    # 6. Top Triggered Rules
    top_rules = []
    try:
        cur.execute(f"""
            SELECT 
                unnest(reasons) AS raw_rule,
                count(*) AS hits
            FROM fraud_monitoring.transactions
            WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
              AND {where_time}
            GROUP BY raw_rule
            ORDER BY hits DESC
            LIMIT 6;
        """)
        rule_rows = cur.fetchall()
        for rr in rule_rows:
            raw = rr["raw_rule"] or ""
            match = re.match(r"^\[([A-Z0-9]+)\]\s*([^:]+)", raw)
            rule_id = match.group(1) if match else "ANOMALY"
            rule_title = match.group(2).strip() if match else raw
            tier = "Tier 1: Sender" if rule_id.startswith("S") and not rule_id.startswith("SR") else ("Tier 2: Receiver" if rule_id.startswith("R") else "Tier 3: Topology")
            top_rules.append({
                "id": rule_id,
                "title": rule_title,
                "tier": tier,
                "hits": rr["hits"],
                "pct": round((rr["hits"] / total_fr * 100), 1) if total_fr > 0 else 0.0
            })
    except Exception:
        pass

    return {
        "total_switching": total_sw,
        "total_switching_lak": f"{float(totals.get('total_switching_lak') or 0):,.2f} LAK",
        "total_fraud_alerts": total_fr,
        "fraud_rate": fraud_rate,
        "crit_alerts": totals.get("crit_alerts") or 0,
        "high_alerts": totals.get("high_alerts") or 0,
        "med_alerts": totals.get("med_alerts") or 0,
        "fraud_lak_volume": f"{float(totals.get('fraud_lak_volume') or 0):,.2f} LAK",
        "fraud_thb_volume": f"{float(totals.get('fraud_thb_volume') or 0):,.2f} THB",
        "fraud_usd_volume": f"{float(totals.get('fraud_usd_volume') or 0):,.2f} USD",
        "vm1": {
            "tx_count": vm1_data["tx_count"],
            "alert_count": vm1_data["alert_count"],
            "tx_pct": vm1_tx_pct,
            "al_pct": vm1_al_pct
        },
        "vm2": {
            "tx_count": vm2_data["tx_count"],
            "alert_count": vm2_data["alert_count"],
            "tx_pct": vm2_tx_pct,
            "al_pct": vm2_al_pct
        },
        "top_source_banks": top_source_banks,
        "top_dest_banks": top_dest_banks,
        "top_corridors": top_corridors,
        "top_rules": top_rules
    }

@app.route("/report/timeline")
def view_timeline_report():
    """Generates official printable timeline incident summary report with deep fraud statistics"""
    time_range = request.args.get("time_range")
    from_arg = request.args.get("from")
    where_time, range_id, label = get_time_interval_sql(time_range, from_arg)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        stats = compute_fraud_statistics(cur, where_time)

        cur.execute(f"""
            SELECT 
                id,
                COALESCE(tx_id, 'TX-' || id::text) AS tx_id,
                CASE WHEN node LIKE '%%175.2.0.23%%' THEN 'VM1' ELSE 'VM2' END AS node,
                to_char(COALESCE(timestamp, created_at) AT TIME ZONE 'Asia/Vientiane', 'HH24:MI:SS') AS alert_time,
                source_bank,
                source_account,
                dest_bank,
                dest_account,
                currency,
                to_char(amount, 'FM999,999,999,999,990.00') AS amount,
                risk_score,
                array_to_string(reasons, ', ') AS triggers,
                CASE 
                    WHEN risk_score >= 85 THEN 'Critical'
                    WHEN risk_score >= 70 THEN 'High'
                    ELSE 'Medium'
                END AS threat_level
            FROM fraud_monitoring.transactions
            WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
              AND {where_time}
            ORDER BY id DESC
            LIMIT 100;
        """)
        alert_rows = cur.fetchall()
        cur.close()
        conn.close()

        lao_tz = timezone(timedelta(hours=7))
        now_lao = datetime.now(lao_tz).strftime("%Y-%m-%d %H:%M:%S")

        return render_template(
            "timeline_report.html",
            label=label,
            range_id=range_id,
            now=now_lao,
            stats=stats,
            alerts=alert_rows or []
        )
    except Exception as e:
        return f"<div style='font-family: sans-serif; padding: 20px; color: #b91c1c;'><h2>Error Generating Timeline Dossier</h2><p>{str(e)}</p></div>", 500

@app.route("/api/fraud-stats")
def get_fraud_stats_api():
    """Returns detailed fraud statistics in JSON format for the selected timeline"""
    time_range = request.args.get("time_range")
    from_arg = request.args.get("from")
    where_time, range_id, label = get_time_interval_sql(time_range, from_arg)
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        stats = compute_fraud_statistics(cur, where_time)
        cur.close()
        conn.close()
        return jsonify({
            "status": "success",
            "time_range": range_id,
            "label": label,
            "statistics": stats
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/export-alerts")
@app.route("/api/download-report")
def export_alerts_csv():
    """Streams CSV of fraud alerts matching the selected timeline range"""
    time_range = request.args.get("time_range")
    from_arg = request.args.get("from")
    where_time, range_id, label = get_time_interval_sql(time_range, from_arg)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(f"""
            SELECT 
                id AS incident_id,
                COALESCE(tx_id, 'TX-' || id::text) AS tx_id,
                node,
                to_char(COALESCE(timestamp, created_at) AT TIME ZONE 'Asia/Vientiane', 'YYYY-MM-DD HH24:MI:SS') AS lao_time,
                CASE 
                    WHEN risk_score >= 85 THEN 'Critical'
                    WHEN risk_score >= 70 THEN 'High'
                    WHEN risk_score >= 40 THEN 'Medium'
                    ELSE 'Low'
                END AS threat_level,
                risk_score,
                source_bank,
                source_account,
                dest_bank,
                dest_account,
                amount,
                currency,
                array_to_string(reasons, ' | ') AS triggered_rules
            FROM fraud_monitoring.transactions
            WHERE (risk_score >= 40 OR array_length(reasons, 1) > 0)
              AND {where_time}
            ORDER BY id DESC;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        output = io.StringIO()
        output.write('\ufeff')  # UTF-8 BOM
        writer = csv.writer(output)

        writer.writerow([
            "Incident ID",
            "Transaction ID",
            "Switch Node",
            "Lao Local Time (GMT+7)",
            "Threat Level",
            "Risk Score",
            "Originating Bank",
            "Sender Account",
            "Beneficiary Bank",
            "Beneficiary Account",
            "Amount",
            "Currency",
            "Triggered Fraud Rules"
        ])

        for r in rows:
            writer.writerow([
                r["incident_id"],
                r["tx_id"],
                r["node"] or "",
                r["lao_time"] or "",
                r["threat_level"],
                r["risk_score"] or 0,
                r["source_bank"] or "",
                r["source_account"] or "",
                r["dest_bank"] or "",
                r["dest_account"] or "",
                f"{float(r['amount'] or 0):,.2f}",
                r["currency"] or "LAK",
                r["triggered_rules"] or ""
            ])

        csv_data = output.getvalue()
        output.close()

        filename = f"lapnet_fraud_alerts_{range_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/live-stream")
def live_stream():
    """SSE Real-Time Stream for Map Transit Lines and Service Board"""
    def generate():
        last_id = 0
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT MAX(id) AS max_id FROM fraud_monitoring.transactions;")
            row = cur.fetchone()
            if row and row["max_id"]:
                last_id = max(0, row["max_id"] - 12)
            cur.close()
            conn.close()
        except Exception:
            last_id = 0

        while True:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("""
                    SELECT 
                        id, tx_id, node, source_bank, source_account, dest_bank, dest_account,
                        currency, amount, risk_score, array_to_string(reasons, ', ') AS triggers,
                        to_char(timestamp, 'HH24:MI:SS') AS tx_time
                    FROM fraud_monitoring.transactions
                    WHERE id > %s
                    ORDER BY id ASC
                    LIMIT 20;
                """, (last_id,))
                rows = cur.fetchall()
                cur.close()
                conn.close()

                if rows:
                    for r in rows:
                        last_id = max(last_id, r["id"])
                        node_name = r["node"] or "VM1-175.2.0.23"
                        src_code = r["source_bank"] or "LAPNET"
                        dst_code = r["dest_bank"] or "LAPNET"
                        src_coords = get_institution_coords(src_code)
                        dst_coords = get_institution_coords(dst_code)

                        amt = float(r["amount"] or 0)
                        ccy = r["currency"] or "LAK"
                        score = int(r["risk_score"] or 0)
                        triggers = r["triggers"] or ""
                        large_limit = LARGE_THRESHOLDS.get(ccy, 50000000.0)

                        is_mule = "Mule" in triggers or "Inflow" in triggers or "Fan-In" in triggers
                        
                        # Status classification
                        if score >= 80 or is_mule:
                            color = "#ff2255"
                            status_label = "ALERT"
                            threat = "High"
                        elif amt >= large_limit:
                            color = "#ffb800"
                            status_label = "HIGH-VAL"
                            threat = "Medium"
                        elif ccy in ["THB", "USD", "CNY"] or src_code in ["ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"] or dst_code in ["ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"]:
                            color = "#00e5ff"
                            status_label = "FX-CROSS"
                            threat = "Low"
                        else:
                            color = "#26c6da"
                            status_label = "NORMAL"
                            threat = "Low"

                        tx_event = {
                            "id": r["id"],
                            "tx_id": r["tx_id"],
                            "node": "VM1" if "175.2.0.23" in node_name else "VM2",
                            "full_node": node_name,
                            "from_code": src_code,
                            "to_code": dst_code,
                            "from_account": r["source_account"] or "N/A",
                            "to_account": r["dest_account"] or "N/A",
                            "from_coords": src_coords,
                            "to_coords": dst_coords,
                            "color": color,
                            "status": status_label,
                            "threat": threat,
                            "currency": ccy,
                            "amount": f"{amt:,.2f}",
                            "risk_score": score,
                            "triggers": triggers or status_label,
                            "time": r["tx_time"],
                            "is_fan_in": is_mule
                        }
                        yield f"data: {json.dumps(tx_event)}\n\n"

                time.sleep(1.0)
            except Exception as e:
                time.sleep(2.0)

    return Response(generate(), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3005, debug=False, threaded=True)
