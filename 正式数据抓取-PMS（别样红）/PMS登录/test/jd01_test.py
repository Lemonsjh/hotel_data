#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JD01 预订明细报表（修复权限版）
解决：10104 无权限门店问题
"""

import requests
import json
import os
from datetime import datetime, timedelta

SESSION_FILE = "pms_session_playwright.json"
OUTPUT_DIR = "output"


# =========================
# 时间默认：本日
# =========================
def get_date_range(days=0):
    end = datetime.today()
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# =========================
# 读取 session
# =========================
def load_session():
    if not os.path.exists(SESSION_FILE):
        print("❌ session不存在")
        return None

    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cookies = data.get("cookies", {})
    payload_template = data.get("jd01_payload", {})
    api_url = data.get("jd01_api_url")

    if not api_url:
        print("❌ 没有 JD01 API（请先抓一次接口）")
        return None

    return cookies, payload_template, api_url


# =========================
# 修复核心：构造 payload
# =========================
def build_payload(template, start_date, end_date):
    payload = template.copy()

    # ❗关键修复：必须用 payload 里的 orgId（不能用 cookie）
    if "orgId" not in payload or not payload["orgId"]:
        raise Exception("❌ payload中没有orgId（请重新抓接口）")

    payload.update({
        "startDate": start_date,
        "endDate": end_date,

        # 保留筛选项（可扩展）
        "orderSource": "",
        "customerCategory": "",
        "orderStatus": "",
        "roomPriceType": "",
        "roomType": "",
        "prePaymentType": "",
        "prePayment": False,
        "analysisChannel": ""
    })

    return payload


# =========================
# 请求接口
# =========================
def fetch_jd01(start_date=None, end_date=None):
    print("\n==============================")
    print("🚀 JD01 修复版启动（解决10104）")
    print("==============================")

    session_data = load_session()
    if not session_data:
        return

    cookies, template, api_url = session_data

    # 默认时间：本日
    if not start_date or not end_date:
        start_date, end_date = get_date_range()

    print(f"\n📅 查询：{start_date} ~ {end_date}")

    try:
        payload = build_payload(template, start_date, end_date)
    except Exception as e:
        print(e)
        return

    print("\n📡 API：", api_url)
    print("\n📦 payload：")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    session = requests.Session()
    session.cookies.update(cookies)

    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://xingfeng.beyondh.com:8081",
        "Referer": "https://xingfeng.beyondh.com:8081/"
    })

    try:
        resp = session.post(api_url, json=payload, timeout=30)

        print("\n📊 状态码：", resp.status_code)

        if resp.status_code != 200:
            print("❌ 请求失败")
            print(resp.text[:500])
            return

        data = resp.json()

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, "JD01.json")

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("\n✅ 成功保存：", out_path)

        try:
            print("📊 数据条数：", len(data.get("Data", [])))
        except:
            pass

        return data

    except Exception as e:
        print("❌ 请求异常：", e)
        return None


# =========================
# 供外部导入的函数
# =========================
def jd01_test():
    """JD01 测试抓取（供外部导入调用）"""
    fetch_jd01()


# =========================
# main
# =========================
if __name__ == "__main__":
    fetch_jd01()