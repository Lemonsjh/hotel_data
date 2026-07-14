#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KF11 实时房态报表抓取（稳定版）
接口：/dragon/api/v1/dragon/myReport/KF11
逻辑：点击查询 -> 捕获响应 -> requests复用
"""

import requests
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

# 导入公共工具模块
import pms_utils

ROOT_DIR = Path(__file__).resolve().parents[1]
SESSION_FILE = ROOT_DIR / "pms_session_playwright.json"
OUTPUT_DIR = ROOT_DIR / "output"
REPORT_URL = "https://xingfeng.beyondh.com:8081/"


# =========================
# 1. 登录态（使用公共模块）
# =========================
def load_session():
    """使用公共模块加载会话"""
    session = pms_utils.load_session()
    if session:
        return session.get("cookies", {})
    return None


def add_cookies(context, cookies):
    """使用公共模块注入 cookies"""
    pms_utils.add_cookies_to_context(context, cookies)


# =========================
# 2. 打开 KF11
# =========================
def open_kf11(page):
    print("\n👉 进入报表中心")
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    print("👉 点击门店")
    page.locator("text=门店").first.click()
    time.sleep(2)

    print("👉 点击 KF11")
    page.locator("text=KF11").first.click()
    pms_utils.get_query_button(page)

    print("当前页面:", page.url)


# =========================
# 3. 关键：捕获 KF11 response
# =========================
def capture_kf11(page):
    print("\n🚀 点击查询并捕获 KF11 接口...")

    query_btn = pms_utils.get_query_button(page)
    with page.expect_response(
        lambda r: (
            "/lion/api/v1/lion/room/KF11" in r.url
            and r.request.method == "POST"
            and r.status == 200
        ),
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

    print("✅ KF11接口:", api_url)
    print("✅ payload keys:", list(payload.keys()))

    return api_url, payload, data


# =========================
# 4. requests复用
# =========================
def save_kf11_session(api_url, payload):
    """保存 KF11 接口信息到会话文件"""
    if not os.path.exists(SESSION_FILE):
        print("❌ 会话文件不存在")
        return

    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        session = json.load(f)

    session["kf11_api_url"] = api_url
    session["kf11_payload"] = payload

    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

    print("✅ KF11 接口信息已保存到会话文件")


def complete_kf11_payload(payload):
    """页面未提供orgId时，复用本轮JD01/JD04捕获的酒店组织ID。"""
    result = dict(payload or {})
    if result.get("orgId") or not os.path.exists(SESSION_FILE):
        return result
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        session = json.load(f)
    for key in ("jd01_payload", "jd04_payload"):
        org_id = str((session.get(key) or {}).get("orgId") or "").strip()
        if org_id:
            result["orgId"] = org_id
            print(f"✅ KF11 已补充酒店组织ID（来源: {key}）")
            break
    return result


def fetch_kf11_requests(cookies, api_url, payload):
    print("\n🚀 requests二次请求 KF11")

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


def get_data_list(data):
    """兼容KF11响应结构并返回房间列表。"""
    if not isinstance(data, dict):
        return []
    inner = data.get("data", {}).get("data", {})
    rows = inner.get("dataList", []) if isinstance(inner, dict) else []
    return rows if isinstance(rows, list) else []


def save_kf11_output(data):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "KF11.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存: {path}，数据条数: {len(get_data_list(data))}")


# =========================
# 5. 主流程
# =========================
def run():
    print("\n=== KF11 实时房态抓取 ===")

    cookies = load_session()
    if not cookies:
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,   # 非 headless 模式，否则会弹出浏览器窗口
                                    slow_mo=120)
        context = browser.new_context()

        add_cookies(context, cookies)

        page = context.new_page()

        open_kf11(page)

        api_url, payload, data = capture_kf11(page)

        browser.close()

    payload = complete_kf11_payload(payload)

    # 保存接口信息到会话文件
    save_kf11_session(api_url, payload)

    if not get_data_list(data):
        print("⚠️ 浏览器响应为空，尝试 requests 二次请求")
        data = fetch_kf11_requests(cookies, api_url, payload)
    if not get_data_list(data):
        raise RuntimeError("KF11 返回 0 条房态数据，禁止覆盖已有文件")
    save_kf11_output(data)


def fetch_kf11(start_date=None, end_date=None):
    """抓取 KF11 报表数据（供外部导入调用）"""
    run()


if __name__ == "__main__":
    fetch_kf11()
