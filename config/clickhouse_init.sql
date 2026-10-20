-- =============================================================================
-- CLICKHOUSE INITIALIZATION SCRIPT FOR FRAUD MONITORING
-- =============================================================================

CREATE DATABASE IF NOT EXISTS fraud_monitoring;

-- 1. All Processed Transactions Table
CREATE TABLE IF NOT EXISTS fraud_monitoring.transactions
(
    tx_id String,
    timestamp DateTime64(3, 'UTC'),
    channel LowCardinality(String),
    source_bank LowCardinality(String),
    source_account String,
    dest_bank LowCardinality(String),
    dest_account String,
    amount Float64,
    currency LowCardinality(String),
    status LowCardinality(String),
    response_code LowCardinality(String),
    velocity_1m UInt32,
    velocity_5m UInt32,
    amount_1h Float64,
    risk_score UInt8,
    is_fraud UInt8,
    reasons Array(String)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, source_bank, dest_bank, tx_id);

-- 2. High-Risk Fraud Alerts Table
CREATE TABLE IF NOT EXISTS fraud_monitoring.fraud_alerts
(
    alert_id UUID DEFAULT generateUUIDv4(),
    tx_id String,
    timestamp DateTime64(3, 'UTC'),
    source_bank String,
    source_account String,
    dest_bank String,
    dest_account String,
    amount Float64,
    currency String,
    risk_score UInt8,
    reasons Array(String),
    alert_status LowCardinality(String) DEFAULT 'NEW', -- NEW, ACKNOWLEDGED, RESOLVED, FALSE_POSITIVE
    notified_telegram UInt8 DEFAULT 0,
    notified_webhook UInt8 DEFAULT 0
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, risk_score, tx_id);
