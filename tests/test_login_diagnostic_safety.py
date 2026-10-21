from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OTA采集服务"))
from login_diagnostic_safety import (
    business_codes, environment_fields, network_error, request_metadata, safe_fields, version_number,
)


class LoginDiagnosticSafetyTests(unittest.TestCase):
    def test_urls_are_reduced_to_fixed_labels_not_paths_or_subdomains(self):
        url = "https://PRIVATE.meituan.com/yoda/PRIVATE/requestcode?account=PRIVATE&token=PRIVATE#PRIVATE"
        metadata = request_metadata(url, "POST", "xhr")
        self.assertEqual(metadata, {"site": "meituan.com", "endpoint": "captcha_init", "method": "POST", "resource_type": "xhr"})
        self.assertNotIn("PRIVATE", json.dumps(metadata))
        self.assertEqual(request_metadata("https://me.meituan.com/login/index.html", "GET", "document")["endpoint"], "login")
        self.assertEqual(request_metadata("https://verify.meituan.com/v2/check", "POST", "fetch")["endpoint"], "captcha_verify")
        self.assertEqual(request_metadata("https://s3.meituan.net/PRIVATE.js?token=PRIVATE", "GET", "script")["endpoint"], "asset")

    def test_unrelated_invalid_and_credential_urls_are_ignored(self):
        for url in ("https://meituan.com.attacker.test/login", "https://example.com/", "data:SECRET",
                    "https://account:SECRET@me.meituan.com/login", "https://[broken", "file:///SECRET"):
            self.assertEqual(request_metadata(url, "GET", "document"), {})

    def test_query_string_cannot_select_an_endpoint(self):
        self.assertEqual(request_metadata("https://me.meituan.com/page?redirect=login&token=yoda", "PRIVATE", "PRIVATE"),
                         {"site": "meituan.com", "endpoint": "other", "method": "other", "resource_type": "other"})

    def test_only_small_business_codes_are_extracted(self):
        payload = {"code": 200, "status": "0", "token": "PRIVATE", "phone": "15185053254",
                   "msg": "PRIVATE", "error": {"code": 121092, "message": "PRIVATE", "token": "PRIVATE"},
                   "data": {"errorCode": "NEED_VERIFY", "account": "PRIVATE", "nested": {"code": "PRIVATE"}}}
        self.assertEqual(business_codes(payload), {"code": 200, "status": 0, "error.code": 121092, "data.errorCode": "NEED_VERIFY"})
        self.assertNotIn("PRIVATE", json.dumps(business_codes(payload)))
        for value in (True, 15185053254, "15185053254", "PRIVATE", "https://PRIVATE", "abc123", 1.2):
            self.assertEqual(business_codes({"code": value}), {})
        for payload in (None, [], "PRIVATE"):
            self.assertEqual(business_codes(payload), {})

    def test_actual_webdriver_boolean_and_versions_only(self):
        for webdriver in (True, False):
            data = environment_fields({"webdriver": webdriver, "ua": "PRIVATE Chrome/148.0.1.2 Edg/155.0.2.3 PRIVATE"})
            self.assertEqual(data, {"webdriver": webdriver, "edge_version": "155.0.2.3", "chromium_version": "148.0.1.2"})
        self.assertNotIn("webdriver", environment_fields({"webdriver": "PRIVATE"}))
        for value in ("PRIVATE", "155.1?token=PRIVATE", None, "1.2.3.4.5"):
            self.assertEqual(version_number(value), "unknown")

    def test_error_text_is_never_copied(self):
        self.assertEqual(network_error("net::ERR_CONNECTION_RESET https://PRIVATE/?token=PRIVATE"), "ERR_CONNECTION_RESET")
        self.assertEqual(network_error("PRIVATE error"), "other")
        self.assertEqual(network_error("net::ERR_PRIVATE_TOKEN PRIVATE"), "other")

    def test_final_write_boundary_drops_unexpected_fields_and_values(self):
        fields = {"url": "PRIVATE", "message": "PRIVATE", "headers": {"Cookie": "PRIVATE"},
                  "body": "PRIVATE", "account": "PRIVATE", "phase": "PRIVATE", "site": "PRIVATE",
                  "method": "POST", "webdriver": False, "request_id": 2, "browser_version": "155.0.1.2",
                  "codes": {"token": "PRIVATE", "code": "PRIVATE", "error.code": 403}, "elapsed_ms": 10**15}
        self.assertEqual(safe_fields(fields), {"method": "POST", "webdriver": False, "request_id": 2,
                                              "browser_version": "155.0.1.2", "codes": {"error.code": 403}})


if __name__ == "__main__":
    unittest.main()
