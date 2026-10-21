"""登录诊断白名单：只输出固定标签、布尔值、版本号及有界错误码。"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


SITES = ("meituan.com", "dianping.com", "meituan.net", "dpfile.com", "sankuai.com")
ENDPOINTS = {"captcha_init", "captcha_verify", "verification", "login", "asset", "other"}
RESOURCES = {"document", "script", "stylesheet", "image", "font", "xhr", "fetch", "other"}
PHASES = {"starting", "opening_login", "waiting_main", "opening_eb", "validating", "saving"}
NETWORK_ERRORS = {
    "ERR_ABORTED", "ERR_FAILED", "ERR_TIMED_OUT", "ERR_CONNECTION_TIMED_OUT",
    "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED",
    "ERR_NAME_NOT_RESOLVED", "ERR_NETWORK_CHANGED", "ERR_INTERNET_DISCONNECTED",
    "ERR_CERT_AUTHORITY_INVALID", "ERR_CERT_DATE_INVALID", "ERR_SSL_PROTOCOL_ERROR",
    "ERR_BLOCKED_BY_CLIENT", "ERR_BLOCKED_BY_RESPONSE", "ERR_EMPTY_RESPONSE",
    "ERR_HTTP2_PROTOCOL_ERROR", "ERR_TOO_MANY_REDIRECTS", "ERR_TUNNEL_CONNECTION_FAILED",
}
CODE_KEYS = {"code", "status", "errorCode", "error_code", "errno", "ret", "statusCode"}
CODE_NAMES = {"SUCCESS", "FAIL", "FAILED", "RISK_CONTROL", "NEED_VERIFY", "UNAUTHORIZED", "FORBIDDEN"}
EVENTS = {"started", "environment", "phase", "http", "business", "network_failed",
          "script_error", "observation_failed", "limit_reached", "finished"}
ENUM_FIELDS = {
    "site": set(SITES), "endpoint": ENDPOINTS, "resource_type": RESOURCES,
    "method": {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH", "other"},
    "phase": PHASES, "outcome": {"success", "cancelled", "failed", "ended"},
    "network_error": NETWORK_ERRORS | {"other"},
    "script_error": {"TypeError", "ReferenceError", "SyntaxError", "SecurityError", "other"},
}


def version_number(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"\d{1,3}(?:\.\d{1,6}){1,3}", value) else "unknown"


def numeric_code(value: object) -> int | str | None:
    if type(value) is int and -999999 <= value <= 999999:
        return value
    if isinstance(value, str):
        if re.fullmatch(r"-?\d{1,6}", value):
            return int(value)
        if value in CODE_NAMES:
            return value
    return None


def business_codes(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    codes = {}
    for prefix, block in [("", payload)] + [(key + ".", payload.get(key)) for key in ("error", "data", "result")]:
        if isinstance(block, dict):
            for key in sorted(CODE_KEYS):
                value = numeric_code(block.get(key))
                if value is not None:
                    codes[prefix + key] = value
    return codes


def request_metadata(url: str, method: str, resource: str) -> dict:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        site = next((base for base in SITES if host == base or host.endswith("." + base)), None)
        if parsed.scheme not in {"https", "http"} or not site or parsed.username or parsed.password:
            return {}
        path = unquote(parsed.path).lower()
    except (TypeError, ValueError):
        return {}
    if resource in {"script", "stylesheet", "image", "font"}:
        endpoint = "asset"
    elif any(word in path for word in ("captcha", "yoda", "verify", "verification", "risk")) or any(
        word in host.split(".") for word in ("verify", "yoda")
    ):
        endpoint = "captcha_init" if any(word in path for word in ("init", "requestcode", "getinfo")) else (
            "captcha_verify" if any(word in path for word in ("verify", "check")) else "verification"
        )
    elif "login" in path or "passport" in host:
        endpoint = "login"
    else:
        endpoint = "other"
    return {"site": site, "endpoint": endpoint,
            "method": method if method in ENUM_FIELDS["method"] else "other",
            "resource_type": resource if resource in RESOURCES else "other"}


def network_error(value: object) -> str:
    match = re.search(r"\bnet::(ERR_[A-Z_]+)\b", str(value))
    return match[1] if match and match[1] in NETWORK_ERRORS else "other"


def environment_fields(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    fields = {}
    if type(payload.get("webdriver")) is bool:
        fields["webdriver"] = payload["webdriver"]
    for key, pattern in (("edge_version", r"Edg/([\d.]+)"), ("chromium_version", r"Chrome/([\d.]+)")):
        match = re.search(pattern, str(payload.get("ua") or ""))
        fields[key] = version_number(match[1] if match else None)
    return fields


def safe_fields(fields: dict) -> dict:
    """写盘前再次过滤，避免未来调用者意外传入异常文本或 URL。"""
    result = {}
    allowed_codes = CODE_KEYS | {prefix + "." + key for prefix in ("error", "data", "result") for key in CODE_KEYS}
    for key, value in fields.items():
        if key in ENUM_FIELDS and isinstance(value, str) and value in ENUM_FIELDS[key]:
            result[key] = value
        elif key in {"switch_account", "had_session", "webdriver", "truncated"} and type(value) is bool:
            result[key] = value
        elif key.endswith("_version") and key in {"browser_version", "playwright_version", "edge_version", "chromium_version"}:
            result[key] = version_number(value)
        elif key in {"request_id", "http_status", "page_index", "elapsed_ms"} and type(value) is int and 0 <= value <= 86400000:
            result[key] = value
        elif key == "codes" and isinstance(value, dict):
            result[key] = {k: numeric_code(v) for k, v in value.items() if k in allowed_codes and numeric_code(v) is not None}
    return result
