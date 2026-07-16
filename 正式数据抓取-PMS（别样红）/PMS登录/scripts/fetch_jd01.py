#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMS（别样红）JD01 报表抓取脚本
完整流程：登录 -> 打开报表 -> 设置预订状态为全部 -> 查询 -> 捕获接口 -> 抓取数据
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
SESSION_FILE = ROOT_DIR / "pms_session_playwright.json"
OUTPUT_DIR = ROOT_DIR / "output"


def fetch_jd01(start_date=None, end_date=None):
    """抓取 JD01 报表数据"""
    print("\n=== 抓取 JD01 报表 ===")
    
    # 使用公共模块加载会话
    session_info = pms_utils.load_session()
    if not session_info:
        return
    
    cookies = session_info.get('cookies', {})
    jd01_api_url = session_info.get('jd01_api_url')
    jd01_payload = session_info.get('jd01_payload', {})
    
    print(f"✅ 会话有效，包含 {len(cookies)} 个 Cookie")
    
    # 如果没有捕获到 JD01 接口，需要先捕获
    if not jd01_api_url:
        print("⚠️ 未捕获到 JD01 接口地址，需要通过浏览器获取")
        capture_jd01_interface(cookies)
        
        # 重新加载会话信息
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            session_info = json.load(f)
        jd01_api_url = session_info.get('jd01_api_url')
        jd01_payload = session_info.get('jd01_payload', {})
    
    if not jd01_api_url:
        print("❌ 无法获取 JD01 接口地址")
        return
    
    # 使用 requests 请求 JD01 接口
    session = requests.Session()
    session.trust_env = False
    session.cookies.update(cookies)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://xingfeng.beyondh.com:8081",
        "Referer": "https://xingfeng.beyondh.com:8081/",
    })
    
    # 使用捕获到的 Payload 作为基础（包含正确的 orgId）
    # 如果没有捕获到 Payload，使用默认参数
    if jd01_payload:
        payload = jd01_payload.copy()
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
    
    # 更新日期范围（如果指定了）
    if start_date:
        payload['startDate'] = start_date
    if end_date:
        payload['endDate'] = end_date
    
    print(f"查询日期范围: {payload['startDate']} ~ {payload['endDate']}")
    
    print(f"请求接口: {jd01_api_url}")
    print(f"请求参数: {json.dumps(payload, indent=2, ensure_ascii=False)}")
    
    try:
        response = None
        for attempt in range(2):
            try:
                response = session.post(jd01_api_url, json=payload, timeout=30)
                break
            except requests.RequestException as exc:
                if attempt:
                    raise
                print(f"⚠️ JD01 request failed, retrying: {exc}")
                time.sleep(2)
        if response is None:
            raise RuntimeError("JD01 request did not return a response")
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                output_file = os.path.join(OUTPUT_DIR, "JD01.json")
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                print(f"✅ JD01 报表已保存到 {output_file}")
                print(f"   数据条数: {len(data.get('Data', []))}")
                return data
            except Exception as e:
                print(f"❌ JSON 解析失败: {e}")
                print(f"响应内容: {response.text[:500]}")
                return None
        else:
            print(f"❌ 请求失败，状态码: {response.status_code}")
            print(f"响应内容: {response.text[:500]}")
            return None
            
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return None


def click_booking_status_all(page):
    """点击预订状态下拉框，并选择第一项：全部"""
    print("设置【预订状态】为【全部】...")

    labels = page.locator("label.fiveChar")
    count = labels.count()
    print(f"找到 {count} 个标签")

    clicked = False

    for i in range(count):
        try:
            text = labels.nth(i).inner_text().strip().replace("：", ":")
            print(f"标签 {i}: {text}")
            
            if "预订状态" in text:
                print("找到【预订状态】字段，下标:", i)

                box = labels.nth(i).bounding_box()

                if not box:
                    print("⚠️ 找到【预订状态】，但无法获取坐标")
                    continue

                # 点击"预订状态"右侧的下拉框
                page.mouse.click(
                    box["x"] + box["width"] + 120,
                    box["y"] + box["height"] / 2
                )
                time.sleep(2)
                clicked = True
                break
        except Exception as e:
            print(f"检查标签 {i} 时出错: {e}")
            continue

    if not clicked:
        print("⚠️ 没有找到【预订状态】字段，尝试其他方式")
        
        # 尝试直接点击预订状态下拉框
        try:
            dropdowns = page.locator(".ant-select-selector")
            if dropdowns.count() > 0:
                print(f"找到 {dropdowns.count()} 个下拉框，尝试点击第一个")
                dropdowns.first.click(timeout=5000)
                time.sleep(2)
                clicked = True
        except Exception as e:
            print(f"尝试点击下拉框失败: {e}")

    if clicked:
        # 当前打开的下拉框，第一项就是"全部"
        try:
            dropdown = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").last
            dropdown_box = dropdown.bounding_box()

            if dropdown_box:
                page.mouse.click(
                    dropdown_box["x"] + 30,
                    dropdown_box["y"] + 20
                )
                print("✅ 已选择【全部】")
                time.sleep(1)
            else:
                print("⚠️ 没有找到已打开的下拉框")
        except Exception as e:
            print(f"选择全部失败: {e}")


def capture_jd01_interface(cookies):
    """通过浏览器捕获 JD01 接口地址"""
    print("\n=== 使用浏览器捕获 JD01 接口 ===")
    
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, slow_mo=200)
            context = browser.new_context()
            
            # 设置 Cookie
            cookie_list = []
            for name, value in cookies.items():
                cookie_list.append({
                    "name": name,
                    "value": value,
                    "url": "https://xingfeng.beyondh.com:8101"
                })
            context.add_cookies(cookie_list)
            
            page = context.new_page()
            
            print("正在访问报表页面...")
            page.goto("https://xingfeng.beyondh.com:8081/", wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            
            # 监听接口请求
            jd01_api_url = None
            captured_payload = None
            
            def handle_request(request):
                nonlocal jd01_api_url, captured_payload
                if "preOrderDetailReport" in request.url:
                    jd01_api_url = request.url
                    print(f"✅ 捕获到 JD01 接口: {jd01_api_url}")
                    
                    try:
                        captured_payload = request.post_data_json
                    except:
                        post_data = request.post_data
                        captured_payload = json.loads(post_data) if post_data else {}
            
            page.on("request", handle_request)
            
            print("点击【门店】...")
            try:
                page.locator("text=门店").first.click(timeout=10000)
                time.sleep(2)
            except Exception as e:
                print(f"⚠️ 未找到【门店】按钮: {e}")
            
            print("点击【JD01 预订明细报表】...")
            try:
                page.locator("text=JD01 预订明细报表").first.click(timeout=10000)
                pms_utils.get_query_button(page)
            except Exception as e:
                print(f"⚠️ 未找到 JD01 报表入口: {e}")
            
            # 设置预订状态为全部
            click_booking_status_all(page)
            
            # 点击查询按钮
            print("点击【查询】按钮...")
            try:
                query_btn = pms_utils.get_query_button(page)
                query_btn.click(timeout=10000)
                time.sleep(5)
            except Exception as e:
                print(f"⚠️ 未找到查询按钮: {e}")
            
            # 保存接口地址到会话文件
            if jd01_api_url:
                session_info = {
                    'url': 'https://xingfeng.beyondh.com:8101',
                    'report_url': 'https://xingfeng.beyondh.com:8081/',
                    'jd01_api_url': jd01_api_url,
                    'jd01_payload': captured_payload,
                    'cookies': cookies,
                    'login_time': time.strftime("%Y-%m-%d %H:%M:%S")
                }
                
                with open(SESSION_FILE, 'w', encoding='utf-8') as f:
                    json.dump(session_info, f, indent=2, ensure_ascii=False)
                
                print(f"✅ JD01 接口地址已保存到 {SESSION_FILE}")
                if captured_payload:
                    print(f"✅ 捕获到 Payload: {json.dumps(captured_payload, indent=2, ensure_ascii=False)}")
            
            print("\n自动关闭浏览器...")
            browser.close()
            
    except Exception as e:
        print(f"❌ 捕获接口失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='JD01 报表抓取')
    parser.add_argument('--start-date', help='开始日期 (格式: YYYY-MM-DD)')
    parser.add_argument('--end-date', help='结束日期 (格式: YYYY-MM-DD)')
    args = parser.parse_args()
    
    fetch_jd01(start_date=args.start_date, end_date=args.end_date)
