"""美团 PMS 当前日期快照及小时快照，同一事务写入。"""

SOURCE = "PMS（美团）"
FORECAST = "pms_room_type_forecast"
HOURLY = "pms_room_type_hourly_status"


def sync_mysql(rows, connection=None):
    if not rows:
        raise ValueError("房态为空，保留既有数据")
    first = rows[0]
    if not first.get("hotel_id") or any(
        row.get("source_platform") != SOURCE or any(row[key] != first[key]
        for key in ("hotel_id", "stay_date", "snapshot_hour", "snapshot_time")) for row in rows
    ):
        raise ValueError("房态酒店、来源或采集时间不一致")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            # 现有唯一键不包含来源；提前拒绝覆盖切换 PMS 前的同 ID 快照。
            for table, time_key in ((FORECAST, "stay_date"), (HOURLY, "snapshot_hour")):
                cur.execute(f"SELECT pms_room_type_id, source_platform FROM `{table}` "
                            f"WHERE hotel_id=%s AND `{time_key}`=%s FOR UPDATE",
                            (first["hotel_id"], first[time_key]))
                incoming = {row["pms_room_type_id"] for row in rows}
                for existing in cur.fetchall():
                    room_id, source = ((existing["pms_room_type_id"], existing["source_platform"])
                                       if isinstance(existing, dict) else existing)
                    if room_id in incoming and source != SOURCE:
                        raise ValueError("同房型 ID 的快照属于其他 PMS，本轮拒绝覆盖")
            cur.execute("SELECT source_room_type_name, MIN(room_type_id) AS room_type_id FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_meituan' AND is_active=1 "
                        "AND source_product_id='' GROUP BY source_room_type_name "
                        "HAVING COUNT(DISTINCT room_type_id)=1", (first["hotel_id"],))
            aliases = {}
            for alias in cur.fetchall():
                name, room_id = ((alias["source_room_type_name"], alias["room_type_id"])
                                 if isinstance(alias, dict) else alias)
                aliases[name] = room_id
            records = [{**row, "room_type_id": aliases.get(row["room_type_name"])} for row in rows]
            for table, time_key in ((FORECAST, None), (HOURLY, "snapshot_hour")):
                where = f" AND `{time_key}`=%s" if time_key else ""
                params = (first["hotel_id"], SOURCE, first[time_key]) if time_key else (first["hotel_id"], SOURCE)
                cur.execute(f"DELETE FROM `{table}` WHERE hotel_id=%s AND source_platform=%s{where}", params)
                columns = ["hotel_id", "hotel_name", "source_platform", "snapshot_time", "stay_date",
                           "room_type_name", "room_type_id", "pms_room_type_id", "total_rooms",
                           "available_rooms", "occupied_rooms", "overbooking_rooms"]
                columns += ["snapshot_hour"] if time_key else ["room_revenue", "adr", "revpar"]
                cur.executemany(f"INSERT INTO `{table}` ({', '.join(columns)}) VALUES "
                                f"({', '.join('%(' + key + ')s' for key in columns)})", records)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
