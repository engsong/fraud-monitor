import psycopg2
from psycopg2.extras import RealDictCursor
import json

def main():
    conn = psycopg2.connect(host='127.0.0.1', user='lev', password='LEV@2026', dbname='lapnet_fraud_db')
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Let's find accounts that form multi-hop chains
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
        LIMIT 5;
    """)
    intermediate_accounts = cur.fetchall()

    for acc_info in intermediate_accounts:
        target_acc = acc_info['dest_account']
        print(f"\n=======================================================")
        print(f"TRACING MULTI-HOP FLOW FOR ACCOUNT: {target_acc} (inflow count: {acc_info['count_as_dest']})")
        print(f"=======================================================")
        
        cur.execute("""
            SELECT id, tx_id, node, source_bank, source_account, dest_bank, dest_account, amount, currency, created_at, risk_score, reasons
            FROM fraud_monitoring.transactions
            WHERE dest_account = %s OR source_account = %s
            ORDER BY created_at ASC
            LIMIT 12;
        """, (target_acc, target_acc))
        rows = cur.fetchall()
        for r in rows:
            direction = "INFLOW  ➔" if r['dest_account'] == target_acc else "OUTFLOW ➔"
            print(f"[{r['created_at'].strftime('%H:%M:%S')}] {direction} {r['source_bank']:<8} ({r['source_account'][:14]}...) ➔ {r['dest_bank']:<8} ({r['dest_account'][:14]}...) | {r['amount']:>12,.2f} {r['currency']} | risk: {r['risk_score']}")

    # Check for Ring / Circular transactions:
    # A -> B, then B -> C, then C -> A (or A -> B -> A)
    print("\n=======================================================")
    print("CHECKING FOR CIRCULAR RING TRANSACTIONS IN DB (A -> B -> ... -> A)")
    print("=======================================================")
    cur.execute("""
        SELECT t1.source_account AS acc_a, t1.source_bank AS bank_a,
               t1.dest_account AS acc_b, t1.dest_bank AS bank_b,
               t2.dest_account AS acc_c, t2.dest_bank AS bank_c,
               t1.amount AS amt1, t2.amount AS amt2,
               t1.created_at AS time1, t2.created_at AS time2
        FROM fraud_monitoring.transactions t1
        JOIN fraud_monitoring.transactions t2 
          ON t1.dest_account = t2.source_account 
          AND t2.created_at >= t1.created_at
          AND t2.created_at <= t1.created_at + INTERVAL '24 hour'
        WHERE t2.dest_account = t1.source_account
          AND t1.source_account != t1.dest_account
          AND t1.source_account IS NOT NULL AND t1.source_account != 'N/A'
        LIMIT 10;
    """)
    rings_2hop = cur.fetchall()
    print(f"Found 2-hop Direct Rings (A -> B -> A): {len(rings_2hop)}")
    for r in rings_2hop:
        print(f"RING DETECTED! [{r['bank_a']}:{r['acc_a'][:10]}...] ➔ [{r['bank_b']}:{r['acc_b'][:10]}...] ➔ [{r['bank_c']}:{r['acc_c'][:10]}...] | amt: {r['amt1']} -> {r['amt2']}")

    # Also check 3-hop Rings: A -> B -> C -> A
    cur.execute("""
        SELECT t1.source_account AS acc_a, t1.source_bank AS bank_a,
               t1.dest_account AS acc_b, t1.dest_bank AS bank_b,
               t2.dest_account AS acc_c, t2.dest_bank AS bank_c,
               t3.dest_account AS acc_d, t3.dest_bank AS bank_d,
               t1.amount AS amt1, t3.amount AS amt3,
               t1.created_at AS time1, t3.created_at AS time3
        FROM fraud_monitoring.transactions t1
        JOIN fraud_monitoring.transactions t2 
          ON t1.dest_account = t2.source_account AND t2.created_at >= t1.created_at AND t2.created_at <= t1.created_at + INTERVAL '24 hour'
        JOIN fraud_monitoring.transactions t3
          ON t2.dest_account = t3.source_account AND t3.created_at >= t2.created_at AND t3.created_at <= t2.created_at + INTERVAL '24 hour'
        WHERE t3.dest_account = t1.source_account
          AND t1.source_account != t1.dest_account
          AND t2.source_account != t2.dest_account
        LIMIT 5;
    """)
    rings_3hop = cur.fetchall()
    print(f"Found 3-hop Rings (A -> B -> C -> A): {len(rings_3hop)}")
    for r in rings_3hop:
        print(f"3-HOP RING! [{r['bank_a']}:{r['acc_a'][:10]}...] ➔ [{r['bank_b']}:{r['acc_b'][:10]}...] ➔ [{r['bank_c']}:{r['acc_c'][:10]}...] ➔ [{r['bank_d']}:{r['acc_d'][:10]}...]")

if __name__ == '__main__':
    main()
