from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from playwright.sync_api import sync_playwright
from meituan_config import MEITUAN_EB_COOKIE, MEITUAN_ME_COOKIE
from meituan_page_capture import browser_profile_lock, cookie_entries, page_access_issue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


TABLE_NAME = "meituan_ota_promotion_performance_30d"
PAGE_SIZE = 50
PERIOD_DAYS = 30
PROMOTION_PAGE_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Fi%2Findex.html%3Fbiz%3Dspread%26page%3Doverview"
)
PROMOTION_API_PATH = "/paginateQueryPlanAndLaunch"
TAB_IDS = (
    "T30002,T30003,T30032,T30034,T30033,T30001,T30004,T300030,"
    "T30005,T30047,T30006,T300071"
)
METRIC_COLUMNS = {
    "T30002": "exposure_count",
    "T30003": "click_count",
    "T30032": "booking_order_count",
    "T30034": "room_night_count",
    "T30033": "booking_order_amount",
    "T30001": "spend_amount",
    "T30004": "cost_per_click",
    "T300030": "click_rate_pct",
    "T30005": "merchant_view_count",
    "T30047": "cash_spend_amount",
}
COLUMNS = [
    "hotel_id", "period_start_date", "period_end_date", "snapshot_time",
    "plan_id", "plan_name", "promotion_status", "launch_id", "launch_name",
    "promotion_name", "promotion_type", "shop_id", "exposure_count", "click_count",
    "booking_order_count", "room_night_count", "booking_order_amount", "spend_amount",
    "cost_per_click", "click_rate_pct", "merchant_view_count", "cash_spend_amount",
]
PAGE_WAIT_SECONDS = 12
COOLDOWN_MIN_HOURS = 22.0
COOLDOWN_MAX_HOURS = 26.0
COOLDOWN_REASONS = {"success", "failure"}


def profile_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "browser_profiles" / "meituan"


def schedule_state_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "state" / "meituan_promotion_performance_schedule.json"


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


def number(value: Any) -> float | int | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
        return int(result) if result.is_integer() else result
    except (TypeError, ValueError):
        return None


def promotion_status(value: Any) -> str:
    statuses = {1: "RUNNING", 3: "PAUSED"}
    return statuses.get(number(value), "UNKNOWN")


def request_payload(period_start: date, period_end: date, page_num: int) -> dict[str, Any]:
    return {
        "searchContent": "", "shopIdList": "", "statusList": "",
        "beginDate": period_start.isoformat(), "endDate": period_end.isoformat(),
        "pageSize": PAGE_SIZE, "pageNum": page_num, "promoTypeList": "",
        "launchAimList": "", "planIdList": "", "launchIdList": "",
        "premiumFilter": "", "clientKey": "cpc.shop.promotion.list",
        "filterInnerAccountList": "", "tabIds": TAB_IDS, "customCols": "", "tabType": 2,
    }


def request_page(context: Any, page: Any, period_start: date, period_end: date, page_num: int) -> dict[str, Any]:
    responses: list[Any] = []
    payload = request_payload(period_start, period_end, page_num)

    def continue_with_period(route: Any) -> None:
        values = dict(parse_qsl(route.request.post_data or "", keep_blank_values=True))
        values.update(payload)
        route.continue_(post_data=urlencode(values))

    def capture_response(response: Any) -> None:
        if PROMOTION_API_PATH in response.url:
            responses.append(response)

    context.route(f"**{PROMOTION_API_PATH}*", continue_with_period)
    page.on("response", capture_response)
    try:
        page.goto(PROMOTION_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
        for _ in range(PAGE_WAIT_SECONDS * 2):
            if responses:
                break
            if issue := page_access_issue(page):
                raise RuntimeError(f"Promotion performance page requires manual action: {issue}")
            page.wait_for_timeout(500)
        if not responses:
            safe_page_url = page.url.split("?", 1)[0]
            raise RuntimeError(
                "Promotion performance page did not issue a data request "
                f"within {PAGE_WAIT_SECONDS}s; expected_path={PROMOTION_API_PATH}; "
                f"page={safe_page_url}; title={page.title()!r}"
            )
        response = responses[-1]
        if response.status != 200:
            raise RuntimeError(f"Promotion performance API failed: HTTP {response.status}")
        response = response.json()
    finally:
        page.remove_listener("response", capture_response)
        context.unroute(f"**{PROMOTION_API_PATH}*", continue_with_period)
    if response.get("code") != 200 or not isinstance(response.get("msg"), dict):
        raise RuntimeError(f"Promotion performance API response is invalid: {response.get('code')}")
    return response["msg"]


def fetch_plans(period_start: date, period_end: date) -> list[dict[str, Any]]:
    with browser_profile_lock(), sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path()),
            channel="msedge",
            headless=True,
            chromium_sandbox=True,
            locale="zh-CN",
        )
        try:
            cookies = (
                cookie_entries(MEITUAN_ME_COOKIE, "https://me.meituan.com/")
                + cookie_entries(MEITUAN_EB_COOKIE, "https://eb.meituan.com/")
            )
            if cookies:
                context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()
            plans: list[dict[str, Any]] = []
            for page_num in range(1, 101):
                response = request_page(context, page, period_start, period_end, page_num)
                page_rows = response.get("planList") or []
                plans.extend(row for row in page_rows if isinstance(row, dict))
                if len(plans) >= int(response.get("total") or 0) or len(page_rows) < PAGE_SIZE:
                    return plans
        finally:
            context.close()
    raise RuntimeError("Promotion performance pagination exceeded 100 pages")


def build_rows(plans: list[dict[str, Any]], period_start: date, period_end: date) -> list[tuple[Any, ...]]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    captured_at = datetime.now()
    rows: list[tuple[Any, ...]] = []
    for plan in plans:
        for launch in plan.get("launchList") or []:
            metrics = {column: None for column in METRIC_COLUMNS.values()}
            for item in launch.get("reportData") or []:
                column = METRIC_COLUMNS.get(str(item.get("id") or ""))
                if column:
                    metrics[column] = number(item.get("originVal"))
            booking_amount = metrics["booking_order_amount"]
            click_rate = metrics["click_rate_pct"]
            if click_rate is not None:
                click_rate *= 100
            rows.append(tuple([
                hotel_id, period_start, period_end, captured_at,
                plan.get("planId"), plan.get("planName") or "", promotion_status(launch.get("launchStatus")),
                launch.get("launchId"), launch.get("launchName") or "", launch.get("promoName") or "",
                launch.get("promoType"), launch.get("longShopId"), metrics["exposure_count"],
                metrics["click_count"], metrics["booking_order_count"], metrics["room_night_count"],
                booking_amount, metrics["spend_amount"], metrics["cost_per_click"], click_rate,
                metrics["merchant_view_count"], metrics["cash_spend_amount"],
            ]))
    return rows


def save_rows(hotel_id: str, rows: list[tuple[Any, ...]]) -> None:
    import pymysql

    hotel_id = hotel_id.strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is required for promotion performance sync")
    if any(str(row[0] or "").strip() != hotel_id for row in rows):
        raise RuntimeError("Promotion performance rows contain a different hotel_id")

    connection = pymysql.connect(**DB_CONFIG, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s", (hotel_id,))
            placeholders = ", ".join(["%s"] * len(COLUMNS))
            updates = ", ".join(
                f"`{column}`=VALUES(`{column}`)" for column in COLUMNS if column not in {
                    "hotel_id", "period_end_date", "plan_id", "launch_id"
                }
            )
            columns = ", ".join(f"`{column}`" for column in COLUMNS)
            if rows:
                cursor.executemany(
                    f"INSERT INTO `{TABLE_NAME}` ({columns}) VALUES ({placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {updates}", rows,
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Meituan promotion performance data")
    parser.add_argument("--force", action="store_true", help="ignore the 22-26 hour automatic cooldown")
    args = parser.parse_args()

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
            "promotion performance skipped: daily cooldown active; "
            f"reason={cooldown_reason}; next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        return 0

    mode = "forced" if args.force else "scheduled"
    mark_attempt_started(now)
    print(f"promotion performance attempt mode={mode}")

    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    period_end = date.today() - timedelta(days=1)
    period_start = period_end - timedelta(days=PERIOD_DAYS - 1)
    try:
        plans = fetch_plans(period_start, period_end)
        rows = build_rows(plans, period_start, period_end)
        save_rows(hotel_id, rows)
    except Exception:
        failed_at = datetime.now()
        next_allowed_at = schedule_cooldown(failed_at, "failure")
        print(
            "promotion performance failed; cooldown scheduled; "
            f"next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        raise

    success_at = datetime.now()
    next_allowed_at = schedule_cooldown(success_at, "success")
    mark_schedule_success(success_at)
    print(f"promotion performance plans={len(plans)} launches={len(rows)}")
    print(f"promotion performance next automatic attempt after {next_allowed_at:%Y-%m-%d %H:%M:%S}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
