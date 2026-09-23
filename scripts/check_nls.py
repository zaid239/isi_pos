import oracledb

dsn = oracledb.makedsn("194.233.91.69", 1521, service_name="orclpdb1")
conn = oracledb.connect(user="isi_pos_test", password="isi_pos_test", dsn=dsn)
cursor = conn.cursor()
cursor.execute("SELECT parameter, value FROM nls_database_parameters WHERE parameter IN ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET')")
res = dict(cursor.fetchall())
print("NLS Parameters:", res)
cursor.execute("SELECT user, sys_context('USERENV', 'CURRENT_SCHEMA') FROM dual")
print("User / Current Schema:", cursor.fetchall())
cursor.execute("SELECT count(*) FROM user_tables")
print("Existing user tables count:", cursor.fetchone()[0])
conn.close()
