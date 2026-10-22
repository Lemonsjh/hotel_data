"""Allowlisted import diagnostics. Never expose exception text, URL, headers or bodies."""
from __future__ import annotations

import re
from builtins import TimeoutError as BuiltinTimeoutError
from urllib.parse import urlsplit

REVIEW_HOST = "me.meituan.com"
REVIEW_PATH = "/api/gw/v1/base/comments/queryGeneralCommentInfo"
PHASES = {"browser_start", "cookie_setup", "page_load", "page_read", "storage_read", "page_title", "completed", "ordinary_browser"}
NETWORK_CODES = {"ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_REFUSED",
                 "ERR_TIMED_OUT", "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED",
                 "ERR_NETWORK_CHANGED", "ERR_SSL_PROTOCOL_ERROR", "ERR_CERT_AUTHORITY_INVALID"}
MESSAGES = {
    "ordinary_page_missing": "请在安装扩展的同一浏览器窗口打开美团评价管理页，待列表加载后再核对。",
    "ordinary_request_missing": "当前评价页没有可用的酒店请求记录，请手动刷新评价页一次、待列表加载后再核对。",
    "ordinary_page_changed": "核对期间评价页或酒店发生了变化，请确认目标酒店后再核对。",
    "ordinary_request_failed": "普通浏览器中的评价核对请求未能完成，请检查评价页，不要连续重试。",
    "ordinary_script_unavailable": "扩展无法在评价页执行核对，请重新加载 0.2.0 扩展并检查其美团网站访问权限。",
    "ordinary_cookie_read_failed": "评价核对已结束，但无法读取更新后的 Cookie，请检查当前普通浏览器窗口后重新读取。",
    "ordinary_evidence_invalid": "普通浏览器核对结果不完整或不兼容，请重新加载新版扩展。",
    "ordinary_evidence_expired": "普通浏览器核对结果已超时，请确认本机时间正确后重新核对。",
    "ordinary_config_changed": "核对期间本机配置已变化，请取消本轮后重新读取。",
    "browser_start_failed": "无法启动用于核对的 Edge，请检查浏览器安装。",
    "cookie_setup_failed": "Cookie 已读取，但无法载入核对用的隔离会话。",
    "page_timeout": "核对页面加载超时，不能据此认定 Cookie 已过期。",
    "page_read_timeout": "读取核对页面内容超时。",
    "page_network_error": "核对页面或评价接口发生网络错误。",
    "page_load_failed": "核对页面未能正常加载。",
    "page_http_error": "核对页面返回非成功 HTTP 状态，尚未确认登录有效。",
    "page_read_failed": "无法读取核对页面或页面存储。",
    "login_redirect": "核对会话跳到了登录页面，尚未确认登录有效。",
    "signed_request_missing": "未观察到完整的评价签名请求，请检查评价页面是否能正常打开。",
    "review_response_missing": "已观察到签名请求，但未收到对应评价接口响应。",
    "review_http_error": "评价接口未返回核对要求的 HTTP 200，尚未确认登录有效。",
    "review_access_denied": "评价接口返回 HTTP 403，访问被拒绝；请在普通浏览器检查评价页面。",
    "review_rate_limited": "评价接口返回 HTTP 429，请暂停导入并等待正常人工访问恢复，不要连续重试。",
    "review_format_invalid": "评价接口未返回可识别的 JSON 业务结果。",
    "review_business_rejected": "评价接口返回非成功业务码，本次核对未通过；这不等同于 Cookie 已过期。",
    "identity_unconfirmed": "评价响应未能与请求中的酒店身份对应，未放行保存。",
    "hotel_parameters_missing": "未识别到完整、有效的酒店参数。",
    "hotel_mismatch": "识别到的门店与当前配置不同，已阻止导入。",
    "old_hotel_unknown": "旧登录状态的酒店归属未知，且本次后台 Cookie 不完整。",
    "hotel_name_missing": "已核对酒店参数，但未识别到有效酒店名称。",
    "probe_invalid": "酒店核对未返回有效结果。",
    "probe_failed": "酒店核对遇到未分类异常；未回显原始错误以保护登录凭证。",
}


def safe_details(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    if type(data.get("phase")) is str and data["phase"] in PHASES:
        result["phase"] = data["phase"]
    for key in ("http_status", "page_http_status"):
        value = data.get(key)
        if type(value) is int and 100 <= value <= 599:
            result[key] = value
    value = data.get("business_code")
    if type(value) in (int, str) and re.fullmatch(r"-?[0-9]{1,6}", str(value)):
        result["business_code"] = str(value)
    if type(data.get("network_code")) is str and data["network_code"] in NETWORK_CODES:
        result["network_code"] = data["network_code"]
    hint = data.get("business_hint")
    if type(hint) is str and hint in {"invalid_parameters", "signature_rejected", "login_required",
                                    "manual_verification", "access_denied"}:
        result["business_hint"] = hint
    for key in ("expected_poi_id", "detected_poi_id"):
        value = data.get(key)
        if type(value) in (int, str) and re.fullmatch(r"[0-9]{1,24}", str(value)):
            result[key] = str(value)
    for key in ("request_seen", "response_seen", "response_valid", "login_redirect"):
        if type(data.get(key)) is bool:
            result[key] = data[key]
    return result


class ImportFailure(ValueError):
    def __init__(self, code, details=None):
        self.code = code if code in MESSAGES else "probe_failed"
        self.details = safe_details(details)
        super().__init__(MESSAGES[self.code])

    def public(self):
        return {"failure_code": self.code, "failure_details": safe_details(self.details),
                "message": "Cookie 已读取；" + MESSAGES[self.code] + " 原配置未修改。"}


class ProbeDiagnostics:
    def __init__(self):
        self.data = {"phase": "browser_start", "request_seen": False, "response_seen": False}

    def phase(self, value):
        if value in PHASES:
            self.data["phase"] = value

    def request(self, captured):
        if captured:
            self.data["request_seen"] = True

    def response(self, url, status):
        parsed = urlsplit(url)
        if parsed.hostname == REVIEW_HOST and parsed.path == REVIEW_PATH:
            self.data.update(response_seen=True, http_status=status)
            return True
        return False

    def business(self, data):
        if isinstance(data, dict):
            self.data["business_code"] = data.get("code")
            self.data["response_valid"] = isinstance(data.get("data"), dict)

    def network(self, value):
        if isinstance(value, str):
            for code in NETWORK_CODES:
                if re.search(r"(?<![A-Z_])" + code + r"(?![A-Z_])", value):
                    self.data["network_code"] = code
                    break

    def navigation(self, response):
        self.data["page_http_status"] = getattr(response, "status", None)

    def location(self, url):
        parsed = urlsplit(url)
        self.data["login_redirect"] = (
            parsed.hostname in {"me.meituan.com", "eb.meituan.com", "passport.meituan.com", "epassport.meituan.com"}
            and ("login" in parsed.path.lower() or "passport" in parsed.hostname)
        )

    def request_failed(self, request):
        parsed = urlsplit(request.url)
        if parsed.hostname == REVIEW_HOST and (parsed.path == REVIEW_PATH or request.resource_type == "document"):
            self.network(request.failure)

    def safe(self):
        return safe_details(self.data)

    def exception(self, exc):
        from playwright.sync_api import Error, TimeoutError
        if isinstance(exc, Error):
            self.network(str(exc))  # Only a fixed net::ERR_* enum survives the whitelist.
        phase = self.data["phase"]
        if phase == "browser_start":
            code = "browser_start_failed"
        elif phase == "cookie_setup":
            code = "cookie_setup_failed"
        elif self.data.get("network_code"):
            code = "page_network_error"
        elif isinstance(exc, (TimeoutError, BuiltinTimeoutError)):
            code = "page_timeout" if phase == "page_load" else "page_read_timeout"
        else:
            code = {"page_load": "page_load_failed", "page_read": "page_read_failed",
                    "storage_read": "page_read_failed", "page_title": "page_read_failed"}.get(phase, "probe_failed")
        return ImportFailure(code, self.safe())


def result_failure(result):
    details = safe_details(result.get("probe_diagnostics"))
    if details.get("page_http_status", 200) >= 400:
        code = "page_http_error"
    elif details.get("login_redirect"):
        code = "login_redirect"
    elif details.get("response_seen"):
        if details.get("http_status") != 200:
            code = {403: "review_access_denied", 429: "review_rate_limited"}.get(details.get("http_status"), "review_http_error")
        elif "business_code" not in details:
            code = "review_format_invalid"
        elif details["business_code"] != "10000":
            code = "review_business_rejected"
        elif not details.get("response_valid"):
            code = "review_format_invalid"
        elif not result.get("ok"):
            code = "hotel_parameters_missing"
        else:
            code = "identity_unconfirmed"
    elif details.get("network_code"):
        code = "page_network_error"
    elif not result.get("ok"):
        code = "signed_request_missing"
    else:
        code = "review_response_missing"
    return ImportFailure(code, details)
