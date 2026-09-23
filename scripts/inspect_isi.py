import oracledb
import os

user = os.environ.get('TEST_DB_USER', 'isi_pos_test')
password = os.environ.get('TEST_DB_PASSWORD', 'isi_pos_test')
host = os.environ.get('DB_HOST', '194.233.91.69')
port = int(os.environ.get('DB_PORT', '1521'))
service_name = os.environ.get('DB_SERVICE_NAME', 'orclpdb1')

conn = oracledb.connect(
    user=user,
    password=password,
    host=host,
    port=port,
    service_name=service_name,
    tcp_connect_timeout=10
)
cur = conn.cursor()

# Query table count for owner ISI
cur.execute("SELECT COUNT(*) FROM all_tables WHERE owner = 'ISI'")
row = cur.fetchone()
print(f"COUNT OF TABLES IN ISI SCHEMA: {row[0]}")

# If any, list them
if row[0] > 0:
    cur.execute("SELECT table_name FROM all_tables WHERE owner = 'ISI'")
    tables = cur.fetchall()
    print("TABLES IN ISI:", [t[0] for t in tables])

# Also check ISI_POS_TEST count
cur.execute("SELECT COUNT(*) FROM all_tables WHERE owner = 'ISI_POS_TEST'")
row_test = cur.fetchone()
print(f"COUNT OF TABLES IN ISI_POS_TEST SCHEMA: {row_test[0]}")

conn.close()
