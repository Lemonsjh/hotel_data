from __future__ import annotations

import json
import os
import sys
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pymysql
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from bypms_room_status_data import fetch_room_master


PAYMENT_URL = os.environ.get("BYPMS_PAYMENT_URL", "https://www.bypms.cn/console/finance/payment/get")
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT_SECONDS = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
PAYMENT_TABLE_NAME = "rs01_room_revenue_daily"
MAPPING_TABLE_NAME = "bypms_channel_unit_mapping_snapshot"
SOURCE_PLATFORM = "PMS（宝寓）"
PAGE_SIZE = 20
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


def current_month_window(today: date | None = None) -> tuple[date, date]:
    current = today or date.today()
    return current.replace(day=1), current.replace(day=monthrange(current.year, current.month)[1])


def parse_datetime(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def order_identifier(payment: dict[str, Any]) -> str:
    """Keep the contract number and payment record distinct without adding a schema field."""
    contract_id = str(payment.get("contractId") or "").strip()
    payment_id = str(payment.get("id") or "").strip()
    return f"{contract_id}:{payment_id}" if contract_id and payment_id else contract_id or payment_id


def fetch_payment_page(session: requests.Session, start: date, end: date, page: int) -> list[dict[str, Any]]:
    response = session.post(
        PAYMENT_URL,
        data={"checkOut": f"{start.isoformat()},{end.isoformat()}", "src": "D", "flowType": "I", "_pidx": str(page)},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Cookie": COOKIE,
            "Referer": "https://www.bypms.cn/console/finance/payment/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    payments = (payload.get("data") or {}).get("payments")
    if payload.get("state") != 0 or not isinstance(payments, list):
        raise RuntimeError(f"ByPMS payment API failed: {payload.get('message') or payload.get('state')}")
    return [item for item in payments if isinstance(item, dict)]


def fetch_payments(session: requests.Session, start: date, end: date) -> list[dict[str, Any]]:
    payments: list[dict[str, Any]] = []
    for page in range(1, 501):
        result = fetch_payment_page(session, start, end, page)
        payments.extend(result)
        if len(result) < PAGE_SIZE:
            return payments
    raise RuntimeError("ByPMS payment API exceeded 500 pages")


def channel_room_type_names(connection) -> dict[tuple[str, str], set[str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT channel, channel_unit_id, bypms_room_type_name
                FROM `{MAPPING_TABLE_NAME}` WHERE hotel_id=%s""",
            (HOTEL_ID,),
        )
        rows = cursor.fetchall()
    result: dict[tuple[str, str], set[str]] = {}
    for channel, unit_id, room_type_name in rows:
        name = str(room_type_name or "").strip()
        if name:
            result.setdefault((normalized(channel), str(unit_id)), set()).add(name)
    return result


def room_master_maps(room_master: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    rooms = room_master.get("vos", []) if room_master else []
    types = room_master.get("typeVos", []) if room_master else []
    type_names = {str(item.get("id")): str(item.get("name") or "").strip() for item in types if isinstance(item, dict)}
    room_numbers = {str(item.get("id")): str(item.get("name") or "").strip() for item in rooms if isinstance(item, dict)}
    room_types = {str(item.get("id")): str(item.get("type") or "").strip() for item in rooms if isinstance(item, dict)}
    return room_numbers, {room_id: type_names.get(type_id, "") for room_id, type_id in room_types.items()}


def build_rows(
    payments: list[dict[str, Any]], channel_types: dict[tuple[str, str], set[str]], room_numbers: dict[str, str], room_types: dict[str, str], snapshot_time: datetime
) -> list[dict[str, Any]]:
    rows = []
    for payment in payments:
        if str(payment.get("flowType") or "").upper() != "I" or str(payment.get("paymentType") or "").strip() != "收房费":
            continue
        occurred_at = parse_datetime(payment.get("occurTime"))
        room_id = str(payment.get("roomId") or "").strip()
        if not occurred_at or not room_id:
            continue
        names = channel_types.get((normalized(payment.get("channel")), str(payment.get("channelUnitId") or "")), set())
        room_type_name = next(iter(names)) if len(names) == 1 else room_types.get(room_id, "")
        check_in = parse_datetime(payment.get("checkIn"))
        check_out = parse_datetime(payment.get("checkOut"))
        rows.append(
            {
                "hotel_name": HOTEL_NAME,
                "hotel_id": HOTEL_ID,
                "source_platform": SOURCE_PLATFORM,
                "business_date": occurred_at.date(),
                "room_no": room_numbers.get(room_id) or room_id,
                "room_type_name": room_type_name or None,
                "guest_name": str(payment.get("contractInfoRealname") or "").strip() or None,
                "customer_source": str(payment.get("channel") or "").strip() or None,
                "checkin_time": check_in,
                "checkout_time": check_out,
                "rack_rate": None,
                "price_type": str(payment.get("paymentChannel") or "").strip() or None,
                "room_daily_price": number(payment.get("priceNight")),
                "stay_type": None,
                "charge_subject": "收房费",
                "room_nights": number(payment.get("amount")),
                "room_fee": number(payment.get("priceFang")),
                "operator_name": str(payment.get("emplName") or "").strip() or None,
                "order_id": order_identifier(payment),
                "channel_unit_id": str(payment.get("channelUnitId") or "").strip() or None,
                "snapshot_time": snapshot_time,
            }
        )
    return rows


def sync_mysql(rows: list[dict[str, Any]], connection=None) -> None:
    if not rows:
        return
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    sql = f"""
        INSERT INTO `{PAYMENT_TABLE_NAME}` (
            hotel_name, hotel_id, source_platform, business_date, room_no, room_type_name,
            guest_name, customer_source, checkin_time, checkout_time, rack_rate, price_type,
            room_daily_price, stay_type, charge_subject, room_nights, room_fee, operator_name,
            order_id, channel_unit_id, snapshot_time
        ) VALUES (
            %(hotel_name)s, %(hotel_id)s, %(source_platform)s, %(business_date)s, %(room_no)s, %(room_type_name)s,
            %(guest_name)s, %(customer_source)s, %(checkin_time)s, %(checkout_time)s, %(rack_rate)s, %(price_type)s,
            %(room_daily_price)s, %(stay_type)s, %(charge_subject)s, %(room_nights)s, %(room_fee)s, %(operator_name)s,
            %(order_id)s, %(channel_unit_id)s, %(snapshot_time)s
        ) ON DUPLICATE KEY UPDATE
            hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform), room_type_name=VALUES(room_type_name),
            guest_name=VALUES(guest_name), customer_source=VALUES(customer_source), checkin_time=VALUES(checkin_time),
            checkout_time=VALUES(checkout_time), price_type=VALUES(price_type), room_daily_price=VALUES(room_daily_price),
            room_nights=VALUES(room_nights), room_fee=VALUES(room_fee), operator_name=VALUES(operator_name),
            channel_unit_id=VALUES(channel_unit_id),
            snapshot_time=VALUES(snapshot_time)
    """
    try:
        with connection.cursor() as cursor:
            cursor.executemany(sql, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def main() -> int:
    if not HOTEL_ID or not COOKIE:
        raise RuntimeError("HOTEL_ID or BYPMS_COOKIE is empty")
    snapshot_time = datetime.now()
    start, end = current_month_window(snapshot_time.date())
    connection = pymysql.connect(**mysql_config())
    try:
        channel_types = channel_room_type_names(connection)
        with requests.Session() as session:
            payments = fetch_payments(session, start, end)
            room_master = fetch_room_master(session)
        room_numbers, room_types = room_master_maps(room_master)
        rows = build_rows(payments, channel_types, room_numbers, room_types, snapshot_time)
        sync_mysql(rows, connection)
    finally:
        connection.close()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "bypms_rs01_summary.json").write_text(
        json.dumps({"checkout_range": [start.isoformat(), end.isoformat()], "payment_count": len(payments), "row_count": len(rows)}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"ByPMS RS01 synced: checkout={start}~{end} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
