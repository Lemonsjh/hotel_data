# -*- coding: utf-8 -*-
"""PMS 会话、Cookie 和报表公共工具。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pms_config import (
    ACTION_TIMEOUT_MS,
    API_TIMEOUT_SECONDS,
    NAVIGATION_TIMEOUT_MS,
    REPORT_BASE_URL,
    SERVICE_API_BASE_URL,
    origin,
    report_url,
)


SESSION_PATH = Path(__file__).resolve().parents[1] / "pms_session_playwright.json"
# 保留旧名称，便于尚未迁移的调用方继续使用同一个绝对路径。
SESSION_FILE = SESSION_PATH
REQUIRED_COOKIES = ("SessionId", "Token", "OwnerId", "LoginOrgId")


def account_fingerprint(username: str) -> str:
    normalized = str(username or "").strip().lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest() if normalized else ""


def _backup_invalid_session(reason: Exception) -> None:
    if not SESSION_PATH.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = SESSION_PATH.with_name(f"{SESSION_PATH.stem}.corrupt-{timestamp}.json")
    try:
        os.replace(SESSION_PATH, backup_path)
        print(f"PMS 会话文件无效，已备份到 {backup_path.name}: {reason}")
    except OSError as backup_error:
        print(f"PMS 会话文件无效且无法备份: {backup_error}")


def read_session(*, require_cookies: bool = False, quiet: bool = False) -> dict[str, Any] | None:
    """读取会话；内容损坏时备份原文件并返回 ``None``。"""
    if not SESSION_PATH.exists():
        if not quiet:
            print("未找到 PMS 会话文件，请先运行 login.py 登录")
        return None
    try:
        with SESSION_PATH.open("r", encoding="utf-8") as file:
            session = json.load(file)
        if not isinstance(session, dict):
            raise ValueError("会话根节点必须是 JSON 对象")
        if require_cookies:
            cookies = session.get("cookies")
            if not isinstance(cookies, dict) or any(not cookies.get(key) for key in REQUIRED_COOKIES):
                raise ValueError("关键 Cookie 不完整")
        return session
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        _backup_invalid_session(exc)
        return None


def write_session(session: dict[str, Any]) -> None:
    """在同一目录原子写入会话文件，避免中断后留下半个 JSON。"""
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=SESSION_PATH.parent,
            prefix=f".{SESSION_PATH.stem}-",
            suffix=".tmp",
            delete=False,
        ) as file:
            json.dump(session, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
            temp_path = Path(file.name)
        os.replace(temp_path, SESSION_PATH)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def update_session(**values: Any) -> bool:
    """合并并原子保存会话字段；会话不存在或损坏时返回 ``False``。"""
    session = read_session(quiet=True)
    if session is None:
        return False
    session.update(values)
    write_session(session)
    return True


def delete_session() -> bool:
    """删除当前会话，返回删除前文件是否存在。"""
    try:
        SESSION_PATH.unlink()
        return True
    except FileNotFoundError:
        return False


def load_session() -> dict[str, Any] | None:
    """加载并校验关键 Cookie。"""
    session = read_session(require_cookies=True)
    if session:
        print(f"登录态正常 ({len(session['cookies'])} cookies)")
    return session


def get_session_cookies() -> dict[str, str] | None:
    session = load_session()
    return session.get("cookies", {}) if session else None


def get_hotel_name_from_session(default_name: str = "") -> str:
    session = read_session(quiet=True)
    if not session:
        return default_name
    hotel_name = str(session.get("hotel_name") or "").strip()
    return hotel_name or default_name


def fetch_hotel_name(cookies: dict[str, str]) -> str:
    """从 PMS 登录校验接口读取当前组织名称。"""
    import requests

    url = f"{SERVICE_API_BASE_URL}/api/v1/fox/user/checkLogin"
    try:
        response = requests.post(
            url,
            json={},
            cookies=cookies,
            headers=request_headers(report_url()),
            timeout=API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        name = str(((payload.get("data") or {}).get("loginOrgName")) or "").strip()
        return name if name and name.lower() != "todo" else ""
    except (requests.RequestException, ValueError, TypeError):
        return ""


def ensure_hotel_name(*, force: bool = False) -> str:
    """确保会话包含 PMS 当前组织名称，并返回该名称。"""
    session = read_session(require_cookies=True, quiet=True)
    if not session:
        return ""
    current = str(session.get("hotel_name") or "").strip()
    if current and not force:
        return current
    name = fetch_hotel_name(session.get("cookies") or {})
    if name and name != current:
        update_session(hotel_name=name)
        print(f"已从 PMS 登录接口获取酒店名称: {name}")
    return name or current or os.environ.get("PMS_HOTEL_NAME", "").strip()


def add_cookies_to_context(context, cookies: dict[str, str]) -> None:
    cookie_list = [
        {"name": key, "value": value, "domain": "xingfeng.beyondh.com", "path": "/"}
        for key, value in cookies.items()
    ]
    context.add_cookies(cookie_list)


def get_query_button(page, timeout: int = ACTION_TIMEOUT_MS):
    """等待报表查询按钮，兼容按钮文字中间带空格的页面版本。"""
    button = page.locator("button:visible").filter(has_text=re.compile(r"查\s*询|搜\s*索")).first
    if button.count() == 0:
        button = page.locator(
            "button.ant-btn[style*='background-color: rgb(232, 80, 80)']:visible"
        ).first
    button.wait_for(state="visible", timeout=timeout)
    return button


def request_headers(referer: str) -> dict[str, str]:
    """构造报表 API 的通用请求头。"""
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": origin(referer),
        "Referer": referer,
    }


def complete_org_ids(payload: dict[str, Any] | None) -> dict[str, Any]:
    """报表请求缺少组织 ID 时，从同一会话的已捕获请求中补充。"""
    result = dict(payload or {})
    if result.get("orgIds"):
        return result
    if result.get("orgId"):
        result["orgIds"] = [str(result["orgId"])]
        return result
    session = read_session(quiet=True) or {}
    for key in ("jd01_payload", "jd04_payload", "kf11_payload"):
        org_id = str((session.get(key) or {}).get("orgId") or "").strip()
        if org_id:
            result["orgIds"] = [org_id]
            print(f"已补充酒店组织 ID（来源: {key}）")
            break
    return result
