from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import runner


PLATFORMS = {
    "meituan": {
        "label": "美团",
        "url": "https://me.meituan.com/ebooking/merchant/comment-manage-react",
        # 旧的 /ebooking/hotel/dataCenter 已下线，会显示 404。
        # 工作台入口会根据当前账号跳转到可用的 EB 页面。
        "eb_url": "https://eb.meituan.com/ebooking/new-workbench/index.html",
    },
    "ctrip": {
        "label": "携程",
        "url": "https://ebooking.ctrip.com/home/mainland",
    },
}
STATE_DIR = runner.ROOT / "state"
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
KERNEL32.OpenProcess.restype = ctypes.c_void_p
KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]


class LoginCancelled(RuntimeError):
    pass


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def status_path(platform: str) -> Path:
    return STATE_DIR / f"platform_login_{platform}.json"


def stop_path(platform: str) -> Path:
    return STATE_DIR / f"platform_login_{platform}.stop"


def profile_path(platform: str) -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local / "HotelAgent" / "browser_profiles" / platform


def require_platform(platform: str) -> dict[str, str]:
    if platform not in PLATFORMS:
        raise ValueError(f"不支持的平台：{platform}")
    return PLATFORMS[platform]


def read_status(platform: str) -> dict[str, Any]:
    require_platform(platform)
    return runner.load_json(
        status_path(platform),
        {"platform": platform, "status": "never", "message": "尚未使用登录助手"},
    )


def write_status(platform: str, status: str, message: str, **fields: Any) -> None:
    data = read_status(platform)
    data.update(
        platform=platform,
        status=status,
        message=message,
        updated_at=now_text(),
        **fields,
    )
    runner.save_json(status_path(platform), data)


def process_alive(pid: Any) -> bool:
    try:
        handle = KERNEL32.OpenProcess(0x1000, 0, int(pid))
    except (TypeError, ValueError):
        return False
    if not handle:
        return False
    KERNEL32.CloseHandle(handle)
    return True


def start(platform: str, settings: dict[str, Any]) -> int:
    info = require_platform(platform)
    old = read_status(platform)
    old_pid = old.get("pid")
    if process_alive(old_pid):
        stop_path(platform).parent.mkdir(parents=True, exist_ok=True)
        stop_path(platform).write_text(now_text(), encoding="utf-8")
        for _ in range(20):
            if not process_alive(old_pid):
                break
            time.sleep(0.25)
        if process_alive(old_pid):
            raise RuntimeError(f"{info['label']}登录窗口仍在运行，请先关闭窗口")

    stop_path(platform).unlink(missing_ok=True)
    write_status(platform, "starting", f"正在打开{info['label']}登录窗口", pid=0)
    command = [str(runner.python_path(settings)), str(Path(__file__).resolve()), platform]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(runner.ROOT),
            env=runner.build_env(settings),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        write_status(platform, "failed", f"登录窗口启动失败：{exc}", pid=0)
        raise
    write_status(platform, "starting", f"正在打开{info['label']}登录窗口", pid=process.pid)
    return process.pid


def cookie_names(context: Any) -> set[str]:
    return {str(item.get("name") or "") for item in context.cookies()}


def cookie_header(context: Any, url: str) -> str:
    selected: dict[str, str] = {}
    cookies = sorted(context.cookies([url]), key=lambda item: len(str(item.get("domain") or "")))
    for item in cookies:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        if name:
            selected[name] = value
    return "; ".join(f"{name}={value}" for name, value in sorted(selected.items()))


def wait_for_auth(context: Any, platform: str, required: set[str], message: str) -> Any:
    write_status(platform, "waiting", message)
    browser = context.browser
    while browser and browser.is_connected():
        if stop_path(platform).exists():
            raise LoginCancelled("登录已取消")
        try:
            pages = context.pages
            if required.issubset(cookie_names(context)) and pages:
                return pages[-1]
            time.sleep(0.5)
        except PlaywrightError:
            time.sleep(0.5)
    raise LoginCancelled("登录窗口已关闭")


def navigate(page: Any, url: str) -> None:
    response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if response is not None and response.status >= 400:
        raise RuntimeError(f"登录页面访问失败：HTTP {response.status} {url}")


def save_cookies(platform: str, context: Any) -> int:
    settings = runner.load_settings()
    section = settings.setdefault(platform, {})
    if platform == "meituan":
        section["me_cookie"] = cookie_header(context, PLATFORMS[platform]["url"])
        section["eb_cookie"] = cookie_header(context, PLATFORMS[platform]["eb_url"])
        count = len(cookie_names(context))
    else:
        section["cookie"] = cookie_header(context, PLATFORMS[platform]["url"])
        count = len(cookie_names(context))
    runner.save_json(runner.CONFIG_PATH, settings)
    return count


def show_success(context: Any, platform: str, count: int) -> None:
    label = PLATFORMS[platform]["label"]
    text = f"{label}登录成功：已保存 {count} 个 Cookie；关闭窗口即可"
    for _ in range(10):
        try:
            pages = context.pages
            if pages:
                pages[-1].evaluate(
                    """text => {
                      const old = document.getElementById('hotel-agent-login-status');
                      if (old) old.remove();
                      const box = document.createElement('div');
                      box.id = 'hotel-agent-login-status';
                      box.textContent = text;
                      Object.assign(box.style, {
                        position:'fixed', top:'0', left:'0', right:'0', zIndex:'2147483647',
                        padding:'14px', background:'#087f5b', color:'#fff',
                        fontSize:'16px', fontWeight:'700', textAlign:'center'
                      });
                      document.body.appendChild(box);
                    }""",
                    text,
                )
                return
        except PlaywrightError:
            pass
        time.sleep(0.5)


def keep_open(context: Any, platform: str) -> None:
    browser = context.browser
    while browser and browser.is_connected() and not stop_path(platform).exists():
        time.sleep(0.5)


def run(platform: str) -> int:
    info = require_platform(platform)
    stop_path(platform).unlink(missing_ok=True)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path(platform)),
                channel="msedge",
                headless=False,
                chromium_sandbox=True,
                no_viewport=True,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=["--start-maximized"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            navigate(page, info["url"])

            if platform == "meituan":
                page = wait_for_auth(context, platform, {"mebsid"}, "请在Edge中完成美团登录")
                write_status(platform, "syncing", "美团主站已登录，正在同步EB会话")
                navigate(page, info["eb_url"])
                page = wait_for_auth(context, platform, {"mebsid", "ebbsid"}, "请完成美团EB登录")
            else:
                page = wait_for_auth(
                    context,
                    platform,
                    {"usertoken", "usersign"},
                    "请在Edge中完成携程登录",
                )

            count = save_cookies(platform, context)
            write_status(
                platform,
                "success",
                f"{info['label']}Cookie已自动保存",
                cookie_count=count,
                completed_at=now_text(),
            )
            show_success(context, platform, count)
            keep_open(context, platform)
            try:
                context.close()
            except PlaywrightError:
                pass
        return 0
    except LoginCancelled as exc:
        write_status(platform, "cancelled", str(exc))
        return 1
    except Exception as exc:
        write_status(platform, "failed", f"{type(exc).__name__}: {exc}")
        return 2
    finally:
        stop_path(platform).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="OTA可见Edge登录助手")
    parser.add_argument("platform", choices=sorted(PLATFORMS))
    return run(parser.parse_args().platform)


if __name__ == "__main__":
    raise SystemExit(main())
