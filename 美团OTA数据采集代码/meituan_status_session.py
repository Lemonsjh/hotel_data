"""只使用当前配置 Cookie 或与其匹配的助手会话，不继承旧账号 profile。"""
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from meituan_config import (
    MEITUAN_EB_COOKIE, MEITUAN_ME_COOKIE, MEITUAN_HIGHLIGHTS_COOKIE, MEITUAN_PUBLIC_WELFARE_COOKIE,
)
from meituan_page_capture import cookie_entries, load_login_session


def browser_cookies():
    return [cookie for header, url in (
        (MEITUAN_EB_COOKIE, "https://eb.meituan.com/"),
        (MEITUAN_ME_COOKIE, "https://me.meituan.com/"),
        (MEITUAN_HIGHLIGHTS_COOKIE, "https://tdc.meituan.com/"),
        (MEITUAN_PUBLIC_WELFARE_COOKIE, "https://gongyi.meituan.com/"),
    ) for cookie in cookie_entries(header, url)]


def launch_status_context(playwright):
    if not MEITUAN_ME_COOKIE or not MEITUAN_EB_COOKIE:
        raise RuntimeError("促销状态采集需要当前配置的 ME、EB Cookie。")
    browser = playwright.chromium.launch(channel="msedge", headless=True, chromium_sandbox=True)
    try:
        context = browser.new_context(
            locale="zh-CN", storage_state=load_login_session(MEITUAN_ME_COOKIE, MEITUAN_EB_COOKIE),
        )
        context.add_cookies(browser_cookies())
        return context
    except Exception:
        browser.close()
        raise


@contextmanager
def status_page(url):
    with sync_playwright() as playwright:
        context = launch_status_context(playwright)
        browser = context.browser
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            yield page
        finally:
            try:
                context.close()
            finally:
                browser.close()


def require_backend_session(cookie, name):
    if not cookie and not load_login_session(MEITUAN_ME_COOKIE, MEITUAN_EB_COOKIE):
        raise RuntimeError(f"缺少{name}会话，请在美团高级参数填写“{name} Cookie”并保存。")
