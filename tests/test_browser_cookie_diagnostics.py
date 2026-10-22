import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import Error, TimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "OTA采集服务")]
import hotel_name_probe
from browser_cookie_diagnostics import ImportFailure, ProbeDiagnostics, result_failure, safe_details
from browser_cookie_import import probe_current
from browser_cookie_payload import verified_identity, headers_from_payload
from tests.test_browser_cookie_import import identity, payload
from tests import test_browser_cookie_guard as guard_tests


class CookieDiagnosticTests(unittest.TestCase):
    def test_only_allowlisted_scalars_survive(self):
        details = safe_details({"phase": "page_load", "http_status": 403, "business_code": 303,
            "expected_poi_id": "123", "detected_poi_id": "456", "network_code": "ERR_CONNECTION_CLOSED",
            "request_seen": True, "url": "https://private.example/?secret=PRIVATE",
            "error": "PRIVATE", "headers": {"Cookie": "PRIVATE"}, "response": "PRIVATE"})
        self.assertEqual(details["http_status"], 403)
        self.assertEqual(details["business_code"], "303")
        self.assertNotIn("PRIVATE", json.dumps(details))
        self.assertEqual(safe_details({"phase": ["PRIVATE"], "http_status": True,
            "business_code": "PRIVATE", "expected_poi_id": "PRIVATE", "network_code": {"PRIVATE": 1}}), {})
        self.assertEqual(safe_details({"business_hint": "invalid_parameters"}), {"business_hint": "invalid_parameters"})
        self.assertEqual(safe_details({"business_hint": "PRIVATE"}), {})

    def test_failure_has_static_message_not_exception_text(self):
        failure = ImportFailure("unrecognized PRIVATE", {"error": "PRIVATE", "phase": "page_load"})
        self.assertEqual(failure.code, "probe_failed")
        self.assertNotIn("PRIVATE", json.dumps(failure.public()))

    def test_result_failure_categories(self):
        cases = [
            ({"ok": False, "probe_diagnostics": {"page_http_status": 503}}, "page_http_error"),
            ({"ok": False, "login_like": True, "probe_diagnostics": {"login_redirect": True}}, "login_redirect"),
            ({"ok": False, "login_like": True, "probe_diagnostics": {"login_redirect": False}}, "signed_request_missing"),
            ({"ok": False}, "signed_request_missing"),
            ({"ok": True}, "review_response_missing"),
            ({"ok": True, "probe_diagnostics": {"response_seen": True, "http_status": 403}}, "review_access_denied"),
            ({"ok": True, "probe_diagnostics": {"response_seen": True, "http_status": 429}}, "review_rate_limited"),
            ({"ok": True, "probe_diagnostics": {"response_seen": True, "http_status": 200}}, "review_format_invalid"),
            ({"ok": True, "probe_diagnostics": {"response_seen": True, "http_status": 200, "business_code": 303}}, "review_business_rejected"),
            ({"ok": True, "probe_diagnostics": {"response_seen": True, "http_status": 200, "business_code": 10000}}, "review_format_invalid"),
        ]
        for result, code in cases:
            with self.subTest(code=code):
                self.assertEqual(result_failure(result).code, code)

    def test_hotel_mismatch_has_two_ids_but_no_sensitive_result_fields(self):
        result = {**identity("456"), "error": "PRIVATE", "current_url": "https://private.example"}
        with self.assertRaises(ImportFailure) as caught:
            verified_identity(result, {"meituan": {"poi_id": "123"}}, headers_from_payload(payload()))
        public = caught.exception.public()
        self.assertEqual(public["failure_code"], "hotel_mismatch")
        self.assertEqual(public["failure_details"], {"expected_poi_id": "123", "detected_poi_id": "456"})
        self.assertNotIn("PRIVATE", json.dumps(public))

    def test_missing_name_and_parameters_are_not_called_hotel_mismatch(self):
        headers = headers_from_payload(payload())
        for result, code in ((dict(identity(), hotel_name=""), "hotel_name_missing"),
                             (dict(identity(), partner_id=""), "hotel_parameters_missing")):
            with self.assertRaises(ImportFailure) as caught:
                verified_identity(result, {"meituan": {"poi_id": "123"}}, headers)
            self.assertEqual(caught.exception.code, code)

    def test_browser_start_error_is_distinct_and_sanitized(self):
        with patch.object(hotel_name_probe, "sync_playwright") as factory:
            factory.return_value.__enter__.return_value.chromium.launch.side_effect = Error("PRIVATE Cookie=PRIVATE")
            with self.assertRaises(ImportFailure) as caught:
                probe_current(headers_from_payload(payload()))
        self.assertEqual(caught.exception.code, "browser_start_failed")
        self.assertNotIn("PRIVATE", json.dumps(caught.exception.public()))

    def test_navigation_timeout_and_network_errors_are_distinct(self):
        for exc, code in ((TimeoutError("PRIVATE"), "page_timeout"),
                          (Error("PRIVATE net::ERR_CONNECTION_CLOSED https://private.example"), "page_network_error")):
            with patch.object(hotel_name_probe, "sync_playwright") as factory:
                browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
                browser.new_context.return_value.new_page.return_value.goto.side_effect = exc
                with self.assertRaises(ImportFailure) as caught:
                    probe_current(headers_from_payload(payload()))
            public = caught.exception.public()
            self.assertEqual(public["failure_code"], code)
            self.assertEqual(public["failure_details"]["phase"], "page_load")
            self.assertNotIn("PRIVATE", json.dumps(public))
            self.assertNotIn("https:", json.dumps(public))

    def test_real_probe_callbacks_record_only_review_response_codes(self):
        result = guard_tests.StrictProbeTests().run_probe(303)
        details = result["probe_diagnostics"]
        self.assertTrue(details["request_seen"])
        self.assertTrue(details["response_seen"])
        self.assertEqual(details["http_status"], 200)
        self.assertEqual(details["business_code"], "303")
        self.assertNotIn("review_detail_url", details)

    def test_observer_ignores_unrelated_response_and_network_text(self):
        observer = ProbeDiagnostics()
        self.assertFalse(observer.response("https://evil.example/api/gw/v1/base/comments/queryGeneralCommentInfo", 403))
        observer.network("PRIVATE unknown network error")
        self.assertNotIn("PRIVATE", json.dumps(observer.safe()))
        self.assertNotIn("http_status", observer.safe())

    def test_login_redirect_requires_known_login_url(self):
        observer = ProbeDiagnostics()
        observer.location("https://me.meituan.com/ebooking/merchant/comment-manage-react#/home")
        self.assertIs(observer.safe()["login_redirect"], False)
        observer.location("https://me.meituan.com/login/index.html?redirect=PRIVATE")
        self.assertIs(observer.safe()["login_redirect"], True)
        observer.location("https://evil.example/login")
        self.assertIs(observer.safe()["login_redirect"], False)


if __name__ == "__main__":
    unittest.main()
