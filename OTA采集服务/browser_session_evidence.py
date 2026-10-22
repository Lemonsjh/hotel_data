"""Accept bounded facts from the paired extension's ordinary-browser check."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from browser_cookie_diagnostics import ImportFailure, result_failure
from hotel_name_probe import extract_meituan_comment_request, looks_like_hotel

SOURCE = "ordinary_browser_v1"
FAILURES = {"ordinary_page_missing", "ordinary_request_missing", "ordinary_page_changed",
            "ordinary_request_failed", "ordinary_script_unavailable", "ordinary_cookie_read_failed"}


def browser_session_result(evidence, challenge, settings):
    details = {"phase": "ordinary_browser"}
    if not isinstance(evidence, dict) or evidence.get("source") != SOURCE or evidence.get("challenge") != challenge:
        raise ImportFailure("ordinary_evidence_invalid", details)
    checked = evidence.get("checked_at")
    if type(checked) not in (int, float) or not abs(time.time() * 1000 - checked) <= 30000:
        raise ImportFailure("ordinary_evidence_expired", details)
    failure = evidence.get("failure_code")
    if failure:
        raise ImportFailure(failure if isinstance(failure, str) and failure in FAILURES else "ordinary_evidence_invalid", details)
    details.update(response_seen=True, http_status=evidence.get("http_status"),
                   business_code=evidence.get("business_code"), response_valid=evidence.get("response_valid") is True,
                   business_hint=evidence.get("business_hint"))
    url = evidence.get("review_detail_url")
    identity = (extract_meituan_comment_request(url) if isinstance(url, str) and len(url) <= 16384
                and urlsplit(url).netloc == "me.meituan.com" else {})
    result = {"ok": bool(identity), "probe_diagnostics": details}
    if evidence.get("http_status") != 200 or evidence.get("business_code") != "10000" or not details["response_valid"]:
        raise result_failure(result)
    if not identity:
        raise ImportFailure("signed_request_missing", details)
    name = evidence.get("hotel_name")
    current = settings.get("meituan") or {}
    # Existing name is safe only after a successful request for the same configured hotel.
    if not name and str(current.get("poi_id") or "") == identity["poi_id"]:
        name = current.get("hotel_name")
    if not isinstance(name, str) or not looks_like_hotel(name):
        raise ImportFailure("hotel_name_missing", details)
    return {**result, **identity, "authenticated": True, "login_like": False, "hotel_name": name}
