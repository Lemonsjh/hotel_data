from __future__ import annotations

import os
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pymysql
import requests


URL = os.environ.get("BYPMS_CONTRACT_URL", "https://pms-api.bypms.cn/core/api/v1/contract/get")
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
SOURCE = "PMS（宝寓）"
TABLE = "jd01_booking_detail"

def checkout_cutoff(today: date) -> date:
    index = today.year * 12 + today.month - 2
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(today.day, monthrange(year, month)[1]))


def mysql_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("MYSQL_HOST") or os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT") or os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER") or os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD") or os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE") or os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    return None


def decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def fetch_contracts(session: requests.Session, cutoff: date) -> tuple[list[dict], list[dict]]:
    contracts: list[dict] = []
    nights: list[dict] = []
    total = None
    for page in range(1, 501):
        response = session.post(
            URL,
            json={"contractState": "D", "checkOut": cutoff.isoformat(),
                  "_pidx": page, "_pageSize": 20},
            headers={"Accept": "application/json", "Cookie": COOKIE,
                     "Referer": "https://www.bypms.cn/console/contract/", "User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("state") != 0 or not isinstance(data, dict):
            raise RuntimeError(f"ByPMS contract API failed on page {page}")
        batch, batch_nights, page_info = data.get("contracts"), data.get("nights"), data.get("_page")
        if not isinstance(batch, list) or not isinstance(batch_nights, list) or not isinstance(page_info, dict):
            raise RuntimeError(f"ByPMS contract API returned incomplete page {page}")
        empty = page_info.get("total") == 0 and not batch and not batch_nights
        if not empty and (page_info.get("pageIndex") != page or not isinstance(page_info.get("total"), int)):
            raise RuntimeError(f"ByPMS contract API returned wrong pagination on page {page}")
        if total is None:
            total = page_info["total"]
        elif total != page_info["total"]:
            raise RuntimeError("ByPMS contract result changed during pagination")
        contracts.extend(item for item in batch if isinstance(item, dict))
        nights.extend(item for item in batch_nights if isinstance(item, dict))
        if len(contracts) >= total:
            return contracts, nights
        if not batch:
            raise RuntimeError("ByPMS contract API ended before total was reached")
    raise RuntimeError("ByPMS contract API exceeded 500 pages")


def room_count(nights: list[dict]) -> int | None:
    if not nights:
        return None
    events: list[tuple[datetime, int]] = []
    for night in nights:
        start, end = parse_datetime(night.get("checkIn")), parse_datetime(night.get("checkOut"))
        if start is None or end is None or start >= end:
            return None
        events.extend(((start, 1), (end, -1)))
    concurrent = highest = 0
    for _, change in sorted(events):
        concurrent += change
        highest = max(highest, concurrent)
    return highest


def transform(contracts: list[dict], nights: list[dict], cutoff: date,
              snapshot: datetime) -> list[dict[str, Any]]:
    by_contract: dict[str, list[dict]] = defaultdict(list)
    for night in nights:
        by_contract[str(night.get("contractId"))].append(night)
    rows = []
    seen: set[str] = set()
    for contract in contracts:
        order_id = str(contract.get("id") or "").strip()
        arrival = parse_datetime(contract.get("checkIn"))
        departure = parse_datetime(contract.get("checkOut"))
        if not order_id or not arrival or not departure or departure.date() < cutoff:
            raise RuntimeError("ByPMS contract lacks order ID or expected checkout date")
        if order_id in seen:
            raise RuntimeError("ByPMS contract API returned duplicate order IDs")
        seen.add(order_id)
        related = by_contract[order_id]
        names = {str(item.get("roomTypeName") or "").strip() for item in related}
        names.discard("")
        fee = decimal(contract.get("priceFang"))
        nights_count = decimal(contract.get("amount"))
        nightly_price = (fee / nights_count).quantize(Decimal("0.01")) if fee is not None and nights_count and nights_count > 0 else None
        state = str(contract.get("state") or "").strip()
        rows.append({
            "hotel_name": HOTEL_NAME, "hotel_id": HOTEL_ID, "source_platform": SOURCE,
            "order_id": order_id, "booking_time": parse_datetime(contract.get("createTime")),
            "contact": contract.get("infoRealname") or None,
            "guest_source": contract.get("channelChs") or contract.get("channel") or None,
            "member_level": contract.get("channelAccountName") or None,
            "arrival_time": arrival, "departure_time": departure,
            "room_type_name": next(iter(names)) if len(names) == 1 else None,
            "room_count": room_count(related), "price_type": contract.get("contractType") or None,
            "room_price": nightly_price, "prepayment": None, "guarantee_method": None,
            "booking_status": {"D": "正常单", "P": "预订", "E": "已离店"}.get(state, f"未知({state})"),
            "hold_time": None, "remarks": contract.get("infoRemark") or None,
            "operator_name": contract.get("emplName") or None, "snapshot_time": snapshot,
        })
    return rows


def sync_mysql(rows: list[dict[str, Any]], cutoff: date) -> None:
    columns = tuple(rows[0]) if rows else (
        "hotel_name", "hotel_id", "source_platform", "order_id", "booking_time", "contact",
        "guest_source", "member_level", "arrival_time", "departure_time", "room_type_name",
        "room_count", "price_type", "room_price", "prepayment", "guarantee_method",
        "booking_status", "hold_time", "remarks", "operator_name", "snapshot_time",
    )
    sql = f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES ({', '.join(f'%({name})s' for name in columns)})"
    connection = pymysql.connect(**mysql_config())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s "
                "AND departure_time >= %s",
                (HOTEL_ID, SOURCE, cutoff),
            )
            if rows:
                cursor.executemany(sql, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    if not COOKIE or not HOTEL_ID:
        raise RuntimeError("BYPMS_COOKIE or HOTEL_ID is empty")
    snapshot = datetime.now()
    cutoff = checkout_cutoff(snapshot.date())
    with requests.Session() as session:
        contracts, nights = fetch_contracts(session, cutoff)
    rows = transform(contracts, nights, cutoff, snapshot)
    sync_mysql(rows, cutoff)
    print(f"ByPMS JD01 synced: checkout_since={cutoff} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
