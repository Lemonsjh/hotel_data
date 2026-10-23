#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 JY03 月报：首次补采两年，日常仅更新当前月。"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import pymysql
from playwright.sync_api import sync_playwright

import pms_utils


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "output" / "JY03.json"
REPORT_URL = pms_utils.report_url()
API_MARKER = "monthSummary"
REQUIRED_CATEGORIES = ["Summary", "CustomerCategory", "CheckinType", "RoomType", "AnalysisChannel"]


def load_cookies() -> dict[str, str] | None:
    session = pms_utils.load_session()
    return session.get("cookies", {}) if session else None


def complete_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = pms_utils.complete_org_ids(payload)
    selected = result.get("searchCategory") or []
    result["searchCategory"] = list(dict.fromkeys([*selected, *REQUIRED_CATEGORIES]))
    return result


def request_data(session: requests.Session, api_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        response = session.post(api_url, json=payload, timeout=pms_utils.API_TIMEOUT_SECONDS)
        data = response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError) as exc:
        print(f"⚠️ JY03 请求异常: {exc}")
        return None
    if not isinstance(data, dict) or data.get("status") != 0:
        print(f"⚠️ JY03 返回异常: {response.status_code} {str(data)[:160]}")
        return None
    return data


def request_with_retry(session: requests.Session, api_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    for attempt in range(2):
        data = request_data(session, api_url, payload)
        if data is not None:
            return data
        if attempt == 0:
            time.sleep(2)
    return None


def capture_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            pms_utils.add_cookies_to_context(context, cookies)
            page = context.new_page()
            page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=pms_utils.NAVIGATION_TIMEOUT_MS)
            try:
                page.get_by_text("门店", exact=True).first.click(timeout=15000)
            except Exception:
                page.locator(".ant-menu-item", has_text="门店").first.click(timeout=15000)
            try:
                page.get_by_text("JY03 酒店综合统计月报表(固化)", exact=True).first.click(timeout=15000)
            except Exception:
                page.locator(".ant-menu-item", has_text="JY03").first.click(timeout=15000)
            button = pms_utils.get_query_button(page, timeout=30000)
            with page.expect_response(
                lambda response: API_MARKER in response.url
                and response.request.method == "POST"
                and response.status == 200,
                timeout=30000,
            ) as response_info:
                button.click(timeout=10000)
            response = response_info.value
            try:
                payload = response.request.post_data_json
            except Exception:
                payload = json.loads(response.request.post_data or "{}")
            return response.url, complete_payload(payload)
        finally:
            browser.close()


def load_template() -> tuple[str | None, dict[str, Any] | None]:
    session = pms_utils.read_session(quiet=True) or {}
    return session.get("jy03_api_url"), session.get("jy03_payload")


def save_template(api_url: str, payload: dict[str, Any]) -> None:
    if not pms_utils.update_session(jy03_api_url=api_url, jy03_payload=payload):
        print("⚠️ JY03 接口信息保存失败: PMS 会话不存在或无效")


def request_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
    api_url, payload = load_template()
    if api_url and isinstance(payload, dict):
        try:
            return api_url, complete_payload(payload)
        except RuntimeError:
            pass
    api_url, payload = capture_template(cookies)
    save_template(api_url, payload)
    print("✅ JY03 已捕获接口")
    return api_url, payload


def needs_initial_backfill(previous_year: int) -> bool:
    """去年尚无月报时自动补齐一次；连接失败时安全按日常模式执行。"""
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    settings_path = ROOT_DIR.parents[1] / "OTA采集服务" / "config" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        hotel_id = hotel_id or str((settings.get("hotel") or {}).get("hotel_id") or "")
        mysql = settings.get("mysql") or {}
        if not hotel_id or not mysql.get("host"):
            return False
        config = {
            "host": os.environ.get("HOTEL_OTA_MYSQL_HOST") or mysql.get("host"),
            "port": int(os.environ.get("HOTEL_OTA_MYSQL_PORT") or mysql.get("port") or 3306),
            "user": os.environ.get("HOTEL_OTA_MYSQL_USER") or mysql.get("user"),
            "password": os.environ.get("HOTEL_OTA_MYSQL_PASSWORD") or mysql.get("password"),
            "database": os.environ.get("HOTEL_OTA_MYSQL_DATABASE") or mysql.get("database"),
            "charset": "utf8mb4",
            "connect_timeout": 5,
        }
        with pymysql.connect(**config) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM jy03_hotel_statistics_month WHERE hotel_id=%s AND period_month LIKE %s",
                (hotel_id, f"{previous_year}-%"),
            )
            return cursor.fetchone()[0] == 0
    except (OSError, json.JSONDecodeError, pymysql.MySQLError) as exc:
        print(f"⚠️ 无法检查 JY03 历史覆盖，按日常模式执行: {exc}")
        return False


def fetch_jy03(backfill: bool = False) -> bool:
    print("\n=== 抓取 JY03 酒店综合统计月报 ===")
    cookies = load_cookies()
    if not cookies:
        return False
    now = datetime.now()
    if not backfill and needs_initial_backfill(now.year - 1):
        backfill = True
        print("ℹ️ 检测到去年 JY03 数据为空，本次自动补采当前年和去年")
    years = (now.year, now.year - 1) if backfill else (now.year,)
    allowed_months = [] if backfill else [f"{now:%Y-%m}"]
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(pms_utils.request_headers(REPORT_URL))
    try:
        api_url, template = request_template(cookies)
        reports = []
        for index, year in enumerate(years, start=1):
            print(f"📥 JY03 [{index}/{len(years)}] 年份: {year}")
            data = request_with_retry(session, api_url, dict(template, year=year))
            if data is None:
                api_url, template = capture_template(cookies)
                save_template(api_url, template)
                data = request_with_retry(session, api_url, dict(template, year=year))
            if data is None:
                raise RuntimeError(f"JY03 {year} 年数据查询失败")
            data["_query"] = {"year": year}
            reports.append(data)
            if index < len(years):
                time.sleep(1)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(
            json.dumps(
                {"_reports": reports, "_meta": {"mode": "backfill" if backfill else "incremental", "allowed_months": allowed_months}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"✅ JY03 已保存：模式={'补采两年' if backfill else '仅更新当前月'}")
        return True
    except Exception as exc:
        print(f"❌ JY03 采集失败: {exc}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JY03 月报抓取")
    parser.add_argument("--backfill", action="store_true", help="补采当前年和去年全部月份")
    args = parser.parse_args()
    raise SystemExit(0 if fetch_jy03(args.backfill) else 1)
