import psycopg2
from psycopg2.extras import RealDictCursor
import json

def main():
    conn = psycopg2.connect(host='127.0.0.1', user='lev', password='LEV@2026', dbname='lapnet_fraud_db')
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'fraud_monitoring' AND table_name = 'transactions';
    """)
    print("=== TRANSACTIONS COLUMNS ===")
    for r in cur.fetchall():
        print(f"{r['column_name']}: {r['data_type']}")

    print("\n=== FIND SAMPLES OF MULTI-HOP / MULE CHAINS ===")
    # Look for accounts that appear as both dest_account and source_account (intermediate hops!)
    cur.execute("""
        SELECT dest_account, count(*) AS count_as_dest
        FROM fraud_monitoring.transactions
        WHERE dest_account IN (
            SELECT source_account FROM fraud_monitoring.transactions
            WHERE source_account IS NOT NULL AND source_account != 'N/A'
        )
        AND dest_account IS NOT NULL AND dest_account != 'N/A'
        GROUP BY dest_account
        ORDER BY count_as_dest DESC
        LIMIT 10;
    """)
    intermediate_accounts = cur.fetchall()
    print("Intermediate Accounts (appear as both receiver and sender):")
    for acc in intermediate_accounts:
        print(acc)

    if intermediate_accounts:
        target_acc = intermediate_accounts[0]['dest_account']
        print(f"\nTracing account: {target_acc}")
        
        cur.execute("""
            SELECT id, source_bank, source_account, dest_bank, dest_account, amount, currency, created_at, risk_score, triggers
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s OR source_account = %s
            ORDER BY created_at ASC
            LIMIT 15;
        """, (target_acc, target_acc))
        rows = cur.fetchall()
        for r in rows:
            print(f"[{r['created_at']}] {r['source_bank']}:{r['source_account'][:12]}... -> {r['dest_bank']}:{r['dest_account'][:12]}... | {r['amount']} {r['currency']} | score: {r['risk_score']}")

    # Also check the mule alerts from the user's screenshot:
    # '000201...C5F8', '000201...14DB'
    cur.execute("""
        SELECT source_bank, source_account, dest_bank, dest_account, amount, currency, created_at, risk_score, triggers
        FROM fraud_monitoring.transactions
        WHERE triggers ILIKE '%Mule%' OR risk_score >= 80
        ORDER BY id DESC LIMIT 5;
    """)
    print("\n=== RECENT HIGH-RISK MULE ALERTS ===")
    for r in cur.fetchall():
        print(f"[{r['created_at']}] {r['source_bank']}:{r['source_account'][:15]}... -> {r['dest_bank']}:{r['dest_account'][:15]}... | {r['amount']} {r['currency']} | {r['triggers']}")

if __name__ == '__main__':
    main()
