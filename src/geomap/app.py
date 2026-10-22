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

@app.route("/api/hourly-stats")
def get_hourly_stats():
    """Returns hourly volume and alert counts for the last 12-24 hours"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                to_char(date_trunc('hour', timestamp), 'HH24:00') AS hour_label,
                count(*) AS total_tx,
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
    """Returns the latest 15 alerts for the left real-time detection table"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                id,
                to_char(timestamp, 'HH24:MI:SS') AS alert_time,
                source_bank,
                source_account,
                dest_bank,
                currency,
                amount,
                risk_score,
                reasons[1] AS primary_reason,
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
    """SSE Real-Time Stream for Map Flight Arcs and Table"""
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
                        id, tx_id, source_bank, source_account, dest_bank, dest_account,
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
                        
                        # Color Determination:
                        # 🔴 Red: Alert / Mule / High Risk
                        # 🟡 Gold: Large Value
                        # 🔵 Cyan: Normal / Cross-Border
                        if score >= 80 or is_mule:
                            color = "#ff2244"
                            category = "Mule / Alert"
                            threat = "High"
                        elif amt >= large_limit:
                            color = "#ffb800"
                            category = "High-Value"
                            threat = "Medium"
                        elif ccy in ["THB", "USD", "CNY"] or src_code in ["ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"] or dst_code in ["ITMX", "NAPAS", "CAMBODIA", "UPI", "e-CNY"]:
                            color = "#00f0ff"
                            category = "Cross-Border FX"
                            threat = "Low"
                        else:
                            color = "#00e5ff"
                            category = "Inter-Bank"
                            threat = "Low"

                        tx_event = {
                            "id": r["id"],
                            "tx_id": r["tx_id"],
                            "from_code": src_code,
                            "to_code": dst_code,
                            "from_coords": src_coords,
                            "to_coords": dst_coords,
                            "color": color,
                            "category": category,
                            "threat": threat,
                            "currency": ccy,
                            "amount": f"{amt:,.2f}",
                            "risk_score": score,
                            "triggers": triggers or category,
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
