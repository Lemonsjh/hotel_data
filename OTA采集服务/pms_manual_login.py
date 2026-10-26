"""别样红可见 Edge 验证码登录；与现有采集器共用会话文件。"""
from __future__ import annotations

import importlib
import sys
import time

import runner


def ensure_idle() -> None:
    status = runner.load_json(runner.STATUS_PATH, {})
    if status.get("last_run_status") in {"starting", "running", "stopping"}:
        raise RuntimeError("当前采集仍在运行，请等待结束后再使用别样红手动登录")


def identity_settings(settings: dict) -> tuple:
    return tuple(settings.get(key) for key in ("pms", "hotel", "mysql"))


def load_pms_utils(settings: dict):
    directory = runner.script_path(settings, "pms", "fetch_main.py").parent
    sys.path.insert(0, str(directory / "scripts"))
    return importlib.import_module("pms_utils")


def save_verified(settings: dict, cookies: dict, name: str, config, utils) -> None:
    import platform_login as helper
    from config_transaction import check_writable, transaction

    ensure_idle()
    with transaction(runner.CONFIG_PATH):
        check_writable(runner.CONFIG_PATH)
        latest = runner.load_settings()
        if identity_settings(latest) != identity_settings(settings):
            raise RuntimeError("登录期间酒店或连接配置已变化，未覆盖旧会话；请重新打开登录助手")
        if helper.stop_path("pms").exists():
            raise helper.LoginCancelled("别样红手动登录已取消，原会话保留")
        # 人工登录以本次有效会话为准，覆盖 PMS 显示名称，不切换酒店 ID 或数据库。
        latest.setdefault("pms", {})["hotel_name"] = name
        runner.save_json(runner.CONFIG_PATH, latest)
        utils.write_session({
            "url": config.LOGIN_BASE_URL, "report_base_url": config.REPORT_BASE_URL,
            "service_api_base_url": config.SERVICE_API_BASE_URL,
            "hotel_id": runner.internal_hotel_id(settings, "pms"), "cookies": cookies,
            "hotel_name": name, "login_mode": "manual",
            "account_fingerprint": utils.account_fingerprint((settings.get("pms") or {}).get("username", "")),
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })


def run() -> int:
    import platform_login as helper
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    settings = runner.load_settings()
    if runner.active_pms_provider(settings) != "byh" or (settings.get("pms") or {}).get("login_mode") != "manual":
        raise RuntimeError("请先在别样红登录地址旁点击手动登录")
    ensure_idle()
    utils = load_pms_utils(settings)
    config = utils.pms_config
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(helper.profile_path("pms")), channel="msedge", headless=False,
            chromium_sandbox=True, no_viewport=True, locale="zh-CN", args=["--start-maximized"],
        )
        try:
            cookies = utils.browser_cookies(context)
            if not all(cookies.get(key) for key in utils.REQUIRED_COOKIES):
                saved = utils.read_session(require_cookies=True, quiet=True)
                if saved and utils.session_matches(saved, (settings.get("pms") or {}).get("username", "")):
                    context.add_cookies([{"name": key, "value": value, "url": config.LOGIN_BASE_URL.removesuffix("/login") + "/"}
                                         for key, value in saved["cookies"].items()])
                    cookies = utils.browser_cookies(context)
            had_session = all(cookies.get(key) for key in utils.REQUIRED_COOKIES)
            page = context.pages[0] if context.pages else context.new_page()
            url = config.LOGIN_BASE_URL.removesuffix("/login") + "/" if had_session else config.LOGIN_URL
            page.goto(url, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
            helper.write_status("pms", "waiting", "请手动完成验证码登录并选择酒店；核验成功后自动保存")
            deadline, checked = time.monotonic() + 900, None
            while time.monotonic() < deadline:
                if helper.stop_path("pms").exists():
                    raise helper.LoginCancelled("别样红手动登录已取消，原会话保留")
                if not context.pages:
                    raise helper.LoginCancelled("别样红登录窗口已关闭，原会话保留")
                page = context.pages[-1]
                page.wait_for_timeout(500)
                cookies = utils.browser_cookies(context)
                signature = tuple(cookies.get(key) for key in utils.REQUIRED_COOKIES)
                if not all(signature) or signature == checked:
                    continue
                checked = signature
                try:
                    name = utils.check_identity(cookies)
                except utils.ManualLoginRequired:
                    had_session = False
                    helper.write_status("pms", "waiting", "会话尚未登录，请完成验证码登录；原会话保留")
                    page.goto(config.LOGIN_URL, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT_MS)
                    continue
                save_verified(settings, cookies, name, config, utils)
                helper.write_status("pms", "success", f"别样红登录成功，会话已保存：{name}",
                                    completed_at=helper.now_text(), cookie_count=len(cookies))
                helper.show_success(context, "pms", len(cookies))
                if had_session:
                    helper.keep_open(context, "pms")
                return 0
            raise RuntimeError("别样红手动登录等待超时（15分钟），原会话保留")
        except PlaywrightError as exc:
            if not context.pages:
                raise helper.LoginCancelled("别样红登录窗口已关闭，原会话保留") from exc
            raise RuntimeError("别样红登录窗口已关闭或断开，原会话保留；请重新打开助手") from exc
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass


def login_control(settings: dict) -> str:
    import platform_login
    from panel_common import esc, get_path

    state = platform_login.read_status("pms")
    manual = get_path(settings, "pms.login_mode") == "manual"
    fallback = (
        "<button type='submit' class='secondary compact' name='login_mode' value='password' "
        "formaction='/platform-login/pms'>切回账号密码</button>"
    ) if manual else ""
    return (
        "<div class='field pms-login-field'><label for='pms-login-url'>登录地址</label>"
        "<div class='pms-login-input'>"
        f"<input id='pms-login-url' name='pms.login_base_url' type='url' value='{esc(get_path(settings, 'pms.login_base_url'))}'>"
        "<button type='submit' class='compact' formaction='/platform-login/pms'>手动登录</button></div>"
        f"<div class='pms-login-feedback' data-login-platform='pms' data-completed-at='{esc(state.get('completed_at') or state.get('updated_at') or '')}'>"
        f"<span class='login-state' data-login-state>{esc(state.get('message') or '尚未使用手动登录')}</span>"
        "<div class='config-actions'>"
        "<button type='submit' class='secondary compact' formaction='/platform-login/pms/cancel'>关闭助手</button>"
        f"{fallback}</div></div>"
        f"<small>当前模式：{'手动会话（失效后需重新手动登录）' if manual else '账号密码自动登录'}</small></div>"
    )
