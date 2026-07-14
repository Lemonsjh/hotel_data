from __future__ import annotations

import argparse
import json
import re
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


MEITUAN_URL = "https://me.meituan.com/ebooking/merchant/comment-manage-react"
MEITUAN_COMMENT_PATH = "/api/gw/v1/base/comments/queryGeneralCommentInfo"
CTRIP_URL = "https://ebooking.ctrip.com/datacenter/inland/businessreport/outline?microJump=true"
HOTEL_WORDS = (
    "\u9152\u5e97",
    "\u5bbe\u9986",
    "\u6c11\u5bbf",
    "\u5ba2\u6808",
    "\u7535\u7ade",
    "\u516c\u5bd3",
    "\u65c5\u9986",
    "\u65c5\u793e",
    "\u5ea6\u5047",
    "\u996d\u5e97",
    "Hotel",
)
BAD_WORDS = (
    "\u9152\u5e97\u5217\u8868",
    "\u9152\u5e97\u8be6\u60c5",
    "\u6211\u7684\u9152\u5e97",
    "Hotel Hub",
    "HotelManagement",
    "\u9152\u5e97\u6536\u85cf",
    "\u9152\u5e97\u5165\u9a7b",
    "\u9152\u5e97\u52a9\u624b",
    "\u9152\u5e97PMS",
    "\u9152\u5e97\u4fe1\u606f",
    "\u9152\u5e97\u4eae\u70b9",
    "\u5957\u9910\u6309\u94ae",
    "HEUserHotelTag",
)
LOGIN_TEXT = "\u767b\u5f55"


def compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def clean_name(value: Any) -> str:
    text = compact(value)
    labels = (
        "\u95e8\u5e97\uff1a",
        "\u95e8\u5e97:",
        "\u9152\u5e97\uff1a",
        "\u9152\u5e97:",
        "\u5f53\u524d\u9152\u5e97\uff1a",
        "\u5f53\u524d\u9152\u5e97:",
    )
    for label in labels:
        if label in text:
            text = text.split(label, 1)[1].strip()
    return text.strip(" -_|")


def looks_like_hotel(value: Any) -> bool:
    text = clean_name(value)
    if not (3 <= len(text) <= 110):
        return False
    if any(mark in text for mark in ("{", "}", "//", "xpath", "XPath")):
        return False
    if any(word in text for word in BAD_WORDS):
        return False
    return any(word in text for word in HOTEL_WORDS)


def has_cjk(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def text_priority(value: Any, platform: str) -> int:
    if platform == "ctrip":
        return 85 if has_cjk(value) else 55
    return 60 if has_cjk(value) else 35


def parse_cookie_header(cookie_header: str, domains: list[str]) -> list[dict[str, str]]:
    jar = SimpleCookie()
    jar.load(cookie_header)
    cookies: list[dict[str, str]] = []
    for name, morsel in jar.items():
        for domain in domains:
            cookies.append({"name": name, "value": morsel.value, "domain": domain, "path": "/"})
    return cookies


def extract_meituan_comment_request(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "me.meituan.com" or parsed.path != MEITUAN_COMMENT_PATH:
        return {}
    query = parse_qs(parsed.query, keep_blank_values=True)

    def first(name: str) -> str:
        return str((query.get(name) or [""])[0]).strip()

    result = {
        "poi_id": first("poiId"),
        "partner_id": first("partnerId"),
        "biz_account_id": first("bizAccountId"),
        "review_detail_url": url,
    }
    if not all(result[key].isdigit() for key in ("poi_id", "partner_id", "biz_account_id")):
        return {}
    if not first("mtgsig"):
        return {}
    return result


def key_priority(key: str, platform: str) -> int:
    if platform == "meituan":
        if key == "poiname":
            return 100
        if key in {"accountname", "hotelname", "name"}:
            return 70
        if key == "partnername":
            return 20
    if platform == "ctrip":
        if key in {"hotelname", "hoteltitle", "hotelidname"}:
            return 100
        if key in {"name", "title"}:
            return 50
    return 0


def hit(source: str, key: str, value: Any, priority: int, url: str = "") -> dict[str, str]:
    return {
        "source": source,
        "key": str(key),
        "value": clean_name(value),
        "priority": str(priority),
        "url_hint": url.split("?", 1)[0][-120:] if url else "",
    }


def walk_json(obj: Any, hits: list[dict[str, str]], url: str, platform: str) -> None:
    if len(hits) >= 80:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = str(key).lower()
            if isinstance(value, str):
                priority = key_priority(lower, platform)
                if priority and looks_like_hotel(value):
                    hits.append(hit("response", key, value, priority, url))
                elif looks_like_hotel(value):
                    hits.append(hit("response", key, value, 30, url))
            walk_json(value, hits, url, platform)
    elif isinstance(obj, list):
        for item in obj[:100]:
            walk_json(item, hits, url, platform)


def unique_hits(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in sorted(items, key=lambda x: int(x.get("priority") or 0), reverse=True):
        value = item.get("value", "")
        if value and value not in seen:
            seen.add(value)
            result.append(item)
    return result


def js_storage_probe() -> str:
    return r"""
    () => {
      const words = ['酒店','宾馆','民宿','客栈','电竞','公寓','旅馆','旅社','度假','饭店','Hotel'];
      const bad = ['酒店列表','酒店详情','我的酒店','Hotel Hub','HotelManagement','酒店收藏','酒店入驻','酒店助手','酒店PMS','酒店信息','酒店亮点','套餐按钮','HEUserHotelTag'];
      const out = [];
      function norm(v) { return String(v || '').replace(/\s+/g, ' ').trim(); }
      function ok(v) {
        const s = norm(v);
        return s.length >= 3 && s.length <= 110 && words.some(w => s.includes(w)) && !bad.some(w => s.includes(w)) && !/[{}]/.test(s);
      }
      function walk(o, key) {
        if (out.length > 50 || o == null) return;
        if (typeof o === 'string') {
          if (ok(o)) out.push({source:'storage', key:key || '', value:norm(o)});
          return;
        }
        if (Array.isArray(o)) { o.slice(0, 80).forEach(x => walk(x, key)); return; }
        if (typeof o === 'object') Object.keys(o).slice(0, 160).forEach(k => walk(o[k], k));
      }
      for (const store of [localStorage, sessionStorage]) {
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i);
          const v = store.getItem(k);
          if (ok(v)) out.push({source:'storage', key:k, value:norm(v)});
          try { walk(JSON.parse(v), k); } catch(e) {}
        }
      }
      return out;
    }
    """


def probe(platform: str, cookie_header: str, timeout_ms: int = 45000) -> dict[str, Any]:
    if not cookie_header.strip():
        return {"ok": False, "error": f"{platform} cookie is empty", "hotel_name": "", "candidates": []}

    if platform == "meituan":
        url = MEITUAN_URL
        domains = [".meituan.com", "meituan.com", ".me.meituan.com", "me.meituan.com", ".eb.meituan.com", "eb.meituan.com"]
    elif platform == "ctrip":
        url = CTRIP_URL
        domains = [".ctrip.com", "ctrip.com", ".ebooking.ctrip.com", "ebooking.ctrip.com"]
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            hits: list[dict[str, str]] = []
            meituan_request: dict[str, str] = {}
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            context.add_cookies(parse_cookie_header(cookie_header, domains))
            page = context.new_page()
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "media", "font"}
                else route.continue_(),
            )

            def on_request(req: Any) -> None:
                if platform != "meituan" or meituan_request:
                    return
                captured = extract_meituan_comment_request(req.url)
                if captured:
                    meituan_request.update(captured)

            def on_response(resp: Any) -> None:
                if (platform == "meituan" and "meituan.com" not in resp.url) or (
                    platform == "ctrip" and "ebooking.ctrip.com" not in resp.url
                ):
                    return
                try:
                    data = resp.json()
                except Exception:
                    return
                walk_json(data, hits, resp.url, platform)

            page.on("request", on_request)
            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                loop_count = 32 if platform == "meituan" else 10
                for _ in range(loop_count):
                    candidates_now = unique_hits([item for item in hits if looks_like_hotel(item.get("value"))])
                    if platform == "meituan":
                        if meituan_request and candidates_now and int(candidates_now[0].get("priority") or 0) >= 100:
                            break
                    elif candidates_now:
                        break
                    page.wait_for_timeout(250)
                if not unique_hits([item for item in hits if looks_like_hotel(item.get("value"))]):
                    if platform == "meituan":
                        try:
                            page.wait_for_load_state("networkidle", timeout=4000)
                        except PlaywrightTimeoutError:
                            pass
                        page.wait_for_timeout(800)
                    else:
                        page.wait_for_timeout(1200)
                body = page.locator("body").inner_text(timeout=8000)
                for line in body.splitlines():
                    if looks_like_hotel(line):
                        hits.append(hit("page_text", "line", line, text_priority(line, platform)))
                for item in page.evaluate(js_storage_probe()):
                    value = item.get("value")
                    if looks_like_hotel(value):
                        hits.append(hit(item.get("source", "storage"), item.get("key", ""), value, text_priority(value, platform) - 10))
                current_url = page.url
                login_like = (
                    "login" in current_url.lower()
                    or "passport" in current_url.lower()
                    or LOGIN_TEXT in body[:4000]
                ) and not meituan_request
                candidates = unique_hits([item for item in hits if looks_like_hotel(item.get("value"))])
                hotel_name = candidates[0]["value"] if candidates and not login_like else ""
                result = {
                    "ok": bool(meituan_request) if platform == "meituan" else bool(hotel_name),
                    "login_like": login_like,
                    "hotel_name": hotel_name,
                    "current_url": current_url,
                    "title": page.title(),
                    "candidates": candidates[:15],
                }
                if platform == "meituan":
                    result.update(meituan_request)
                return result
            finally:
                context.close()
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=["meituan", "ctrip"])
    parser.add_argument("--cookie-file")
    parser.add_argument("--cookie")
    args = parser.parse_args()
    cookie_header = args.cookie or ""
    if args.cookie_file:
        cookie_header = Path(args.cookie_file).read_text(encoding="utf-8", errors="ignore").strip()
    print(json.dumps(probe(args.platform, cookie_header), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "hotel_name": "", "candidates": []}, ensure_ascii=True))
        sys.exit(1)
