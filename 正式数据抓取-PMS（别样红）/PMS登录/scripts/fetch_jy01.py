#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PMS（别样红）JY01 酒店综合统计日报表抓取脚本
（已对齐 JY03 架构：Playwright捕获 + requests执行）
接口：dailySummary
"""

import requests
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

# 导入公共工具模块
import pms_utils
import pms_history

ROOT_DIR = Path(__file__).resolve().parents[1]
SESSION_FILE = ROOT_DIR / "pms_session_playwright.json"
OUTPUT_DIR = ROOT_DIR / "output"
REPORT_URL = "https://xingfeng.beyondh.com:8081/"


# ========================
# 1. 读取登录态（使用公共模块）
# ========================
def load_session():
    """使用公共工具模块加载会话"""
    session = pms_utils.load_session()
    if session:
        return session.get("cookies", {})
    return None


# ========================
# 2. 注入cookie（使用公共模块）
# ========================
def add_cookies(context, cookies):
    """使用公共工具模块注入 cookies"""
    pms_utils.add_cookies_to_context(context, cookies)


# ========================
# 3. 打开 JY01 页面（增加超时时间和重试机制）
# ========================
def open_jy01(page):
    print("\n👉 打开报表中心...")
    # 使用 domcontentloaded 替代 networkidle，减少超时概率
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)

    print("👉 点击门店...")
    # 尝试多种定位方式
    try:
        page.locator("text=门店").first.click(timeout=15000)
    except:
        print("⚠️ 使用 text=门店 定位失败，尝试其他方式")
        page.locator(".ant-menu-item", has_text="门店").first.click(timeout=15000)
    time.sleep(3)

    print("👉 点击 JY01...")
    try:
        page.locator("text=JY01").first.click(timeout=15000)
    except:
        print("⚠️ 使用 text=JY01 定位失败，尝试其他方式")
        page.locator(".ant-menu-item", has_text="JY01").first.click(timeout=15000)
    pms_utils.get_query_button(page)


# ========================
# 4. 核心：捕获 dailySummary
# ========================
def capture_jy01(page):
    print("\n🚀 点击查询并捕获 dailySummary...")

    query_btn = pms_utils.get_query_button(page)
    with page.expect_response(
        lambda r: "dailySummary" in r.url and r.status == 200,
        timeout=30000
    ) as resp_info:
        query_btn.click(timeout=10000)

    response = resp_info.value
    request = response.request

    api_url = response.url

    try:
        payload = request.post_data_json
    except:
        payload = json.loads(request.post_data or "{}")

    data = response.json()

    print("✅ 捕获接口:", api_url)
    print("✅ payload keys:", list(payload.keys()))

    return api_url, payload, data


def apply_checkboxes(payload):
    payload = pms_utils.complete_org_ids(payload)
    required = ["Summary", "CustomerCategory", "CheckinType", "RoomType", "AnalysisChannel"]
    selected = payload.get("searchCategory")
    payload["searchCategory"] = list(dict.fromkeys([*(selected if isinstance(selected, list) else []), *required]))
    return payload


# ========================
# 5. requests复用
# ========================
def fetch_with_requests(cookies, api_url, payload):
    print("\n🚀 requests二次请求...")

    session = requests.Session()
    session.cookies.update(cookies)

    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://xingfeng.beyondh.com:8081",
        "Referer": "https://xingfeng.beyondh.com:8081/"
    })

    resp = session.post(api_url, json=payload, timeout=30)

    print("状态码:", resp.status_code)

    if resp.status_code != 200:
        print(resp.text[:300])
        return None

    return resp.json()


def save_output(data):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "JY01.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ 已保存:", path)


# ========================
# 保存会话信息
# ========================
def save_session_info(api_url, payload):
    if not SESSION_FILE.exists():
        return

    with SESSION_FILE.open("r", encoding="utf-8") as f:
        session = json.load(f)

    session["jy01_api_url"] = api_url
    session["jy01_payload"] = payload

    with SESSION_FILE.open("w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

    print("✅ JY01 接口信息已保存到会话文件")


# ========================
# 6. 主流程（增加异常处理和重试）
# ========================
def run(start_date=None, end_date=None):
    print("\n=== JY01 报表抓取 ===")

    cookies = load_session()
    if not cookies:
        return

    max_retries = 2
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            with sync_playwright() as p:
                # 使用 headless=False 方便调试，但如果需要静默运行可以改为 True
                browser = p.chromium.launch(headless=True, slow_mo=200)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                )

                add_cookies(context, cookies)

                page = context.new_page()

                # 设置页面超时
                page.set_default_navigation_timeout(60000)
                
                open_jy01(page)

                api_url, payload, _ = capture_jy01(page)
                payload = apply_checkboxes(payload)
                browser.close()

            if start_date and end_date:
                backfill, windows = False, [("指定日期", start_date, end_date)]
            else:
                backfill, windows = pms_history.query_plan("jy01_hotel_statistics_daily", "JY01")
            responses = []
            for label, window_start, window_end in windows:
                month_payload = dict(payload, startDate=window_start, endDate=window_end)
                print(f"✅ 查询营业日期[{label}]: {window_start} 至 {window_end}")
                data = fetch_with_requests(cookies, api_url, month_payload)
                if not isinstance(data, dict):
                    raise RuntimeError(f"JY01 {label} 未返回有效JSON")
                responses.append(data)
            result = (
                pms_history.merge_jy01(responses, windows[0][1], windows[-1][2])
                if backfill else responses[0]
            )
            save_session_info(api_url, dict(payload, startDate=windows[-1][1], endDate=windows[-1][2]))
            save_output(result)
            return  # 成功完成，退出循环
            
        except Exception as e:
            retry_count += 1
            print(f"\n❌ 第 {retry_count} 次尝试失败: {e}")
            if retry_count <= max_retries:
                print(f"🔄 等待 5 秒后进行第 {retry_count + 1} 次尝试...")
                time.sleep(5)
            else:
                print(f"\n❌ 已重试 {max_retries} 次，仍然失败")
                import traceback
                traceback.print_exc()
                return


# ========================
# 供外部导入的函数
# ========================
def fetch_jy01(start_date=None, end_date=None):
    """抓取 JY01 报表数据（供外部导入调用）"""
    run(start_date, end_date)


# ========================
# 入口
# ========================
if __name__ == "__main__":
    fetch_jy01()
