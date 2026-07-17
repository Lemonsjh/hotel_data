#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMS（别样红）RS01 房费日报表(固化)抓取脚本
流程：
读取已登录 Cookie
-> 打开报表中心
-> 打开 RS01 房费日报表
-> 点击查询并捕获接口 Payload
-> 将日期改为最近 30 天
-> requests 请求接口
-> 保存 output/RS01.json
"""

import requests
import json
import os
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright

# 导入公共工具模块
import pms_utils
import pms_history

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
REPORT_URL = pms_utils.report_url()


def get_data_count(data):
    """兼容不同返回结构，统计数据条数"""
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            if isinstance(data["data"].get("dataList"), list):
                return len(data["data"]["dataList"])
            if isinstance(data["data"].get("Data"), list):
                return len(data["data"]["Data"])
        if isinstance(data.get("Data"), list):
            return len(data["Data"])
        if isinstance(data.get("dataList"), list):
            return len(data["dataList"])
    return 0


def load_session():
    """加载已登录 Cookie（使用公共模块）"""
    session = pms_utils.load_session()
    if session:
        return session.get("cookies", {})
    return None


def add_cookies_to_context(context, cookies):
    """把已登录 Cookie 注入浏览器上下文（使用公共模块）"""
    pms_utils.add_cookies_to_context(context, cookies)


def open_rs01_report(page):
    """打开 RS01 房费日报表"""
    print("\n正在访问 PMS 报表中心...")
    page.goto(pms_utils.report_url("report/RS01"), wait_until="domcontentloaded", timeout=pms_utils.NAVIGATION_TIMEOUT_MS)
    try:
        pms_utils.get_query_button(page, timeout=15000)
        print("当前页面:", page.url)
        print("页面标题:", page.title())
        return
    except Exception as exc:
        print("⚠️ 直达 RS01 页面失败，尝试从菜单进入:", exc)

    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=pms_utils.NAVIGATION_TIMEOUT_MS)
    print("点击【门店】...")
    try:
        page.locator("text=门店").first.click(timeout=15000)
    except Exception:
        page.locator(".ant-menu-item", has_text="门店").first.click(timeout=15000)
    time.sleep(2)

    print("点击【RS01 房费日报表(固化)】...")
    try:
        page.locator("text=RS01 房费日报表(固化)").first.click(timeout=15000)
    except Exception:
        page.locator(".ant-menu-item", has_text="RS01").first.click(timeout=15000)
    pms_utils.get_query_button(page)

    print("当前页面:", page.url)
    print("页面标题:", page.title())


def click_query_and_capture_rs01(page):
    """点击查询并捕获 RS01 接口 URL 和 Payload"""
    print("\n点击查询并捕获 RS01 接口...")

    query_btn = pms_utils.get_query_button(page)
    with page.expect_response(
        lambda r: "/revenue/roomRateReport" in r.url and r.status == 200,
        timeout=30000
    ) as response_info:
        query_btn.click(timeout=10000)

    response = response_info.value
    request = response.request

    api_url = response.url

    try:
        payload = request.post_data_json
    except Exception:
        post_data = request.post_data
        payload = json.loads(post_data) if post_data else {}

    print("✅ 捕获到 RS01 接口:", api_url)
    print("✅ 捕获到 Payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    return api_url, payload


def load_rs01_template():
    """读取上次捕获到的 RS01 接口模板"""
    session = pms_utils.read_session(quiet=True) or {}
    api_url = session.get("rs01_api_url")
    payload = session.get("rs01_payload")
    if api_url and isinstance(payload, dict):
        return api_url, payload
    return None, None


def save_rs01_template(api_url, payload):
    """保存 RS01 接口模板，页面偶发打不开时可复用"""
    if pms_utils.update_session(rs01_api_url=api_url, rs01_payload=payload):
        print("✅ RS01 接口信息已保存到会话文件")
    else:
        print("⚠️ RS01 接口信息保存失败: PMS 会话不存在或无效")


def set_recent_30_days(payload, start_date=None, end_date=None):
    """把 Payload 日期改成截至昨天的最近 30 天"""
    if not start_date or not end_date:
        latest_closed_day = datetime.now() - timedelta(days=1)
        start_date = (latest_closed_day - timedelta(days=29)).strftime("%Y-%m-%d")
        end_date = latest_closed_day.strftime("%Y-%m-%d")

    print(f"设置查询日期: {start_date} ~ {end_date}")

    start_keys = [
        "startDate",
        "beginDate",
        "businessStartDate",
        "bussinessStartDate"
    ]

    end_keys = [
        "endDate",
        "businessEndDate",
        "bussinessEndDate"
    ]

    changed = False

    for key in start_keys:
        if key in payload:
            payload[key] = start_date
            changed = True

    for key in end_keys:
        if key in payload:
            payload[key] = end_date
            changed = True

    if not changed:
        print("⚠️ Payload 里没找到常见日期字段，请检查捕获到的 Payload")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    return payload


def fetch_rs01_with_requests(cookies, api_url, payload):
    """使用 requests 请求 RS01 接口"""
    print("\n使用 requests 抓取 RS01 数据...")

    session = requests.Session()
    session.trust_env = False
    session.cookies.update(cookies)

    session.headers.update(pms_utils.request_headers(REPORT_URL))

    print("请求接口:", api_url)
    print("请求参数:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    response = session.post(api_url, json=payload, timeout=pms_utils.API_TIMEOUT_SECONDS)

    print("状态码:", response.status_code)

    if response.status_code != 200:
        print("❌ 请求失败:")
        print(response.text[:500])
        return None

    try:
        data = response.json()
    except Exception as e:
        print("❌ JSON 解析失败:", e)
        print(response.text[:500])
        return None

    return data


def save_rs01_output(data):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_file = os.path.join(OUTPUT_DIR, "RS01.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ RS01 报表已保存到 {output_file}")
    print(f"   数据条数: {get_data_count(data)}")


def fetch_rs01(start_date=None, end_date=None):
    """主流程"""
    print("\n=== 抓取 RS01 房费日报表(固化) ===")

    cookies = load_session()
    if not cookies:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            context = browser.new_context()
            add_cookies_to_context(context, cookies)

            page = context.new_page()

            open_rs01_report(page)

            api_url, payload = click_query_and_capture_rs01(page)

            browser.close()

    except Exception as e:
        print("❌ 浏览器捕获 RS01 接口失败:", e)
        api_url, payload = load_rs01_template()
        if not api_url:
            return None
        print("✅ 使用会话文件中缓存的 RS01 接口模板")
    else:
        save_rs01_template(api_url, payload)

    if start_date and end_date:
        backfill, windows = False, [("指定日期", start_date, end_date)]
    else:
        backfill, windows = pms_history.query_plan("rs01_room_revenue_daily", "RS01")
    responses = []
    for label, window_start, window_end in windows:
        month_payload = set_recent_30_days(dict(payload), window_start, window_end)
        print(f"📥 RS01 查询[{label}]")
        data = fetch_rs01_with_requests(cookies, api_url, month_payload)
        if not isinstance(data, dict):
            raise RuntimeError(f"RS01 {label} 未返回有效JSON")
        responses.append(data)
    result = (
        pms_history.merge_rs01(responses, windows[0][1], windows[-1][2])
        if backfill else responses[0]
    )
    save_rs01_output(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RS01 房费日报表抓取")
    parser.add_argument("--start-date", help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束日期，格式 YYYY-MM-DD")
    args = parser.parse_args()

    fetch_rs01(
        start_date=args.start_date,
        end_date=args.end_date
    )
