from __future__ import annotations

import os
import json
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


_LOCK_DEPTH = 0


def profile_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "browser_profiles" / "meituan"


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


@contextmanager
def browser_profile_lock(timeout_seconds: int = 120) -> Any:
    global _LOCK_DEPTH
    if _LOCK_DEPTH:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    path = profile_path() / ".capture.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode())
            break
        except FileExistsError:
            try:
                owner = int(path.read_text(encoding="ascii").strip() or "0")
                if not process_alive(owner):
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            except (ValueError, OSError):
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("Meituan browser profile is busy; retry after the active task finishes")
            time.sleep(1)
    try:
        _LOCK_DEPTH = 1
        yield
    finally:
        _LOCK_DEPTH = 0
        os.close(descriptor)
        path.unlink(missing_ok=True)


def cookie_entries(cookie_header: str, url: str) -> list[dict[str, str]]:
    parsed = SimpleCookie()
    parsed.load(cookie_header)
    return [
        {"name": name, "value": value.value, "url": url}
        for name, value in parsed.items()
    ]


def page_access_issue(page: Any) -> str:
    """Return a human-actionable reason when a page is blocked before data can load."""
    if any(token in page.url.lower() for token in ("/login", "verify", "captcha")):
        return "登录会话无效或需要安全验证"
    texts = []
    for frame in page.frames:
        try:
            texts.append(frame.locator("body").inner_text(timeout=500))
        except Exception:
            continue
    body = "\n".join(texts)
    for marker in ("当前操作异常", "安全验证", "请完成验证", "请重新登录", "暂无权限", "无权限访问"):
        if marker in body:
            return marker
    return ""


def capture_json_responses(
    page_url: str,
    endpoints: dict[str, str],
    timeout_seconds: int = 45,
    cookies_by_url: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Open a stable Meituan page and collect its own JSON responses by path."""
    captured: dict[str, Any] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        context = browser.new_context(locale="zh-CN")
        cookies = [
            cookie
            for url, header in (cookies_by_url or {}).items()
            for cookie in cookie_entries(header, url)
        ]
        if cookies:
            context.add_cookies(cookies)
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response: Any) -> None:
            if response.status != 200:
                return
            path = urlparse(response.url).path
            for name, endpoint in endpoints.items():
                if path.endswith(endpoint):
                    captured[name] = response

        page.on("response", on_response)
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(timeout_seconds * 2):
                if len(captured) == len(endpoints):
                    break
                page.wait_for_timeout(500)
            missing = sorted(set(endpoints) - set(captured))
            if missing:
                raise RuntimeError(f"Page did not return expected responses: {', '.join(missing)}")
            return {name: response.json() for name, response in captured.items()}
        finally:
            page.remove_listener("response", on_response)
            context.close()
            browser.close()


def capture_json_response_with_payload(
    page_url: str,
    endpoint: str,
    payload_updates: dict[str, Any],
    cookies_by_url: dict[str, str] | None = None,
) -> Any:
    """Replace the JSON body of a page-owned request and return its response."""
    responses: list[Any] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        context = browser.new_context(locale="zh-CN")
        cookies = [
            cookie
            for url, header in (cookies_by_url or {}).items()
            for cookie in cookie_entries(header, url)
        ]
        if cookies:
            context.add_cookies(cookies)
        page = context.pages[0] if context.pages else context.new_page()

        def update_request(route: Any) -> None:
            payload = json.loads(route.request.post_data or "{}")
            payload.update(payload_updates)
            route.continue_(post_data=json.dumps(payload, ensure_ascii=False))

        def on_response(response: Any) -> None:
            if response.status == 200 and urlparse(response.url).path.endswith(endpoint):
                responses.append(response)

        context.route(f"**{endpoint}*", update_request)
        page.on("response", on_response)
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(90):
                if responses:
                    break
                page.wait_for_timeout(500)
            if not responses:
                raise RuntimeError(f"Page did not return expected response: {endpoint}")
            return responses[-1].json()
        finally:
            page.remove_listener("response", on_response)
            context.unroute(f"**{endpoint}*", update_request)
            context.close()
            browser.close()
