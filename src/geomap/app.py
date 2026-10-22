import os
import json
import time
from datetime import datetime
from flask import Flask, render_template, Response, jsonify, send_from_directory
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

@app.route("/api/node-stats")
def get_node_stats():
    """Returns real-time switching metrics comparing VM1 (175.2.0.23) and VM2 (175.2.0.24)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                node, 
                count(*) AS tx_count,
                count(*) FILTER (WHERE risk_score >= 80) AS alert_count,
                COALESCE(sum(amount) FILTER (WHERE currency = 'LAK'), 0) AS lak_volume,
                COALESCE(sum(amount) FILTER (WHERE currency = 'THB'), 0) AS thb_volume,
                COALESCE(sum(amount) FILTER (WHERE currency = 'USD'), 0) AS usd_volume
            FROM fraud_monitoring.transactions
            GROUP BY node;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        vm1 = next((r for r in rows if "175.2.0.23" in (r["node"] or "")), {"tx_count": 0, "alert_count": 0, "lak_volume": 0})
        vm2 = next((r for r in rows if "175.2.0.24" in (r["node"] or "")), {"tx_count": 0, "alert_count": 0, "lak_volume": 0})
        
        total_tx = vm1["tx_count"] + vm2["tx_count"]
        vm1_pct = round((vm1["tx_count"] / total_tx * 100), 1) if total_tx > 0 else 0
        vm2_pct = round((vm2["tx_count"] / total_tx * 100), 1) if total_tx > 0 else 0

        return jsonify({
            "total_tx": total_tx,
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
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                to_char(date_trunc('hour', timestamp), 'HH24:00') AS hour_label,
                count(*) FILTER (WHERE node LIKE '%175.2.0.23%') AS vm1_tx,
                count(*) FILTER (WHERE node LIKE '%175.2.0.24%') AS vm2_tx,
                count(*) FILTER (WHERE risk_score >= 80) AS alert_tx
            FROM fraud_monitoring.transactions
            WHERE timestamp >= NOW() - INTERVAL '24 hours'
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
    """Returns the latest 15 alerts for the right live alerts board"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                id,
                to_char(timestamp, 'HH24:MI:SS') AS alert_time,
                node,
                source_bank,
                source_account,
                dest_bank,
                dest_account,
                currency,
                amount,
                risk_score,
                array_to_string(reasons, ', ') AS triggers,
                CASE 
                    WHEN risk_score >= 85 THEN 'Critical'
                    WHEN risk_score >= 70 THEN 'High'
                    WHEN risk_score >= 40 THEN 'Medium'
                    ELSE 'Low'
                END AS threat_level
            FROM fraud_monitoring.transactions
            WHERE risk_score >= 40 OR array_length(reasons, 1) > 0
            ORDER BY id DESC
            LIMIT 18;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(rows or [])
    except Exception as e:
        return jsonify([])

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
                to_char(sum(amount), 'FM999,999,990.00') AS volume,
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
                to_char(sum(amount), 'FM999,999,990.00') AS volume,
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
