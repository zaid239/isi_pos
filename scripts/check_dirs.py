import oracledb

conn = oracledb.connect(
    user='isi_pos_test',
    password='isi_pos_test',
    host='194.233.91.69',
    port=1521,
    service_name='orclpdb1',
    tcp_connect_timeout=10
)
cur = conn.cursor()
try:
    cur.execute("SELECT directory_name, directory_path FROM all_directories")
    rows = cur.fetchall()
    print("Available Directories in Oracle:")
    for r in rows:
        print(r)
except Exception as e:
    print("Error querying all_directories:", e)

conn.close()
