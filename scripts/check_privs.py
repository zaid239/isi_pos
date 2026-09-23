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
cur.execute("SELECT privilege FROM user_sys_privs")
print("System Privileges:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT table_name, privilege FROM user_tab_privs")
print("Table/Object Privileges:", cur.fetchall())

cur.execute("SELECT granted_role FROM user_role_privs")
print("Roles:", [r[0] for r in cur.fetchall()])

conn.close()
