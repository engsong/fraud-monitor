import os
import sys
import time
import json
import csv
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from flask import Flask, render_template, jsonify, request, Response
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
from dotenv import load_dotenv

load_dotenv()

from src.common.logger import get_logger

logger = get_logger("FortiADCWebApp")

app = Flask(__name__, template_folder="templates")

# Database & Redis Settings
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
DB_USER = os.getenv("POSTGRES_USER", "lev")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "LEV@2026")
DB_NAME = os.getenv("POSTGRES_DB", "lapnet_fraud_db")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

PORT = int(os.getenv("PORT", os.getenv("FORTIADC_WEB_PORT", 3006)))

try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
    redis_client.ping()
except Exception as e:
    logger.warning(f"Redis connection failed: {e}. Live OCC stats will use PostgreSQL fallback.")
    redis_client = None

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname=DB_NAME,
        cursor_factory=RealDictCursor
    )

def get_time_interval_sql(time_range: Optional[str] = None, from_arg: Optional[str] = None):
    """Resolves timeline window SQL condition"""
    if from_arg:
        try:
            val = int(from_arg)
            if val <= 60:
                return f"timestamp >= NOW() - INTERVAL '{val} minutes'", f"{val}m", f"Last {val}m"
            else:
                hrs = val // 60
                return f"timestamp >= NOW() - INTERVAL '{hrs} hours'", f"{hrs}h", f"Last {hrs}h"
        except ValueError:
            pass

    if not time_range:
        return "timestamp >= NOW() - INTERVAL '1 hour'", "1h", "Last 1 hour"

    tr = time_range.lower()
    if tr in ["15m", "15min"]:
        return "timestamp >= NOW() - INTERVAL '15 minutes'", "15m", "Last 15 minutes"
    elif tr in ["30m", "30min"]:
        return "timestamp >= NOW() - INTERVAL '30 minutes'", "30m", "Last 30 minutes"
    elif tr in ["1h", "1hour", "hour"]:
        return "timestamp >= NOW() - INTERVAL '1 hour'", "1h", "Last 1 hour"
    elif tr in ["3h", "3hour", "3hours"]:
        return "timestamp >= NOW() - INTERVAL '3 hours'", "3h", "Last 3 hours"
    elif tr in ["6h", "6hour", "6hours"]:
        return "timestamp >= NOW() - INTERVAL '6 hours'", "6h", "Last 6 hours"
    elif tr in ["12h", "12hour", "12hours"]:
        return "timestamp >= NOW() - INTERVAL '12 hours'", "12h", "Last 12 hours"
    elif tr in ["24h", "24hour", "24hours"]:
        return "timestamp >= NOW() - INTERVAL '24 hours'", "24h", "Last 24 hours"
    elif tr in ["today", "current_day"]:
        return "timestamp >= DATE_TRUNC('day', NOW() AT TIME ZONE 'Asia/Vientiane') AT TIME ZONE 'Asia/Vientiane'", "today", "Today (Since 00:00)"
    else:
        return "timestamp >= NOW() - INTERVAL '1 hour'", "1h", "Last 1 hour"

@app.route("/")
def fortiadc_index():
    """Serves the standalone FortiADC Load Balancer Live Monitor Dashboard"""
    return render_template("fortiadc_dashboard.html")

@app.route("/api/fortiadc-stats")
def get_fortiadc_stats():
    """Returns comprehensive FortiADC Load Balancer traffic, reject status, latency, and node metrics"""
    time_range = request.args.get("time_range")
    from_arg = request.args.get("from")
    filter_bank = (request.args.get("bank") or "").strip()
    where_time, range_id, label = get_time_interval_sql(time_range, from_arg)

    # 1. Check Redis for real-time live pulse
    is_online = False
    live_rps = 0.0
    if redis_client:
        try:
            last_seen = float(redis_client.get("fortiadc:live:last_seen") or 0)
            if time.time() - last_seen < 15:
                is_online = True
            live_rps = float(redis_client.get("fortiadc:live:rps") or 0.0)
        except Exception:
            pass

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 2. Macro counts in selected timeline
        cur.execute(f"""
            SELECT 
                count(*) AS total_req,
                count(*) FILTER (WHERE status_category = 'SUCCESS') AS success_cnt,
                count(*) FILTER (WHERE status_category = 'APP_REJECT') AS app_reject_cnt,
                count(*) FILTER (WHERE status_category = 'LB_REJECT') AS lb_reject_cnt,
                count(*) FILTER (WHERE target_node = 'VM1') AS vm1_cnt,
                count(*) FILTER (WHERE target_node = 'VM2') AS vm2_cnt,
                COALESCE(ROUND(AVG(duration_ms)), 0) AS avg_duration_ms,
                COALESCE(ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric), 0) AS p95_duration_ms
            FROM fraud_monitoring.fortiadc_traffic_logs
            WHERE {where_time};
        """)
        macro = cur.fetchone() or {}
        tot = macro.get("total_req") or 0
        succ = macro.get("success_cnt") or 0
        app_rej = macro.get("app_reject_cnt") or 0
        lb_rej = macro.get("lb_reject_cnt") or 0
        vm1 = macro.get("vm1_cnt") or 0
        vm2 = macro.get("vm2_cnt") or 0

        # Percentages
        succ_pct = round(succ / tot * 100, 1) if tot > 0 else 100.0
        app_rej_pct = round(app_rej / tot * 100, 1) if tot > 0 else 0.0
        lb_rej_pct = round(lb_rej / tot * 100, 1) if tot > 0 else 0.0
        node_tot = vm1 + vm2
        vm1_pct = round(vm1 / node_tot * 100, 1) if node_tot > 0 else 50.0
        vm2_pct = round(vm2 / node_tot * 100, 1) if node_tot > 0 else 50.0

        # 3. Top Transaction Types (/transfer, /inquiry, etc.)
        cur.execute(f"""
            SELECT 
                CASE 
                    WHEN http_url ILIKE '%%/transfer%%' THEN 'TRANSFER'
                    WHEN http_url ILIKE '%%/inquiry%%' THEN 'INQUIRY'
                    WHEN http_url ILIKE '%%/qr%%' THEN 'QR_PAYMENT'
                    WHEN http_url ILIKE '%%/crossborder%%' THEN 'CROSSBORDER'
                    WHEN http_url ILIKE '%%/upi%%' THEN 'UPI'
                    WHEN http_url = '/' THEN 'HEALTH_CHECK'
                    WHEN http_url IN ('unknown', 'none', '') AND http_method = 'OPTIONS' THEN 'OPTIONS_PROBE'
                    ELSE COALESCE(NULLIF(http_url, ''), 'OTHER')
                END AS tx_type,
                count(*) AS count,
                count(*) FILTER (WHERE status_category != 'SUCCESS') AS reject_count
            FROM fraud_monitoring.fortiadc_traffic_logs
            WHERE {where_time}
            GROUP BY 1
            ORDER BY count DESC
            LIMIT 12;
        """)
        top_types = cur.fetchall()

        # 4. All Source Banks & Reject Ratios (All participating members, ordered by volume)
        cur.execute(f"""
            SELECT 
                bank_name,
                count(*) AS total_req,
                count(*) FILTER (WHERE status_category = 'SUCCESS') AS success_cnt,
                count(*) FILTER (WHERE status_category = 'APP_REJECT') AS app_reject_cnt,
                count(*) FILTER (WHERE status_category = 'LB_REJECT') AS lb_reject_cnt,
                ROUND(AVG(duration_ms)) AS avg_latency_ms
            FROM fraud_monitoring.fortiadc_traffic_logs
            WHERE {where_time}
            GROUP BY bank_name
            ORDER BY total_req DESC;
        """)
        top_banks = cur.fetchall()

        # 5. Live Rejections Ledger (with optional Bank filter and enriched details)
        bank_filter_clause = ""
        query_params = []
        if filter_bank and filter_bank.upper() != "ALL":
            bank_filter_clause = " AND bank_name = %s "
            query_params.append(filter_bank)

        cur.execute(f"""
            SELECT 
                to_char(timestamp AT TIME ZONE 'Asia/Vientiane', 'HH24:MI:SS') AS time,
                bank_name AS bank,
                client_ip,
                CASE 
                    WHEN http_url IN ('unknown', 'none', '') AND http_method = 'OPTIONS' THEN 'OPTIONS / (Probe)'
                    WHEN http_url IN ('unknown', 'none', '') THEN http_method || ' / (Unknown)'
                    ELSE http_url 
                END AS url,
                CASE 
                    WHEN http_url ILIKE '%%/transfer%%' THEN 'TRANSFER'
                    WHEN http_url ILIKE '%%/inquiry%%' THEN 'INQUIRY'
                    WHEN http_url ILIKE '%%/qr%%' THEN 'QR_PAYMENT'
                    WHEN http_method = 'OPTIONS' THEN 'PROBE'
                    ELSE http_method
                END AS type,
                http_retcode AS retcode,
                status_category AS category,
                COALESCE(
                    NULLIF(reject_reason, ''),
                    CASE 
                        WHEN real_server = 'none' AND http_retcode = 400 THEN 'LB: 400 Bad Request (Missing Host / OPTIONS Probe)'
                        WHEN http_retcode = 400 THEN 'APP: 400 Bad Request (Field Validation Error)'
                        WHEN http_retcode = 401 THEN 'APP: 401 Unauthorized (Auth / Key Invalid)'
                        WHEN http_retcode = 404 THEN 'APP: 404 Not Found (Invalid Endpoint Route)'
                        WHEN http_retcode = 500 THEN 'APP: 500 Internal Error (Backend Exception)'
                        WHEN status_category = 'LB_REJECT' THEN 'LB: Gateway / Network Drop'
                        ELSE 'APP: HTTP ' || http_retcode::text
                    END
                ) AS reason,
                COALESCE(NULLIF(tx_ref, ''), substring(raw_log from 'msg_id=([0-9]+)'), 'N/A') AS tx_ref,
                CASE 
                    WHEN target_node = 'STANDBY/OTHER' THEN 'LB_DIRECT'
                    ELSE target_node
                END AS node,
                duration_ms AS latency_ms,
                action
            FROM fraud_monitoring.fortiadc_traffic_logs
            WHERE status_category IN ('APP_REJECT', 'LB_REJECT')
              AND {where_time}
              {bank_filter_clause}
            ORDER BY id DESC
            LIMIT 100;
        """, tuple(query_params))
        recent_rejects = cur.fetchall()

        cur.close()
        conn.close()

        return jsonify({
            "status": "online" if is_online else ("active" if tot > 0 else "standby"),
            "is_online": is_online,
            "devname": "LMDCLB01001",
            "vip": "175.2.0.29:8055",
            "policy": "vs-lmps-app-8055",
            "time_range": range_id,
            "label": label,
            "filter_bank": filter_bank or "ALL",
            "live_rps": live_rps,
            "macro": {
                "total_requests": tot,
                "success_count": succ,
                "success_pct": succ_pct,
                "app_reject_count": app_rej,
                "app_reject_pct": app_rej_pct,
                "lb_reject_count": lb_rej,
                "lb_reject_pct": lb_rej_pct,
                "avg_duration_ms": int(macro.get("avg_duration_ms") or 0),
                "p95_duration_ms": int(macro.get("p95_duration_ms") or 0),
                "vm1_count": vm1,
                "vm1_pct": vm1_pct,
                "vm2_count": vm2,
                "vm2_pct": vm2_pct
            },
            "top_types": top_types,
            "top_banks": top_banks,
            "recent_rejects": recent_rejects
        })
    except Exception as e:
        logger.error(f"Error fetching FortiADC stats: {e}")
        return jsonify({
            "error": str(e),
            "status": "offline",
            "live_rps": 0.0,
            "macro": {"total_requests": 0, "success_pct": 100.0, "app_reject_pct": 0.0, "lb_reject_pct": 0.0}
        }), 500

@app.route("/api/export-fortiadc-rejects")
def export_fortiadc_rejects():
    """Streams CSV audit export of FortiADC rejection logs"""
    time_range = request.args.get("time_range")
    from_arg = request.args.get("from")
    filter_bank = (request.args.get("bank") or "").strip()
    where_time, range_id, label = get_time_interval_sql(time_range, from_arg)

    bank_clause = ""
    params = []
    if filter_bank and filter_bank.upper() != "ALL":
        bank_clause = " AND bank_name = %s "
        params.append(filter_bank)

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(f"""
            SELECT 
                to_char(timestamp AT TIME ZONE 'Asia/Vientiane', 'YYYY-MM-DD HH24:MI:SS') AS lao_time,
                COALESCE(NULLIF(tx_ref, ''), substring(raw_log from 'msg_id=([0-9]+)'), 'N/A') AS tx_ref,
                bank_name AS bank,
                client_ip,
                http_method,
                http_url,
                http_retcode AS retcode,
                status_category AS category,
                COALESCE(
                    NULLIF(reject_reason, ''),
                    CASE 
                        WHEN real_server = 'none' AND http_retcode = 400 THEN 'LB: 400 Bad Request (Missing Host / OPTIONS Probe)'
                        WHEN http_retcode = 400 THEN 'APP: 400 Bad Request (Field Validation Error)'
                        WHEN http_retcode = 401 THEN 'APP: 401 Unauthorized (Auth / Key Invalid)'
                        WHEN http_retcode = 404 THEN 'APP: 404 Not Found (Invalid Endpoint Route)'
                        WHEN http_retcode = 500 THEN 'APP: 500 Internal Error (Backend Exception)'
                        ELSE 'HTTP ' || http_retcode::text
                    END
                ) AS reason,
                target_node AS node,
                duration_ms AS latency_ms,
                action
            FROM fraud_monitoring.fortiadc_traffic_logs
            WHERE status_category IN ('APP_REJECT', 'LB_REJECT')
              AND {where_time}
              {bank_clause}
            ORDER BY id DESC
            LIMIT 5000;
        """, tuple(params))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        output = io.StringIO()
        output.write('\ufeff')  # UTF-8 BOM
        writer = csv.writer(output)
        writer.writerow([
            "Timestamp (GMT+7)",
            "Transaction Reference",
            "Bank",
            "Client IP",
            "HTTP Method",
            "HTTP URL / Path",
            "HTTP Code",
            "Reject Category",
            "Rejection Reason / Root Cause",
            "Target Node",
            "Latency (ms)",
            "FortiADC Action"
        ])
        for r in rows:
            writer.writerow([
                r["lao_time"],
                r["tx_ref"],
                r["bank"],
                r["client_ip"],
                r["http_method"],
                r["http_url"],
                r["retcode"],
                r["category"],
                r["reason"],
                r["node"],
                r["latency_ms"],
                r["action"]
            ])

        csv_data = output.getvalue()
        output.close()
        bank_suffix = f"_{filter_bank}" if filter_bank and filter_bank != "ALL" else ""
        filename = f"fortiadc_rejections_{range_id}{bank_suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LAPNet FortiADC Dedicated Load Balancer OCC Web Service")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port to listen on (default: {PORT})")
    args, _ = parser.parse_known_args()
    print(f"[*] Starting LAPNet FortiADC Dedicated Web Service on port {args.port}...")
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
