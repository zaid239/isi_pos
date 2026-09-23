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
    cur.execute("SELECT owner, directory_name, directory_path FROM all_directories WHERE directory_name = 'DATA_PUMP_DIR'")
    row = cur.fetchone()
    print("DATA_PUMP_DIR:", row)
except Exception as e:
    print("Error:", e)

conn.close()
