"""促销子项：在当前会话内读取接口及明确的页面状态，未知状态不落库。"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from meituan_config import (
    MEITUAN_HIGHLIGHTS_COOKIE, MEITUAN_PUBLIC_WELFARE_COOKIE, PARTNER_ID, POI_ID,
)
from meituan_status_session import require_backend_session, status_page
from meituan_page_capture import page_access_issue

BUSINESS_TRAVEL_NAME = "商旅专享价"
HIGHLIGHTS_NAME = "酒店亮点"
AUTO_ORDER_NAME = "自动接单"
PUBLIC_WELFARE_NAME = "公益流量"
SCHEDULED_INVOICE_NAME = "预约发票"
BUSINESS_TRAVEL_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Fpromotion-new%2Findex.html%23%2Fbusiness-hotel%2Fhome"
)
WORKBENCH_URL = "https://eb.meituan.com/ebooking/new-workbench/index.html"
AUTO_ORDER_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Forder%2Findex.html%23%2Fauto"
)
SCHEDULED_INVOICE_URL = "https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febk%2Fhotel%2Fhotelinfo.html%23%2F"
HIGHLIGHTS_EMPTY_TEXT = "当前酒店暂无特色亮点数据"
# 2026-11 改版：酒店未获亮点功能时展示该文案，且前端不再自动调用 highlight/query
HIGHLIGHTS_UNAVAILABLE_TEXT = "系统未识别到您的酒店具备特色亮点"
HIGHLIGHTS_QUERY_PATH = "/gw/api/publish/eb/highlight/query"
# 主动探测最多两次、间隔 3 秒：接口异常时不在轮询里反复请求
HIGHLIGHTS_PROBE_RETRY_SECONDS = 3.0
HIGHLIGHTS_PROBE_MAX_ATTEMPTS = 2
INVOICE_CLOSED_TEXT = "当前门店暂未开通，快去开通预约发票吧"
PAGE_CHECK_SECONDS = 10
PAGE_ACTION_TIMEOUT_MS = 5_000
WORKBENCH_MENU_TIMEOUT_MS = 20_000


def find_visible_text(page: Any, text: str):
    locators = page.get_by_text(text, exact=True)
    for index in range(locators.count()):
        locator = locators.nth(index)
        try:
            if locator.is_visible(timeout=500):
                return locator
        except Exception:
            continue
    return None


def wait_for_visible_text(page: Any, text: str, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        locator = find_visible_text(page, text)
        if locator:
            return locator
        page.wait_for_timeout(250)
    raise RuntimeError(f"Visible workbench menu was not found: {text}")


def wait_for_workbench_wrapper(page: Any, timeout_ms: int = WORKBENCH_MENU_TIMEOUT_MS) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if urlparse(page.url).hostname == "me.meituan.com":
            return
        page.wait_for_timeout(250)
    raise RuntimeError("Meituan workbench wrapper did not finish loading")


def click_workbench_menu(page: Any, parent_name: str, item_name: str) -> None:
    parent = wait_for_visible_text(page, parent_name, WORKBENCH_MENU_TIMEOUT_MS)
    item = find_visible_text(page, item_name)
    if not item:
        parent.click(force=True, timeout=PAGE_ACTION_TIMEOUT_MS)
        item = wait_for_visible_text(page, item_name, WORKBENCH_MENU_TIMEOUT_MS)
    item.click(force=True, timeout=PAGE_ACTION_TIMEOUT_MS)


def raise_unrecognized_status(page: Any, name: str) -> None:
    urls = [page.url, *(frame.url for frame in page.frames)]
    if any(urlparse(url).hostname == "epassport.meituan.com"
           or (urlparse(url).hostname == "me.meituan.com" and urlparse(url).path.startswith("/login/"))
           for url in urls):
        raise RuntimeError(f"{name}内页未登录，请在普通浏览器登录对应后台后重新导入 Cookie。")
    if issue := page_access_issue(page):
        raise RuntimeError(f"{name}页面不可用：{issue}，保留原值")
    # 只记录域名及内页数，不记录可能携带酒店参数、签名或凭证的 URL。
    hosts = sorted({urlparse(url).hostname for url in urls if urlparse(url).hostname})
    raise RuntimeError(f"{name}未返回明确的开通状态，保留原值；"
                       f"页面域名={','.join(hosts[:8]) or '无'}，内页数={len(page.frames)}")


def fetch_business_travel_status() -> str:
    with status_page(BUSINESS_TRAVEL_URL) as page:
        for _ in range(PAGE_CHECK_SECONDS * 2):
            for frame in page.frames:
                if urlparse(frame.url).hostname not in {"eb.meituan.com", "me.meituan.com"}:
                    continue
                try:
                    if "已加入商旅合作" in frame.locator("body").inner_text(timeout=2_000):
                        return "OPEN"
                except Exception:
                    continue
            page.wait_for_timeout(500)
        raise_unrecognized_status(page, BUSINESS_TRAVEL_NAME)


def poi_query_matches(query: str) -> bool:
    """接口带 poiId 时必须与当前酒店一致；未带该参数时不据此否决。"""
    values = parse_qs(query).get("poiId")
    return not values or values == [POI_ID]


def highlight_query_status(payload: Any) -> str | None:
    """亮点列表接口：code=0 且 data 为空表示尚未上传任何亮点。"""
    if not isinstance(payload, dict) or payload.get("code") != 0 or "data" not in payload:
        raise RuntimeError("酒店亮点接口返回异常，保留原值")
    return "CLOSED" if payload["data"] in (None, [], {}) else None


HIGHLIGHTS_PROBE_JS = """async poiId => {
    const ids = encodeURIComponent(poiId);
    try {
        const response = await fetch(`/gw/api/publish/eb/highlight/query?poiId=${ids}`, {credentials: 'include'});
        const body = (await response.text()).slice(0, 200000);
        return {http: response.status, body};
    } catch (error) {
        return {http: -1, body: String(error)};
    }
}"""


def probe_highlights_api(frame: Any) -> Any:
    """在亮点页内主动请求列表接口。

    2026-11 改版后前端不再自动调用 highlight/query，只能自己发请求；
    接口本身仍可用，返回 data=null 即表示确实没有亮点。
    """
    raw = frame.evaluate(HIGHLIGHTS_PROBE_JS, POI_ID)
    if not isinstance(raw, dict):
        return {}
    try:
        payload = json.loads(raw.get("body") or "")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fetch_hotel_highlights_status() -> str:
    if not POI_ID:
        raise RuntimeError("酒店亮点采集缺少酒店参数，请先识别酒店")
    require_backend_session(MEITUAN_HIGHLIGHTS_COOKIE, HIGHLIGHTS_NAME)
    with status_page(WORKBENCH_URL) as page:
        query_payloads: list = []

        def capture_highlights(response):
            url = urlparse(response.url)
            if url.scheme != "https" or url.hostname != "tdc.meituan.com":
                return
            if url.path != HIGHLIGHTS_QUERY_PATH:
                return
            if not poi_query_matches(url.query):
                return
            try:
                payload = response.json() if response.status == 200 else {}
            except ValueError:
                payload = {}
            query_payloads.append(payload)

        page.on("response", capture_highlights)
        wait_for_workbench_wrapper(page)
        click_workbench_menu(page, "信息管理", HIGHLIGHTS_NAME)

        probe_query: dict = {}
        probe_attempts = 0
        next_probe_at = 0.0

        def probe_if_due() -> None:
            nonlocal probe_query, probe_attempts, next_probe_at
            now = time.monotonic()
            if probe_attempts >= HIGHLIGHTS_PROBE_MAX_ATTEMPTS or now < next_probe_at:
                return
            probe_attempts += 1
            next_probe_at = now + HIGHLIGHTS_PROBE_RETRY_SECONDS
            for frame in page.frames:
                if urlparse(frame.url).hostname != "tdc.meituan.com":
                    continue
                try:
                    query_payload = probe_highlights_api(frame)
                except Exception:
                    continue
                if query_payload.get("code") == 0:
                    probe_query = query_payload
                    return

        for _ in range(PAGE_CHECK_SECONDS * 2):
            probe_if_due()
            tags: list[str] = []
            unavailable = False
            for frame in page.frames:
                if urlparse(frame.url).hostname != "tdc.meituan.com":
                    continue
                try:
                    body = "".join(frame.locator("body").inner_text(timeout=1_000).split())
                    frame_tags = [text.strip() for text in frame.locator(".list-card-item .top-tag").all_text_contents()]
                except Exception:
                    continue
                tags.extend(frame_tags)
                if HIGHLIGHTS_UNAVAILABLE_TEXT in body or HIGHLIGHTS_EMPTY_TEXT in body:
                    unavailable = True

            # 页面上的「在线」卡片是最直接的已开通证据，先于接口判空
            if "在线" in tags:
                return "OPEN"
            if probe_query.get("code") == 0 and (status := highlight_query_status(probe_query)):
                return status
            if unavailable:
                return "CLOSED"
            if tags and all(text in {"待提交", "待审核", "驳回"} for text in tags):
                return "CLOSED"
            if query_payloads and (status := highlight_query_status(query_payloads[-1])):
                return status
            page.wait_for_timeout(500)
        raise_unrecognized_status(page, HIGHLIGHTS_NAME)


def fetch_auto_order_status() -> str:
    with status_page(AUTO_ORDER_URL) as page:
        for _ in range(PAGE_CHECK_SECONDS * 2):
            for frame in page.frames:
                if urlparse(frame.url).hostname != "eb.meituan.com":
                    continue
                try:
                    open_radio = frame.locator('input#status-open[value="1"]')
                    closed_radio = frame.locator('input#status-shut[value="0"]')
                    if open_radio.count() and open_radio.first.is_checked(timeout=1_000):
                        return "OPEN"
                    if closed_radio.count() and closed_radio.first.is_checked(timeout=1_000):
                        return "CLOSED"
                except Exception:
                    continue
            page.wait_for_timeout(500)
        raise_unrecognized_status(page, AUTO_ORDER_NAME)


def public_welfare_page_status(states: list[str], body: str) -> str | None:
    states = [text.strip() for text in states]
    if "生效中" in states or "权益生效中" in body:
        return "OPEN"
    if states and all(text in {"未生效", "已失效", "已过期", "未开通"} for text in states):
        return "CLOSED"
    return None


def public_welfare_merchant_status(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("status") != 0:
        return None
    sign = payload.get("signStatus")
    modes = sign.get("donationMode") if isinstance(sign, dict) else None
    if isinstance(modes, dict) and all(modes.get(key) in {"INVALID", "NOT_EXIST"} for key in ("ORDER", "CASH")):
        return "CLOSED"
    return None


def fetch_public_welfare_status() -> str:
    require_backend_session(MEITUAN_PUBLIC_WELFARE_COOKIE, PUBLIC_WELFARE_NAME)
    with status_page(WORKBENCH_URL) as page:
        merchant_info = []

        def capture_merchant(response):
            url = urlparse(response.url)
            if (response.status == 200 and url.scheme == "https" and url.hostname == "gongyi.meituan.com"
                    and url.path.endswith("/SuggestSignFacade_queryMerchantInfo")):
                try:
                    merchant_info.append(response.json())
                except ValueError:
                    pass

        page.on("response", capture_merchant)
        wait_for_workbench_wrapper(page)
        click_workbench_menu(page, "促销推广", PUBLIC_WELFARE_NAME)
        for _ in range(PAGE_CHECK_SECONDS * 2):
            for frame in page.frames:
                if urlparse(frame.url).hostname != "gongyi.meituan.com":
                    continue
                try:
                    states = frame.locator("span.benefits-color-desc").all_text_contents()
                    body = frame.locator("body").inner_text(timeout=1_000)
                    if status := public_welfare_page_status(states, body):
                        return status
                    if merchant_info and public_welfare_merchant_status(merchant_info[-1]) == "CLOSED":
                        button = frame.locator(".pc-cpn-sign-button")
                        if button.count() and button.first.is_visible():
                            return "CLOSED"
                except Exception:
                    continue
            page.wait_for_timeout(500)
        raise_unrecognized_status(page, PUBLIC_WELFARE_NAME)


def invoice_status(payload: Any) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or payload.get("status") != 0:
        raise RuntimeError("预约发票接口未返回有效业务数据，保留原值")
    if (str(data.get("poiIdStr") or data.get("poiId") or "") != POI_ID
            or str(data.get("partnerId") or "") != PARTNER_ID):
        raise RuntimeError("预约发票接口的酒店身份与当前配置不一致，保留原值")
    biz_type = data.get("bizType")
    if type(biz_type) is not int:
        raise RuntimeError("预约发票开通字段缺失或无效，保留原值")
    return "OPEN" if biz_type == 1 else "CLOSED"


def fetch_scheduled_invoice_status() -> str:
    if not POI_ID or not PARTNER_ID:
        raise RuntimeError("预约发票采集缺少酒店参数，请先识别酒店")
    with status_page(SCHEDULED_INVOICE_URL) as page:
        for _ in range(PAGE_CHECK_SECONDS * 2):
            for frame in page.frames:
                if urlparse(frame.url).hostname != "eb.meituan.com":
                    continue
                try:
                    body = frame.locator("body").inner_text(timeout=1_000)
                except Exception:
                    continue
                if INVOICE_CLOSED_TEXT in "".join(body.split()):
                    return "CLOSED"
                if not find_visible_text(frame, "酒店发票信息维护"):
                    continue
                response = frame.evaluate("""async ids => {
                    const url = new URL('/api/v1/ebooking/invoice/find', location.origin);
                    url.searchParams.set('poiId', ids.poiId);
                    url.searchParams.set('partnerId', ids.partnerId);
                    const response = await fetch(url.href, {
                        method: 'GET', credentials: 'include', signal: AbortSignal.timeout(20000)
                    });
                    return {http: response.status, payload: response.ok ? await response.json() : null};
                }""", {"poiId": POI_ID, "partnerId": PARTNER_ID})
                if response["http"] != 200:
                    raise RuntimeError(f"预约发票接口请求失败：HTTP {response['http']}，保留原值")
                return invoice_status(response["payload"])
            page.wait_for_timeout(500)
        raise_unrecognized_status(page, SCHEDULED_INVOICE_NAME)
