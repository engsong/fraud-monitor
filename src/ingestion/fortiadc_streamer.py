import socket
import re
import os
import sys
import time
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import psycopg2
from psycopg2.extras import execute_values
import redis
from dotenv import load_dotenv

load_dotenv()

from src.common.logger import get_logger

logger = get_logger("FortiADCStreamer")

# Configuration
SYSLOG_HOST = os.getenv("FORTIADC_SYSLOG_HOST", "0.0.0.0")
SYSLOG_PORT = int(os.getenv("FORTIADC_SYSLOG_PORT", 514))
BUFFER_SIZE = 8192
BATCH_SIZE = 50
BATCH_TIMEOUT_SEC = 2.0

# Database & Redis Settings
DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
DB_USER = os.getenv("POSTGRES_USER", "lev")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "LEV@2026")
DB_NAME = os.getenv("POSTGRES_DB", "lapnet_fraud_db")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Authoritative Member Bank Subnet Mapping (verified against LMPSCore MariaDB)
BANK_IP_MAP = {
    # 4-octet specific overrides
    "175.28.19.1": "VMB",
    "175.28.19.4": "Umoney.MB",
    "175.28.23.1": "LBB",
    "175.28.23.2": "FINA.LBB",
    "175.28.8.1": "ACLE",
    "175.2.0.23": "VM1_CORE",
    "175.2.0.24": "VM2_CORE",
    "175.2.2.40": "FORTIADC_LB",
    "10.79.79.177": "CAMBODIA",
    "10.3.0.1": "CORE_PROBE",
    "10.3.0.17": "CORE_PROBE",
    "175.11.0.122": "INTERNAL_MGMT",
    "175.2.0.112": "INTERNAL_CLEARING",
    
    # 3-octet subnet mappings
    "175.28.1.": "BCEL",
    "175.28.2.": "APB",
    "175.28.3.": "LDB",
    "175.28.4.": "LVB",
    "175.28.5.": "MJB",
    "175.28.6.": "JDB",
    "175.28.7.": "ICBC",
    "175.28.8.": "ACLE",
    "175.28.9.": "LCNB",
    "175.28.10.": "BOC",
    "175.28.11.": "BIC",
    "175.28.12.": "VTB",
    "175.28.13.": "SACOM",
    "175.28.14.": "IDB",
    "175.28.15.": "STB",
    "175.28.16.": "PBLL",
    "175.28.17.": "KBANK",
    "175.28.18.": "BFL",
    "175.28.19.": "APB",
    "175.28.20.": "PSV",
    "175.28.21.": "VCBLAO",
    "175.28.22.": "LFX",
    "175.28.23.": "ACLE",
    "175.28.24.": "BIC",
    "175.28.25.": "VIB",
    "203.184.80.": "UPI",
    "175.29.": "UMONEY_GW",
    "175.17.4.": "LAPNET_ADMIN",
    "10.": "INTERNAL_CORE",
    "192.168.": "INTERNAL_CORE"
}

def resolve_bank_name(src_ip: str, agent: str = "", url: str = "") -> str:
    """Infers member bank from IP address, agent, or URL heuristics"""
    # 1. Exact or prefix match from BANK_IP_MAP (sorted longest prefix first)
    for prefix in sorted(BANK_IP_MAP.keys(), key=len, reverse=True):
        if src_ip.startswith(prefix):
            return BANK_IP_MAP[prefix]

    # 2. Check User-Agent & URL hints
    combined = f"{agent} {url}".upper()
    for bank in ["BCEL", "LDB", "JDB", "APB", "MOFPAY", "LFX", "LVB", "MJB", "MJBL", "ACLE", "ACLEDA", 
                 "BIC", "VIB", "PSV", "PSVB", "STB", "BFL", "IDB", "BOC", "ICBC", "KBANK", "VTB", "VMB", "UPI"]:
        if bank in combined:
            return bank

    if src_ip.startswith("10.") or src_ip.startswith("192.168.") or src_ip.startswith("127."):
        return "INTERNAL_CORE"

    parts = src_ip.split('.')
    return f"IP_{parts[2]}.{parts[3]}" if len(parts) == 4 else src_ip

class FortiADCStreamer:
    """
    High-Performance UDP Syslog Receiver for FortiADC Load Balancers.
    Monitors all payment switch transaction types (/transfer, /inquiry, etc.),
    tracks latency & node balancing, and distinguishes App Rejects vs LB/Network Rejects.
    """

    def __init__(self):
        self.running = True
        self.queue = []
        self.queue_lock = threading.Lock()
        
        # Redis connection for real-time OCC gauges
        try:
            self.r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
            self.r.ping()
            logger.info("Connected to Redis for live telemetry publishing.")
        except Exception as e:
            logger.warning(f"Redis not available: {e}. Live OCC metrics will bypass Redis.")
            self.r = None

        # Rolling counters for RPS calculation
        self.rolling_req_count = 0
        self.last_rps_calc = time.time()
        self.recent_script_refs = []

    def get_db_connection(self):
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            dbname=DB_NAME
        )

    def parse_fortiadc_log(self, raw_str: str) -> Optional[Dict[str, Any]]:
        """Parses FortiADC key=value syslog string"""
        try:
            # Match key="value" or key=value
            pattern = r'(\w+)=(?:"([^"]*)"|([^\s]+))'
            matches = re.findall(pattern, raw_str)
            if not matches:
                return None

            data = {}
            for k, v_quoted, v_unquoted in matches:
                data[k] = v_quoted if v_quoted else v_unquoted

            # Only process SLB HTTP / traffic events
            sub_type = data.get("subtype") or data.get("type")
            if sub_type not in ["slb_http", "traffic", "http"]:
                # Check if it has http_url
                if "http_url" not in data:
                    return None

            src_ip = data.get("src", "0.0.0.0")
            dst_ip = data.get("dst", "0.0.0.0")
            dst_port = data.get("dst_port", "8055")
            trans_dst = data.get("trans_dst", "0.0.0.0")
            real_server = data.get("real_server", "none")
            action = data.get("action", "none").lower()
            http_method = data.get("http_method", "POST").upper()
            raw_url = data.get("http_url", "unknown")
            http_agent = data.get("http_agent", "none")
            retcode_str = data.get("http_retcode", "200")
            http_qry = data.get("http_qry", "none")
            msg_id = data.get("msg_id", "")

            try:
                retcode = int(retcode_str)
            except ValueError:
                retcode = 200

            try:
                duration = int(data.get("duration", 0))
            except ValueError:
                duration = 0

            try:
                ibytes = int(data.get("ibytes", 0))
                obytes = int(data.get("obytes", 0))
            except ValueError:
                ibytes, obytes = 0, 0

            # Determine Target Node (VM1 vs VM2)
            if "175.2.0.23" in trans_dst or "app1" in real_server.lower():
                target_node = "VM1"
            elif "175.2.0.24" in trans_dst or "app2" in real_server.lower():
                target_node = "VM2"
            elif real_server != "none":
                target_node = real_server
            else:
                target_node = "LB_DIRECT"

            # Resolve Member Bank Name
            bank_name = resolve_bank_name(src_ip, http_agent, raw_url)

            # Extract Transaction Reference / ID
            tx_ref = None
            if http_qry and http_qry != "none":
                ref_match = re.search(r'(?:Search|ref|reference|tx_id|txn|token)=([A-Za-z0-9_\-]+)', http_qry, re.IGNORECASE)
                if ref_match:
                    tx_ref = ref_match.group(1)
                elif len(http_qry) >= 8 and "=" not in http_qry:
                    tx_ref = http_qry[:24]

            if not tx_ref:
                # Check recent script-extracted references within 4 seconds
                now_ts = time.time()
                with self.queue_lock:
                    for ts, sref in reversed(self.recent_script_refs):
                        if now_ts - ts <= 4.0:
                            tx_ref = sref
                            break

            if not tx_ref and msg_id:
                tx_ref = f"LB-{msg_id}"
            elif not tx_ref:
                tx_ref = "N/A"

            # Clean URL and standard transaction type
            if raw_url in ["unknown", "none", ""]:
                if http_method == "OPTIONS":
                    http_url = "OPTIONS / (Probe)"
                    tx_type = "OPTIONS_PROBE"
                else:
                    http_url = f"{http_method} / (Unknown)"
                    tx_type = "UNKNOWN_ROUTE"
            else:
                http_url = raw_url
                clean_url = http_url.lower()
                if "/crossborder" in clean_url:
                    tx_type = "CROSSBORDER"
                elif "/transfer" in clean_url:
                    tx_type = "TRANSFER"
                elif "/inquiry" in clean_url:
                    tx_type = "INQUIRY"
                elif "/qr" in clean_url or "/pay" in clean_url:
                    tx_type = "QR_PAYMENT"
                elif "/balance" in clean_url:
                    tx_type = "BALANCE"
                elif "/stmt" in clean_url or "/statement" in clean_url or "/report" in clean_url:
                    tx_type = "REPORT"
                elif "/upi/" in clean_url:
                    tx_type = "UPI"
                elif clean_url == "/":
                    tx_type = "HEALTH_CHECK"
                else:
                    first_seg = clean_url.strip("/").split("/")[0].upper()
                    tx_type = first_seg if first_seg else "API_CALL"

            # Categorize Transaction Status & Granular Rejection Reason
            # 1. LB / Network Reject (Dropped at Load Balancer or backend unreachable)
            if action in ["drop", "block", "close", "reject"] or retcode in [502, 503, 504]:
                status_category = "LB_REJECT"
                if retcode == 502:
                    reject_reason = "LB: Bad Gateway (502 Upstream Down)"
                elif retcode == 504:
                    reject_reason = "LB: Gateway Timeout (504 Backend Slow)"
                elif action in ["drop", "block"]:
                    reject_reason = f"LB: WAF / Policy {action.upper()}"
                else:
                    reject_reason = f"LB: Connection {action.upper()}"
            elif real_server == "none" and retcode == 400:
                # FortiADC rejects malformed HTTP/1.1 requests without host header
                status_category = "LB_REJECT"
                if http_method == "OPTIONS" or raw_url == "unknown":
                    reject_reason = "LB: 400 Bad Request (Missing Host / OPTIONS Probe)"
                else:
                    reject_reason = "LB: 400 Bad Request (Malformed HTTP Header)"
            elif retcode >= 400:
                status_category = "APP_REJECT"
                if retcode == 400:
                    reject_reason = "APP: 400 Bad Request (Field Validation Error)"
                elif retcode == 401:
                    reject_reason = "APP: 401 Unauthorized (Auth / Key Invalid)"
                elif retcode == 403:
                    reject_reason = "APP: 403 Forbidden (Access Denied)"
                elif retcode == 404:
                    reject_reason = "APP: 404 Not Found (Invalid Endpoint Route)"
                elif retcode == 405:
                    reject_reason = "APP: 405 Method Not Allowed"
                elif retcode == 500:
                    reject_reason = "APP: 500 Internal Error (Backend Exception)"
                else:
                    reject_reason = f"APP: HTTP {retcode}"
            else:
                status_category = "SUCCESS"
                reject_reason = "OK"

            lao_now = datetime.now(timezone(timedelta(hours=7)))

            return {
                "timestamp": lao_now,
                "devname": data.get("devname", "LMDCLB01001"),
                "policy": data.get("policy", "vs-lmps-app"),
                "vip": f"{dst_ip}:{dst_port}",
                "client_ip": src_ip,
                "bank_name": bank_name,
                "real_server": real_server,
                "target_node": target_node,
                "http_method": http_method,
                "http_url": http_url,
                "tx_type": tx_type,
                "tx_ref": tx_ref,
                "http_agent": http_agent if http_agent != "none" else "",
                "http_retcode": retcode,
                "status_category": status_category,
                "reject_reason": reject_reason,
                "action": action,
                "duration_ms": duration,
                "bytes_in": ibytes,
                "bytes_out": obytes,
                "raw_log": raw_str[:1000]
            }
        except Exception as e:
            logger.debug(f"Error parsing log: {e}")
            return None

    def update_redis_telemetry(self, parsed: Dict[str, Any]):
        """Publishes live rolling stats and reject events to Redis"""
        if not self.r:
            return

        try:
            pipe = self.r.pipeline()
            pipe.hincrby("fortiadc:live:totals", "all", 1)
            
            # Status count
            if parsed["status_category"] == "SUCCESS":
                pipe.hincrby("fortiadc:live:totals", "success", 1)
            elif parsed["status_category"] == "APP_REJECT":
                pipe.hincrby("fortiadc:live:totals", "app_reject", 1)
            else:
                pipe.hincrby("fortiadc:live:totals", "lb_reject", 1)

            # Node distribution
            if parsed["target_node"] == "VM1":
                pipe.hincrby("fortiadc:live:nodes", "vm1", 1)
            elif parsed["target_node"] == "VM2":
                pipe.hincrby("fortiadc:live:nodes", "vm2", 1)

            # Transaction type
            pipe.hincrby("fortiadc:live:types", parsed["tx_type"], 1)

            # Bank volume
            pipe.hincrby("fortiadc:live:banks", parsed["bank_name"], 1)

            # Latency running sum and sample count
            pipe.hincrby("fortiadc:live:latency", "sum", parsed["duration_ms"])
            pipe.hincrby("fortiadc:live:latency", "count", 1)

            # If Rejected (App or LB), push to recent rejects list (max 50)
            if parsed["status_category"] in ["APP_REJECT", "LB_REJECT"]:
                reject_event = {
                    "time": parsed["timestamp"].strftime("%H:%M:%S"),
                    "bank": parsed["bank_name"],
                    "client_ip": parsed["client_ip"],
                    "type": parsed["tx_type"],
                    "url": parsed["http_url"],
                    "retcode": parsed["http_retcode"],
                    "category": parsed["status_category"],
                    "reason": parsed["reject_reason"],
                    "tx_ref": parsed["tx_ref"],
                    "node": parsed["target_node"],
                    "latency_ms": parsed["duration_ms"],
                    "action": parsed["action"]
                }
                pipe.lpush("fortiadc:recent_rejects", json.dumps(reject_event))
                pipe.ltrim("fortiadc:recent_rejects", 0, 49)

            pipe.execute()
        except Exception as e:
            logger.debug(f"Redis update error: {e}")

    def db_writer_loop(self):
        """Worker thread to batch insert logs into PostgreSQL"""
        logger.info("FortiADC DB Batch Writer started.")
        while self.running:
            time.sleep(BATCH_TIMEOUT_SEC)
            batch = []
            with self.queue_lock:
                if self.queue:
                    batch = self.queue[:BATCH_SIZE * 2]
                    self.queue = self.queue[len(batch):]

            if not batch:
                continue

            try:
                conn = self.get_db_connection()
                cur = conn.cursor()
                query = """
                    INSERT INTO fraud_monitoring.fortiadc_traffic_logs (
                        timestamp, devname, policy, vip, client_ip, bank_name,
                        real_server, target_node, http_method, http_url, http_agent,
                        http_retcode, status_category, action, duration_ms, bytes_in, bytes_out, raw_log,
                        tx_ref, reject_reason
                    ) VALUES %s
                """
                tuples = [
                    (
                        item["timestamp"], item["devname"], item["policy"], item["vip"],
                        item["client_ip"], item["bank_name"], item["real_server"], item["target_node"],
                        item["http_method"], item["http_url"], item["http_agent"], item["http_retcode"],
                        item["status_category"], item["action"], item["duration_ms"], item["bytes_in"],
                        item["bytes_out"], item["raw_log"], item.get("tx_ref"), item.get("reject_reason")
                    )
                    for item in batch
                ]
                execute_values(cur, query, tuples)
                conn.commit()
                cur.close()
                conn.close()
                logger.debug(f"Inserted {len(batch)} FortiADC traffic records into database.")
            except Exception as e:
                logger.error(f"Error inserting batch to PostgreSQL: {e}")

    def start(self):
        """Starts UDP syslog listener and batch worker"""
        # Start DB Writer thread
        writer_thread = threading.Thread(target=self.db_writer_loop, daemon=True)
        writer_thread.start()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((SYSLOG_HOST, SYSLOG_PORT))
            logger.info(f"FortiADC Syslog UDP Listener bound successfully on {SYSLOG_HOST}:{SYSLOG_PORT}")
        except Exception as e:
            logger.error(f"Failed to bind socket on {SYSLOG_HOST}:{SYSLOG_PORT}: {e}")
            return

        sock.settimeout(1.0)
        logger.info("Listening for FortiADC traffic logs in real-time...")

        while self.running:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                raw_msg = data.decode("utf-8", errors="ignore").strip()
                if not raw_msg:
                    continue

                # Check for FortiADC script logs (LMPS_TXN_REF or LMPS_PAYLOAD_BODY)
                script_ref_match = re.search(r'LMPS_TXN_REF:\s*([A-Za-z0-9_\-]+)', raw_msg)
                if not script_ref_match and "LMPS_PAYLOAD_BODY" in raw_msg:
                    script_ref_match = re.search(r'"(?:reference|txnRef|tx_id|traceNo)"\s*:\s*"([^"]+)"', raw_msg)

                if script_ref_match:
                    s_ref = script_ref_match.group(1).strip()
                    logger.info(f"Captured FortiADC script reference: {s_ref}")
                    with self.queue_lock:
                        self.recent_script_refs.append((time.time(), s_ref))
                        if len(self.recent_script_refs) > 100:
                            self.recent_script_refs.pop(0)

                parsed = self.parse_fortiadc_log(raw_msg)
                if parsed:
                    # Update Redis real-time telemetry
                    self.update_redis_telemetry(parsed)

                    # Add to DB queue (limit queue size to prevent memory leaks)
                    with self.queue_lock:
                        if len(self.queue) < 10000:
                            self.queue.append(parsed)

                    # Calculate live RPS
                    self.rolling_req_count += 1
                    now = time.time()
                    if now - self.last_rps_calc >= 2.0:
                        rps = round(self.rolling_req_count / (now - self.last_rps_calc), 1)
                        if self.r:
                            self.r.set("fortiadc:live:rps", rps)
                            self.r.set("fortiadc:live:last_seen", now)
                        self.rolling_req_count = 0
                        self.last_rps_calc = now

            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"Socket receive error: {e}")
                time.sleep(0.5)

        sock.close()
        logger.info("FortiADC Streamer stopped.")

if __name__ == "__main__":
    streamer = FortiADCStreamer()
    try:
        streamer.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        streamer.running = False
