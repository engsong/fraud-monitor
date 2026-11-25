import psycopg2
import json

def main():
    conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname="lapnet_fraud_db", user="lev", password="LEV@2026")
    cur = conn.cursor()
    
    print("=== DISTINCT DEVNAME ===")
    cur.execute("SELECT devname, count(1) FROM fraud_monitoring.fortiadc_traffic_logs GROUP BY devname;")
    for row in cur.fetchall():
        print(row)
        
    print("\n=== DISTINCT ACTION & HTTP_RETCODE FOR /transfer ===")
    cur.execute("""
        SELECT devname, action, http_retcode, real_server, count(1) 
        FROM fraud_monitoring.fortiadc_traffic_logs 
        WHERE http_url LIKE '%transfer%' 
        GROUP BY devname, action, http_retcode, real_server 
        ORDER BY count(1) DESC LIMIT 20;
    """)
    for row in cur.fetchall():
        print(row)
        
    print("\n=== SAMPLE /transfer LOGS WITH RETCODE != 200 OR ACTION != 'none' ===")
    cur.execute("""
        SELECT timestamp, devname, client_ip, bank_name, http_method, http_url, http_retcode, action, real_server, raw_log 
        FROM fraud_monitoring.fortiadc_traffic_logs 
        WHERE http_url LIKE '%transfer%' AND (http_retcode >= 400 OR action NOT IN ('none', ''))
        ORDER BY timestamp DESC LIMIT 5;
    """)
    for row in cur.fetchall():
        print(f"Time: {row[0]}, Dev: {row[1]}, Bank: {row[3]}, Method: {row[4]}, URL: {row[5]}, Code: {row[6]}, Action: {row[7]}, RealServer: {row[8]}")
        print(f"Raw: {row[9][:200]}...\n")
        
    print("\n=== SAMPLE RAW LOGS FOR ANY ACTION in ('close', 'drop', 'block', 'reject', 'reset') ===")
    cur.execute("""
        SELECT timestamp, devname, client_ip, bank_name, http_method, http_url, http_retcode, action, real_server, raw_log 
        FROM fraud_monitoring.fortiadc_traffic_logs 
        WHERE action NOT IN ('none', '') OR http_retcode IN (502, 503, 504)
        ORDER BY timestamp DESC LIMIT 5;
    """)
    for row in cur.fetchall():
        print(f"Time: {row[0]}, Dev: {row[1]}, Bank: {row[3]}, Code: {row[6]}, Action: {row[7]}, RealServer: {row[8]}")
        print(f"Raw: {row[9][:200]}...\n")

if __name__ == "__main__":
    main()
