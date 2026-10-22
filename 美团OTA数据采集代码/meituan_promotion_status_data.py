from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from meituan_config import MEITUAN_EB_COOKIE, MEITUAN_ME_COOKIE, PARTNER_ID, POI_ID, USER_AGENT
from meituan_goods_price_mapping import MeituanGoodsClient
from meituan_page_capture import browser_profile_lock
from meituan_promotion_status_checks import (
    AUTO_ORDER_NAME, BUSINESS_TRAVEL_NAME, HIGHLIGHTS_NAME, PUBLIC_WELFARE_NAME,
    SCHEDULED_INVOICE_NAME, fetch_auto_order_status, fetch_business_travel_status,
    fetch_hotel_highlights_status, fetch_public_welfare_status, fetch_scheduled_invoice_status,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG, OUTPUT_DIR


STATUS_URL = "https://eb.meituan.com/api/v1/vip/member/platformMember/statusAndRight"
PROMOTION_CODE = "youmeihui"
PROMOTION_NAME = "\u4f18\u7f8e\u4f1a"
BUSINESS_TRAVEL_CODE = "business_travel_price"
HOURLY_ROOM_CODE = "hourly_room"
HOURLY_ROOM_NAME = "\u949f\u70b9\u623f"
HIGHLIGHTS_CODE = "hotel_highlights"
AUTO_ORDER_CODE = "auto_order_acceptance"
PUBLIC_WELFARE_CODE = "public_welfare_traffic"
SCHEDULED_INVOICE_CODE = "reservation_invoice"
COOLDOWN_MIN_HOURS = 10.0
COOLDOWN_MAX_HOURS = 14.0
COOLDOWN_REASONS = {"success", "failure"}


def schedule_state_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "state" / "meituan_promotion_status_schedule.json"


def load_schedule_state() -> dict[str, Any]:
    path = schedule_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_schedule_state(data: dict[str, Any]) -> None:
    path = schedule_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        temporary = Path(file.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_state_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def mark_attempt_started(attempted_at: datetime) -> None:
    state = load_schedule_state()
    state["last_attempt_at"] = attempted_at.isoformat(timespec="seconds")
    save_schedule_state(state)


def schedule_cooldown(completed_at: datetime, reason: str) -> datetime:
    if reason not in COOLDOWN_REASONS:
        raise ValueError(f"unsupported cooldown reason: {reason}")
    cooldown_hours = random.uniform(COOLDOWN_MIN_HOURS, COOLDOWN_MAX_HOURS)
    next_allowed_at = completed_at + timedelta(hours=cooldown_hours)
    state = load_schedule_state()
    state.update(
        {
            "last_result_at": completed_at.isoformat(timespec="seconds"),
            "cooldown_reason": reason,
            "next_allowed_at": next_allowed_at.isoformat(timespec="seconds"),
            "cooldown_hours": round(cooldown_hours, 3),
        }
    )
    save_schedule_state(state)
    return next_allowed_at


def mark_schedule_success(success_at: datetime) -> None:
    state = load_schedule_state()
    state["last_success_at"] = success_at.isoformat(timespec="seconds")
    save_schedule_state(state)


def fetch_youmeihui_status() -> str:
    if not MEITUAN_EB_COOKIE:
        raise RuntimeError("MEITUAN_EB_COOKIE is empty")
    response = requests.get(
        STATUS_URL,
        params={
            "poiId": POI_ID,
            "partnerId": PARTNER_ID,
            "yodaReady": "h5",
            "csecplatform": "4",
            "csecversion": "4.2.4",
        },
        headers={"Cookie": MEITUAN_EB_COOKIE, "User-Agent": USER_AGENT, "Referer": "https://eb.meituan.com/"},
        timeout=30,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    if payload.get("status") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"Youmeihui API failed: {payload.get('message') or payload.get('status')}")
    return "OPEN" if payload["data"].get("activeStatus") == 1 else "CLOSED"


def fetch_hourly_room_status() -> str:
    client = MeituanGoodsClient(MEITUAN_ME_COOKIE)
    goods_data = client.query_goods()
    business_date = datetime.now().strftime("%Y-%m-%d")
    status_rows = client.query_price_status(goods_data, "", business_date)
    for item in status_rows:
        goods = item.get("goodsBaseInfo") or {}
        if not re.search(r"-[0-9]+(?:[.][0-9]+)?\u5c0f\u65f6-", str(goods.get("goodsName") or "")):
            continue
        status = (item.get("goodsStatusMap") or {}).get(business_date) or {}
        if status.get("fullRoomCode") in (0, "0", None) and not status.get("fullRoomDesc"):
            return "OPEN"
    return "CLOSED"


def save_status(hotel_id: str, code: str, name: str, status: str) -> None:
    import pymysql

    snapshot_time = datetime.now()
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO meituan_ota_promotion_status
                   (hotel_id, promotion_code, promotion_name, status, snapshot_time)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE promotion_name=VALUES(promotion_name),
                   status=VALUES(status), snapshot_time=VALUES(snapshot_time)""",
                (hotel_id, code, name, status, snapshot_time),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Meituan promotion status data")
    parser.add_argument("--force", action="store_true", help="ignore the 10-14 hour automatic cooldown")
    args = parser.parse_args()

    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty")

    now = datetime.now()
    state = load_schedule_state()
    next_allowed_at = parse_state_time(state.get("next_allowed_at"))
    cooldown_reason = str(state.get("cooldown_reason") or "")
    if (
        not args.force
        and cooldown_reason in COOLDOWN_REASONS
        and next_allowed_at is not None
        and now < next_allowed_at
    ):
        print(
            "promotion status skipped: cooldown active; "
            f"reason={cooldown_reason}; next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        return 0

    mode = "forced" if args.force else "scheduled"
    mark_attempt_started(now)
    print(f"promotion status attempt mode={mode}")

    checks = [
        (PROMOTION_CODE, PROMOTION_NAME, fetch_youmeihui_status),
        (BUSINESS_TRAVEL_CODE, BUSINESS_TRAVEL_NAME, fetch_business_travel_status),
        (PUBLIC_WELFARE_CODE, PUBLIC_WELFARE_NAME, fetch_public_welfare_status),
        (SCHEDULED_INVOICE_CODE, SCHEDULED_INVOICE_NAME, fetch_scheduled_invoice_status),
        (HOURLY_ROOM_CODE, HOURLY_ROOM_NAME, fetch_hourly_room_status),
        (HIGHLIGHTS_CODE, HIGHLIGHTS_NAME, fetch_hotel_highlights_status),
        (AUTO_ORDER_CODE, AUTO_ORDER_NAME, fetch_auto_order_status),
    ]
    results = []
    failures = []
    try:
        with browser_profile_lock():
            for code, name, check in checks:
                started = time.monotonic()
                print(f"{code} check started", flush=True)
                try:
                    status = check()
                    save_status(hotel_id, code, name, status)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:300]}"
                    failures.append((code, name, message))
                    print(f"{code} check failed; previous database status retained: {message}")
                    continue
                finally:
                    print(f"{code} check finished; duration={time.monotonic() - started:.2f}s", flush=True)
                results.append((code, name, status))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "meituan_ota_promotion_status.json").write_text(
            json.dumps(
                {
                    "hotel_id": hotel_id,
                    "checked_at": datetime.now(),
                    "items": [{"promotion_code": code, "status": status} for code, _name, status in results],
                    "failures": [
                        {"promotion_code": code, "promotion_name": name, "error": error}
                        for code, name, error in failures
                    ],
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        failed_at = datetime.now()
        next_allowed_at = schedule_cooldown(failed_at, "failure")
        print(
            "promotion status failed; cooldown scheduled; "
            f"next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        raise

    print(", ".join(f"{code} status={status}" for code, _name, status in results))
    completed_at = datetime.now()
    if failures:
        next_allowed_at = schedule_cooldown(completed_at, "failure")
        summary = "; ".join(f"{code}: {error}" for code, _name, error in failures)
        print(f"RuntimeError: 促销状态部分失败：已确认{len(results)}/{len(checks)}项；保留失败项旧值；{summary}")
    else:
        next_allowed_at = schedule_cooldown(completed_at, "success")
        mark_schedule_success(completed_at)
    print(f"promotion status next automatic attempt after {next_allowed_at:%Y-%m-%d %H:%M:%S}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
