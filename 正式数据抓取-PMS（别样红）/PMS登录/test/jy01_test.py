#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
import os
from datetime import datetime, timedelta

SESSION_FILE = "pms_session_playwright.json"
OUTPUT_DIR = "output"


# =========================
# 默认时间：昨天（你刚刚要求）
# =========================
def get_yesterday():
    d = datetime.today() - timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# =========================
# session
# =========================
def load_session():
    if not os.path.exists(SESSION_FILE):
        print("❌ session不存在")
        return None

    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return (
        data.get("cookies", {}),
        data.get("jy01_api_url"),
        data.get("jy01_payload", {})
    )


# =========================
# 🚨 核心：勾选映射（关键）
# =========================
def apply_checkboxes(payload):
    payload = payload.copy()

    # =========================
    # ☑ 客源统计
    # =========================
    payload["analysisDimensionSubjectName"] = "客源统计"
    payload["analysisDimensionSubjectValue"] = "1"

    # =========================
    # ☑ 渠道统计
    # =========================
    payload["analysisDimensionKey"] = "CHANNEL"

    # UI checkbox 本质控制 variables 输出
    payload["showCustomerShare"] = True
    payload["showChannelShare"] = True

    return payload


# =========================
# request
# =========================
def fetch_jy01():
    print("\n==============================")
    print("🚀 JY01（最终API勾选版）")
    print("==============================")

    cookies, api_url, template = load_session()
    if not api_url:
        print("❌ 没有JY01 API，请先抓接口")
        return

    date = get_yesterday()

    print(f"\n📅 查询日期：{date}")
    print(f"📡 API：{api_url}")

    payload = template.copy()
    payload["startDate"] = date
    payload["endDate"] = date

    # 🚨 加入勾选逻辑
    payload = apply_checkboxes(payload)

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

    resp = session.post(api_url, json=payload, timeout=30)

    print("\n📊 状态码：", resp.status_code)

    if resp.status_code != 200:
        print(resp.text[:500])
        return

    data = resp.json()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR, "JY01.json")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\n✅ 完成：", out)
    return data

# =========================
# 供外部导入的函数
# =========================
def jy01_test():
    """JY01 测试抓取（供外部导入调用）"""
    fetch_jy01()


if __name__ == "__main__":
    fetch_jy01()