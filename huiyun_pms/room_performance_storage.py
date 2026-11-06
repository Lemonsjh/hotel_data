"""慧云房型日报按日替换，保留其他日期及平台数据。"""
from OTA采集服务.pms_history_retention import cleanup_history

SOURCE = 'PMS（慧云）'
TABLE = 'jl01_room_type_performance_daily'


def sync_mysql(rows, hotel_id, dates, snapshot, connection=None):
    keys = {(row['business_date'], row['room_type_name']) for row in rows}
    if (not hotel_id or not rows or len(keys) != len(rows) or {day for day, _ in keys} != set(dates)
            or any(row.get('hotel_id') != hotel_id or row.get('source_platform') != SOURCE
                   or row.get('snapshot_time') != snapshot or row.get('hotel_name') != rows[0]['hotel_name']
                   or not row.get('room_type_name') or row['room_type_name'] in {'总计', '合计', '哑房'}
                   or row.get('pms_rate_room_type_id') != '' for row in rows)):
        raise ValueError('慧云JL01入库批次不完整或范围不一致')
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            marks = ', '.join(['%s'] * len(dates))
            cur.execute(f'SELECT source_platform,snapshot_time FROM `{TABLE}` WHERE hotel_id=%s '
                        f'AND business_date IN ({marks}) FOR UPDATE', (hotel_id, *dates))
            if any(source != SOURCE or (stamp and stamp > snapshot) for source, stamp in cur.fetchall()):
                raise ValueError('慧云JL01目标日期已有其他PMS或更新批次，拒绝覆盖')
            cur.execute("SELECT source_room_type_name, MIN(room_type_id) FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_huiyun' AND is_active=1 AND source_product_id='' "
                        "GROUP BY source_room_type_name HAVING COUNT(DISTINCT room_type_id)=1", (hotel_id,))
            aliases = dict(cur.fetchall())
            records = [{**row, 'room_type_id': aliases.get(row['room_type_name'])} for row in rows]
            cur.execute(f'DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s '
                        f'AND business_date IN ({marks})', (hotel_id, SOURCE, *dates))
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
