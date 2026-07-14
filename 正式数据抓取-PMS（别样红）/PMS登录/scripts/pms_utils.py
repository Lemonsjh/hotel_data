# -*- coding: utf-8 -*-
"""
PMS 公共工具模块
提供会话管理、酒店名称获取等通用功能
"""

import json
import os
import re

SESSION_FILE = "../pms_session_playwright.json"


def load_session():
    """
    加载会话信息（包含 cookies 和酒店名称）
    
    Returns:
        dict or None: 会话信息，如果不存在返回 None
    """
    session_path = os.path.join(os.path.dirname(__file__), SESSION_FILE)
    
    if not os.path.exists(session_path):
        print("❌ 未找到会话文件，请先运行 login.py 登录")
        return None
    
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            session = json.load(f)
        
        cookies = session.get("cookies", {})
        required = ["SessionId", "Token", "OwnerId", "LoginOrgId"]
        
        if any(k not in cookies for k in required):
            print("❌ Cookie 不完整")
            return None
        
        print(f"✅ 登录态正常 ({len(cookies)} cookies)")
        return session
        
    except Exception as e:
        print(f"❌ 读取会话文件失败: {e}")
        return None


def get_session_cookies():
    """
    获取会话中的 cookies
    
    Returns:
        dict or None: cookies，如果获取失败返回 None
    """
    session = load_session()
    if session:
        return session.get("cookies", {})
    return None


def get_hotel_name_from_session(default_name="星锋电竞酒店（贵州大学花溪公园店）"):
    """
    从会话文件中获取酒店名称
    
    Args:
        default_name: 如果无法获取，返回默认酒店名称
    
    Returns:
        str: 酒店名称
    """
    session_path = os.path.join(os.path.dirname(__file__), SESSION_FILE)
    
    if not os.path.exists(session_path):
        print(f"⚠️ 会话文件不存在，使用默认酒店名称")
        return default_name
    
    try:
        with open(session_path, 'r', encoding='utf-8') as f:
            session_info = json.load(f)
        
        hotel_name = session_info.get('hotel_name')
        if hotel_name and hotel_name.strip():
            print(f"✅ 从会话获取酒店名称: {hotel_name}")
            return hotel_name.strip()
        else:
            print(f"⚠️ 会话文件中未找到酒店名称，使用默认值")
            return default_name
            
    except Exception as e:
        print(f"⚠️ 读取会话文件失败: {e}")
        return default_name


def add_cookies_to_context(context, cookies):
    """
    向 Playwright context 注入 cookies
    
    Args:
        context: Playwright browser context
        cookies: cookies 字典
    """
    cookie_list = [
        {
            "name": k,
            "value": v,
            "domain": "xingfeng.beyondh.com",
            "path": "/"
        }
        for k, v in cookies.items()
    ]
    context.add_cookies(cookie_list)


def get_query_button(page, timeout=30000):
    """等待报表查询按钮，兼容按钮文字中间带空格的页面版本。"""
    button = page.locator("button:visible").filter(
        has_text=re.compile(r"查\s*询|搜\s*索")
    ).first
    if button.count() == 0:
        button = page.locator(
            "button.ant-btn[style*='background-color: rgb(232, 80, 80)']:visible"
        ).first
    button.wait_for(state="visible", timeout=timeout)
    return button


def complete_org_ids(payload):
    """报表页面未带组织ID时，复用同一会话中已确认的酒店组织ID。"""
    result = dict(payload or {})
    if result.get("orgIds"):
        return result
    if result.get("orgId"):
        result["orgIds"] = [str(result["orgId"])]
        return result
    session_path = os.path.join(os.path.dirname(__file__), SESSION_FILE)
    if not os.path.exists(session_path):
        return result
    with open(session_path, "r", encoding="utf-8") as file:
        session = json.load(file)
    for key in ("jd01_payload", "jd04_payload", "kf11_payload"):
        org_id = str((session.get(key) or {}).get("orgId") or "").strip()
        if org_id:
            result["orgIds"] = [org_id]
            print(f"已补充酒店组织ID（来源: {key}）")
            break
    return result
