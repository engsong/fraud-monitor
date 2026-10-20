# Real-Time Fraud Monitoring System Architecture & Service Documentation
**Target Network**: Lao National Payment Network (LAPNet) / LMPS Switching System  
**Deployment Date**: October 2026  
**Repository**: [https://github.com/engsong/fraud-monitor](https://github.com/engsong/fraud-monitor)

---

## 1. System Topology & Machine Inventory

| Host Role | Hostname / IP | OS & Specs | Purpose |
| :--- | :--- | :--- | :--- |
| **Switching Node 1** | `lmps-app1` (`175.2.0.23`) | Ubuntu 20.04 LTS | Active Core Switching Engine. Generates transaction log `/root/LMPSCore/log/LMPS.log`. |
| **Switching Node 2** | `lmps-app2` (`175.2.0.24`) | Ubuntu 20.04 LTS | Active Core Switching Engine. Generates transaction log `/root/LMPSCore/log/LMPS.log`. |
| **Fraud Monitoring Host** | `WEBSERVER01` (`175.0.198.10`) | CentOS Linux 8 (128 GB RAM) | Runs the AI fraud evaluation engine, Docker containers, and database. Path: `/root/LAPNet/Fraud-monitor`. |

---

## 2. Service Architecture & Responsibilities

```
  ┌─────────────────────────┐           ┌─────────────────────────┐
  │   LMPS Switch Node 1    │           │   LMPS Switch Node 2    │
  │      175.2.0.23         │           │      175.2.0.24         │
  │  ~/LMPSCore/log/LMPS.log│           │  ~/LMPSCore/log/LMPS.log│
  └────────────┬────────────┘           └────────────┬────────────┘
               │ (Real-Time Encrypted SSH Stream)    │ (Real-Time Encrypted SSH Stream)
               └──────────────────┬──────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FRAUD MONITORING SERVER (175.0.198.10)                                      │
│                                                                              │
│  [ Service: fraud-monitor.service (Python Daemon) ]                          │
│                                                                              │
│  1. Ingestion Layer (remote_streamer.py & parser.py)                         │
│     - Runs 2 concurrent streaming workers tailing LMPS.log in real-time.     │
│     - Detects transfer events: [POST /transfer] and /transfer CONTENT.       │
│     - Normalizes sender, receiver, amounts, currency, and timestamps.        │
│                                                                              │
│  2. In-Memory State & Velocity Engine (redis_velocity.py)                    │
│     - Talks to [ fraud-redis (Docker, Port 6379) ].                          │
│     - Tracks 60s velocity, 300s velocity, and 1-hour total amounts.          │
│                                                                              │
│  3. Rule Engine (rules.py)                                                   │
│     - Blacklist checks (Instant score: 100/100).                             │
│     - Excessive frequency (>5 tx/min).                                       │
│     - Large single amount thresholds (>50,000,000 LAK).                      │
│     - Off-hours anomalies (01:00 AM - 04:30 AM).                             │
│                                                                              │
│  4. AI Anomaly Scoring Engine (ai_model.py)                                  │
│     - Unsupervised Isolation Forest model.                                   │
│     - Outputs statistical anomaly score (0 - 100) within 2ms.                │
│                                                                              │
│  5. Storage & Alerting Dispatchers                                           │
│     - High-Risk (Score >= 80): Dispatches Telegram Alert & saves alert.      │
│     - All Transactions: Persisted into PostgreSQL.                           │
└───────────────────────┬──────────────────────────────┬───────────────────────┘
                        │                              │
                        ▼                              ▼
      ┌───────────────────────────────────┐    ┌───────────────────────────────┐
      │ Container: fraud-postgres         │    │ Container: fraud-grafana      │
      │ Image: postgres:16-alpine         │    │ Port: 3002:3000               │
      │ Port : 5432                       │    │ Web Dashboard & Heatmaps      │
      │ DB   : lapnet_fraud_db            │    └───────────────────────────────┘
      │ User : lev (schema: fraud_mon...) │
      └───────────────────────────────────┘
```

---

## 3. Detailed Breakdown: "Which Service Does What"

### Service 1: `fraud-monitor.service` (Background Systemd Daemon)
* **What it is**: The core analytical engine running 24/7 as a Linux systemd background service.
* **Working Directory**: `/root/LAPNet/Fraud-monitor`
* **Python Runtime**: Python 3.9 virtual environment (`/root/LAPNet/Fraud-monitor/venv`)
* **Sub-components**:
  1. `remote_streamer.py`:
     - Spawns two persistent background SSH streaming sessions (`tail -n 0 -F /root/LMPSCore/log/LMPS.log`) directly to Node 1 (`175.2.0.23`) and Node 2 (`175.2.0.24`).
     - Includes automatic reconnection handling if network switches or routers reset.
  2. `parser.py`:
     - Identifies real-time transfer operations (`POST /transfer`, QR payment requests).
     - Extracts: `frommember` (Source Bank), `fromaccount`, `tomember` (Destination Bank), `toaccount`, `amount`, `ccy`, `reference` (Tx ID), and transaction timestamp.
  3. `redis_velocity.py`:
     - Maintains sliding-window counters in Redis using Sorted Sets (`ZSET`).
     - Calculates: How many times has this account sent money in the last 60 seconds? In 5 minutes? How much total money in 1 hour?
  4. `rules.py`:
     - Evaluates deterministic financial security rules in microseconds.
  5. `ai_model.py`:
     - Uses **Isolation Forest** (Machine Learning) to evaluate multi-dimensional anomalies (amount vs time of day vs past frequency).
  6. `postgres_writer.py`:
     - Writes every evaluated transaction and any critical fraud alerts to PostgreSQL.
  7. `telegram_alert.py`:
     - Dispatches formatted Telegram notifications when transactions exceed risk thresholds.

---

### Service 2: `fraud-postgres` (Docker Container)
* **What it is**: Dedicated relational database for permanent transaction history, auditing, and fraud case investigations.
* **Technology**: PostgreSQL 16 on Alpine Linux
* **Port**: `5432`
* **Database Name**: `lapnet_fraud_db`
* **User**: `lev` (Password: `LEV@2026`)
* **Schema**: `fraud_monitoring`
* **Core Tables**:
  * `fraud_monitoring.transactions`: Stores 100% of switched transactions with risk scores, bank IDs, account numbers, amounts, and source node (`VM1` vs `VM2`).
  * `fraud_monitoring.fraud_alerts`: Stores high-risk transactions (Score $\ge 80$), flagged reasons, and investigation status (`NEW`, `ACKNOWLEDGED`, `RESOLVED`).

---

### Service 3: `fraud-redis` (Docker Container)
* **What it is**: In-memory data store for ultra-fast state tracking.
* **Technology**: Redis 7 on Alpine Linux
* **Port**: `6379`
* **Role**:
  * Stores rolling transaction timestamps and amounts per account using sliding windows.
  * Stores the active Blacklist/Watchlist set (`fraud:blacklist:accounts`) for sub-millisecond lookups.

---

### Service 4: `fraud-grafana` (Docker Container)
* **What it is**: Web dashboard for the operations (NOC) and risk management teams.
* **Technology**: Grafana Latest
* **Port**: `3002` (Mapped to avoid port 3000 used by other host services)
* **URL**: `http://175.0.198.10:3002`
* **Default Credentials**: Username: `admin` / Password: `admin`
* **Role**: Visualizes live TPS, fraud score distributions, top transferring institutions, and alert feeds.

---

## 4. CI/CD & Auto-Deployment Workflow

```
[ Developer PC (D:\Dev_By_Eng\Fraud-monitor) ]
                   │
                   │ git push origin main
                   ▼
[ GitHub Repository: engsong/fraud-monitor ]
                   │
                   │ (Auto trigger or bash deploy.sh)
                   ▼
[ Production Server (175.0.198.10) ]
  1. git pull origin main
  2. docker compose up -d (ensures DB & Redis are healthy)
  3. systemctl restart fraud-monitor.service
```

---

## 5. Daily Operations & Useful Commands

### 1. Monitor Live Stream & Fraud Decisions:
```bash
journalctl -u fraud-monitor -f
```

### 2. Check Service Status:
```bash
systemctl status fraud-monitor.service
docker ps
```

### 3. Query PostgreSQL Database:
```bash
# Check total transaction count
docker exec -it fraud-postgres psql -U lev -d lapnet_fraud_db -c "
SELECT count(*) AS total_transactions FROM fraud_monitoring.transactions;
"

# Check latest transactions
docker exec -it fraud-postgres psql -U lev -d lapnet_fraud_db -c "
SELECT tx_id, node, source_bank, dest_bank, amount, currency, risk_score, timestamp 
FROM fraud_monitoring.transactions 
ORDER BY id DESC LIMIT 10;
"

# Check high-risk fraud alerts
docker exec -it fraud-postgres psql -U lev -d lapnet_fraud_db -c "
SELECT tx_id, node, source_bank, dest_bank, amount, risk_score, reasons, timestamp 
FROM fraud_monitoring.fraud_alerts 
ORDER BY timestamp DESC;
"
```

### 4. Restart or Stop Services:
```bash
# Restart fraud monitor streaming engine
systemctl restart fraud-monitor

# Restart database / redis containers
cd /root/LAPNet/Fraud-monitor
docker compose restart
```
