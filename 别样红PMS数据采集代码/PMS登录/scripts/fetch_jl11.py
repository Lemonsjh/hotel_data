#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch the JL11 room-type classification report for the latest 30 days."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

import pms_utils


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "output" / "JL11.json"
REPORT_URL = pms_utils.report_url("report/JL11")
API_MARKER = "/dragon/api/v1/dragon/jl/jl11"
WINDOW_DAYS = 30


def report_window(today: date | None = None) -> tuple[str, str]:
    end = (today or date.today()) - timedelta(days=1)
    return ((end - timedelta(days=WINDOW_DAYS - 1)).isoformat(), end.isoformat())


def load_cookies() -> dict[str, str] | None:
    session = pms_utils.load_session()
    return session.get("cookies", {}) if session else None


def complete_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    result.setdefault("firstLevel", "")
    result.setdefault("secondLevel", [])
    result.setdefault("indicator", [])
    if result.get("orgId"):
        return result
    session = pms_utils.read_session(quiet=True) or {}
    for key in ("jl11_payload", "jl02_payload", "jl01_payload", "jd01_payload", "kf11_payload"):
        org_id = str((session.get(key) or {}).get("orgId") or "").strip()
        if org_id:
            result["orgId"] = org_id
            return result
    raise RuntimeError("JL11 is missing the PMS organization ID")


def save_template(api_url: str, payload: dict[str, Any]) -> None:
    if not pms_utils.update_session(jl11_api_url=api_url, jl11_payload=payload):
        print("JL11 template save warning: PMS session is missing or invalid")


def cached_template() -> tuple[str | None, dict[str, Any] | None]:
    session = pms_utils.read_session(quiet=True) or {}
    return session.get("jl11_api_url"), session.get("jl11_payload")


def capture_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            pms_utils.add_cookies_to_context(context, cookies)
            page = context.new_page()
            page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=pms_utils.NAVIGATION_TIMEOUT_MS)
            query_button = pms_utils.get_query_button(page)
            with page.expect_response(
                lambda response: API_MARKER in response.url
                and response.request.method == "POST"
                and response.status == 200,
                timeout=30_000,
            ) as response_info:
                query_button.click(timeout=10_000)
            response = response_info.value
            try:
                payload = response.request.post_data_json
            except Exception:
                payload = json.loads(response.request.post_data or "{}")
            return response.url, complete_payload(payload)
        finally:
            browser.close()


def request_template(cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
    api_url, payload = cached_template()
    if api_url and isinstance(payload, dict):
        try:
            return api_url, complete_payload(payload)
        except RuntimeError:
            pass
    api_url, payload = capture_template(cookies)
    save_template(api_url, payload)
    return api_url, payload


def fetch_report(cookies: dict[str, str], api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = False
    session.cookies.update(cookies)
    session.headers.update(pms_utils.request_headers(REPORT_URL))
    response = session.post(api_url, json=payload, timeout=pms_utils.API_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise RuntimeError(f"JL11 request failed: HTTP {response.status_code}")
    data = response.json()
    if data.get("status") != 0 or not isinstance(data.get("data"), dict):
        raise RuntimeError(f"JL11 response failed: {data.get('message') or data.get('status')}")
    return data


def fetch_jl11(start_date: str | None = None, end_date: str | None = None) -> bool:
    print("\n=== Fetching JL11 room-type classification report ===")
    cookies = load_cookies()
    if not cookies:
        return False
    start_date, end_date = start_date or report_window()[0], end_date or report_window()[1]
    try:
        api_url, template = request_template(cookies)
        payload = dict(template, startDate=start_date, endDate=end_date)
        data = fetch_report(cookies, api_url, payload)
        data["_query"] = {"startDate": start_date, "endDate": end_date}
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        save_template(api_url, template)
        print(f"JL11 saved: {OUTPUT_FILE} ({start_date} to {end_date})")
        return True
    except Exception as exc:
        print(f"JL11 fetch failed: {exc}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch JL11 room-type classification report")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()
    raise SystemExit(0 if fetch_jl11(args.start_date, args.end_date) else 1)
