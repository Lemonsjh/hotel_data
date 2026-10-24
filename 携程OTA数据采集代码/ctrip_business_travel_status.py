from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


PROMOTION_HOME_URL = "https://ebooking.ctrip.com/promotion/promotionCenter?microJump=true"
PROMOTION_HOME_PATH = "/restapi/soa2/24267/getDomesticHome"
KNOWN_STATUSES = {"EFFECTIVE", "PAUSE", "AUDIT", "EXPIRED"}


def business_travel_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("resStatus"), dict):
        raise RuntimeError("Ctrip business-travel response is invalid")
    if payload["resStatus"].get("rcode") not in (200, "200"):
        raise RuntimeError("Ctrip business-travel API is not successful")
    service = payload.get("ResponseStatus", {})
    if not isinstance(service, dict) or service.get("Ack", "Success") != "Success" or service.get("Errors"):
        raise RuntimeError("Ctrip business-travel response contains service errors")
    cards = payload.get("dtos")
    if not isinstance(cards, list) or any(not isinstance(card, dict) for card in cards):
        raise RuntimeError("Ctrip promotion home did not return valid promotion cards")
    matches = [card for card in cards if card.get("promotionType") == "BTP"]
    if len(matches) != 1:
        raise RuntimeError("Ctrip business-travel BTP card is missing or duplicated")
    card = matches[0]
    actions, statuses = card.get("actions"), card.get("promotionStatusList")
    if any(not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
           for value in (actions, statuses)):
        raise RuntimeError("Ctrip business-travel actions or statuses are incomplete")
    if actions == ["ADD"] and statuses == []:
        return {"enabled": 0, "status": "NOT_JOINED", "status_detail": "ADD"}
    if not statuses or set(statuses) - KNOWN_STATUSES or actions == ["ADD"]:
        raise RuntimeError("Ctrip business-travel status is unknown or inconsistent")
    active = "EFFECTIVE" in statuses
    detail = "|".join(sorted(set(statuses)))
    return {"enabled": int(active), "status": "JOINED" if active else detail, "status_detail": detail}


def business_travel_status(page: Any) -> dict[str, Any]:
    def is_home_response(response: Any) -> bool:
        url = urlsplit(response.url)
        return url.hostname == "ebooking.ctrip.com" and url.path == PROMOTION_HOME_PATH

    # Capture the page's own request, preserving its current account and generated metadata.
    # No signup, edit, retry loop, or legacy-page text fallback.
    with page.expect_response(is_home_response, timeout=30_000) as captured:
        page.goto(PROMOTION_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    response = captured.value
    if response.status != 200:
        raise RuntimeError("Ctrip business-travel API HTTP request failed")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Ctrip business-travel API did not return JSON") from exc
    return business_travel_from_payload(payload)
