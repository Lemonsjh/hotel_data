# -*- coding: utf-8 -*-
"""
PMS 会话管理模块
用于从会话文件中获取酒店名称等信息
"""

import json
import os

SESSION_FILE = "../../pms_session_playwright.json"


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
        print(f"⚠️ 会话文件不存在: {session_path}")
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


def get_session_info():
    """
    获取完整的会话信息
    
    Returns:
        dict: 会话信息（包含 cookies, hotel_name 等）
    """
    session_path = os.path.join(os.path.dirname(__file__), SESSION_FILE)
    
    if not os.path.exists(session_path):
        return None
    
    try:
        with open(session_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 读取会话文件失败: {e}")
        return None