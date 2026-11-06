"""慧云日报/月报共用事务写入与保留周期。"""
from OTA采集服务.pms_history_retention import cleanup_history

SOURCE = 'PMS（慧云）'


def sync_report(rows, hotel_id, dates, snapshot, table, period_key, connection=None):
    if (table, period_key) not in {('jy01_hotel_statistics_daily', 'business_date'), ('jy03_hotel_statistics_month', 'period_month')}:
        raise ValueError('不支持的慧云经营报表')
    if (not hotel_id or not rows or len(rows) != len(dates) or len(set(dates)) != len(dates)
            or {row[period_key] for row in rows} != set(dates)
            or any(row.get('hotel_id') != hotel_id or row.get('source_platform') != SOURCE
                   or row.get('hotel_name') != rows[0]['hotel_name'] or row.get('snapshot_time') != snapshot
                   or row.get('dimension_type') != '总营业指标' or row.get('dimension_name') != '总营业指标'
                   for row in rows)):
        raise ValueError('慧云经营报表入库批次不完整或范围不一致')
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT hotel_id, snapshot_time FROM `{table}` WHERE hotel_name=%s '
                        f'AND source_platform=%s AND {period_key} BETWEEN %s AND %s '
                        'AND dimension_type=%s AND dimension_name=%s FOR UPDATE',
                        (rows[0]['hotel_name'], SOURCE, min(dates), max(dates), '总营业指标', '总营业指标'))
            if any(str(existing) != hotel_id or (stamp and stamp > snapshot) for existing, stamp in cur.fetchall()):
                raise ValueError('慧云经营报表已有其他酒店或更新批次，拒绝覆盖')
            columns = list(rows[0])
            keys = {'hotel_id', 'hotel_name', 'source_platform', period_key, 'dimension_type', 'dimension_name'}
            cur.executemany(f'INSERT INTO `{table}` ({", ".join(columns)}) VALUES '
                            f'({", ".join("%(" + key + ")s" for key in columns)}) ON DUPLICATE KEY UPDATE '
                            + ', '.join(f'{key}=VALUES({key})' for key in columns if key not in keys), rows)
            cleanup_history(cur, table, hotel_id, SOURCE, snapshot)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
