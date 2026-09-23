import oracledb

if __name__ == "__main__":
    try:
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
            handle = cur.callfunc('dbms_datapump.open', int, ['EXPORT', 'SCHEMA'])
            print('DBMS_DATAPUMP.OPEN handle:', handle)
            cur.callproc('dbms_datapump.stop_job', [handle])
        except Exception as e:
            print('DBMS_DATAPUMP error:', e)
        conn.close()
    except Exception as e:
        print('Connection error:', e)
