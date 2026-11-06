"""慧云JL11事务内替换最新房型汇总，不清理其他平台。"""
from datetime import timedelta

SOURCE = 'PMS（慧云）'
TABLE = 'jl11_room_type_classification'


def sync_mysql(rows, hotel_id, snapshot, connection=None):
    start, end = snapshot.date() - timedelta(days=30), snapshot.date() - timedelta(days=1)
    names = {row['room_type_name'] for row in rows}
    if (not hotel_id or not rows or len(names) != len(rows) or names & {'', '总计', '合计', '哑房'}
            or any(row.get('hotel_id') != hotel_id or row.get('source_platform') != SOURCE
                   or row.get('hotel_name') != rows[0]['hotel_name'] or row.get('snapshot_time') != snapshot
                   or row.get('snapshot_date') != snapshot.date() or row.get('period_start') != start
                   or row.get('period_end') != end or row.get('section') != 'summary'
                   or row.get('dimension_code') != '' or row.get('dimension_name') != '' for row in rows)):
        raise ValueError('慧云JL11入库批次或统计范围不一致')
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT source_platform,snapshot_date,snapshot_time FROM `{TABLE}` WHERE hotel_id=%s FOR UPDATE', (hotel_id,))
            if any((source == SOURCE and stamp and stamp > snapshot) or (source != SOURCE and day == snapshot.date())
                   for source, day, stamp in cur.fetchall()):
                raise ValueError('慧云JL11已有其他PMS当日数据或更新批次，拒绝覆盖')
            cur.execute("SELECT source_room_type_name, MIN(room_type_id) FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_huiyun' AND is_active=1 AND source_product_id='' "
                        "GROUP BY source_room_type_name HAVING COUNT(DISTINCT room_type_id)=1", (hotel_id,))
            aliases = dict(cur.fetchall())
            records = [{**row, 'room_type_id': aliases.get(row['room_type_name'])} for row in rows]
            cur.execute(f'DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s', (hotel_id, SOURCE))
            columns = list(records[0])
            cur.executemany(f'INSERT INTO `{TABLE}` ({", ".join(columns)}) VALUES '
                            f'({", ".join("%(" + key + ")s" for key in columns)})', records)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
