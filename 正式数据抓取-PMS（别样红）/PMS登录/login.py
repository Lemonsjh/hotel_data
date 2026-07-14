#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMS（别样红）登录脚本 - 只负责登录并保存会话信息
"""

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
import json
import time
import os

SESSION_FILE = "pms_session_playwright.json"

# 关键 Cookie 列表 - 登录成功必须获取到这些
REQUIRED_COOKIES = [
    'SessionId',
    'Token',
    'OwnerId',
    'LoginOrgId'
]


def extract_hotel_name(page):
    """从页面上提取酒店名称"""
    print("\n尝试获取酒店名称...")
    
    # 尝试多种方式获取酒店名称
    selectors = [
        '.ant-layout-header .logo',
        '.ant-layout-header span',
        '.hotel-name',
        '.org-name',
        'span:has-text("酒店")',
        '//span[contains(text(),"酒店")]',
        '.user-info span',
        '.org-selector',
        '#orgName',
        'div.org-name',
        'span.org-name'
    ]
    
    for selector in selectors:
        try:
            element = page.locator(selector)
            if element.is_visible(timeout=2000):
                hotel_name = element.text_content(timeout=2000).strip()
                if hotel_name and len(hotel_name) > 2:
                    print(f"✅ 获取酒店名称: {hotel_name}")
                    return hotel_name
        except:
            continue
    
    # 尝试通过页面标题获取
    try:
        title = page.title()
        if title and "酒店" in title:
            # 提取标题中的酒店名称
            parts = title.split("-")
            for part in parts:
                if "酒店" in part:
                    hotel_name = part.strip()
                    print(f"✅ 从标题获取酒店名称: {hotel_name}")
                    return hotel_name
    except:
        pass
    
    print("⚠️ 未能自动获取酒店名称，将使用默认值")
    return None


def login(username, password):
    """使用 Playwright 登录并保存业务 Cookie"""
    print(f"\n=== 使用 Playwright 登录 ===")
    print(f"用户名: {username}")
    print("模式: 后台运行")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, slow_mo=300)   #headless=True 隐藏浏览器窗口
            page = browser.new_page()
            
            print("正在访问登录页面...")
            page.goto("https://xingfeng.beyondh.com:8101/login", timeout=60000)
            page.wait_for_load_state('networkidle')
            time.sleep(2)
            
            # 检查并勾选用户协议
            print("\n检查用户协议...")
            agreement_selectors = [
                '#agreement',
                'input[type="checkbox"][name*="agree"]',
                'input[type="checkbox"][id*="agree"]',
                '.ant-checkbox-input',
                'label.ant-checkbox-wrapper'
            ]
            
            for selector in agreement_selectors:
                try:
                    if page.locator(selector).is_visible(timeout=3000):
                        if not page.locator(selector).is_checked():
                            print(f"勾选用户协议: {selector}")
                            page.click(selector)
                        break
                except:
                    continue
            
            print("等待输入框加载...")
            page.wait_for_selector('#idUserNameInput', timeout=30000)
            
            print("填写用户名...")
            page.fill('#idUserNameInput', username)
            page.locator('#idUserNameInput').press("Tab")
            page.wait_for_selector('.ant-select-selection-item[title]', timeout=15000)

            # 显式确认页面当前推荐班次，避免异步加载尚未写入表单状态。
            shift_select = page.locator('.ant-select').nth(0)
            shift_name = shift_select.locator('.ant-select-selection-item').get_attribute('title')
            shift_select.click()
            shift_options = page.locator('.ant-select-item-option:visible')
            selected = False
            for index in range(shift_options.count()):
                option = shift_options.nth(index)
                if option.get_attribute('title') == shift_name:
                    option.click(force=True)
                    selected = True
                    break
            if not selected and shift_options.count():
                shift_options.last.click(force=True)
            
            print("填写密码...")
            page.fill('#idPasswordInput', password)
            
            print("点击登录按钮...")
            with page.expect_response(lambda response: '/API/Home/Login' in response.url, timeout=30000) as login_info:
                page.click('#idLoginButton')
            login_payload = login_info.value.json()
            login_code = login_payload.get("Code")
            if str(login_code) != "0":
                print(f"❌ 登录接口返回失败: {login_payload.get('Message') or login_code}")
                browser.close()
                return False
            print(f"✅ 登录接口返回成功: {login_payload.get('Message') or 'Code=0'}")
            
            # 检查并关闭手机号绑定弹窗
            print("\n检查弹窗...")
            try:
                modal = page.locator('.ant-modal:visible')
                modal.wait_for(state="visible", timeout=10000)
                close_button = modal.locator('.ant-modal-close')
                if close_button.count():
                    print("关闭手机号绑定弹窗")
                    close_button.evaluate("(element) => element.click()")
                else:
                    buttons = modal.locator('button')
                    for index in range(buttons.count()):
                        text = "".join(buttons.nth(index).inner_text().split())
                        if text in {"跳过", "以后再说", "取消"}:
                            buttons.nth(index).evaluate("(element) => element.click()")
                            break
            except PlaywrightTimeoutError:
                pass
            except Exception as popup_error:
                # 弹窗不影响登录 Cookie，关闭失败时继续验证实际登录状态。
                print(f"⚠️ 关闭登录弹窗失败，将继续检查登录状态: {popup_error}")

            print("等待进入系统...")
            try:
                page.wait_for_url(lambda url: not str(url).rstrip("/").endswith("/login"), timeout=30000)
            except PlaywrightTimeoutError:
                print(f"⚠️ 登录后页面未自动跳转，将继续检查登录 Cookie: {page.url}")

            # 等待页面稳定后获取 Cookie
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
            except PlaywrightTimeoutError:
                pass
            time.sleep(2)

            cookies = {cookie['name']: cookie['value'] for cookie in page.context.cookies()}
            print(f"\n获取到 {len(cookies)} 个 Cookie")

            # 尝试获取酒店名称（从页面上提取）
            hotel_name = extract_hotel_name(page)

            # 检查关键 Cookie
            print("\n检查关键 Cookie:")
            missing_cookies = []
            for cookie_name in REQUIRED_COOKIES:
                if cookie_name in cookies:
                    print(f"  ✅ {cookie_name}: {cookies[cookie_name][:30]}...")
                else:
                    print(f"  ❌ {cookie_name}: 缺失")
                    missing_cookies.append(cookie_name)

            browser.close()

            if missing_cookies:
                print("\n❌ 登录失败")
                print(f"   缺失关键 Cookie: {', '.join(missing_cookies)}")
                return False

            print("\n✅ 登录成功!")
            save_session(cookies, hotel_name)
            return True
                
    except Exception as e:
        print(f"\n❌ 登录失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_session(cookies, hotel_name=None):
    """保存会话信息（包含酒店名称）"""
    session_info = {
        'url': 'https://xingfeng.beyondh.com:8101',
        'cookies': cookies,
        'hotel_name': hotel_name,
        'login_time': time.strftime('%Y-%m-%d %H:%M:%S')
    }
    
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(session_info, f, indent=2, ensure_ascii=False)
    print(f"会话信息已保存到 {SESSION_FILE}")
    if hotel_name:
        print(f"酒店名称: {hotel_name}")


def load_session():
    """加载会话信息"""
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def main():
    print("=" * 60)
    print("PMS（别样红）系统登录工具")
    print("=" * 60)
    
    username = os.environ.get("PMS_USERNAME", "").strip()
    password = os.environ.get("PMS_PASSWORD", "").strip()
    if not username or not password:
        raise SystemExit("PMS_USERNAME 或 PMS_PASSWORD 未配置")
    
    raise SystemExit(0 if login(username, password) else 1)


if __name__ == "__main__":
    main()
