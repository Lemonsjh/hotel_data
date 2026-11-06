"""慧云当前营业日房态与小时快照，同一事务写入。"""
from datetime import date, datetime

from OTA采集服务.pms_history_retention import cleanup_history

SOURCE = 'PMS（慧云）'
TABLES = (('pms_room_type_forecast', 'stay_date'), ('pms_room_type_hourly_status', 'snapshot_hour'))
BASE_COLUMNS = ['hotel_id', 'hotel_name', 'source_platform', 'snapshot_time', 'stay_date',
                'room_type_name', 'room_type_id', 'pms_room_type_id', 'total_rooms',
                'available_rooms', 'occupied_rooms', 'overbooking_rooms']


def sync_mysql(rows, connection=None):
    if not rows or not rows[0].get('hotel_id'):
        raise ValueError('慧云房态或内部酒店ID缺失')
    first = rows[0]
    if (len({row['pms_room_type_id'] for row in rows}) != len(rows) or any(
        row.get('source_platform') != SOURCE or
        any(row[key] != first[key] for key in ('hotel_id', 'stay_date', 'snapshot_hour', 'snapshot_time')) or
        any(row.get(key) is None for key in ('room_revenue', 'adr', 'revpar')) for row in rows)):
        raise ValueError('慧云房态酒店、时间或财务指标无效')
    records = []
    for row in rows:
        records.append({**row, 'stay_date': date.fromisoformat(row['stay_date']),
                        **{key: datetime.fromisoformat(row[key]).replace(tzinfo=None)
                           for key in ('snapshot_time', 'snapshot_hour')}})
    first = records[0]
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            incoming = {row['pms_room_type_id'] for row in records}
            for table, key in TABLES:
                cur.execute(f'SELECT pms_room_type_id, source_platform FROM `{table}` '
                            f'WHERE hotel_id=%s AND `{key}`=%s FOR UPDATE', (first['hotel_id'], first[key]))
                if any(room_id in incoming and source != SOURCE for room_id, source in cur.fetchall()):
                    raise ValueError('同房型快照已有其他PMS数据，拒绝覆盖')
            cur.execute("SELECT source_room_type_name, MIN(room_type_id) FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_huiyun' AND is_active=1 "
                        "AND source_product_id='' GROUP BY source_room_type_name "
                        "HAVING COUNT(DISTINCT room_type_id)=1", (first['hotel_id'],))
            aliases = dict(cur.fetchall())
            for row in records:
                row['room_type_id'] = aliases.get(row['room_type_name'])
            for table, key in TABLES:
                hourly = key == 'snapshot_hour'
                where = ' AND snapshot_hour=%s' if hourly else ''
                params = (first['hotel_id'], SOURCE, first[key]) if hourly else (first['hotel_id'], SOURCE)
                cur.execute(f'DELETE FROM `{table}` WHERE hotel_id=%s AND source_platform=%s{where}', params)
                columns = BASE_COLUMNS + (['snapshot_hour'] if hourly else ['room_revenue', 'adr', 'revpar'])
                cur.executemany(f'INSERT INTO `{table}` ({", ".join(columns)}) VALUES '
                                f'({", ".join("%(" + key + ")s" for key in columns)})', records)
            cleanup_history(cur, TABLES[1][0], first['hotel_id'], SOURCE, first['snapshot_time'])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
