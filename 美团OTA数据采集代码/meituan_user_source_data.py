from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from meituan_config import MEITUAN_EB_COOKIE, MEITUAN_ME_COOKIE

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_user_source_history


APP_URL = (
    "https://eb.meituan.com/newhb-sub-app/data-center-pc/home/index.html"
    "?isMeIframeContainer=me&portalName=ME#/index"
)
LABELS = {
    "local_user_pct": "本地用户占比",
    "nonlocal_user_pct": "异地用户占比",
    "new_user_pct": "新客占比",
    "returning_user_pct": "老客占比",
}
HEADERS = [
    "hotel_id",
    "business_date",
    "snapshot_time",
    "platform_type",
    *LABELS,
]


def cookie_items(header: str, url: str) -> list[dict[str, str]]:
    result = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name:
            result.append({"name": name, "value": value, "url": url})
    return result


def read_percent(text: str, label: str) -> float:
    matched = re.search(rf"{re.escape(label)}\s*(\d+(?:\.\d+)?)%", text)
    if not matched:
        raise RuntimeError(f"未在美团人群分析页面找到字段：{label}")
    return float(matched.group(1))


def validate(values: dict[str, float]) -> None:
    if any(value < 0 or value > 100 for value in values.values()):
        raise RuntimeError("美团人群分析页面返回了无效比例")
    if abs(values["local_user_pct"] + values["nonlocal_user_pct"] - 100) > 1:
        raise RuntimeError("本地与异地用户占比之和异常，未写入数据库")
    if abs(values["new_user_pct"] + values["returning_user_pct"] - 100) > 1:
        raise RuntimeError("新客与老客占比之和异常，未写入数据库")


def business_date(now: datetime) -> str:
    """美团每天 9 点更新前日数据；9 点前页面仍可能显示前两日窗口。"""
    days = 1 if now.hour >= 9 else 2
    return (now.date() - timedelta(days=days)).isoformat()


def collect() -> tuple[list[object], dict[str, float]]:
    if not MEITUAN_EB_COOKIE or not MEITUAN_ME_COOKIE:
        raise RuntimeError("美团登录信息缺失，请先在配置面板中执行 Edge 登录")
    cookies = cookie_items(MEITUAN_EB_COOKIE, "https://eb.meituan.com/")
    cookies += cookie_items(MEITUAN_ME_COOKIE, "https://me.meituan.com/")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
                context.add_cookies(cookies)
                page = context.new_page()
                page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
                page.get_by_text("人群分析", exact=True).click(timeout=30_000)
                page.get_by_role("button", name="近30天", exact=True).click(timeout=30_000)
                page.get_by_text("本地用户占比", exact=True).wait_for(timeout=30_000)
                body_text = page.locator("body").inner_text(timeout=30_000)
                values = {key: read_percent(body_text, label) for key, label in LABELS.items()}
            finally:
                browser.close()
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("美团人群分析页面加载超时，请重新执行 Edge 登录后重试") from exc

    validate(values)
    now = datetime.now()
    row = [
        os.environ.get("HOTEL_ID", "").strip(),
        business_date(now),
        now,
        "美团",
        *(values[key] for key in LABELS),
    ]
    return row, values


def write_output(row: list[object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {key: value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value
               for key, value in zip(HEADERS, row)}
    (OUTPUT_DIR / "meituan_ota_user_source_monthly.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="采集美团近30天用户来源数据")
    parser.add_argument("--no-sync", action="store_true", help="只校验页面采集，不写入数据库")
    args = parser.parse_args()
    row, _values = collect()
    write_output(row)
    if not args.no_sync:
        sync_user_source_history(HEADERS, [row])
    print("美团用户来源数据采集完成：近30天 4 项占比已校验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
