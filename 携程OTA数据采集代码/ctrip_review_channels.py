from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


PAGE_URL = "https://ebooking.ctrip.com/comment/commentList?microJump=true"
CHANNELS = (
    ("\u643a\u7a0b", "\u643a\u7a0b"),
    ("\u53bb\u54ea\u513f", "\u53bb\u54ea\u513f"),
    ("\u540c\u7a0b\u65c5\u884c", "\u540c\u7a0b\u65c5\u884c"),
    ("\u667a\u884c", "\u667a\u884c"),
)
COUNT_LABELS = (
    ("\u5168\u90e8\u70b9\u8bc4", "total_review_count"),
    ("\u5f85\u56de\u590d", "unreplied_review_count"),
    ("\u5dee\u8bc4", "negative_review_count"),
)


def profile_path() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local / "HotelAgent" / "browser_profiles" / "ctrip"


def read_count(page: Any, label: str) -> int:
    text = page.get_by_text(label, exact=True).locator("xpath=following-sibling::p[1]").inner_text().strip()
    matched = re.search(r"\d+", text.replace(",", ""))
    if not matched:
        raise RuntimeError(f"Ctrip review count is missing: {label}")
    return int(matched.group())


def counts(page: Any) -> dict[str, int]:
    return {field: read_count(page, label) for label, field in COUNT_LABELS}


def response_payload(response: Any, source: str) -> dict[str, Any]:
    payload = response.json()
    status = payload.get("resStatus") or {}
    if status.get("rcode") != 200 or not isinstance(payload.get("ratingInfo"), dict):
        raise RuntimeError(f"Ctrip review channel request failed: {source}")
    return payload


def collect_review_channels() -> list[tuple[str, dict[str, Any], dict[str, int]]]:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path()), channel="msedge", headless=True,
            no_viewport=True, locale="zh-CN", timezone_id="Asia/Shanghai",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            with page.expect_response(lambda response: "getHotelRating" in response.url, timeout=60_000) as initial:
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
            page.get_by_text(CHANNELS[0][1], exact=True).wait_for(state="visible", timeout=45_000)
            page.wait_for_timeout(250)
            results = [(CHANNELS[0][0], response_payload(initial.value, CHANNELS[0][0]), counts(page))]
            for source, label in CHANNELS[1:]:
                with page.expect_response(lambda response: "getHotelRating" in response.url, timeout=30_000) as rating:
                    page.get_by_text(label, exact=True).click()
                page.wait_for_timeout(250)
                results.append((source, response_payload(rating.value, source), counts(page)))
            return results
        finally:
            context.close()
