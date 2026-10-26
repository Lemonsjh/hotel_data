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
from urllib.parse import urlsplit

import pms_config
import requests

from pms_config import (
    ACTION_TIMEOUT_MS,
    API_TIMEOUT_SECONDS,
    NAVIGATION_TIMEOUT_MS,
    REPORT_BASE_URL,
    SERVICE_API_BASE_URL,
    origin,
    report_url,
)


def session_path() -> Path:
    current = Path(__file__).resolve().parents[1] / "pms_session_playwright.json"
    alternate = {"别样红PMS数据采集代码": "正式数据抓取-PMS（别样红）",
                 "正式数据抓取-PMS（别样红）": "别样红PMS数据采集代码"}.get(current.parents[1].name)
    if alternate:
        other = current.parents[2] / alternate / "PMS登录" / current.name
        if not current.exists() and other.is_file():
            return other
    return current


SESSION_PATH = session_path()
# 保留旧名称，便于尚未迁移的调用方继续使用同一个绝对路径。
SESSION_FILE = SESSION_PATH
REQUIRED_COOKIES = ("SessionId", "Token", "OwnerId", "LoginOrgId")


class SessionCheckError(RuntimeError):
    pass


class ManualLoginRequired(SessionCheckError):
    pass


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


def fetch_hotel_name(cookies: dict[str, str], *, strict: bool = False) -> str:
    """读取组织名称；人工会话须严格核验，旧调用仍在失败时返回空名称。"""
    if strict and any(not cookies.get(key) for key in REQUIRED_COOKIES):
        raise ManualLoginRequired("别样红会话不完整，请在配置页点击手动登录")
    url = f"{SERVICE_API_BASE_URL}/api/v1/fox/user/checkLogin"
    try:
        response = requests.post(
            url,
            json={},
            cookies=cookies,
            headers=request_headers(report_url()),
            timeout=API_TIMEOUT_SECONDS,
            allow_redirects=not strict,
        )
        if strict:
            redirected = response.status_code in (301, 302, 303, 307, 308) and any(
                word in response.headers.get("Location", "").lower() for word in ("login", "passport"))
            if response.status_code == 401 or redirected:
                raise ManualLoginRequired("别样红会话已失效，请在配置页重新手动登录")
            if response.status_code != 200:
                raise SessionCheckError(f"别样红会话核验返回 HTTP {response.status_code}，原会话保留")
        else:
            response.raise_for_status()
        payload = response.json()
        if strict and (not isinstance(payload, dict) or payload.get("success") is False or any(
            key in payload and str(payload[key]) not in {"0", "200"} for key in ("code", "Code")
        )):
            raise SessionCheckError("别样红会话核验被业务接口拒绝，原会话保留；请检查登录或权限")
        data = payload.get("data") if isinstance(payload, dict) else None
        name = str(data.get("loginOrgName") or "").strip() if isinstance(data, dict) else ""
        if strict and (not name or name.lower() == "todo"):
            raise SessionCheckError("别样红会话尚未确认有效酒店，原会话保留；请完成登录和酒店选择")
        return name if name and name.lower() != "todo" else ""
    except requests.RequestException as exc:
        if strict:
            raise SessionCheckError("别样红会话核验网络失败，原会话保留；不能据此认定登录失效") from exc
        return ""
    except (ValueError, TypeError) as exc:
        if strict:
            raise SessionCheckError("别样红会话核验未返回有效 JSON，原会话保留；请检查登录和网络") from exc
        return ""


def check_identity(cookies: dict[str, str]) -> str:
    return fetch_hotel_name(cookies, strict=True)


def same_server(saved: str, current: str) -> bool:
    def normalized(value):
        parts = urlsplit(str(value).rstrip("/"))
        return parts.scheme.lower(), parts.netloc.lower(), parts.path.removesuffix("/login")
    return normalized(saved) == normalized(current)


def session_matches(session: dict, username: str = "") -> bool:
    addresses = {"url": pms_config.LOGIN_BASE_URL, "report_base_url": REPORT_BASE_URL,
                 "service_api_base_url": SERVICE_API_BASE_URL}
    changed_address = any(not same_server(session.get(key, ""), value)
                          for key, value in addresses.items() if key == "url" or session.get(key))
    changed_hotel = session.get("hotel_id") and session["hotel_id"] != os.environ.get("HOTEL_ID", "").strip()
    changed_account = username and session.get("account_fingerprint") != account_fingerprint(username)
    return not (changed_address or changed_hotel or changed_account)


def require_manual_session(username: str = "") -> str:
    session = read_session(require_cookies=True, quiet=True)
    if not session:
        raise ManualLoginRequired("尚无可用的别样红会话，请在配置页点击手动登录")
    if not session_matches(session, username):
        raise ManualLoginRequired("别样红地址或配置账号/酒店标识已变化，请重新手动登录")
    return check_identity(session["cookies"])


def browser_cookies(context) -> dict[str, str]:
    # 同名 Cookie 可合法存在于不同域/路径；只取核验接口适用项，优先更具体的作用域。
    cookies = context.cookies([f"{SERVICE_API_BASE_URL}/api/v1/fox/user/checkLogin"])
    cookies.sort(key=lambda item: (len(item.get("path", "/")),
                                  len(item.get("domain", "").lstrip(".")),
                                  not item.get("domain", "").startswith(".")))
    return {item["name"]: item["value"] for item in cookies}


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
    domain = urlsplit(REPORT_BASE_URL).hostname
    if not domain:
        raise ValueError(f"PMS report URL has no hostname: {REPORT_BASE_URL}")
    cookie_list = [
        {"name": key, "value": value, "domain": domain, "path": "/"}
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
