"""慧云 JD01 按预计到店日期替换，保留90天。"""
from datetime import datetime, time, timedelta

from huiyun_pms.booking_window import retention_cutoff

SOURCE = 'PMS（慧云）'
TABLE = 'jd01_booking_detail'
COLUMNS = ['hotel_name', 'hotel_id', 'source_platform', 'order_id', 'booking_time', 'contact', 'guest_source',
           'member_level', 'arrival_time', 'departure_time', 'room_type_name', 'room_type_id', 'room_count',
           'price_type', 'room_price', 'prepayment', 'guarantee_method', 'booking_status', 'hold_time', 'remarks',
           'operator_name', 'snapshot_time']


def sync_mysql(rows, hotel_id, start, end, snapshot, connection=None):
    if not hotel_id or start > end or any(
        row.get('hotel_id') != hotel_id or row.get('source_platform') != SOURCE or
        not start <= row['arrival_time'].date() <= end or row.get('snapshot_time') != snapshot for row in rows
    ):
        raise ValueError('慧云预订批次酒店或日期不一致')
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    begin, finish = datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT snapshot_time FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s '
                        'AND arrival_time >= %s AND arrival_time < %s FOR UPDATE', (hotel_id, SOURCE, begin, finish))
            if any(stamp and stamp > snapshot for (stamp,) in cur.fetchall()):
                raise ValueError('慧云预订范围已有更新批次')
            cur.execute("SELECT source_room_type_name, MIN(room_type_id) FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_huiyun' AND is_active=1 AND source_product_id='' "
                        "GROUP BY source_room_type_name HAVING COUNT(DISTINCT room_type_id)=1", (hotel_id,))
            aliases = dict(cur.fetchall())
            records = [{**row, 'room_type_id': aliases.get(row['room_type_name'])} for row in rows]
            cur.execute(f'DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s '
                        'AND arrival_time >= %s AND arrival_time < %s', (hotel_id, SOURCE, begin, finish))
            if records:
                cur.executemany(f'INSERT INTO `{TABLE}` ({", ".join(COLUMNS)}) VALUES '
                                f'({", ".join("%(" + key + ")s" for key in COLUMNS)})', records)
            cutoff = datetime.combine(retention_cutoff(snapshot.date()), time.min)
            cur.execute(f'DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s AND arrival_time < %s',
                        (hotel_id, SOURCE, cutoff))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
