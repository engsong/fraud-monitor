-- =============================================================================
-- POSTGRESQL INITIALIZATION SCRIPT FOR FRAUD MONITORING
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS fraud_monitoring AUTHORIZATION lev;
GRANT ALL ON SCHEMA fraud_monitoring TO lev;

CREATE TABLE IF NOT EXISTS fraud_monitoring.transactions (
    id BIGSERIAL PRIMARY KEY,
    tx_id VARCHAR(100) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    channel VARCHAR(50),
    source_bank VARCHAR(50),
    source_account TEXT,
    dest_bank VARCHAR(50),
    dest_account TEXT,
    amount NUMERIC(18, 2),
    currency VARCHAR(10),
    status VARCHAR(20),
    response_code VARCHAR(20),
    velocity_1m INT DEFAULT 0,
    velocity_5m INT DEFAULT 0,
    amount_1h NUMERIC(18, 2) DEFAULT 0,
    risk_score INT DEFAULT 0,
    is_fraud SMALLINT DEFAULT 0,
    reasons TEXT[],
    raw_reference VARCHAR(100),
    node VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fraud_monitoring.fraud_alerts (
    alert_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tx_id VARCHAR(100) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    source_bank VARCHAR(50),
    source_account TEXT,
    dest_bank VARCHAR(50),
    dest_account TEXT,
    amount NUMERIC(18, 2),
    currency VARCHAR(10),
    risk_score INT DEFAULT 0,
    reasons TEXT[],
    alert_status VARCHAR(20) DEFAULT 'NEW',
    node VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tx_timestamp ON fraud_monitoring.transactions (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_tx_src_acc ON fraud_monitoring.transactions (source_account);
CREATE INDEX IF NOT EXISTS idx_tx_dst_acc ON fraud_monitoring.transactions (dest_account);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON fraud_monitoring.fraud_alerts (timestamp DESC);
