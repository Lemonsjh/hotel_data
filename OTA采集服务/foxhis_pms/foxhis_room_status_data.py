#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foxhis X5 采集当天房型快照与小时房型快照；不写入 KF11 房号快照

用法
  python foxhis_room_status_data.py                # 正常采集（实时，不受夜审闸门限制）
  python foxhis_room_status_data.py --dry-run      # 只取数不落库，验证映射
  python foxhis_room_status_data.py --introspect   # 打印库版本 / roomno 实际字段后退出
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

# 便携运行时是嵌入式发行版，脚本自身目录不在 sys.path 里，需显式加入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from foxhis_common import (  # noqa: E402
    SOURCE_PLATFORM,
    Config,
    FoxhisError,
    db_version,
    mysql_connection,
    query,
    report_main,
    require_columns,
    save_output,
    table_columns,
)
from foxhis_common import cleanup_history
from foxhis_sql import (  # noqa: E402
    ROOM_STATUS_NAME_QUERY,
    ROOM_STATUS_QUERY,
    ROOM_STATUS_REQUIRED,
    ROOM_TYPE_NAME_QUERY,
    ROOM_TYPE_FORECAST_TABLE,
    ROOM_TYPE_HOURLY_STATUS_TABLE,
)

from foxhis_realtime_revenue import (
    apply_posted_revenue, apply_realtime_revenue, posted_revenue, realtime_revenue,
)

SUMMARY_FILE = "foxhis_room_status_summary.json"

# Foxhis 房态代码 → 中文状态。
# 中文状态仅用于本轮内存分组；本采集器不保存房号级快照。
#
# 优先级：rmstalist 字典表（系统自带，最权威）→ 本表 → ocsta 占用状态 → 原样保留。
# 本表只是字典表读取失败时的兜底，具体代码含义以 rmstalist.descript 为准。
ROOM_STATUS_LABELS = {
    "V": "空房",
    "VC": "空房",
    "VD": "空房待清扫",
    "O": "维修",
    "OC": "在住",
    "OD": "在住待清扫",
    "C": "干净",
    "D": "待清扫",
    "X": "维修房",
    "OOO": "维修房",
    "OOS": "停用房",
    "B": "预订",
    "R": "干净",
    "S": "锁定",
    "I": "检查",
    "N": "未使用",
}

# 占用状态兜底：ocsta 只有 V(空) / O(占) 两个量级时的最终回落
OCCUPY_LABELS = {"V": "空房", "O": "在住"}


def room_type_name_map(config: Config) -> dict[str, str]:
    """房型代码 → 名称；字典表取不到时返回空表（不阻断采集）。"""
    try:
        rows = query(ROOM_TYPE_NAME_QUERY, config, timeout=120)
    except FoxhisError as exc:
        print(f"WARN 房型字典表读取失败，房型将保留代码：{exc}")
        return {}
    mapping: dict[str, str] = {}
    for row in rows:
        code = (row.get("room_type_code") or "").strip()
        name = (row.get("room_type_name") or "").strip()
        if code and name:
            mapping[code] = name
    return mapping


def room_status_name_map(config: Config) -> dict[str, str]:
    """房态代码 → 中文描述（取系统字典 rmstalist）；失败不阻断采集。"""
    try:
        rows = query(ROOM_STATUS_NAME_QUERY, config, timeout=120)
    except FoxhisError as exc:
        print(f"WARN 房态字典表读取失败，将回落到内置映射：{exc}")
        return {}
    mapping: dict[str, str] = {}
    for row in rows:
        code = (row.get("status_code") or "").strip().upper()
        name = (row.get("status_name") or "").strip()
        if code and name:
            mapping[code] = name
    return mapping


def resolve_status(raw_status: str, occupy: str, status_names: dict[str, str]) -> str:
    """房态代码 → 中文；优先级见 ROOM_STATUS_LABELS 上方注释。"""
    if raw_status and raw_status in status_names:
        return status_names[raw_status]
    if raw_status in ROOM_STATUS_LABELS:
        return ROOM_STATUS_LABELS[raw_status]
    if occupy in OCCUPY_LABELS:
        return OCCUPY_LABELS[occupy]
    return raw_status or "未知"


def build_rows(records: list[dict[str, str]], names: dict[str, str], config: Config,
               business_date: date, snapshot_time: datetime,
               status_names: dict[str, str] | None = None) -> list[dict]:
    status_names = status_names or {}
    rows: list[dict] = []
    for record in records:
        room_no = (record.get("room_no") or "").strip()
        if not room_no:
            continue
        type_code = (record.get("room_type_code") or "").strip()
        raw_status = (record.get("room_status") or "").strip().upper()
        occupy = (record.get("occupy_status") or "").strip().upper()
        rows.append(
            {
                "hotel_name": config.hotel_name,
                "hotel_id": config.hotel_id,
                "source_platform": SOURCE_PLATFORM,
                "business_date": business_date.isoformat(),
                "room_no": room_no,
                "room_type_name": names.get(type_code, type_code) or "未知房型",
                # room_type_id 留给 room_type_enrichment 按名称匹配填充
                "room_type_id": None,
                # 住客/入住离店时间不在房态表内，需另外关联（见 foxhis_sql.py 说明）
                "guest_name": None,
                "checkin_time": None,
                "checkout_time": None,
                "snapshot_time": snapshot_time.strftime("%Y-%m-%d %H:%M:%S"),
                # 以下为输出文件用的附加信息，不落库
                "_raw_status": raw_status,
                "_room_type_code": type_code,
                "_locked": (record.get("locked") or "").strip().upper(),
                "_occupy_status": occupy,
                "_floor": (record.get("floor_no") or "").strip(),
                "_building": (record.get("building") or "").strip(),
            }
        )
    for row in rows:
        row["room_status"] = resolve_status(
            row["_raw_status"], row["_occupy_status"], status_names
        )
    return rows


FORECAST_COLUMNS = [
    "hotel_id", "hotel_name", "source_platform", "snapshot_time", "stay_date",
    "room_type_name", "room_type_id", "pms_room_type_id", "total_rooms",
    "available_rooms", "occupied_rooms", "overbooking_rooms", "room_revenue", "adr", "revpar",
]
HOURLY_COLUMNS = [
    "hotel_id", "hotel_name", "source_platform", "snapshot_time", "snapshot_hour",
    "stay_date", "room_type_name", "room_type_id", "pms_room_type_id",
    "total_rooms", "available_rooms", "occupied_rooms", "overbooking_rooms",
]


def build_forecast_rows(room_rows: list[dict], snapshot_time: datetime) -> list[dict]:
    """仅当天物理房态：空闲房扣除锁定/维修，不推算未来预订或收入。"""
    grouped: dict[str, dict] = {}
    room_numbers: set[str] = set()
    for room in room_rows:
        code = room["_room_type_code"]
        if not code or room["room_no"] in room_numbers:
            raise FoxhisError("房态缺少房型或存在重复房号，拒绝替换实时快照。")
        room_numbers.add(room["room_no"])
        occupy = room["_occupy_status"]
        locked = room["_locked"]
        if occupy not in {"V", "O"} or locked not in {"N", "L"}:
            raise FoxhisError("发现未验证的占用或锁房代码，拒绝替换实时快照。")
        row = grouped.setdefault(code, {
            "hotel_id": room["hotel_id"], "hotel_name": room["hotel_name"],
            "source_platform": SOURCE_PLATFORM, "snapshot_time": snapshot_time,
            "stay_date": snapshot_time.date(), "room_type_name": room["room_type_name"],
            "room_type_id": None, "pms_room_type_id": code, "total_rooms": 0,
            "available_rooms": 0, "occupied_rooms": 0,
            "overbooking_rooms": None, "room_revenue": None, "adr": None, "revpar": None,
        })
        row["total_rooms"] += 1
        row["occupied_rooms"] += int(occupy == "O")
        blocked = locked == "L" or room["_raw_status"] in {"S", "O", "X", "OOO", "OOS"}
        row["available_rooms"] += int(occupy == "V" and not blocked)
    return [grouped[code] for code in sorted(grouped)]


def build_hourly_rows(forecast_rows: list[dict]) -> list[dict]:
    """与当天快照共用一批房态和采集时间，小时键向下取整。"""
    return [
        {**{name: row[name] for name in HOURLY_COLUMNS if name != "snapshot_hour"},
         "snapshot_hour": row["snapshot_time"].replace(minute=0, second=0, microsecond=0)}
        for row in forecast_rows
    ]


def sync_inventory(rows: list[dict], config: Config) -> int:
    """同一事务替换当天最新快照及本小时快照，保留其他小时历史。"""
    if not rows or not config.hotel_id.strip():
        raise FoxhisError("实时房态为空或酒店 ID 未配置，拒绝替换快照。")
    stamp = rows[0]["snapshot_time"]
    if any(row["hotel_id"] != config.hotel_id or row["source_platform"] != SOURCE_PLATFORM
           or row["snapshot_time"] != stamp or row["stay_date"] != stamp.date() for row in rows):
        raise FoxhisError("实时房态的酒店、来源或日期不一致，拒绝替换快照。")
    hourly_rows = build_hourly_rows(rows)
    hour = hourly_rows[0]["snapshot_hour"]
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT snapshot_time FROM `{ROOM_TYPE_FORECAST_TABLE}` "
                "WHERE hotel_id=%s AND source_platform=%s FOR UPDATE",
                (config.hotel_id, SOURCE_PLATFORM),
            )
            if any(item[0] > stamp for item in cursor.fetchall()):
                raise FoxhisError("已有更新的实时房态，拒绝用较早快照覆盖。")
            cursor.execute(
                f"SELECT snapshot_time FROM `{ROOM_TYPE_HOURLY_STATUS_TABLE}` "
                "WHERE hotel_id=%s AND source_platform=%s AND snapshot_hour=%s FOR UPDATE",
                (config.hotel_id, SOURCE_PLATFORM, hour),
            )
            if any(item[0] > stamp for item in cursor.fetchall()):
                raise FoxhisError("已有更新的小时房态，拒绝用较早快照覆盖。")
            cursor.execute(
                f"DELETE FROM `{ROOM_TYPE_FORECAST_TABLE}` WHERE hotel_id=%s AND source_platform=%s",
                (config.hotel_id, SOURCE_PLATFORM),
            )
            # 目标主键不含来源。与其他 PMS 冲突时让 INSERT 报错并回滚，避免覆盖。
            cursor.executemany(
                f"INSERT INTO `{ROOM_TYPE_FORECAST_TABLE}` "
                f"({', '.join('`' + name + '`' for name in FORECAST_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(FORECAST_COLUMNS))})",
                [tuple(row[name] for name in FORECAST_COLUMNS) for row in rows],
            )
            cursor.execute(
                f"DELETE FROM `{ROOM_TYPE_HOURLY_STATUS_TABLE}` "
                "WHERE hotel_id=%s AND source_platform=%s AND snapshot_hour=%s",
                (config.hotel_id, SOURCE_PLATFORM, hour),
            )
            cursor.executemany(
                f"INSERT INTO `{ROOM_TYPE_HOURLY_STATUS_TABLE}` "
                f"({', '.join('`' + name + '`' for name in HOURLY_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(HOURLY_COLUMNS))})",
                [tuple(row[name] for name in HOURLY_COLUMNS) for row in hourly_rows],
            )
            cleanup_history(cursor, ROOM_TYPE_HOURLY_STATUS_TABLE, hourly_rows, config)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def collect(config: Config, business_date: date, snapshot_time: datetime) -> dict:
    business_date = snapshot_time.date()
    require_columns(config, *ROOM_STATUS_REQUIRED)
    names = room_type_name_map(config)
    status_names = room_status_name_map(config)
    records = query(ROOM_STATUS_QUERY, config, timeout=240)
    if not records or any(not (record.get("room_no") or "").strip() for record in records):
        raise FoxhisError("房态查询为空或房号缺失，保留上次实时快照。")
    rows = build_rows(records, names, config, business_date, snapshot_time, status_names)
    forecast_rows = build_forecast_rows(rows, snapshot_time)
    revenue_summary = realtime_revenue(config, rows, forecast_rows, business_date)
    hourly_rows = build_hourly_rows(forecast_rows)

    dry_run = "--dry-run" in sys.argv
    forecast_written = 0 if dry_run else sync_inventory(forecast_rows, config)

    state_counts: dict[str, int] = {}
    for row in rows:
        state_counts[row["room_status"]] = state_counts.get(row["room_status"], 0) + 1

    save_output(
        SUMMARY_FILE,
        {
            "report": "room_status",
            "source_platform": SOURCE_PLATFORM,
            "hotel_id": config.hotel_id,
            "business_date": business_date.isoformat(),
            "snapshot_time": snapshot_time.isoformat(timespec="seconds"),
            "source_room_count": len(rows),
            "row_count": len(forecast_rows),
            "written": forecast_written,
            "dry_run": dry_run,
            "state_counts": state_counts,
            "forecast_row_count": len(forecast_rows),
            "forecast_written": forecast_written,
            "forecast_rows": forecast_rows,
            "hourly_row_count": len(hourly_rows),
            "hourly_written": forecast_written,
            "hourly_rows": hourly_rows,
            "inventory_basis": "当天实时物理房态；空闲房扣除锁定/维修，未扣未分房预订；超订未采集",
            "revenue_summary": revenue_summary,
        },
        config,
    )
    return {"business_date": business_date.isoformat(), "source_room_count": len(rows),
            "row_count": len(forecast_rows), "written": forecast_written}


def introspect() -> int:
    config = Config.from_env()
    print("Foxhis 库结构自省（只读）")
    print("=" * 62)
    print("数据库版本:", db_version(config) or "读取失败")
    for table in ROOM_STATUS_REQUIRED[0:1] + ("typim", "rmstalist", "rmstarep"):
        columns = table_columns(config, table)
        print(f"  表 {table:<12} {'字段: ' + ', '.join(columns) if columns else '不存在或不可见'}")
    print("\n请按上面的实际字段核对 foxhis_sql.py 中的 ROOM_STATUS_QUERY。")
    return 0


def main() -> int:
    if "--introspect" in sys.argv:
        return introspect()
    return report_main("room_status", collect, realtime=True)


if __name__ == "__main__":
    raise SystemExit(main())
