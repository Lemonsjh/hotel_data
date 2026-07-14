#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JD04 报表（JD01同构API版）
完全参照 JD01 写法
"""

import requests
import json
import os
from datetime import datetime

SESSION_FILE = "pms_session_playwright.json"
OUTPUT_DIR = "output"


# =========================
# 今日默认
# =========================
def get_today():
    today = datetime.today().strftime("%Y-%m-%d")
    return today, today


# =========================
# session加载（和JD01一致）
# =========================
def load_session():
    if not os.path.exists(SESSION_FILE):
        print("❌ session不存在")
        return None

    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cookies = data.get("cookies", {})
    payload_template = data.get("jd04_payload", {})
    api_url = data.get("jd04_api_url")

    if not api_url:
        print("❌ 没有 JD04 API（请先抓接口）")
        return None

    return cookies, payload_template, api_url


# =========================
# payload构造（完全参照JD01逻辑）
# =========================
def build_payload(template, start_date, end_date, filters=None):
    payload = template.copy()

    # ❗核心：必须orgId（和JD01一致）
    if not payload.get("orgId"):
        raise Exception("❌ 缺少 orgId（请重新抓接口）")

    payload.update({
        "startDate": start_date,
        "endDate": end_date,

        # ===== JD04筛选字段（统一JD01风格）=====
        "orderSource": "",
        "customerCategory": "",
        "orderStatus": "",
        "roomPriceType": "",
        "roomType": "",
        "prePaymentType": "",
        "prePayment": False,
        "analysisChannel": ""
    })

    # ===== 可选筛选（完全同JD01写法）=====
    allowed = [
        "orderSource",
        "customerCategory",
        "orderStatus",
        "roomPriceType",
        "roomType",
        "prePaymentType",
        "analysisChannel",
        "prePayment"
    ]

    if filters:
        for k in allowed:
            if k in filters and filters[k] not in ["", None, []]:
                payload[k] = filters[k]

    # 清理空值（和JD01一致）
    payload = {k: v for k, v in payload.items() if v not in ["", None]}

    return payload


# =========================
# 请求（完全参照JD01）
# =========================
def fetch_jd04(filters=None):
    print("\n==============================")
    print("🚀 JD04（JD01同构API版）")
    print("==============================")

    session_data = load_session()
    if not session_data:
        return

    cookies, template, api_url = session_data

    start_date, end_date = get_today()

    print(f"\n📅 日期：{start_date}")

    payload = build_payload(template, start_date, end_date, filters)

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
        out_file = os.path.join(OUTPUT_DIR, "JD04.json")

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("\n✅ 成功保存：", out_file)

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
def jd04_test():
    """JD04 测试抓取（供外部导入调用）"""
    fetch_jd04()



# =========================
# main
# =========================
if __name__ == "__main__":
    fetch_jd04()