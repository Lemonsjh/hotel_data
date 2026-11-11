"""两个及以上西软业务库：分别查询，在内存里拼成一份，再走现有的整段替换。"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from dataclasses import replace

import foxhis_common
from foxhis_accounts import MAX_ACCOUNTS
from foxhis_config import Config


def config_for_account(base: Config, account: dict[str, Any]) -> Config:
    updates: dict[str, Any] = {
        "database": str(account.get("database") or "").strip(),
        "username": str(account.get("username") or "").strip(),
        "password": str(account.get("password") or ""),
    }
    for key in ("server", "charset", "isql_path", "interfaces_file", "fetch_after"):
        value = str(account.get(key) or "").strip()
        if value:
            updates[key] = value
    offset = account.get("business_date_offset_days")
    if offset not in (None, ""):
        updates["business_date_offset_days"] = int(offset)
    return replace(base, **updates)


def participates(account: dict[str, Any], report_key: str, shared_reports: str) -> bool:
    raw = account.get("reports") or shared_reports
    if not str(raw or "").strip():
        return True
    selected = {part.strip() for part in str(raw).split(",") if part.strip()}
    return report_key in selected


def freeze(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"__t": "decimal", "v": str(value)}
    if isinstance(value, datetime):
        return {"__t": "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {"__t": "date", "v": value.isoformat()}
    if isinstance(value, list):
        return [freeze(item) for item in value]
    if isinstance(value, dict):
        return {key: freeze(item) for key, item in value.items()}
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, list):
        return [thaw(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"__t", "v"}:
            kind, raw = value["__t"], value["v"]
            if kind == "decimal":
                return Decimal(raw)
            if kind == "date":
                return date.fromisoformat(raw)
            if kind == "datetime":
                return datetime.fromisoformat(raw)
            raise foxhis_common.FoxhisError("无法读取业务库日结缓存。")
        return {key: thaw(item) for key, item in value.items()}
    return value


def _require_accounts(accounts: Any) -> list[dict[str, Any]]:
    if not isinstance(accounts, list) or len(accounts) < 2:
        raise foxhis_common.FoxhisError("至少需要两个业务库才汇总。")
    if len(accounts) > MAX_ACCOUNTS:
        raise foxhis_common.FoxhisError(f"业务库最多 {MAX_ACCOUNTS} 个。")
    seen: list[str] = []
    for item in accounts:
        if not isinstance(item, dict):
            raise foxhis_common.FoxhisError("业务库缺少库名、账号或密码。")
        database = str(item.get("database") or "").strip()
        username = str(item.get("username") or "").strip()
        password = str(item.get("password") or "")
        if not database or not username or not password:
            raise foxhis_common.FoxhisError("业务库缺少库名、账号或密码。")
        if database in seen:
            raise foxhis_common.FoxhisError(f"数据库名 {database} 重复。")
        seen.append(database)
    return accounts


def _identity(config: Config, report_key: str, stamp: datetime, realtime: bool) -> dict[str, str]:
    day = stamp.date() if realtime else foxhis_common.business_date(config)
    mysql = config.mysql or {}
    return {
        "report": report_key,
        "database": config.database,
        "username": config.username,
        "server": config.server,
        "hotel_id": config.hotel_id,
        "hotel_name": config.hotel_name,
        "business_date": day.isoformat(),
        "mysql_host": str(mysql.get("host") or ""),
        "mysql_database": str(mysql.get("database") or ""),
    }


def _load_state(config: Config) -> dict[str, Any]:
    path = Path(config.output_dir) / foxhis_common.DAILY_STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(config: Config, state: dict[str, Any]) -> None:
    foxhis_common.save_output(foxhis_common.DAILY_STATE_FILE, state, config)


def _cached(state: dict[str, Any], report_key: str, identity: dict[str, str]) -> dict[str, Any] | None:
    try:
        entry = state["multi"][report_key][identity["database"]]
    except (KeyError, TypeError):
        return None
    if not isinstance(entry, dict) or entry.get("identity") != identity:
        return None
    try:
        payload = thaw(entry.get("payload"))
    except (foxhis_common.FoxhisError, ValueError, TypeError, InvalidOperation):
        return None
    return payload if isinstance(payload, dict) else None


def _remember(state: dict[str, Any], report_key: str, identity: dict[str, str], payload: dict[str, Any]) -> None:
    bucket = state.setdefault("multi", {})
    reports = bucket.setdefault(report_key, {})
    if not isinstance(reports, dict):
        reports = {}
        bucket[report_key] = reports
    reports[identity["database"]] = {"identity": identity, "payload": freeze(payload)}


def _query(report_key: str, collect: Callable[..., dict[str, Any]], config: Config,
           stamp: datetime, realtime: bool) -> dict[str, Any]:
    reachable, detail = foxhis_common.check_reachable(config)
    if not reachable:
        raise foxhis_common.FoxhisError(f"数据库不可达：{detail}")
    day = stamp.date() if realtime else foxhis_common.business_date(config)
    summary = collect(config, day, stamp, write=False)
    if not isinstance(summary, dict):
        raise foxhis_common.FoxhisError("采集没有返回结果。")
    rows = summary.get("rows") or []
    if not isinstance(rows, list):
        raise foxhis_common.FoxhisError("采集结果缺少明细。")
    if report_key == "jy03" and not isinstance(summary.get("by_month"), dict):
        raise foxhis_common.FoxhisError("月报缺少可相加的明细。")
    return {"rows": rows, "by_month": summary.get("by_month"), "components": summary.get("components"),
            "database": config.database}


def _decimal(value: Any) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise foxhis_common.FoxhisError("总营业指标无法相加。") from None
    if not number.is_finite():
        raise foxhis_common.FoxhisError("总营业指标无法相加。")
    return number


def _whole(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise foxhis_common.FoxhisError(f"无法汇总{label}。") from None
    if number < 0:
        raise foxhis_common.FoxhisError(f"无法汇总{label}。")
    return number


def sum_jy01(batches: list[list[dict[str, Any]]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    from foxhis_jy01_data import DIMENSION, rounded

    if not batches:
        return []
    date_sets = []
    for rows in batches:
        found = [row.get("business_date") for row in rows]
        if len(found) != len(set(found)):
            raise foxhis_common.FoxhisError("经营日报日期重复，拒绝汇总。")
        date_sets.append(set(found))
    if any(item != date_sets[0] for item in date_sets):
        raise foxhis_common.FoxhisError("各业务库的经营日报日期不一致，拒绝汇总。")
    try:
        ordered = sorted(date_sets[0])
    except TypeError:
        raise foxhis_common.FoxhisError("各业务库的经营日报日期不一致，拒绝汇总。") from None
    merged = []
    for day in ordered:
        items = [row for rows in batches for row in rows if row.get("business_date") == day]
        room_count = sum(_whole(row.get("room_count"), "房量") for row in items)
        nights = sum((_decimal(row.get("room_nights")) for row in items), Decimal(0))
        revenue = sum((_decimal(row.get("room_revenue")) for row in items), Decimal(0))
        sold = sum(_whole(row.get("sold_rooms"), "已售房") for row in items)
        remaining = sum(_whole(row.get("remaining_rooms"), "可售房") for row in items)
        available = sold + remaining
        merged.append({
            "hotel_name": config.hotel_name,
            "hotel_id": config.hotel_id,
            "source_platform": foxhis_common.SOURCE_PLATFORM,
            "business_date": day,
            "dimension_type": DIMENSION,
            "dimension_name": DIMENSION,
            "room_type_id": None,
            "room_count": room_count,
            "room_nights": nights,
            "room_revenue": rounded(revenue),
            "occupancy_rate": rounded(nights * 100 / available) if available else Decimal(0),
            "adr": rounded(revenue / nights) if nights else Decimal(0),
            "revpar": rounded(revenue / room_count) if room_count else Decimal(0),
            "sold_rooms": sold,
            "remaining_rooms": remaining,
            "orders_today": None,
            "snapshot_time": stamp,
        })
    return merged


def sum_jy03(batches: list[dict[str, Any]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    from foxhis_jy03_data import output_row

    if not batches:
        return []
    month_sets = [set(item) for item in batches]
    shared = set.intersection(*month_sets) if month_sets else set()
    if not shared:
        raise foxhis_common.FoxhisError("各业务库没有可汇总的共同月份，拒绝写入。")
    omitted = sorted(set.union(*month_sets) - shared)
    if omitted:
        print("INFO [jy03] 以下月份不是每个业务库都完整，本轮不写入：" + "、".join(omitted))
    rows = []
    for month in sorted(shared):
        parts = [item[month] for item in batches]
        try:
            year_text, month_text = str(month).split("-", 1)
            month_date = date(int(year_text), int(month_text), 1)
        except (TypeError, ValueError):
            raise foxhis_common.FoxhisError("月报月份无效，拒绝汇总。") from None
        rows.append(output_row(
            config,
            month_date,
            stamp,
            sum((_decimal(part.get("room_nights")) for part in parts), Decimal(0)),
            sum((_decimal(part.get("net_room_revenue")) for part in parts), Decimal(0)),
            sum((_decimal(part.get("room_days")) for part in parts), Decimal(0)),
            sum((_decimal(part.get("maintain_room_days")) for part in parts), Decimal(0)),
            sum((_decimal(part.get("available_room_days")) for part in parts), Decimal(0)),
        ))
    return rows


def _component_key(report_key: str, row: dict[str, Any]) -> str:
    name = str(row.get("room_type_name") or "").strip()
    if report_key != "jl01":
        return name
    day = row.get("business_date")
    text = day.isoformat() if isinstance(day, date) else str(day or "")
    return f"{text}|{name}"


def _rows_with_components(report_key: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("rows") or []
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise foxhis_common.FoxhisError("汇总明细无效。")
    components = payload.get("components") if isinstance(payload.get("components"), dict) else {}
    rows = []
    for row in raw:
        extra = components.get(_component_key(report_key, row))
        if isinstance(extra, dict):
            row = {**row, **{key: value for key, value in extra.items() if key not in row}}
        rows.append(row)
    return rows


def _same_name_missing_components(report_key: str, payloads: list[dict[str, Any]]) -> bool:
    """同名房型要相加时，旧缓存里没有可用房日，需要重新查询。"""
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for payload in payloads:
        for row in _rows_with_components(report_key, payload):
            if report_key == "jl11":
                key = (str(row.get("room_type_name") or "").strip(),)
            else:
                key = (row.get("business_date"), str(row.get("room_type_name") or "").strip())
            grouped.setdefault(key, []).append(row)
    required = ("available_rooms",) if report_key == "jl11" else ("available_rooms", "total_rooms")
    return any(len(items) > 1 and any(field not in item for item in items for field in required) for items in grouped.values())


def sum_jl11(batches: list[list[dict[str, Any]]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    from decimal import ROUND_HALF_UP

    from foxhis_jy01_data import rounded

    grouped: dict[str, dict[str, Any]] = {}
    for rows in batches:
        seen: set[str] = set()
        for row in rows:
            name = str(row.get("room_type_name") or "").strip()
            if not name or name in seen:
                raise foxhis_common.FoxhisError("JL11 房型名称重复或为空，拒绝汇总。")
            seen.add(name)
            current = grouped.get(name)
            if current is None:
                grouped[name] = {
                    **row,
                    "hotel_id": config.hotel_id,
                    "hotel_name": config.hotel_name,
                    "room_type_name": name,
                    "snapshot_time": stamp,
                    "_sources": 1,
                }
                continue
            for field in ("snapshot_date", "period_start", "period_end", "section", "source_platform"):
                if current.get(field) != row.get(field):
                    raise foxhis_common.FoxhisError("JL11 各业务库的统计周期不一致，拒绝汇总。")
            if "available_rooms" not in current or "available_rooms" not in row:
                raise foxhis_common.FoxhisError("JL11 同名房型缺少可加的可用房日，拒绝汇总。")
            for field in ("room_count", "room_nights", "room_revenue", "available_rooms"):
                current[field] = _decimal(current.get(field)) + _decimal(row.get(field))
            current["_sources"] += 1
    result = []
    for name in sorted(grouped):
        item = grouped[name]
        sources = item.pop("_sources")
        if sources == 1:
            item.pop("available_rooms", None)
            result.append(item)
            continue
        count = _decimal(item["room_count"])
        nights = _decimal(item["room_nights"])
        revenue = rounded(_decimal(item["room_revenue"]))
        available = _decimal(item.pop("available_rooms"))
        occupancy = (nights * 100 / available).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if available else None
        item.update({
            "room_count": count,
            "room_nights": nights,
            "room_revenue": revenue,
            "occupancy_rate": occupancy,
            "average_room_price": rounded(revenue / nights) if nights else None,
            "revpar": rounded(revenue / count) if count else None,
            "overnight_room_count": nights,
            "overnight_occupancy_rate": occupancy,
        })
        result.append(item)
    return result


def _day_text(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def sum_jl01(batches: list[list[dict[str, Any]]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    from foxhis_jl01_data import derived, rounded

    date_sets = [
        {row.get("business_date") for row in rows if isinstance(row, dict)}
        for rows in batches
    ]
    shared = set.intersection(*date_sets) if date_sets else set()
    if not shared:
        raise foxhis_common.FoxhisError("各业务库没有可汇总的共同营业日，拒绝写入。")
    omitted = sorted(set.union(*date_sets) - shared, key=_day_text)
    if omitted:
        print("INFO [jl01] 以下营业日不是每个业务库都完整，本轮不写入：" + "、".join(_day_text(item) for item in omitted))
    grouped: dict[tuple, dict[str, Any]] = {}
    for rows in batches:
        seen: set[tuple] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise foxhis_common.FoxhisError("JL01 房型名称重复或为空，拒绝汇总。")
            if row.get("business_date") not in shared:
                continue
            name = str(row.get("room_type_name") or "").strip()
            key = (row.get("business_date"), name)
            if not name or key in seen:
                raise foxhis_common.FoxhisError("JL01 房型名称重复或为空，拒绝汇总。")
            seen.add(key)
            current = grouped.get(key)
            if current is None:
                grouped[key] = {
                    **row,
                    "hotel_id": config.hotel_id,
                    "hotel_name": config.hotel_name,
                    "room_type_name": name,
                    "snapshot_time": stamp,
                    "_sources": 1,
                }
                continue
            if any(field not in current or field not in row for field in ("available_rooms", "total_rooms")):
                raise foxhis_common.FoxhisError("JL01 同名房型缺少可加的房日，拒绝汇总。")
            if (current.get("pms_rate_room_type_id") or "") != (row.get("pms_rate_room_type_id") or ""):
                current["pms_rate_room_type_id"] = ""
            for field in ("room_nights", "room_revenue", "available_rooms", "total_rooms"):
                current[field] = _decimal(current.get(field)) + _decimal(row.get(field))
            current["_sources"] += 1
    result = []
    for key in sorted(grouped, key=lambda item: (str(item[0]), item[1])):
        item = grouped[key]
        sources = item.pop("_sources")
        if sources == 1:
            item.pop("available_rooms", None)
            item.pop("total_rooms", None)
            result.append(item)
            continue
        metrics = derived({
            "room_revenue": _decimal(item["room_revenue"]),
            "room_nights": _decimal(item["room_nights"]),
            "available_rooms": _decimal(item.pop("available_rooms")),
            "total_rooms": _decimal(item.pop("total_rooms")),
        })
        item.update({
            "room_nights": metrics["room_nights"],
            "room_revenue": rounded(_decimal(item["room_revenue"])),
            "occupancy_rate": metrics["occupancy_rate"],
            "adr": metrics["adr"],
            "revpar": metrics["revpar"],
        })
        result.append(item)
    return result


def _keep_orders(payloads: list[dict[str, Any]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        database = str(payload.get("database") or "").strip()
        if not database:
            raise foxhis_common.FoxhisError("JD01 缺少业务库名，拒绝汇总。")
        raw_rows = payload.get("rows") or []
        if not isinstance(raw_rows, list):
            raise foxhis_common.FoxhisError("汇总明细无效。")
        seen: set[tuple] = set()
        for row in raw_rows:
            if not isinstance(row, dict):
                raise foxhis_common.FoxhisError("汇总明细无效。")
            item = dict(row)
            raw = str(item.get("order_id") or "").strip()
            if not raw or len(raw) > 100:
                raise foxhis_common.FoxhisError("JD01 订单号为空或超过字段长度，拒绝汇总。")
            item["order_id"] = raw
            item["hotel_id"] = config.hotel_id
            item["hotel_name"] = config.hotel_name
            if "snapshot_time" in item:
                item["snapshot_time"] = stamp
            # 单号相同可以是不同业务库里的不同订单。只拒绝同一个库里整行完全一样的记录。
            identity = tuple((key, item.get(key)) for key in sorted(item) if key != "snapshot_time")
            if identity in seen:
                raise foxhis_common.FoxhisError("JD01 记录重复，拒绝写入。")
            seen.add(identity)
            rows.append(item)
    return rows


def _fee_key(row: dict[str, Any]) -> tuple:
    return (row.get("order_id"), row.get("business_date"), row.get("room_no"), row.get("charge_subject"),
            str(row.get("source_payment_id") or ""))


def _merge_revenue(payloads: list[dict[str, Any]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    owners: dict[tuple, str] = {}
    for payload in payloads:
        database = str(payload.get("database") or "").strip()
        if not database:
            raise foxhis_common.FoxhisError("RS01 缺少业务库名，拒绝汇总。")
        raw_rows = payload.get("rows") or []
        if not isinstance(raw_rows, list):
            raise foxhis_common.FoxhisError("汇总明细无效。")
        for row in raw_rows:
            if not isinstance(row, dict):
                raise foxhis_common.FoxhisError("汇总明细无效。")
            item = dict(row)
            if "hotel_id" in item:
                item["hotel_id"] = config.hotel_id
            if "hotel_name" in item:
                item["hotel_name"] = config.hotel_name
            if "snapshot_time" in item:
                item["snapshot_time"] = stamp
            payment = str(item.get("source_payment_id") or "")
            item["source_payment_id"] = payment
            key = _fee_key(item)
            owner = owners.get(key)
            if owner == database:
                raise foxhis_common.FoxhisError("RS01 同一流水重复，拒绝写入。")
            if owner is not None:
                payment = f"{database}:{payment}"
                if len(payment) > 100:
                    raise foxhis_common.FoxhisError("RS01 流水号超过字段长度，拒绝写入。")
                item["source_payment_id"] = payment
                key = _fee_key(item)
                if key in owners:
                    raise foxhis_common.FoxhisError("RS01 同一流水重复，拒绝写入。")
            owners[key] = database
            rows.append(item)
    return rows


_OVERLAP = {
    "room_status": ("pms_room_type_id",),
}


def _merge(report_key: str, payloads: list[dict[str, Any]], config: Config, stamp: datetime) -> list[dict[str, Any]]:
    if report_key == "jy01":
        return sum_jy01([payload.get("rows") or [] for payload in payloads], config, stamp)
    if report_key == "jy03":
        return sum_jy03([payload.get("by_month") or {} for payload in payloads], config, stamp)
    if report_key == "jl11":
        return sum_jl11([_rows_with_components(report_key, payload) for payload in payloads], config, stamp)
    if report_key == "jl01":
        return sum_jl01([_rows_with_components(report_key, payload) for payload in payloads], config, stamp)
    if report_key == "jd01":
        return _keep_orders(payloads, config, stamp)
    if report_key == "room_revenue":
        return _merge_revenue(payloads, config, stamp)
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        rows.extend(payload.get("rows") or [])
    fields = _OVERLAP.get(report_key)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise foxhis_common.FoxhisError("汇总明细无效。")
        if "hotel_id" in row:
            row["hotel_id"] = config.hotel_id
        if "hotel_name" in row:
            row["hotel_name"] = config.hotel_name
        if "snapshot_time" in row:
            row["snapshot_time"] = stamp
        if fields:
            key = tuple(row.get(field) for field in fields)
            if key in seen:
                raise foxhis_common.FoxhisError(f"{report_key} 多个业务库出现相同的房型代码，拒绝写入。")
            seen.add(key)
    return rows


def _write(report_key: str, rows: list[dict[str, Any]], config: Config, stamp: datetime, sync: Callable[..., int]) -> int:
    if not rows:
        print(f"OK [{report_key}] 汇总结果为空，保留上一份数据")
        return 0
    if report_key == "room_revenue":
        from foxhis_room_revenue_data import month_window
        start, end = month_window(stamp.date())
        written = sync(rows, config, start, end)
    else:
        written = sync(rows, config)
    print(f"OK [{report_key}] rows={len(rows)} written={written}")
    return 0


def run_multi(report_key: str, collect: Callable[..., dict[str, Any]], accounts: Any, *,
              sync: Callable[..., int], realtime: bool = False) -> int:
    accounts = _require_accounts(accounts)
    base = foxhis_common.Config.from_env()
    if not str(base.hotel_name or "").strip():
        raise foxhis_common.FoxhisError("汇总酒店名称为空，本轮不采集。")
    shared_reports = os.environ.get("FOXHIS_REPORTS", "")
    selected = [
        (account, config_for_account(base, account))
        for account in accounts
        if participates(account, report_key, shared_reports)
    ]
    if not selected:
        print(f"SKIP [{report_key}] 没有业务库采集这张报表")
        return 0

    stamp = foxhis_common.datetime.now().replace(microsecond=0)
    daily = report_key in foxhis_common.DAILY_REPORTS
    bypass = any(flag in sys.argv for flag in ("--force", "--dry-run", "--initial"))
    use_cache = daily and not bypass
    record_cache = daily and "--dry-run" not in sys.argv
    state = _load_state(base) if daily else {}
    waiting: list[str] = []
    ready = []
    for account, config in selected:
        if realtime:
            ready.append((account, config))
            continue
        allowed, message = foxhis_common.after_gate(config)
        if not allowed:
            waiting.append(f"{config.database}：{message}")
            continue
        ready.append((account, config))

    failures: list[str] = []
    payloads: list[dict[str, Any]] = []
    for _account, config in ready:
        identity = _identity(config, report_key, stamp, realtime)
        if use_cache:
            cached = _cached(state, report_key, identity)
            if cached is not None:
                payloads.append(cached)
                continue
        try:
            payload = _query(report_key, collect, config, stamp, realtime)
        except Exception as exc:
            failures.append(f"{config.database}：{exc}")
            continue
        payloads.append(thaw(freeze(payload)))
        if record_cache:
            _remember(state, report_key, identity, payload)
            _save_state(base, state)

    if failures:
        raise foxhis_common.FoxhisError(f"{report_key} 有业务库失败，本轮不写入：" + "；".join(failures))
    if waiting:
        print(f"SKIP [{report_key}] 还有业务库未到采集时机，本轮不写入：" + "；".join(waiting))
        return 0
    if report_key in {"jl01", "jl11"} and _same_name_missing_components(report_key, payloads):
        payloads = []
        for _account, config in ready:
            payload = _query(report_key, collect, config, stamp, realtime)
            payloads.append(thaw(freeze(payload)))
            if record_cache:
                _remember(state, report_key, _identity(config, report_key, stamp, realtime), payload)
                _save_state(base, state)
    merged = _merge(report_key, payloads, base, stamp)
    if "--dry-run" in sys.argv:
        print(f"OK [{report_key}] dry-run rows={len(merged)}")
        return 0
    return _write(report_key, merged, base, stamp, sync)
