"""JD02 按酒店、来源及昨日日期刷新，事务内保留60天。"""
from datetime import datetime, timedelta, timezone

TABLE = "jd02_guest_stay_detail"
RETENTION_DAYS = 60


def now_shanghai():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def sync_guest_details(rows, hotel_id, snapshot, source, pms_platform, connection=None):
    day = snapshot.date() - timedelta(days=1)
    if not hotel_id or any(row.get("hotel_id") != hotel_id or row.get("source_platform") != source
                           or row.get("snapshot_time") != snapshot or row.get("business_date") != day
                           or row.get("main_checkin_person") not in (0, 1, None) for row in rows):
        raise ValueError("JD02 客人明细快照范围不一致")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            aliases = {}
            if rows:
                cur.execute("SELECT source_room_type_name, room_type_id FROM hotel_room_type_mapping "
                            "WHERE hotel_id=%s AND source_platform=%s AND is_active=1 AND source_product_id=''",
                            (hotel_id, pms_platform))
                for name, room_id in cur.fetchall():
                    if name and room_id:
                        aliases.setdefault(name, set()).add(room_id)
            cur.execute(f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s AND business_date=%s",
                        (hotel_id, source, day))
            if rows:
                records = [{**row, "room_type_id": next(iter(aliases[row["room_type_name"]]))
                            if len(aliases.get(row["room_type_name"], ())) == 1 else None} for row in rows]
                columns = list(records[0])
                cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                                f"({', '.join('%(' + key + ')s' for key in columns)})", records)
            cur.execute(f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s AND business_date < %s",
                        (hotel_id, source, day - timedelta(days=RETENTION_DAYS - 1)))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
