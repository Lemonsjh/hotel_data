"""慧云房费按日期刷新，精确映射原房型，事务内清理过期数据。"""
from OTA采集服务.pms_history_retention import cleanup_history

SOURCE = 'PMS（慧云）'
TABLE = 'rs01_room_revenue_daily'


def sync_mysql(rows, hotel_id, start, end, snapshot, connection=None):
    if not hotel_id or start > end or any(
        row.get('hotel_id') != hotel_id or row.get('source_platform') != SOURCE or row.get('snapshot_time') != snapshot
        or not start <= row['business_date'] <= end or row.get('charge_subject') != '房费' for row in rows
    ):
        raise ValueError('慧云RS01入库范围不一致')
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT source_platform,snapshot_time FROM `{TABLE}` WHERE hotel_id=%s '
                        'AND business_date BETWEEN %s AND %s FOR UPDATE', (hotel_id, start, end))
            if any(source != SOURCE or (stamp and stamp > snapshot) for source, stamp in cur.fetchall()):
                raise ValueError('慧云RS01目标日期已有其他PMS或更新批次，拒绝覆盖')
            cur.execute("SELECT source_room_type_name, MIN(room_type_id) FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_huiyun' AND is_active=1 AND source_product_id='' "
                        "GROUP BY source_room_type_name HAVING COUNT(DISTINCT room_type_id)=1", (hotel_id,))
            aliases = dict(cur.fetchall())
            cur.execute(f'DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s '
                        'AND business_date BETWEEN %s AND %s AND charge_subject=%s', (hotel_id, SOURCE, start, end, '房费'))
            if rows:
                records = [{**row, 'room_type_id': aliases.get(row['room_type_name'])} for row in rows]
                columns = list(records[0])
                cur.executemany(f'INSERT INTO `{TABLE}` ({", ".join(columns)}) VALUES '
                                f'({", ".join("%(" + key + ")s" for key in columns)})', records)
            cleanup_history(cur, TABLE, hotel_id, SOURCE, snapshot)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
