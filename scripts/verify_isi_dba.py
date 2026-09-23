import oracledb

conn = oracledb.connect(user='isi', password='isi', host='194.233.91.69', port=1521, service_name='orclpdb1')
cur = conn.cursor()

print("--- CONNECTED AS: isi ---")
cur.execute("SELECT COUNT(*) FROM dba_tables WHERE owner = 'ISI'")
print("SELECT COUNT(*) FROM dba_tables WHERE owner = 'ISI':", cur.fetchone()[0])

cur.execute("SELECT object_type, COUNT(*) FROM dba_objects WHERE owner = 'ISI' GROUP BY object_type")
print("SELECT object_type, COUNT(*) FROM dba_objects WHERE owner = 'ISI' GROUP BY object_type:", cur.fetchall())

cur.execute("SELECT NVL(SUM(bytes)/1024/1024, 0) AS mb FROM dba_segments WHERE owner = 'ISI'")
print("SELECT NVL(SUM(bytes)/1024/1024, 0) AS mb FROM dba_segments WHERE owner = 'ISI':", cur.fetchone()[0])

print("\n--- USER VIEWS (AS isi) ---")
cur.execute("SELECT COUNT(*) FROM user_tables")
print("SELECT COUNT(*) FROM user_tables:", cur.fetchone()[0])

cur.execute("SELECT object_type, COUNT(*) FROM user_objects GROUP BY object_type")
print("SELECT object_type, COUNT(*) FROM user_objects GROUP BY object_type:", cur.fetchall())

conn.close()
