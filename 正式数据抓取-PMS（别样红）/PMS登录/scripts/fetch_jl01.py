#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 JL01 经理综合日报表中的实际房型经营数据。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

import pms_utils
from pms_daily_dates import query_dates


ROOT_DIR = Path(__file__).resolve().parents[1]
SESSION_FILE = ROOT_DIR / "pms_session_playwright.json"
OUTPUT_FILE = ROOT_DIR / "output" / "JL01.json"
REPORT_URL = "https://xingfeng.beyondh.com:8081/report/JL01"
API_MARKER = "/lion/api/v1/lion/manager/actualManagerDailyReport"


def load_cookies() -> dict[str, str] | None:
    session = pms_utils.load_session()
    return session.get("cookies", {}) if session else None


def complete_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    result.setdefault("expandAgentCompany", True)
    if result.get("orgId"):
        return result
    try:
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        for key in ("jl02_payload", "jd01_payload", "jd04_payload", "kf11_payload"):
            org_id = str((session.get(key) or {}).get("orgId") or "").strip()
            if org_id:
                result["orgId"] = org_id
                break
    except (OSError, json.JSONDecodeError):
        pass
    if not result.get("orgId"):
        raise RuntimeError("JL01 缺少 PMS 组织 ID，请重新登录 PMS 后重试")
    return result


def post_report(cookies: dict[str, str], api_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://xingfeng.beyondh.com:8081",
            "Referer": REPORT_URL,
        }
    )
    try:
        response = session.post(api_url, json=payload, timeout=30)
        data = response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError) as exc:
        print(f"⚠️ JL01 请求异常: {exc}")
        return None
    if not isinstance(data, dict) or data.get("status") != 0:
        print(f"⚠️ JL01 返回异常: {response.status_code} {str(data)[:160]}")
        return None
    return data


def post_with_retry(cookies: dict[str, str], api_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    for attempt in range(2):
        data = post_report(cookies, api_url, payload)
        if data is not None:
            return data
        if attempt == 0:
            time.sleep(2)
    return None


def capture_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, slow_mo=100)
        try:
            context = browser.new_context()
            pms_utils.add_cookies_to_context(context, cookies)
            page = context.new_page()
            page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
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
            return response.url, complete_payload(payload), response.json()
        finally:
            browser.close()


def cached_template() -> tuple[str | None, dict[str, Any] | None]:
    try:
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        return session.get("jl01_api_url"), session.get("jl01_payload")
    except (OSError, json.JSONDecodeError):
        return None, None


def save_template(api_url: str, payload: dict[str, Any]) -> None:
    try:
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        session["jl01_api_url"] = api_url
        session["jl01_payload"] = payload
        SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"⚠️ JL01 接口信息保存失败: {exc}")


def request_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
    api_url, payload = cached_template()
    if api_url and isinstance(payload, dict):
        try:
            return api_url, complete_payload(payload)
        except RuntimeError:
            pass
    api_url, payload, _ = capture_template(cookies)
    save_template(api_url, payload)
    print("✅ JL01 已捕获接口")
    return api_url, payload


def save_output(reports: list[dict[str, Any]]) -> None:
    if not reports:
        raise RuntimeError("JL01 没有可保存的数据")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content: dict[str, Any] = reports[0] if len(reports) == 1 else {"_reports": reports}
    OUTPUT_FILE.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ JL01 已保存 {len(reports)} 个营业日")


def fetch_jl01(start_date: str | None = None, end_date: str | None = None) -> bool:
    print("\n=== 抓取 JL01 经理综合日报 ===")
    cookies = load_cookies()
    if not cookies:
        return False
    try:
        dates = query_dates(start_date, end_date)
        api_url, template = request_template(cookies)
        reports: list[dict[str, Any]] = []
        for index, business_date in enumerate(dates, start=1):
            print(f"📥 JL01 [{index}/{len(dates)}] 营业日: {business_date}")
            data = post_with_retry(cookies, api_url, dict(template, businessDate=business_date))
            if data is None:
                api_url, template, captured = capture_template(cookies)
                save_template(api_url, template)
                data = post_with_retry(cookies, api_url, dict(template, businessDate=business_date))
                if data is None and str(template.get("businessDate") or "") == business_date:
                    data = captured
            if data is None:
                raise RuntimeError(f"JL01 {business_date} 查询失败，请稍后从该日期重新补采")
            data["_query"] = {"businessDate": business_date}
            reports.append(data)
            if index < len(dates):
                time.sleep(1)
        save_output(reports)
        save_template(api_url, dict(template, businessDate=dates[-1]))
        return True
    except Exception as exc:
        print(f"❌ JL01 采集失败: {exc}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JL01 经理综合日报抓取")
    parser.add_argument("--start-date", help="开始营业日，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束营业日，未填则与开始日期相同")
    args = parser.parse_args()
    raise SystemExit(0 if fetch_jl01(args.start_date, args.end_date) else 1)
