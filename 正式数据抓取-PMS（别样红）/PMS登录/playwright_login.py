#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from playwright.sync_api import sync_playwright
import requests
import json
import time
import os


class PMSPlaywrightLogin:
    def __init__(self):
        self.base_url = "https://xingfeng.beyondh.com:8101"
        self.login_url = "https://xingfeng.beyondh.com:8101/login"
        self.report_url = "https://xingfeng.beyondh.com:8081/"
        self.session = requests.Session()
        self.cookies = {}

    def close_phone_popup(self, page):
        """关闭绑定手机号弹窗"""
        print("检查手机号绑定弹窗...")

        try:
            if page.locator("button:has-text('跳过')").count() > 0:
                page.locator("button:has-text('跳过')").first.click(timeout=3000)
                print("已点击跳过")
                time.sleep(1)
        except Exception:
            pass

        try:
            close_btn = page.locator(".ant-modal-close, .ant-modal-close-x")
            if close_btn.count() > 0:
                close_btn.first.click(timeout=3000, force=True)
                print("已点击右上角关闭")
                time.sleep(1)
        except Exception:
            pass

        try:
            page.keyboard.press("Escape")
            time.sleep(1)
        except Exception:
            pass

    def login_with_playwright(self, username, password):
        """使用 Playwright 无头模式登录"""
        print("\n=== 使用 Playwright 登录 ===")
        print(f"用户名: {username}")
        print("模式: headless 后台运行，不显示窗口")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,  # 无头模式，不显示窗口
                    slow_mo=300
                )

                page = browser.new_page()

                print("正在访问登录页面...")
                page.goto(self.login_url, timeout=60000)
                page.wait_for_load_state("networkidle")
                time.sleep(2)

                print("等待输入框加载...")
                page.wait_for_selector("#idUserNameInput", timeout=30000)

                print("填写用户名...")
                page.fill("#idUserNameInput", username)

                print("填写密码...")
                page.fill("#idPasswordInput", password)

                print("勾选协议...")
                try:
                    page.check("input[type='checkbox']")
                except Exception:
                    page.locator("input[type='checkbox']").click(force=True)

                print("点击登录按钮...")
                page.click("#idLoginButton")

                print("等待登录结果...")
                page.wait_for_load_state("networkidle")
                time.sleep(5)

                self.close_phone_popup(page)

                time.sleep(3)

                print("当前页面URL:", page.url)
                print("当前页面标题:", page.title())

                # page.screenshot(path="after_login.png", full_page=True)
                # print("登录后截图已保存: after_login.png")

                self.cookies = {
                    cookie["name"]: cookie["value"]
                    for cookie in page.context.cookies()
                }

                print(f"\n获取到 {len(self.cookies)} 个 Cookie:")
                for name, value in self.cookies.items():
                    print(f"  {name}: {value[:30]}...")

                required = ["SessionId", "Token", "OwnerId", "LoginOrgId"]

                if not all(k in self.cookies for k in required):
                    print("\n❌ 未真正登录成功，缺少关键 Cookie")
                    print("已有 Cookie:", list(self.cookies.keys()))
                    self.save_session()
                    # browser.close()# 关闭浏览器
                    return False

                print("\n✅ 真正登录成功!")
                self.save_session()

                # browser.close()   # 关闭浏览器
                return True

        except Exception as e:
            print(f"\n❌ 登录失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def save_session(self):
        """保存会话信息"""
        session_info = {
            "url": self.base_url,
            "report_url": self.report_url,
            "cookies": self.cookies,
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        with open("pms_session_playwright.json", "w", encoding="utf-8") as f:
            json.dump(session_info, f, indent=2, ensure_ascii=False)

        print("会话信息已保存到 pms_session_playwright.json")

    def fetch_data_with_requests(self):
        """使用 requests 抓取数据"""
        print("\n=== 使用 requests 抓取数据 ===")

        self.session.cookies.update(self.cookies)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        })

        data_endpoints = [
            "/API/Dashboard/GetData",
            "/API/Report/GetReportList",
            "/API/Home/VirtualLogin",
            "/API/Order/GetList",
            "/API/BusinessData/GetTodayData"
        ]

        for endpoint in data_endpoints:
            url = f"{self.base_url}{endpoint}"
            print(f"\n尝试: {url}")

            try:
                response = self.session.get(url, timeout=10)
                print(f"状态码: {response.status_code}")

                if response.status_code == 200:
                    try:
                        data = response.json()
                        print(f"响应成功，数据长度: {len(str(data))} 字符")

                        filename = f"data_{endpoint.replace('/', '_').replace(':', '_')}.json"

                        with open(filename, "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)

                        print(f"数据已保存到 {filename}")

                    except Exception:
                        print(f"响应成功，内容长度: {len(response.text)} 字符")

            except Exception as e:
                print(f"请求失败: {e}")

    def run(self, username, password):
        """主程序"""
        print("=" * 60)
        print("PMS（别样红）系统登录工具 - Playwright 无头模式")
        print("=" * 60)

        success = self.login_with_playwright(username, password)

        if success:
            self.fetch_data_with_requests()
        else:
            print("\n❌ 登录失败，请检查用户名密码、协议勾选或弹窗")


if __name__ == "__main__":
    pms = PMSPlaywrightLogin()

    username = os.environ.get("PMS_USERNAME", "").strip()
    password = os.environ.get("PMS_PASSWORD", "").strip()
    if not username or not password:
        raise SystemExit("PMS_USERNAME 或 PMS_PASSWORD 未配置")

    pms.run(username, password)
