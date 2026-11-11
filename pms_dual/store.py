"""把汇总行写入现有 PMS 表。不改字段名，列与单系统报表一致。"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

from pms_dual.business import (
    JD01_COLUMNS, JD02_COLUMNS, JL01_COLUMNS, JL02_COLUMNS, JL11_COLUMNS, JY01_COLUMNS, JY03_COLUMNS,
    RS01_COLUMNS, SOURCE,
)
from pms_dual.merge import SOURCE as MERGE_SOURCE


SCHEMA = Path(__file__).resolve().parents[1] / "OTA采集服务" / "database" / "create_all_tables.sql"
TABLES = {
    "jy01_hotel_statistics_daily": JY01_COLUMNS,
    "jy03_hotel_statistics_month": JY03_COLUMNS,
    "jl02_hotel_performance_daily": JL02_COLUMNS,
    "jl01_room_type_performance_daily": JL01_COLUMNS,
    "jl11_room_type_classification": JL11_COLUMNS,
    "jd01_booking_detail": JD01_COLUMNS,
    "jd02_guest_stay_detail": JD02_COLUMNS,
    "rs01_room_revenue_daily": RS01_COLUMNS,
}
if SOURCE != MERGE_SOURCE:
    raise RuntimeError("汇总来源不一致")


def create_statement(table: str) -> str:
    if table not in TABLES:
        raise ValueError("汇总表不在现有报表范围内")
    text = SCHEMA.read_text(encoding="utf-8")
    marker = f"-- Table: {table}\n"
    start = text.find(marker)
    create = text.find("CREATE TABLE", start)
    end = text.find(";", create)
    if start < 0 or create < 0 or end < 0 or f"`{table}`" not in text[create:end]:
        raise ValueError(f"建表脚本没有 {table}")
    return text[create:end + 1]


def _cell(row):
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _connect():
    from ota_mysql_writer import connect_mysql
    return connect_mysql()


def ensure_table(connection, table: str) -> None:
    with connection.cursor() as cur:
        cur.execute(create_statement(table))
    connection.commit()


def _records(rows: list[dict], columns: tuple[str, ...]) -> list[dict]:
    prepared = []
    for row in rows:
        if "mapping_platform" in row:
            raise ValueError("写入列与现有报表不一致，拒绝写入")
        if set(row) != set(columns):
            raise ValueError("写入列与现有报表不一致，拒绝写入")
        prepared.append({key: row[key] for key in columns})
    return prepared


def _fields(row, *keys):
    if isinstance(row, dict):
        return tuple(row[key] for key in keys)
    return tuple(row)


def _aliases(cursor, hotel_id: str) -> dict[tuple[str, str], str]:
    cursor.execute("SELECT source_platform, source_room_type_name, room_type_id FROM hotel_room_type_mapping "
                   "WHERE hotel_id=%s AND is_active=1 AND source_product_id='' "
                   "AND source_platform IN ('pms_meituan','pms_rezen')", (hotel_id,))
    grouped: dict[tuple[str, str], set] = {}
    for item in cursor.fetchall():
        platform, name, room_id = _fields(item, "source_platform", "source_room_type_name", "room_type_id")
        if platform and name and room_id:
            grouped.setdefault((str(platform), str(name)), set()).add(str(room_id))
    return {key: next(iter(ids)) for key, ids in grouped.items() if len(ids) == 1}


def _attach_room_types(cursor, rows: list[dict]) -> None:
    if not rows:
        return
    aliases = _aliases(cursor, rows[0]["hotel_id"])
    for row in rows:
        platform = row.pop("mapping_platform", None)
        row["room_type_id"] = aliases.get((platform, row.get("room_type_name")))


def _upsert(cursor, table: str, rows: list[dict], columns: tuple[str, ...], identity: set[str]) -> None:
    if not rows:
        return
    updates = [key for key in columns if key not in identity]
    cursor.executemany(
        f"INSERT INTO `{table}` ({', '.join(columns)}) VALUES ({', '.join('%(' + key + ')s' for key in columns)}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(f'{key}=VALUES({key})' for key in updates)}",
        _records(rows, columns))


def _cleanup(cursor, table: str, hotel_id: str, snapshot: datetime) -> None:
    from OTA采集服务.pms_history_retention import cleanup_history
    cleanup_history(cursor, table, hotel_id, SOURCE, snapshot)


def write_jy01(rows: list[dict], connection=None) -> None:
    from meituan_pms.daily_report import RECENT_DAYS, collection_dates

    if len(rows) != RECENT_DAYS:
        raise ValueError("经营日报必须是最近 30 个完整营业日")
    first = rows[0]
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jy01_hotel_statistics_daily")
        with connection.cursor() as cur:
            expected = set(collection_dates(first["snapshot_time"].date()))
            scope = (first["hotel_name"], SOURCE, min(expected), max(expected), "总营业指标", "总营业指标")
            cur.execute("SELECT hotel_id FROM `jy01_hotel_statistics_daily` WHERE hotel_name=%s AND source_platform=%s "
                        "AND business_date BETWEEN %s AND %s AND dimension_type=%s AND dimension_name=%s FOR UPDATE", scope)
            for existing in cur.fetchall():
                if str(_cell(existing)) != first["hotel_id"]:
                    raise ValueError("同名日报属于其他酒店，拒绝覆盖")
            _upsert(cur, "jy01_hotel_statistics_daily", rows, JY01_COLUMNS,
                    {"hotel_name", "source_platform", "business_date", "dimension_type", "dimension_name", "hotel_id"})
            _cleanup(cur, "jy01_hotel_statistics_daily", first["hotel_id"], first["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def write_jy03(rows: list[dict], connection=None) -> None:
    if not rows:
        raise ValueError("月报为空，保留旧数据")
    first = rows[0]
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jy03_hotel_statistics_month")
        with connection.cursor() as cur:
            months = [row["period_month"] for row in rows]
            marks = ", ".join(["%s"] * len(months))
            cur.execute(f"SELECT hotel_id FROM `jy03_hotel_statistics_month` WHERE hotel_name=%s AND source_platform=%s "
                        f"AND dimension_type=%s AND dimension_name=%s AND period_month IN ({marks}) FOR UPDATE",
                        (first["hotel_name"], SOURCE, "总营业指标", "总营业指标", *months))
            for existing in cur.fetchall():
                if str(_cell(existing)) != first["hotel_id"]:
                    raise ValueError("同名月报属于其他酒店，拒绝覆盖")
            _upsert(cur, "jy03_hotel_statistics_month", rows, JY03_COLUMNS,
                    {"hotel_id", "hotel_name", "source_platform", "period_month", "dimension_type", "dimension_name"})
            _cleanup(cur, "jy03_hotel_statistics_month", first["hotel_id"], first["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def needs_month_backfill(hotel_id: str, snapshot: datetime, connection=None) -> bool:
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jy03_hotel_statistics_month")
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `jy03_hotel_statistics_month` WHERE hotel_id=%s AND source_platform=%s "
                        "AND dimension_type=%s AND dimension_name=%s AND period_month BETWEEN %s AND %s",
                        (hotel_id, SOURCE, "总营业指标", "总营业指标",
                         f"{snapshot.year - 1}-01", f"{snapshot.year}-12"))
            return _cell(cur.fetchone()) == 0
    finally:
        if owns:
            connection.close()


def write_jl02(rows: list[dict], connection=None) -> None:
    from meituan_pms.performance_report import CATEGORY, CORE_METRICS, collection_dates

    if not rows:
        raise ValueError("经营业绩为空，保留旧数据")
    first = rows[0]
    expected = set(collection_dates(first["snapshot_time"].date()))
    keys = {(row["business_date"], row["metric_name"]) for row in rows}
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jl02_hotel_performance_daily")
        with connection.cursor() as cur:
            dates = sorted(expected)
            marks = ", ".join(["%s"] * len(dates))
            cur.execute(f"SELECT business_date, metric_name, source_platform FROM `jl02_hotel_performance_daily` "
                        f"WHERE hotel_id=%s AND category=%s AND business_date IN ({marks}) FOR UPDATE",
                        (first["hotel_id"], CATEGORY, *dates))
            for existing in cur.fetchall():
                day, name, source = _fields(existing, "business_date", "metric_name", "source_platform")
                if (day, name) in keys and source != SOURCE:
                    raise ValueError("同指标的经营业绩属于其他 PMS，拒绝覆盖")
            if any(not CORE_METRICS <= {metric for row_day, metric in keys if row_day == day} for day in expected):
                raise ValueError("经营业绩核心指标不完整，拒绝写入")
            _upsert(cur, "jl02_hotel_performance_daily", rows, JL02_COLUMNS,
                    {"hotel_id", "business_date", "category", "metric_name"})
            _cleanup(cur, "jl02_hotel_performance_daily", first["hotel_id"], first["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def write_jl01(rows: list[dict], connection=None) -> None:
    from meituan_pms.room_performance_report import collection_dates

    if not rows:
        raise ValueError("房型业绩为空，保留旧数据")
    first = rows[0]
    dates = collection_dates(first["snapshot_time"].date())
    keys = {(row["business_date"], row["room_type_name"]) for row in rows}
    days = {day for day, _name in keys}
    if (len(keys) != len(rows) or dates[0] not in days or not days <= set(dates)
            or any(row.get("hotel_id") != first["hotel_id"] or row.get("hotel_name") != first["hotel_name"]
                   or row.get("snapshot_time") != first["snapshot_time"] or row.get("source_platform") != SOURCE
                   or row.get("pms_rate_room_type_id") != "" or not row.get("room_type_name")
                   or row["room_type_name"] == "总计" for row in rows)):
        raise ValueError("房型业绩酒店、批次、日期或房型不一致，拒绝写入")
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jl01_room_type_performance_daily")
        with connection.cursor() as cur:
            marks = ", ".join(["%s"] * len(dates))
            cur.execute(f"SELECT business_date, room_type_name, source_platform, snapshot_time "
                        f"FROM `jl01_room_type_performance_daily` WHERE hotel_id=%s AND business_date IN ({marks}) "
                        f"FOR UPDATE", (first["hotel_id"], *dates))
            for existing in cur.fetchall():
                day, name, source, stamp = _fields(existing, "business_date", "room_type_name", "source_platform", "snapshot_time")
                if (day, name) in keys and source != SOURCE:
                    raise ValueError("同房型业绩属于其他 PMS，拒绝覆盖")
                if source == SOURCE and stamp > first["snapshot_time"] and (day, name) in keys:
                    raise ValueError("房型业绩已有更新批次，拒绝旧快照覆盖")
            _upsert(cur, "jl01_room_type_performance_daily", rows, JL01_COLUMNS,
                    {"hotel_id", "business_date", "room_type_name"})
            _cleanup(cur, "jl01_room_type_performance_daily", first["hotel_id"], first["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def write_jl11(rows: list[dict], connection=None) -> None:
    if not rows:
        raise ValueError("房型分类为空，保留旧数据")
    first = rows[0]
    names = {row.get("room_type_name") for row in rows}
    if (len(names) != len(rows) or "总计" in names or "" in names or None in names
            or any(row.get("hotel_id") != first["hotel_id"] or row.get("hotel_name") != first["hotel_name"]
                   or row.get("source_platform") != SOURCE or row.get("snapshot_time") != first["snapshot_time"]
                   or row.get("snapshot_date") != first["snapshot_date"] or row.get("period_start") != first["period_start"]
                   or row.get("period_end") != first["period_end"] or row.get("section") != "summary"
                   or row.get("dimension_code") != "" or row.get("dimension_name") != "" for row in rows)):
        raise ValueError("房型分类酒店、批次或统计窗口不一致，拒绝写入")
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jl11_room_type_classification")
        with connection.cursor() as cur:
            cur.execute("SELECT snapshot_date, section, room_type_name, dimension_code, source_platform, snapshot_time "
                        "FROM `jl11_room_type_classification` WHERE hotel_id=%s FOR UPDATE", (first["hotel_id"],))
            for existing in cur.fetchall():
                day, section, name, dimension, source, stamp = _fields(
                    existing, "snapshot_date", "section", "room_type_name", "dimension_code", "source_platform", "snapshot_time")
                if source == SOURCE and stamp > first["snapshot_time"]:
                    raise ValueError("房型分类已有更新批次，拒绝旧快照覆盖")
                if (source != SOURCE and day == first["snapshot_date"] and section == "summary"
                        and name in names and dimension == ""):
                    raise ValueError("同房型分类快照属于其他 PMS，拒绝覆盖")
            cur.execute("DELETE FROM `jl11_room_type_classification` WHERE hotel_id=%s AND source_platform=%s",
                        (first["hotel_id"], SOURCE))
            _insert(cur, "jl11_room_type_classification", rows, JL11_COLUMNS)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def needs_revenue_backfill(hotel_id: str, connection=None) -> bool:
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "rs01_room_revenue_daily")
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `rs01_room_revenue_daily` WHERE hotel_id=%s AND source_platform=%s",
                        (hotel_id, SOURCE))
            return _cell(cur.fetchone()) == 0
    finally:
        if owns:
            connection.close()


def ensure_source_payment_id(cursor) -> None:
    cursor.execute("SHOW COLUMNS FROM `rs01_room_revenue_daily` WHERE `Field`=%s", ("source_payment_id",))
    if cursor.fetchone():
        return
    cursor.execute("""
        ALTER TABLE `rs01_room_revenue_daily`
        ADD COLUMN source_payment_id VARCHAR(100) NOT NULL DEFAULT '' COMMENT '来源流水 ID',
        DROP INDEX uk_room_fee_daily,
        ADD UNIQUE KEY uk_room_fee_daily
            (hotel_id, order_id, business_date, room_no, charge_subject, source_payment_id)
    """)


def write_rs01(rows: list[dict], snapshot: datetime, *, initial: bool, connection=None) -> None:
    from meituan_pms.revenue_report import collection_windows

    windows = collection_windows(snapshot.date(), initial)
    if not rows:
        return
    first = rows[0]
    keys = {(row.get("order_id"), row.get("business_date"), row.get("room_no"), row.get("charge_subject"),
             str(row.get("source_payment_id") or "")) for row in rows}
    if (len(keys) != len(rows) or any(
            row.get("hotel_id") != first["hotel_id"] or row.get("hotel_name") != first["hotel_name"]
            or row.get("source_platform") != SOURCE or row.get("snapshot_time") != snapshot
            or not row.get("order_id") or not row.get("room_no") or not row.get("charge_subject")
            or not any(start <= row["business_date"] <= end for start, end in windows) for row in rows)):
        raise ValueError("房费明细酒店、批次、唯一键或日期范围不一致，拒绝写入")
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "rs01_room_revenue_daily")
        with connection.cursor() as cur:
            ensure_source_payment_id(cur)
            cur.execute("SELECT source_platform, snapshot_time FROM `rs01_room_revenue_daily` WHERE hotel_id=%s "
                        "AND business_date BETWEEN %s AND %s FOR UPDATE",
                        (first["hotel_id"], windows[0][0], windows[-1][1]))
            for existing in cur.fetchall():
                source, stamp = _fields(existing, "source_platform", "snapshot_time")
                if source != SOURCE or stamp > snapshot:
                    raise ValueError("房费查询范围已有其他 PMS 或更新批次，拒绝覆盖")
            _attach_room_types(cur, rows)
            # 以前为了躲开唯一键写成 mt:/rz: 的单号，下次写入时从本次查询窗口里清掉，避免和新的原单号叠成两份房费。
            cur.execute(
                "DELETE FROM `rs01_room_revenue_daily` WHERE hotel_id=%s AND source_platform=%s "
                "AND business_date BETWEEN %s AND %s AND (order_id LIKE 'mt:%%' OR order_id LIKE 'rz:%%')",
                (first["hotel_id"], SOURCE, windows[0][0], windows[-1][1]))
            _upsert(cur, "rs01_room_revenue_daily", rows, RS01_COLUMNS,
                    {"hotel_id", "order_id", "business_date", "room_no", "charge_subject", "source_payment_id"})
            _cleanup(cur, "rs01_room_revenue_daily", first["hotel_id"], snapshot)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def write_jd01(rows: list[dict], snapshot: datetime, connection=None) -> None:
    from meituan_pms.booking_report import RETENTION_DAYS, collection_window

    start_day, end_day = collection_window(snapshot.date())
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jd01_booking_detail")
        with connection.cursor() as cur:
            _attach_room_types(cur, rows)
            start = datetime.combine(start_day, time.min)
            end = datetime.combine(end_day + timedelta(days=1), time.min)
            cur.execute("DELETE FROM `jd01_booking_detail` WHERE hotel_id=%s AND source_platform=%s "
                        "AND arrival_time >= %s AND arrival_time < %s",
                        (rows[0]["hotel_id"] if rows else _hotel_from_env(), SOURCE, start, end))
            if rows:
                _insert(cur, "jd01_booking_detail", rows, JD01_COLUMNS)
            cutoff = datetime.combine(snapshot.date() - timedelta(days=RETENTION_DAYS - 1), time.min)
            cur.execute("DELETE FROM `jd01_booking_detail` WHERE hotel_id=%s AND source_platform=%s AND arrival_time < %s",
                        (_hotel_from_env(), SOURCE, cutoff))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def write_jd02(rows: list[dict], snapshot: datetime, connection=None) -> None:
    from OTA采集服务.pms_guest_storage import RETENTION_DAYS

    day = snapshot.date() - timedelta(days=1)
    hotel_id = rows[0]["hotel_id"] if rows else _hotel_from_env()
    owns = connection is None
    connection = connection or _connect()
    try:
        ensure_table(connection, "jd02_guest_stay_detail")
        with connection.cursor() as cur:
            _attach_room_types(cur, rows)
            cur.execute("DELETE FROM `jd02_guest_stay_detail` WHERE hotel_id=%s AND source_platform=%s AND business_date=%s",
                        (hotel_id, SOURCE, day))
            if rows:
                _insert(cur, "jd02_guest_stay_detail", rows, JD02_COLUMNS)
            cur.execute("DELETE FROM `jd02_guest_stay_detail` WHERE hotel_id=%s AND source_platform=%s AND business_date < %s",
                        (hotel_id, SOURCE, day - timedelta(days=RETENTION_DAYS - 1)))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def _hotel_from_env() -> str:
    import os
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    return hotel_id


def _insert(cursor, table: str, rows: list[dict], columns: tuple[str, ...]) -> None:
    prepared = _records(rows, columns)
    cursor.executemany(
        f"INSERT INTO `{table}` ({', '.join(columns)}) VALUES ({', '.join('%(' + key + ')s' for key in columns)})",
        prepared)
