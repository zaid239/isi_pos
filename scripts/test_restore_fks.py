import oracledb
conn = oracledb.connect(user='isi_pos_test', password='isi_pos_test', host='194.233.91.69', port=1521, service_name='orclpdb1')
cur = conn.cursor()
cur.execute("""
BEGIN
    FOR c IN (SELECT table_name, constraint_name FROM user_constraints WHERE constraint_type = 'R') LOOP
        EXECUTE IMMEDIATE 'ALTER TABLE "' || c.table_name || '" DISABLE CONSTRAINT "' || c.constraint_name || '"';
        EXECUTE IMMEDIATE 'ALTER TABLE "' || c.table_name || '" ENABLE CONSTRAINT "' || c.constraint_name || '"';
    END LOOP;
END;
""")
print("DISABLE/ENABLE CONSTRAINTS SUCCESS!")
conn.close()
