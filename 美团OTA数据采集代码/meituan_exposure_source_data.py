from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from meituan_config import MEITUAN_EB_COOKIE, MEITUAN_ME_COOKIE

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import (
    OUTPUT_DIR,
    sync_exposure_source_daily_history,
    sync_exposure_source_history,
)


APP_URL = (
    "https://eb.meituan.com/newhb-sub-app/data-center-pc/home/index.html"
    "?isMeIframeContainer=me&portalName=ME#/index"
)
HEADERS = [
    "hotel_id", "business_date", "snapshot_time", "total_exposure", "non_ad_exposure",
    "ad_exposure", "ad_exposure_ratio_pct", "ad_exposure_score", "data_status",
]
DAILY_HEADERS = [
    "hotel_id", "business_date", "snapshot_time", "total_exposure",
    "non_ad_exposure", "ad_exposure", "ad_exposure_ratio_pct",
]
FIELD_LABELS = {
    "total_exposure": ("整体曝光", "总曝光", "曝光量"),
    "non_ad_exposure": ("非广告曝光", "自然曝光", "免费曝光"),
    "ad_exposure": ("广告曝光", "付费曝光", "广告流量"),
}


def cookie_items(header: str, url: str) -> list[dict[str, str]]:
    return [
        {"name": name, "value": value, "url": url}
        for part in header.split(";") if "=" in part
        for name, value in [part.strip().split("=", 1)] if name
    ]


def browser_cookies() -> list[dict[str, str]]:
    entries: dict[tuple[str, str], dict[str, str]] = {}
    for header, url in (
        (MEITUAN_EB_COOKIE, "https://eb.meituan.com/"),
        (MEITUAN_ME_COOKIE, "https://me.meituan.com/"),
        (MEITUAN_EB_COOKIE, "https://epassport.meituan.com/"),
        (MEITUAN_ME_COOKIE, "https://epassport.meituan.com/"),
    ):
        for item in cookie_items(header, url):
            entries[(item["name"], item["url"])] = item
    return list(entries.values())


def read_value(text: str, labels: tuple[str, ...]) -> int:
    for label in labels:
        prefix = r"(?<!非)" if label == "广告曝光" else ""
        matched = re.search(rf"{prefix}{re.escape(label)}\s*[：:]?\s*([\d,]+)", text)
        if matched:
            return int(matched.group(1).replace(",", ""))
    raise RuntimeError(f"未在美团流量来源页面找到字段：{' / '.join(labels)}")


def score(total: int, ad: int) -> tuple[float, int, str]:
    if total <= 0:
        return 0.0, 0, "NO_EXPOSURE"
    ratio = ad / total * 100
    return ratio, 100 if ratio > 20 else 50 if ratio > 0 else 0, "NORMAL"


def business_date(now: datetime) -> str:
    return (now.date() - timedelta(days=1 if now.hour >= 9 else 2)).isoformat()


def read_source_values(page) -> dict[str, int]:
    for _ in range(60):
        text = page.locator("body").inner_text(timeout=5_000)
        try:
            return {field: read_value(text, labels) for field, labels in FIELD_LABELS.items()}
        except RuntimeError:
            page.wait_for_timeout(500)
    raise RuntimeError("美团流量来源页面未返回完整曝光数据，请稍后重试")


def source_period_button(page):
    button = page.locator(
        "xpath=//*[contains(@class, 'card-container')]"
        "[.//*[normalize-space()='流量来源']]//button[normalize-space()='近30天']"
    )
    button.wait_for(state="visible", timeout=30_000)
    if button.count() != 1:
        raise RuntimeError("未能唯一定位美团流量来源的近30天筛选按钮")
    return button


def exposure_detail(response) -> dict[str, list[dict[str, object]]] | None:
    if not urlparse(response.url).path.endswith("/flowDetails"):
        return None
    try:
        payload = response.json()
        details = payload.get("data", {}).get("exposureDetail", {})
        noads, ads = details.get("noads"), details.get("ads")
    except Exception:
        return None
    if isinstance(noads, list) and isinstance(ads, list):
        return {"noads": noads, "ads": ads}
    return None


def daily_rows(
    hotel_id: str, snapshot_time: datetime, details: dict[str, list[dict[str, object]]]
) -> list[list[object]]:
    ads = {str(item.get("dateTime")): int(item.get("value") or 0) for item in details["ads"]}
    noads = {str(item.get("dateTime")): int(item.get("value") or 0) for item in details["noads"]}
    rows = []
    for day in sorted(set(noads) | set(ads)):
        non_ad, ad = noads.get(day, 0), ads.get(day, 0)
        total = non_ad + ad
        rows.append([hotel_id, day, snapshot_time, total, non_ad, ad, round(ad / total * 100, 4) if total else 0])
    return rows


def collect() -> tuple[list[object], list[list[object]]]:
    if not MEITUAN_EB_COOKIE or not MEITUAN_ME_COOKIE:
        raise RuntimeError("美团登录信息缺失，请先在配置面板中执行 Edge 登录")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
                context.add_cookies(browser_cookies())
                page = context.new_page()
                details: dict[str, list[dict[str, object]]] = {}

                def capture_flow_details(response) -> None:
                    value = exposure_detail(response)
                    if value:
                        details.update(value)

                page.on("response", capture_flow_details)
                page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
                page.get_by_text("流量分析", exact=True).click(
                    force=True, no_wait_after=True, timeout=30_000
                )
                source_period_button(page).click(timeout=30_000)
                values = read_source_values(page)
                for _ in range(20):
                    if details:
                        break
                    page.wait_for_timeout(250)
                if not details:
                    raise RuntimeError("美团流量来源页面未返回每日曝光数据")
            finally:
                browser.close()
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("美团流量来源页面加载超时，请重新执行 Edge 登录后重试") from exc

    ratio, points, status = score(values["total_exposure"], values["ad_exposure"])
    now = datetime.now()
    summary = [
        os.environ.get("HOTEL_ID", "").strip(), business_date(now), now,
        values["total_exposure"], values["non_ad_exposure"], values["ad_exposure"],
        round(ratio, 4), points, status,
    ]
    return summary, daily_rows(str(summary[0]), now, details)


def main() -> int:
    row, daily = collect()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "meituan_ota_exposure_source_monthly.json").write_text(
        json.dumps(dict(zip(HEADERS, row)), ensure_ascii=False, default=str, indent=2), encoding="utf-8"
    )
    sync_exposure_source_history(HEADERS, [row])
    (OUTPUT_DIR / "meituan_ota_exposure_source_daily.json").write_text(
        json.dumps([dict(zip(DAILY_HEADERS, item)) for item in daily], ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )
    sync_exposure_source_daily_history(DAILY_HEADERS, daily)
    print(f"美团近30天流量来源采集完成：广告占比 {row[6]}%，评分 {row[7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
