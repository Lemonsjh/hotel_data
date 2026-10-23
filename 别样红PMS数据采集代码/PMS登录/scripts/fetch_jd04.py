#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMS（别样红）JD04 报表抓取脚本
"""

import requests
import json
import os
import sys
import time
import argparse
from pathlib import Path

# 导入公共工具模块
import pms_utils

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "output"


def complete_jd04_payload(payload, session_info):
    """JD04 页面偶尔漏传 orgId，复用同一会话已验证的组织 ID。"""
    result = dict(payload or {})
    if str(result.get("orgId") or "").strip():
        return result
    for key in ("jd01_payload", "kf11_payload", "jl01_payload", "jl02_payload"):
        org_id = str((session_info.get(key) or {}).get("orgId") or "").strip()
        if org_id:
            result["orgId"] = org_id
            print(f"✅ JD04 已补充酒店组织ID（来源: {key}）")
            return result
    raise RuntimeError("JD04 请求缺少 orgId，请重新登录 PMS 后重试")


def fetch_jd04():
    """抓取 JD04 报表数据"""
    print("\n=== 抓取 JD04 报表 ===")
    
    # 使用公共模块加载会话
    session_info = pms_utils.load_session()
    if not session_info:
        return
    
    cookies = session_info.get('cookies', {})
    jd04_api_url = session_info.get('jd04_api_url')
    jd04_payload = session_info.get('jd04_payload', {})
    
    print(f"✅ 会话有效")
    
    if not jd04_api_url:
        print("⚠️ 未捕获到 JD04 接口地址，需要通过浏览器获取")
        capture_jd04_interface(cookies)
        
        session_info = pms_utils.read_session(quiet=True) or {}
        jd04_api_url = session_info.get('jd04_api_url')
        jd04_payload = session_info.get('jd04_payload', {})
    
    if not jd04_api_url:
        print("❌ 无法获取 JD04 接口地址")
        return
    
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(pms_utils.request_headers(pms_utils.report_url()))
    
    if jd04_payload:
        payload = jd04_payload.copy()
        print("✅ 使用捕获到的 Payload")
    else:
        payload = {
            "orgId": "",
            "dateType": "1",
            "startDate": time.strftime("%Y-%m-%d"),
            "endDate": time.strftime("%Y-%m-%d"),
            "orderSource": "",
            "customerCategory": "",
            "orderStatus": "",
            "roomPriceType": "",
            "roomType": "",
            "prePaymentType": "",
            "prePayment": False,
            "analysisChannel": ""
        }
        print("⚠️ 使用默认 Payload")

    payload = complete_jd04_payload(payload, session_info)
    
    print(f"请求接口: {jd04_api_url}")
    
    try:
        response = session.post(jd04_api_url, json=payload, timeout=pms_utils.API_TIMEOUT_SECONDS)
        
        if response.status_code == 200:
            try:
                data = response.json()
                
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                output_file = os.path.join(OUTPUT_DIR, "JD04.json")
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                print(f"✅ JD04 报表已保存到 {output_file}")
                payload_data = data.get("data") or {}
                rows = payload_data.get("dataList") or [] if isinstance(payload_data, dict) else []
                print(f"   数据条数: {len(rows)}")
                return data
            except Exception as e:
                print(f"❌ JSON 解析失败: {e}")
                return None
        else:
            print(f"❌ 请求失败，状态码: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return None


def capture_jd04_interface(cookies):
    """通过浏览器捕获 JD04 接口地址"""
    print("\n=== 使用浏览器捕获 JD04 接口 ===")
    
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            pms_utils.add_cookies_to_context(context, cookies)
            
            page = context.new_page()
            page.goto(
                pms_utils.report_url(),
                wait_until="domcontentloaded",
                timeout=pms_utils.NAVIGATION_TIMEOUT_MS,
            )
            
            jd04_api_url = None
            captured_payload = None
            
            def handle_request(request):
                nonlocal jd04_api_url, captured_payload
                if "/reception/jd04" in request.url:
                    jd04_api_url = request.url
                    print(f"✅ 捕获到 JD04 接口: {jd04_api_url}")
                    
                    try:
                        captured_payload = request.post_data_json
                    except PlaywrightError:
                        post_data = request.post_data
                        captured_payload = json.loads(post_data) if post_data else {}
            
            page.on("request", handle_request)
            
            print("点击【门店】...")
            try:
                page.locator("text=门店").first.click(timeout=10000)
                time.sleep(2)
            except Exception as e:
                print(f"⚠️ 未找到【门店】按钮: {e}")
            
            print("点击【JD04 报表】...")
            try:
                page.locator("text=JD04").first.click(timeout=10000)
                pms_utils.get_query_button(page)
            except Exception as e:
                print(f"⚠️ 未找到 JD04 报表入口: {e}")
            
            print("点击【查询】按钮...")
            try:
                query_btn = pms_utils.get_query_button(page)
                query_btn.click(timeout=10000)
                time.sleep(5)
            except Exception as e:
                print(f"⚠️ 未找到查询按钮: {e}")
            
            if jd04_api_url:
                saved = pms_utils.update_session(
                    jd04_api_url=jd04_api_url,
                    jd04_payload=captured_payload,
                )
                if not saved:
                    raise RuntimeError("PMS 会话不存在或无效，无法保存 JD04 接口")
                print(f"✅ JD04 接口地址已保存")
            
            print("\n自动关闭浏览器...")
            browser.close()
            
    except Exception as e:
        print(f"❌ 捕获接口失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    fetch_jd04()
