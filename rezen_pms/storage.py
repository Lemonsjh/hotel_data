"""同一事务替换酒店预测快照及当前来源的小时快照。"""
from __future__ import annotations

from OTA采集服务.pms_history_retention import cleanup_history


FORECAST = "pms_room_type_forecast"
HOURLY = "pms_room_type_hourly_status"
BASE_COLUMNS = ["hotel_id", "hotel_name", "source_platform", "snapshot_time", "stay_date",
                "room_type_name", "room_type_id", "pms_room_type_id", "total_rooms",
                "available_rooms", "occupied_rooms", "overbooking_rooms"]


def _pair(row):
    if isinstance(row, dict):
        return row.get("pms_room_type_id"), row.get("source_platform")
    return row[0], row[1]


def _reject_other_source(cur, table: str, time_key: str, rows: list[dict], source: str) -> None:
    first = rows[0]
    for moment in {row[time_key] for row in rows}:
        incoming = {row["pms_room_type_id"] for row in rows if row[time_key] == moment}
        cur.execute(f"SELECT pms_room_type_id, source_platform FROM `{table}` "
                    f"WHERE hotel_id=%s AND `{time_key}`=%s FOR UPDATE", (first["hotel_id"], moment))
        for existing in cur.fetchall():
            room_id, existing_source = _pair(existing)
            if room_id in incoming and existing_source != source:
                raise ValueError("同房型 ID 的快照属于其他 PMS，本轮拒绝覆盖")


def _mapped_ids(cur, hotel_id: str, platform: str) -> dict[str, str]:
    cur.execute("SELECT source_room_type_name, COUNT(DISTINCT room_type_id) "
                "FROM hotel_room_type_mapping WHERE hotel_id=%s AND source_platform=%s "
                "AND is_active=1 AND source_product_id='' GROUP BY source_room_type_name "
                "HAVING COUNT(DISTINCT room_type_id)>1", (hotel_id, platform))
    if cur.fetchall():
        raise ValueError("同一 PMS 房型名称对应多个统一房型，拒绝写入")
    cur.execute("SELECT source_room_type_name, MIN(room_type_id) AS room_type_id "
                "FROM hotel_room_type_mapping WHERE hotel_id=%s AND source_platform=%s "
                "AND is_active=1 AND source_product_id='' GROUP BY source_room_type_name",
                (hotel_id, platform))
    result = {}
    for alias in cur.fetchall():
        if isinstance(alias, dict):
            result[alias["source_room_type_name"]] = alias["room_type_id"]
        else:
            result[alias[0]] = alias[1]
    return result


def _check(rows: list[dict], source: str, same_day: bool) -> None:
    if not rows or not rows[0].get("hotel_id"):
        raise ValueError("房态为空，保留既有数据")
    first = rows[0]
    keys = ("hotel_id", "source_platform", "snapshot_time", "snapshot_hour")
    if same_day:
        keys += ("stay_date",)
    grouped: dict = {}
    for row in rows:
        if row.get("source_platform") != source or any(row.get(key) != first.get(key) for key in keys):
            raise ValueError("房态酒店、来源或采集时间不一致")
        grouped.setdefault(row["stay_date"], []).append(row["pms_room_type_id"])
    if any(len(set(ids)) != len(ids) for ids in grouped.values()):
        raise ValueError("房型主键重复，拒绝写入")


def sync_mysql(forecast_rows: list[dict], hourly_rows: list[dict], *, source: str,
               mapping_platform: str | None = None, connection=None) -> None:
    _check(forecast_rows, source, same_day=False)
    _check(hourly_rows, source, same_day=True)
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            _reject_other_source(cur, HOURLY, "snapshot_hour", hourly_rows, source)
            first = forecast_rows[0]
            cur.execute(f"SELECT snapshot_time FROM `{FORECAST}` WHERE hotel_id=%s AND snapshot_time>%s "
                        "LIMIT 1 FOR UPDATE", (first["hotel_id"], first["snapshot_time"]))
            if cur.fetchone():
                raise ValueError("已有更新的实时快照，拒绝用较早数据覆盖")
            aliases = _mapped_ids(cur, forecast_rows[0]["hotel_id"], mapping_platform) if mapping_platform else {}
            cur.execute(f"DELETE FROM `{FORECAST}` WHERE hotel_id=%s", (first["hotel_id"],))
            for table, rows, time_key, extra in (
                (FORECAST, forecast_rows, None, ["room_revenue", "adr", "revpar"]),
                (HOURLY, hourly_rows, "snapshot_hour", ["snapshot_hour"]),
            ):
                prepared = []
                for row in rows:
                    room_type_id = aliases.get(row["room_type_name"]) if mapping_platform else row.get("room_type_id")
                    prepared.append({**row, "room_type_id": room_type_id})
                if time_key:
                    cur.execute(f"DELETE FROM `{table}` WHERE hotel_id=%s AND source_platform=%s AND `{time_key}`=%s",
                                (rows[0]["hotel_id"], source, rows[0][time_key]))
                columns = BASE_COLUMNS + extra
                cur.executemany(
                    f"INSERT INTO `{table}` ({', '.join(columns)}) VALUES "
                    f"({', '.join('%(' + key + ')s' for key in columns)})", prepared)
            cleanup_history(cur, HOURLY, hourly_rows[0]["hotel_id"], source, hourly_rows[0]["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()
