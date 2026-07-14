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

        self.jd01_api_url = None
        self.jd01_payload = None

        self.session = requests.Session()
        self.cookies = {}

    def close_phone_popup(self, page):
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

    def open_jd01_report(self, page):
        print("\n正在进入 PMS 报表中心...")
        page.goto(self.report_url, wait_until="networkidle", timeout=60000)
        time.sleep(5)

        print("报表页面URL:", page.url)
        print("报表页面标题:", page.title())

        print("\n点击顶部【门店】...")
        page.locator("text=门店").first.click(timeout=10000)
        time.sleep(2)

        print("点击【JD01 预订明细报表】...")
        page.locator("text=JD01 预订明细报表").first.click(timeout=10000)
        page.wait_for_load_state("networkidle")
        time.sleep(5)

        print("当前报表URL:", page.url)
        print("当前报表标题:", page.title())

    def capture_jd01_api_url(self, page):
        print("\n开始监听 JD01 接口地址...")

        def handle_request(request):
            if "preOrderDetailReport" in request.url:
                self.jd01_api_url = request.url
                print("✅ 捕获到 JD01 接口地址:", request.url)

        page.on("request", handle_request)

    def click_booking_status_all(self, page):
        """点击预订状态下拉框，并选择第一项：全部"""
        print("设置【预订状态】为【全部】...")

        labels = page.locator("label.fiveChar")
        count = labels.count()

        clicked = False

        for i in range(count):
            text = labels.nth(i).inner_text().strip().replace("：", "")

            if text == "预订状态":
                print("找到【预订状态】字段，下标:", i)

                box = labels.nth(i).bounding_box()

                if not box:
                    raise Exception("找到【预订状态】，但无法获取坐标")

                # 点击“预订状态”右侧的下拉框
                page.mouse.click(
                    box["x"] + box["width"] + 120,
                    box["y"] + box["height"] / 2
                )

                clicked = True
                break

        if not clicked:
            raise Exception("没有找到【预订状态】字段")

        time.sleep(1)

        # 当前打开的下拉框，第一项就是“全部”
        dropdown = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").last
        dropdown_box = dropdown.bounding_box()

        if not dropdown_box:
            raise Exception("没有找到已打开的预订状态下拉框")

        page.mouse.click(
            dropdown_box["x"] + 30,
            dropdown_box["y"] + 20
        )

        time.sleep(1)

    def click_query_button(self, page):
        """点击查询按钮"""
        print("点击【查询】按钮...")

        query_btn = page.locator(
            "button.ant-btn[style*='background-color: rgb(232, 80, 80)']"
        ).first

        query_btn.click(timeout=10000)

    def set_status_all_and_capture_payload(self, page):
        """设置预订状态为全部，点击查询，并捕获 Payload 和返回数据"""
        print("\n开始设置【预订状态】为【全部】，并点击查询...")

        try:
            with page.expect_response(
                lambda r: "preOrderDetailReport" in r.url and r.status == 200,
                timeout=30000
            ) as response_info:

                self.click_booking_status_all(page)
                self.click_query_button(page)

            response = response_info.value
            request = response.request

            self.jd01_api_url = response.url

            print("接口URL:", response.url)
            print("接口状态:", response.status)

            try:
                self.jd01_payload = request.post_data_json
            except Exception:
                post_data = request.post_data
                self.jd01_payload = json.loads(post_data) if post_data else {}

            print("\n捕获到 Payload:")
            print(json.dumps(self.jd01_payload, indent=2, ensure_ascii=False))

            # with open("JD01_payload.json", "w", encoding="utf-8") as f:
            #     json.dump(self.jd01_payload, f, indent=2, ensure_ascii=False)

            # data = response.json()

            # with open("JD01_from_playwright_response.json", "w", encoding="utf-8") as f:
            #     json.dump(data, f, indent=2, ensure_ascii=False)

            # print("✅ 页面查询返回数据已保存: JD01_from_playwright_response.json")
            # print("✅ Payload 已保存: JD01_payload.json")

            return True

        except Exception as e:
            print("❌ 自动选择全部并查询失败:", e)
            return False

    def fetch_jd01_with_requests(self):
        print("\n=== 使用 requests 直接请求 JD01 接口 ===")

        if not self.jd01_api_url:
            print("❌ 没有捕获到 JD01 接口地址")
            return

        if not self.jd01_payload:
            print("❌ 没有捕获到 JD01 Payload")
            return

        self.session.cookies.update(self.cookies)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://xingfeng.beyondh.com:8081",
            "Referer": "https://xingfeng.beyondh.com:8081/",
        })

        print("请求接口:", self.jd01_api_url)

        response = self.session.post(
            self.jd01_api_url,
            json=self.jd01_payload,
            timeout=30
        )

        print("状态码:", response.status_code)
        print("响应前500字符:")
        print(response.text[:500])

        if response.status_code == 200:
            try:
                data = response.json()

                with open("JD01_preOrderDetailReport.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                print("✅ requests 抓取成功，已保存: JD01_preOrderDetailReport.json")

            except Exception as e:
                print("JSON 解析失败:", e)
        else:
            print("❌ requests 请求失败")

    def login_with_playwright(self, username, password):
        print("\n=== 使用 Playwright 登录 ===")
        print(f"用户名: {username}")
        print("模式: 显示浏览器窗口")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,
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
                    print("已有Cookie:", list(self.cookies.keys()))
                    self.save_session()

                    input("\n登录失败，按回车关闭浏览器...")
                    browser.close()
                    return False

                print("\n✅ 真正登录成功!")
                self.save_session()

                self.open_jd01_report(page)
                self.capture_jd01_api_url(page)

                ok = self.set_status_all_and_capture_payload(page)

                if ok:
                    self.fetch_jd01_with_requests()

                input("\n浏览器保持打开，按回车退出并关闭浏览器...")
                browser.close()
                return True

        except Exception as e:
            print(f"\n❌ 登录失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def save_session(self):
        session_info = {
            "url": self.base_url,
            "report_url": self.report_url,
            "jd01_api_url": self.jd01_api_url,
            "cookies": self.cookies,
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        with open("pms_session_playwright.json", "w", encoding="utf-8") as f:
            json.dump(session_info, f, indent=2, ensure_ascii=False)

        print("会话信息已保存到 pms_session_playwright.json")

    def run(self, username, password):
        print("=" * 60)
        print("PMS 登录 + JD01 预订状态全部 + 自动抓接口数据")
        print("=" * 60)

        success = self.login_with_playwright(username, password)

        if success:
            print("\n✅ 全流程完成")
        else:
            print("\n❌ 失败，请检查登录或页面元素")


if __name__ == "__main__":
    pms = PMSPlaywrightLogin()

    username = os.environ.get("PMS_USERNAME", "").strip()
    password = os.environ.get("PMS_PASSWORD", "").strip()
    if not username or not password:
        raise SystemExit("PMS_USERNAME 或 PMS_PASSWORD 未配置")

    pms.run(username, password)
