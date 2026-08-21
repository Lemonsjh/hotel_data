from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql
import requests


STATE_URL = os.environ.get("BYPMS_STATE_URL", "https://www.bypms.cn/console/state/get")
STATE_PAGE_URL = os.environ.get("BYPMS_STATE_PAGE_URL", "https://www.bypms.cn/console/state/")
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT_SECONDS = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
TABLE_NAME = "bypms_room_status_snapshot"
ROOM_TYPE_TABLE_NAME = "bypms_room_type_hourly_status"
OUTPUT_DIR = Path(os.environ.get("HOTEL_OTA_OUTPUT_DIR", "OTA数据"))


def mysql_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("MYSQL_HOST") or os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT") or os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER") or os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD") or os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE") or os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }


def state_form(business_date: date) -> dict[str, str]:
    end_date = business_date + timedelta(days=20)
    return {
        "date": f"{business_date:%Y-%m-%d},{end_date:%Y-%m-%d}",
        "tagId": "0",
        "tbl": "monthA",
    }


def fetch_payload(business_date: date, session: requests.Session | None = None) -> dict[str, Any]:
    if not COOKIE:
        raise RuntimeError("BYPMS_COOKIE is empty")
    owns_session = session is None
    session = session or requests.Session()
    try:
        response = session.post(
            STATE_URL,
            data=state_form(business_date),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Cookie": COOKIE,
                "Referer": "https://www.bypms.cn/console/state/",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_session:
            session.close()
    if payload.get("state") != 0 or not isinstance((payload.get("data") or {}).get("data"), list):
        raise RuntimeError(f"ByPMS state API failed: {payload.get('message') or payload.get('state')}")
    return payload


def fetch_room_master(session: requests.Session) -> dict[str, Any]:
    response = session.get(
        STATE_PAGE_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cookie": COOKIE,
            "User-Agent": "Mozilla/5.0",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return extract_room_master(response.text)


def extract_room_master(html: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?:window\.)?_ROOMS\s*=\s*", html):
        source = html[match.end():].lstrip()
        try:
            data, _ = decoder.raw_decode(source)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("vos"), list):
            return data
    raise RuntimeError("ByPMS room master data _ROOMS was not found")


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def active_today(item: dict[str, Any], business_date: date) -> bool:
    check_in = parse_date(item.get("checkIn"))
    check_out = parse_date(item.get("checkOut"))
    return check_in is not None and check_out is not None and check_in <= business_date < check_out


def state_label(value: Any) -> str:
    return {"P": "预订", "S": "在住", "L": "锁房"}.get(str(value or "").strip(), "未知")


def room_type_inventory_rows(
    payload: dict[str, Any], room_master: dict[str, Any], business_date: date, snapshot_time: datetime
) -> list[dict[str, Any]]:
    type_names = {
        str(item.get("id")): str(item.get("name") or "").strip()
        for item in room_master.get("typeVos", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    room_types = {
        str(item.get("id")): str(item.get("type"))
        for item in room_master.get("vos", [])
        if isinstance(item, dict) and item.get("id") is not None and item.get("type") is not None
    }
    grouped: dict[str, dict[str, Any]] = {}
    for room_type_id in room_types.values():
        grouped.setdefault(room_type_id, {"total": 0, "sold": set(), "living": set(), "arrival": set(), "departure": set(), "locked": set(), "repair": set()})
    for room_type_id in room_types.values():
        grouped[room_type_id]["total"] += 1

    for item in (payload.get("data") or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        room_id = str(item.get("roomId") or "").strip()
        room_type_id = room_types.get(room_id) or str(item.get("roomTypeId") or "").strip()
        if not room_id or not room_type_id:
            continue
        stats = grouped.setdefault(room_type_id, {"total": 0, "sold": set(), "living": set(), "arrival": set(), "departure": set(), "locked": set(), "repair": set()})
        state = str(item.get("state") or "").strip()
        check_in, check_out = parse_date(item.get("checkIn")), parse_date(item.get("checkOut"))
        active = check_in is not None and check_out is not None and check_in <= business_date < check_out
        if state in {"P", "S"} and active:
            stats["sold"].add(room_id)
            if state == "S":
                stats["living"].add(room_id)
        if state in {"P", "S"} and check_in == business_date:
            stats["arrival"].add(room_id)
        if state in {"P", "S"} and check_out == business_date:
            stats["departure"].add(room_id)
        if state == "L" and active:
            label = str(item.get("tag") or "").lower()
            stats["repair" if "fix" in label or "维修" in label else "locked"].add(room_id)

    snapshot_hour = snapshot_time.replace(minute=0, second=0, microsecond=0)
    rows = []
    for room_type_id, stats in grouped.items():
        total = len(stats["sold"] | stats["locked"] | stats["repair"]) if not stats["total"] else stats["total"]
        locked, repair, sold = len(stats["locked"]), len(stats["repair"]), len(stats["sold"])
        saleable = max(total - locked - repair, 0)
        rows.append(
            {
                "hotel_id": HOTEL_ID,
                "hotel_name": HOTEL_NAME,
                "business_date": business_date,
                "snapshot_time": snapshot_time,
                "snapshot_hour": snapshot_hour,
                "bypms_room_type_id": room_type_id,
                "room_type_name": type_names.get(room_type_id) or f"未命名房型（{room_type_id}）",
                "total_rooms": total,
                "empty_rooms": max(total - sold - locked - repair, 0),
                "living_rooms": len(stats["living"]),
                "arrival_rooms": len(stats["arrival"]),
                "departure_rooms": len(stats["departure"]),
                "saleable_rooms": saleable,
                "sold_rooms": sold,
                "locked_rooms": locked,
                "repair_rooms": repair,
                "remaining_saleable_rooms": max(total - sold - locked - repair, 0),
                "occupancy_rate": round(sold * 100 / saleable, 4) if saleable else 0,
            }
        )
    return rows


def build_rows(payload: dict[str, Any], business_date: date, snapshot_time: datetime) -> list[dict[str, Any]]:
    snapshot_hour = snapshot_time.replace(minute=0, second=0, microsecond=0)
    rows = []
    for item in (payload.get("data") or {}).get("data") or []:
        if not isinstance(item, dict) or not active_today(item, business_date):
            continue
        record_id = str(item.get("id") or "").strip()
        room_id = str(item.get("roomId") or "").strip()
        if not record_id or not room_id:
            continue
        rows.append(
            {
                "hotel_id": HOTEL_ID,
                "hotel_name": HOTEL_NAME,
                "business_date": business_date,
                "snapshot_time": snapshot_time,
                "snapshot_hour": snapshot_hour,
                "source_record_id": record_id,
                "room_id": room_id,
                "room_type_id": str(item.get("roomTypeId") or "").strip() or None,
                "room_state": str(item.get("state") or "").strip() or None,
                "room_state_name": state_label(item.get("state")),
                "sort_state": str(item.get("sortState") or "").strip() or None,
                "check_in": parse_date(item.get("checkIn")),
                "check_out": parse_date(item.get("checkOut")),
                "stay_nights": int(item.get("amount") or 0) or None,
                "contract_id": str(item.get("contractId") or "").strip() or None,
                "channel": str(item.get("channel") or "").strip() or None,
                "channel_name": str(item.get("channelChs") or "").strip() or None,
                "contract_type": str(item.get("contractType") or "").strip() or None,
                "booking_created_at": str(item.get("contractCreateTime") or "").strip() or None,
            }
        )
    return rows


def sync_mysql(
    rows: list[dict[str, Any]], inventory_rows: list[dict[str, Any]], snapshot_hour: datetime, connection=None
) -> None:
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s AND snapshot_hour=%s",
                (HOTEL_ID, snapshot_hour),
            )
            cursor.execute(
                f"DELETE FROM `{ROOM_TYPE_TABLE_NAME}` WHERE hotel_id=%s AND snapshot_hour=%s",
                (HOTEL_ID, snapshot_hour),
            )
            if rows:
                cursor.executemany(
                    f"""
                    INSERT INTO `{TABLE_NAME}` (
                        hotel_id, hotel_name, business_date, snapshot_time, snapshot_hour,
                        source_record_id, room_id, room_type_id, room_state, room_state_name,
                        sort_state, check_in, check_out, stay_nights, contract_id, channel,
                        channel_name, contract_type, booking_created_at
                    ) VALUES (
                        %(hotel_id)s, %(hotel_name)s, %(business_date)s, %(snapshot_time)s,
                        %(snapshot_hour)s, %(source_record_id)s, %(room_id)s, %(room_type_id)s,
                        %(room_state)s, %(room_state_name)s, %(sort_state)s, %(check_in)s,
                        %(check_out)s, %(stay_nights)s, %(contract_id)s, %(channel)s,
                        %(channel_name)s, %(contract_type)s, %(booking_created_at)s
                    )
                    """,
                    rows,
                )
            if inventory_rows:
                cursor.executemany(
                    f"""
                    INSERT INTO `{ROOM_TYPE_TABLE_NAME}` (
                        hotel_id, hotel_name, business_date, snapshot_time, snapshot_hour,
                        bypms_room_type_id, room_type_name, total_rooms, empty_rooms,
                        living_rooms, arrival_rooms, departure_rooms, saleable_rooms,
                        sold_rooms, locked_rooms, repair_rooms, remaining_saleable_rooms, occupancy_rate
                    ) VALUES (
                        %(hotel_id)s, %(hotel_name)s, %(business_date)s, %(snapshot_time)s,
                        %(snapshot_hour)s, %(bypms_room_type_id)s, %(room_type_name)s,
                        %(total_rooms)s, %(empty_rooms)s, %(living_rooms)s, %(arrival_rooms)s,
                        %(departure_rooms)s, %(saleable_rooms)s, %(sold_rooms)s, %(locked_rooms)s,
                        %(repair_rooms)s, %(remaining_saleable_rooms)s, %(occupancy_rate)s
                    )
                    """,
                    inventory_rows,
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def save_output(
    business_date: date, snapshot_time: datetime, rows: list[dict[str, Any]], inventory_rows: list[dict[str, Any]]
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "hotel_id": HOTEL_ID,
        "business_date": business_date.isoformat(),
        "snapshot_time": snapshot_time.isoformat(timespec="seconds"),
        "record_count": len(rows),
        "room_type_count": len(inventory_rows),
        "state_counts": {
            label: sum(1 for row in rows if row["room_state_name"] == label)
            for label in ("预订", "在住", "锁房", "未知")
        },
    }
    (OUTPUT_DIR / "bypms_room_status_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    if not HOTEL_ID:
        raise RuntimeError("HOTEL_ID is empty")
    snapshot_time = datetime.now()
    business_date = snapshot_time.date()
    with requests.Session() as session:
        payload = fetch_payload(business_date, session)
        room_master = fetch_room_master(session)
    rows = build_rows(payload, business_date, snapshot_time)
    inventory_rows = room_type_inventory_rows(payload, room_master, business_date, snapshot_time)
    sync_mysql(rows, inventory_rows, snapshot_time.replace(minute=0, second=0, microsecond=0))
    save_output(business_date, snapshot_time, rows, inventory_rows)
    print(f"ByPMS room status synced: date={business_date} rows={len(rows)} room_types={len(inventory_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
