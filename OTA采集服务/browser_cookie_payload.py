"""Validate five explicitly permitted browser Cookie groups; never echo values."""
from __future__ import annotations

import math
import re
import time
from http.cookies import SimpleCookie
from browser_cookie_diagnostics import ImportFailure, result_failure, safe_details

HOSTS = {
    "me_cookie": "me.meituan.com",
    "eb_cookie": "eb.meituan.com",
    "promotion_cookie": "ebmidas.dianping.com",
    "highlights_cookie": "tdc.meituan.com",
    "public_welfare_cookie": "gongyi.meituan.com",
}
NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,180}")
VALUE = re.compile(r'[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*')
REQUIRED = {"me_cookie": "mebsid", "eb_cookie": "ebbsid"}
COMMON_ONLY = {"uuid", "__rcf___", "__rcf_com.sankuai.fetalos.web.hotelfeme__"}


def headers_from_payload(payload: dict) -> dict[str, str]:
    groups = payload.get("groups")
    if not isinstance(groups, dict) or set(groups) != set(HOSTS):
        raise ValueError("请使用当前版本扩展读取全部五类 Cookie")
    result = {}
    stores = set()
    for key, host in HOSTS.items():
        items = groups[key]
        if not isinstance(items, list) or len(items) > 300:
            raise ValueError("Cookie 数据格式或数量不正确")
        pairs = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Cookie 数据格式不正确")
            if not isinstance(item.get("domain"), str):
                raise ValueError("Cookie 域名不在允许范围内")
            domain = item["domain"].lstrip(".").lower()
            if type(item.get("hostOnly")) is not bool:
                raise ValueError("Cookie 域名作用域不明确")
            matches = host == domain or (not item.get("hostOnly") and host.endswith("." + domain))
            if not domain or not matches or domain not in {host, "meituan.com", "dianping.com"}:
                raise ValueError("Cookie 域名不在允许范围内")
            if "partitionKey" in item:
                raise ValueError("暂不支持分区 Cookie，请保留手动配置方式")
            if not isinstance(item.get("storeId"), str) or not item["storeId"]:
                raise ValueError("缺少浏览器 Cookie 配置文件信息")
            stores.add(item["storeId"])
            name, value = item.get("name"), item.get("value")
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise ValueError("Cookie 名称格式不受支持")
            if not isinstance(value, str) or len(value) > 16384 or not VALUE.fullmatch(value):
                raise ValueError("Cookie 值格式不受支持，请使用手动配置")
            if "expirationDate" in item:
                expiry = item["expirationDate"]
                if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
                    raise ValueError("浏览器中存在已过期 Cookie，请重新读取")
            if (not isinstance(item.get("path"), str) or not item["path"].startswith("/")
                    or len(item["path"]) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in item["path"])):
                raise ValueError("Cookie 路径不正确")
            if name in pairs and pairs[name] != value:
                raise ValueError("存在同名但不同值的 Cookie，无法安全转换，请使用手动配置")
            pairs[name] = value
        if key in REQUIRED and not pairs.get(REQUIRED[key]):
            raise ValueError(f"缺少 {REQUIRED[key]} 登录 Cookie，已阻止保存；请重新加载 0.2.2 扩展并检查网站访问权限")
        if key not in REQUIRED and pairs and set(pairs).issubset(COMMON_ONLY):
            pairs = {}
        header = "; ".join(f"{name}={value}" for name, value in pairs.items())
        # Existing collectors parse SimpleCookie: reject values that would change in transit.
        jar = SimpleCookie()
        jar.load(header)
        if {name: value.value for name, value in jar.items()} != pairs:
            raise ValueError("Cookie 无法兼容现有采集器，请使用手动配置")
        if len(header) > 65536:
            raise ValueError("单类 Cookie 数据过大")
        result[key] = header
    if len(stores) != 1 or not result["me_cookie"] or not result["eb_cookie"]:
        raise ValueError("请在同一个普通浏览器配置文件中登录并读取 ME、EB Cookie")
    return result


def scoped_cookies_from_payload(payload: dict) -> list[dict]:
    """Keep browser Cookie scope for the isolated identity check; never persist it."""
    cookies = {}
    for key in ("me_cookie", "eb_cookie"):
        for item in payload["groups"][key]:
            domain = item["domain"].lstrip(".").lower()
            domain = domain if item["hostOnly"] else "." + domain
            cookie = {"name": item["name"], "value": item["value"], "domain": domain,
                      "path": item["path"]}
            for field in ("secure", "httpOnly"):
                if type(item.get(field)) is bool:
                    cookie[field] = item[field]
            same_site = {"strict": "Strict", "lax": "Lax", "no_restriction": "None"}.get(
                item.get("sameSite") if isinstance(item.get("sameSite"), str) else None)
            if same_site:
                cookie["sameSite"] = same_site
            if "expirationDate" in item:
                cookie["expires"] = item["expirationDate"]
            identity = (cookie["name"], domain, cookie["path"])
            if identity in cookies and cookies[identity]["value"] != cookie["value"]:
                raise ValueError("同一作用域的 Cookie 值不一致，无法安全核对")
            cookies[identity] = cookie
    return list(cookies.values())


def verified_identity(result: dict, settings: dict, headers: dict) -> dict:
    from hotel_name_probe import extract_meituan_comment_request
    if not isinstance(result, dict):
        raise ImportFailure("probe_invalid")
    if not result.get("ok") or result.get("login_like") or result.get("authenticated") is not True:
        raise result_failure(result)
    details = safe_details(result.get("probe_diagnostics"))
    identity = extract_meituan_comment_request(str(result.get("review_detail_url") or ""))
    if not identity or any(str(result.get(key)) != identity[key] for key in ("poi_id", "partner_id", "biz_account_id")):
        raise ImportFailure("hotel_parameters_missing", details)
    current = settings.get("meituan") or {}
    if current.get("poi_id") and str(current["poi_id"]) != identity["poi_id"]:
        raise ImportFailure("hotel_mismatch", {**details, "expected_poi_id": current["poi_id"], "detected_poi_id": identity["poi_id"]})
    if not current.get("poi_id") and any(current.get(key) and not headers[key] for key in HOSTS):
        raise ImportFailure("old_hotel_unknown", details)
    name = result.get("hotel_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 110 or any(ord(c) < 32 for c in name):
        raise ImportFailure("hotel_name_missing", details)
    identity["hotel_name"] = name.strip()
    return identity
