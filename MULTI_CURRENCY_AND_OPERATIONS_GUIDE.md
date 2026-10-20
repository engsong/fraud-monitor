# 🛡️ LAPNet / LMPS Payment Switch - Multi-Currency Fraud Monitoring & Operations Guide

> **Production Host**: `175.0.198.10` (CentOS 8 / `WEBSERVER01`)  
> **Switching Nodes Streamed**: `175.2.0.23` (`lmps-app1`) & `175.2.0.24` (`lmps-app2`)  
> **Grafana Live UI**: [http://175.0.198.10:3002](http://175.0.198.10:3002) (`admin` / `admin`)  
> **Supported Currencies**: **LAK** (Lao Kip), **USD** (US Dollar), **THB** (Thai Baht), **CNY** (Chinese Yuan)

---

## 1. System Architecture Overview

The Fraud Monitoring System operates via non-intrusive real-time streaming, ensuring **zero latency impact** on switching operations:

```
[Switching Node 1] 175.2.0.23 (/root/LMPSCore/log/LMPS.log) ──┐ (SSH Stream)
                                                                ▼
                                                    [fraud-monitor.service] (175.0.198.10)
                                                                │
                                                                ├─► Parser (ISO20022 XML / JSON)
                                                                ├─► Redis 7 (In-Memory Sliding Velocity)
                                                                ├─► Rule Engine (Multi-Currency Limits)
                                                                ├─► AI Model (Isolation Forest Outlier)
                                                                │
[Switching Node 2] 175.2.0.24 (/root/LMPSCore/log/LMPS.log) ──┘ ├─► PostgreSQL 16 (lapnet_fraud_db)
                                                                ├─► Grafana Dashboard (:3002)
                                                                └─► Telegram Alert Dispatcher (Optional)
```

---

## 2. Multi-Currency Fraud Detection Rules

Transactions across LAPNet switching channels are processed in multiple currencies. Because raw values differ drastically (e.g., \$2,500 USD is ~55,000,000 LAK, while 80,000 THB is ~52,000,000 LAK), the system enforces currency-specific thresholds:

### 2.1 Threshold Matrix

| Currency | Large Transfer Threshold (`MAX_AMOUNT_*`) | Off-Hours Threshold (`OFF_HOURS_*`) (01:00-04:30 UTC) | Micro-Probing Limit (`PROBING_*`) (Velocity $\ge 3$/min) | AI Normalization Multiplier (`RATE_*_TO_LAK`) |
| :---: | :---: | :---: | :---: | :---: |
| **LAK** | **50,000,000 LAK** (~$2,300) | **10,000,000 LAK** | **$\le$ 10,000 LAK** | `1.0` (Base) |
| **THB** | **80,000 THB** (~$2,400) | **15,000 THB** | **$\le$ 20 THB** | `650.0` |
| **USD** | **$2,500 USD** | **$500 USD** | **$\le$ $1.00 USD** | `22,000.0` |
| **CNY** | **15,000 CNY** | **3,000 CNY** | **$\le$ 5 CNY** | `3,100.0` |

### 2.2 Risk Scoring Calculation

Every incoming transaction is evaluated dynamically and scored from `0` to `100`:

1. **Watchlist / Blacklist Match**: Immediate **+100 points** (CRITICAL alert).
2. **High Velocity**: $\gt 5$ transfers in 60 seconds $\rightarrow$ **+45 points**.
3. **Large Transfer**: Amount exceeds currency threshold $\rightarrow$ **+35 points**.
4. **Off-Hours Activity**: Large transfer between 01:00 AM and 04:30 AM UTC $\rightarrow$ **+25 points**.
5. **Micro-Probing**: Rapid small amounts ($\le$ probing limit) with velocity $\ge 3$ $\rightarrow$ **+35 points**.
6. **AI Behavioral Outlier**: Statistical anomaly detected by Isolation Forest $\rightarrow$ up to **+40 points**.

> **Alert Condition**: If `Composite Risk Score >= RISK_ALERT_THRESHOLD` (Default: `80`), the transaction is logged to `fraud_monitoring.fraud_alerts`, highlighted in red on Grafana, and dispatched via Telegram.

---

## 3. Configuration Reference (`.env`)

All thresholds and conversion rates are completely configurable in `.env` without modifying code:

```ini
# ==============================================================================
# FRAUD MONITORING ENVIRONMENT CONFIGURATION (175.0.198.10)
# ==============================================================================

# Database Engine Selection ('postgres')
DB_ENGINE=postgres

# PostgreSQL Configuration
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=lev
POSTGRES_PASSWORD=LEV@2026
POSTGRES_DB=lapnet_fraud_db
POSTGRES_SCHEMA=fraud_monitoring

# Redis Configuration (In-Memory Velocity Tracking)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# ------------------------------------------------------------------------------
# FRAUD DETECTION THRESHOLDS & MULTI-CURRENCY RULES
# ------------------------------------------------------------------------------
RISK_ALERT_THRESHOLD=80
MAX_VELOCITY_1MIN=5
MAX_SINGLE_AMOUNT=50000000

# 1. Multi-Currency Large Transfer Limits (Triggers "Large Transfer" Rule +35 pts)
MAX_AMOUNT_LAK=50000000
MAX_AMOUNT_THB=80000
MAX_AMOUNT_USD=2500
MAX_AMOUNT_CNY=15000

# 2. Multi-Currency Off-Hours Limits (01:00 AM - 04:30 AM UTC, +25 pts)
OFF_HOURS_LAK=10000000
OFF_HOURS_THB=15000
OFF_HOURS_USD=500
OFF_HOURS_CNY=3000

# 3. Multi-Currency Micro-Probing Limits (Rapid card/account test attempts, +35 pts)
PROBING_LAK=10000
PROBING_THB=20
PROBING_USD=1.0
PROBING_CNY=5.0

# 4. Multi-Currency Exchange Rates to LAK (For AI Machine Learning Normalization)
RATE_THB_TO_LAK=650
RATE_USD_TO_LAK=22000
RATE_CNY_TO_LAK=3100

# ------------------------------------------------------------------------------
# TELEGRAM BOT ALERT CONFIGURATION
# ------------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
TELEGRAM_CHAT_ID=YOUR_TELEGRAM_CHAT_ID_HERE
TELEGRAM_ENABLED=false
```

---

## 4. GitHub Actions vs. 1-Click Local Deploy

### Why GitHub Actions Cloud Runners Failed
- **Private Subnet / Firewall**: Server `175.0.198.10` is an internal on-premise IP on the LAPNet bank intranet. GitHub's cloud runners run in Microsoft Azure public datacenters and cannot establish inbound SSH connections to `175.0.198.10:22`.
- **Missing Secrets**: Without `secrets.SSH_PRIVATE_KEY` stored in GitHub repository settings, cloud SSH runners abort immediately.

### Recommended Deployment Methods

#### Method A: 1-Click Deploy from Local PC (Easiest & Fastest)
Your PC (`175.17.4.20`) already has authorized network access to `175.0.198.10`.
Simply **Double-Click** `deploy_to_server.bat` in `D:\Dev_By_Eng\Fraud-monitor\`.
It will automatically:
1. Push local changes to GitHub (`git push origin main`).
2. SSH to `175.0.198.10` and execute `git pull origin main`.
3. Restart `fraud-monitor.service` and Grafana with zero downtime.

#### Method B: GitHub Self-Hosted Runner (For Automated Cloud CI/CD)
If you require automatic deployment immediately upon `git push` without running scripts on your PC:
1. Open GitHub: **Repo Settings** $\rightarrow$ **Actions** $\rightarrow$ **Runners** $\rightarrow$ **New self-hosted runner**.
2. Select **Linux** and run the provided registration script on `175.0.198.10`.
3. The runner on `175.0.198.10` will poll GitHub and pull updates internally, bypassing external firewall restrictions.

---

## 5. Operations & Troubleshooting Runbook

### Service Management Commands (on `175.0.198.10`)
```bash
# Check service status and active SSH streams
systemctl status fraud-monitor.service

# Restart service after changing .env or code
systemctl restart fraud-monitor.service

# Stop / Start service
systemctl stop fraud-monitor.service
systemctl start fraud-monitor.service

# View live real-time transaction processing logs
journalctl -u fraud-monitor.service -f
```

### Docker Containers Management
```bash
# Check status of Postgres, Redis, and Grafana containers
docker ps

# Restart Grafana dashboard container
docker restart fraud-grafana

# Restart Redis velocity container
docker restart fraud-redis

# Restart PostgreSQL database container
docker restart fraud-postgres
```

### Database Verification Queries
```bash
# Check transaction breakdown by currency
docker exec -it fraud-postgres psql -U lev -d lapnet_fraud_db -c "
SELECT currency, count(id) AS count, to_char(sum(amount), 'FM999,999,999,990.00') AS total_volume 
FROM fraud_monitoring.transactions 
GROUP BY currency 
ORDER BY count DESC;
"

# Check recent high-risk fraud alerts
docker exec -it fraud-postgres psql -U lev -d lapnet_fraud_db -c "
SELECT timestamp, tx_id, currency, amount, risk_score, reasons 
FROM fraud_monitoring.fraud_alerts 
ORDER BY timestamp DESC 
LIMIT 10;
"
```

---

## 6. Accessing Grafana Monitoring UI

1. Open your browser and navigate to: **http://175.0.198.10:3002**
2. Credentials:
   - **Username**: `admin`
   - **Password**: `admin`
3. Features on Dashboard (`LAPNet Payment Switch - Real-Time Fraud Monitor`):
   - **Live Metrics**: Total Transactions, High-Risk Alerts, Hourly Velocity.
   - **Multi-Currency Cards**: Real-time totals for **LAK**, **THB**, and **USD**.
   - **Currency Distribution**: Pie chart breakdown of all processed currencies.
   - **Live Feed**: Full transaction stream with sender/receiver accounts and bank codes.
   - **High-Risk Alerts Feed**: Instant alert table with highlighted risk scores and triggered fraud reasons.
