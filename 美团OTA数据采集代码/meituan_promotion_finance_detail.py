from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


PAGE_SIZE = 100
HISTORY_DAYS = 181


def profile_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "browser_profiles" / "meituan"


def request_page(page: Any, url: str, page_index: int) -> dict[str, Any]:
    end_date = date.today()
    payload = {
        "startDate": (end_date - timedelta(days=HISTORY_DAYS)).isoformat(),
        "endDate": end_date.isoformat(),
        "type": -1,
        "payType": 0,
        "pageIndex": page_index,
        "pageSize": PAGE_SIZE,
        "dpShopIdList": [],
        "queryPreUpgrade": False,
    }
    result = page.evaluate(
        """async ({url, payload}) => {
          const response = await fetch(url, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
          });
          return {status: response.status, payload: await response.json()};
        }""",
        {"url": url, "payload": payload},
    )
    if result["status"] != 200 or result["payload"].get("code") != 0:
        raise RuntimeError(f"Promotion finance API failed: HTTP {result['status']}")
    return result["payload"].get("data") or {}


def fetch_records(url: str) -> list[dict[str, Any]]:
    if not url:
        raise RuntimeError("MEITUAN_PROMOTION_FINANCE_URL is empty")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path()), channel="msedge", headless=True,
            chromium_sandbox=True, locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://ebmidas.dianping.com/", wait_until="domcontentloaded", timeout=60_000)
            records: list[dict[str, Any]] = []
            for page_index in range(1, 101):
                data = request_page(page, url, page_index)
                rows = data.get("list") or []
                records.extend(row for row in rows if row.get("id") is not None)
                if len(records) >= int(data.get("count") or 0) or len(rows) < PAGE_SIZE:
                    return records
        finally:
            context.close()
    raise RuntimeError("Promotion finance pagination exceeded 100 pages")


def save_records(hotel_id: str, records: list[dict[str, Any]]) -> None:
    import pymysql

    rows = [
        (hotel_id, int(item["id"]), str(item.get("addTime") or ""),
         str(item.get("productType") or ""), str(item.get("payType") or ""),
         item.get("amount") or 0, item.get("totalBalance") or 0)
        for item in records
    ]
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO meituan_ota_promotion_finance_detail
                   (hotel_id, record_id, transaction_time, product_type, transaction_type,
                    transaction_amount, balance)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE transaction_time=VALUES(transaction_time),
                   product_type=VALUES(product_type), transaction_type=VALUES(transaction_type),
                   transaction_amount=VALUES(transaction_amount), balance=VALUES(balance)""",
                rows,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty")
    records = fetch_records(os.environ.get("MEITUAN_PROMOTION_FINANCE_URL", "").strip())
    save_records(hotel_id, records)
    print(f"promotion finance records={len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
