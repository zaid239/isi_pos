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
cur.execute("SELECT grantee, table_schema, table_name, privilege FROM all_tab_privs WHERE table_name LIKE '%DIR%'")
for r in cur.fetchall():
    print(r)

print("--- DIRECTORIES ---")
cur.execute("SELECT * FROM all_directories")
print("all_directories:", cur.fetchall())

conn.close()
