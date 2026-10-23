#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 JL02 酒店经营业绩日报（支持单日及逐日补采）。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

import pms_utils
from pms_daily_dates import query_dates, same_day_last_year


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "output" / "JL02.json"
REPORT_URL = pms_utils.report_url("report/JL02")
DEFAULT_CODES = ["RoomType", "CustomerCategory", "AnalysisChannel", "CheckinType"]


def collection_dates(
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    today: date | None = None,
) -> list[str]:
    current = today or date.today()
    previous_month_end = current.replace(day=1) - timedelta(days=1)
    two_months_ago_end = previous_month_end.replace(day=1) - timedelta(days=1)
    dates = query_dates(start_date, end_date, today=current)
    for month_end in (previous_month_end, two_months_ago_end):
        dates.extend((month_end.isoformat(), same_day_last_year(month_end).isoformat()))
    return list(dict.fromkeys(dates))


def load_session() -> dict[str, str] | None:
    session = pms_utils.load_session()
    return session.get("cookies", {}) if session else None


def add_cookies(context: Any, cookies: dict[str, str]) -> None:
    pms_utils.add_cookies_to_context(context, cookies)


def complete_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    result.setdefault("statisticsCodes", list(DEFAULT_CODES))
    if result.get("orgId"):
        return result
    session = pms_utils.read_session(quiet=True) or {}
    for key in ("jd01_payload", "jd04_payload", "kf11_payload"):
        org_id = str((session.get(key) or {}).get("orgId") or "").strip()
        if org_id:
            result["orgId"] = org_id
            print(f"✅ JL02 已补充酒店组织ID（来源: {key}）")
            break
    if not result.get("orgId"):
        raise RuntimeError("JL02 缺少 PMS 组织 ID，请重新登录 PMS 后重试")
    return result


def fetch_requests(cookies: dict[str, str], api_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(pms_utils.request_headers(REPORT_URL))
    try:
        response = session.post(api_url, json=payload, timeout=pms_utils.API_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"⚠️ JL02 接口请求异常: {exc}")
        return None
    if response.status_code != 200:
        print(f"⚠️ JL02 接口状态码: {response.status_code}，{response.text[:200]}")
        return None
    try:
        data = response.json()
    except ValueError:
        print(f"⚠️ JL02 接口未返回 JSON: {response.text[:200]}")
        return None
    if data.get("status") != 0 or not isinstance(data.get("data"), dict):
        print(f"⚠️ JL02 返回异常: {data.get('message') or data.get('status')}")
        return None
    return data


def fetch_with_retry(cookies: dict[str, str], api_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    for attempt in range(1, 3):
        data = fetch_requests(cookies, api_url, payload)
        if data is not None:
            return data
        if attempt == 1:
            time.sleep(2)
    return None


def save_output(reports: list[dict[str, Any]]) -> None:
    if not reports:
        raise RuntimeError("JL02 没有可保存的数据")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = reports[0] if len(reports) == 1 else {"_reports": reports}
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ JL02 已保存 {len(reports)} 个营业日: {OUTPUT_FILE}")


def save_session(api_url: str, payload: dict[str, Any]) -> None:
    if not pms_utils.update_session(jl02_api_url=api_url, jl02_payload=payload):
        print("⚠️ JL02 接口信息保存失败: PMS 会话不存在或无效")


def cached_request() -> tuple[str | None, dict[str, Any] | None]:
    session = pms_utils.read_session(quiet=True) or {}
    return session.get("jl02_api_url"), session.get("jl02_payload")


def capture_request(cookies: dict[str, str]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            add_cookies(context, cookies)
            page = context.new_page()
            page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=pms_utils.NAVIGATION_TIMEOUT_MS)
            query_button = pms_utils.get_query_button(page)
            with page.expect_response(
                lambda response: "/dragon/api/v1/dragon/manager/jl02" in response.url
                and response.request.method == "POST"
                and response.status == 200,
                timeout=30000,
            ) as response_info:
                query_button.click(timeout=10000)
            response = response_info.value
            request = response.request
            try:
                payload = request.post_data_json
            except Exception:
                payload = json.loads(request.post_data or "{}")
            return response.url, complete_payload(payload), response.json()
        finally:
            browser.close()


def request_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
    api_url, payload = cached_request()
    if api_url and isinstance(payload, dict):
        try:
            return api_url, complete_payload(payload)
        except RuntimeError:
            pass
    api_url, payload, _ = capture_request(cookies)
    save_session(api_url, payload)
    print("✅ JL02 已重新捕获接口")
    return api_url, payload


def fetch_jl02(start_date: str | None = None, end_date: str | None = None) -> bool:
    print("\n=== 抓取 JL02 酒店经营业绩日报 ===")
    cookies = load_session()
    if not cookies:
        print("❌ PMS 登录态不存在，请重新登录")
        return False
    try:
        dates = collection_dates(start_date, end_date)
        api_url, template = request_template(cookies)
        reports: list[dict[str, Any]] = []
        for index, business_date in enumerate(dates, start=1):
            print(f"📥 JL02 [{index}/{len(dates)}] 营业日: {business_date}")
            payload = dict(template, businessDate=business_date)
            data = fetch_with_retry(cookies, api_url, payload)
            if data is None:
                print("⚠️ 缓存接口不可用，重新捕获后重试")
                api_url, template, browser_data = capture_request(cookies)
                save_session(api_url, template)
                payload = dict(template, businessDate=business_date)
                data = fetch_with_retry(cookies, api_url, payload)
                captured_date = str(template.get("businessDate") or "")
                if data is None and captured_date == business_date:
                    data = browser_data
                    print("✅ 已使用浏览器捕获的 JL02 数据")
            if data is None:
                raise RuntimeError(f"JL02 {business_date} 查询失败，请稍后从该日期重新补采")
            data["_query"] = {"businessDate": business_date}
            reports.append(data)
            if len(dates) > 1 and index < len(dates):
                time.sleep(1)
        save_output(reports)
        save_session(api_url, dict(template, businessDate=dates[-1]))
        return True
    except Exception as exc:
        print(f"❌ JL02 采集失败: {exc}")
        return False


def fetch_jl02_report(start_date: str | None = None, end_date: str | None = None) -> bool:
    return fetch_jl02(start_date, end_date)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JL02 酒店经营业绩日报抓取")
    parser.add_argument("--start-date", help="开始营业日，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束营业日，格式 YYYY-MM-DD；未填则与开始日期相同")
    args = parser.parse_args()
    raise SystemExit(0 if fetch_jl02(args.start_date, args.end_date) else 1)
