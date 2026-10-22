import psycopg2

def check():
    conn = psycopg2.connect(host='127.0.0.1', user='lev', password='LEV@2026', dbname='lapnet_fraud_db')
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT bank, count(*) FROM (
            SELECT source_bank AS bank FROM fraud_monitoring.transactions
            UNION ALL
            SELECT dest_bank AS bank FROM fraud_monitoring.transactions
        ) t
        GROUP BY bank
        ORDER BY count(*) DESC;
    """)
    rows = cur.fetchall()
    print("=== ALL MEMBERS IN LAPNET FRAUD DB ===")
    for r in rows:
        print(f"Member: {r[0]:<20} Count: {r[1]}")

    cur.execute("SELECT DISTINCT node FROM fraud_monitoring.transactions;")
    print("\n=== NODES ===")
    for r in cur.fetchall():
        print("Node:", r[0])

if __name__ == '__main__':
    check()
