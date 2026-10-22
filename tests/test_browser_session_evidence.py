import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "OTA采集服务")]
import runner
import hotel_name_probe
from browser_cookie_import import CookieImport
from browser_session_evidence import SOURCE
from tests.test_browser_cookie_import import identity, payload


def evidence(job_id, **changes):
    return {"source": SOURCE, "challenge": job_id, "checked_at": time.time() * 1000,
            "http_status": 200, "business_code": "10000", "response_valid": True,
            "review_detail_url": identity()["review_detail_url"], "hotel_name": "测试酒店", **changes}


class OrdinaryBrowserImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / "config/settings.json"
        guard = patch.object(runner, "CONFIG_PATH", self.config)
        guard.start()
        self.addCleanup(guard.stop)
        runner.save_json(self.config, {"meituan": {"poi_id": "123", "hotel_name": "原配置酒店"}, "tasks": {}})
        self.before = self.config.read_bytes()
        self.imports = CookieImport(idle=lambda _: None)
        self.addCleanup(self.imports.cancel)

    def preview(self):
        return self.imports.start({**payload(), "protocol": SOURCE}, "owner")

    def test_default_flow_never_opens_another_browser_and_only_saves_after_confirmation(self):
        with patch.object(hotel_name_probe, "sync_playwright") as browser:
            job = self.preview()
            self.assertEqual(job["state"], "checking")
            ready = self.imports.verify(job["id"], "owner", evidence(job["id"]), payload())
            self.assertEqual(ready["state"], "ready")
            self.assertEqual(self.config.read_bytes(), self.before)
            self.imports.commit(job["id"], "owner", True)
            self.assertEqual(runner.load_json(self.config, {})["meituan"]["me_cookie"], "mebsid=me_cookie-fake")
            browser.assert_not_called()

    def test_old_extension_receives_upgrade_message(self):
        with self.assertRaisesRegex(ValueError, "0.2.0"):
            self.imports.start(payload(), "owner")

    def test_failed_response_wrong_hotel_stale_or_replayed_evidence_cannot_save(self):
        cases = [({"business_code": "303"}, "review_business_rejected"),
                 ({"business_code": "1000", "business_hint": "invalid_parameters"}, "review_business_rejected"),
                 ({"http_status": 403}, "review_access_denied"),
                 ({"http_status": 429}, "review_rate_limited"),
                 ({"checked_at": 0}, "ordinary_evidence_expired"),
                 ({"challenge": "another-job"}, "ordinary_evidence_invalid"),
                 ({"review_detail_url": identity("999")["review_detail_url"]}, "hotel_mismatch"),
                 ({"review_detail_url": "https://evil.example/?PRIVATE"}, "signed_request_missing"),
                 ({"failure_code": "ordinary_page_missing"}, "ordinary_page_missing")]
        for changes, code in cases:
            with self.subTest(code=code):
                job = self.preview()
                result = self.imports.verify(job["id"], "owner", evidence(job["id"], **changes), payload())
                self.assertEqual(result["failure_code"], code)
                self.assertEqual(result["groups"], dict.fromkeys(result["groups"], True))
                self.assertNotIn("headers", self.imports.job)
                self.assertNotIn("PRIVATE", json.dumps(result))
                with self.assertRaises(ValueError):
                    self.imports.commit(job["id"], "owner", True)
                self.assertEqual(self.config.read_bytes(), self.before)
                self.imports.cancel()

    def test_existing_name_requires_successful_same_hotel_response(self):
        job = self.preview()
        result = self.imports.verify(job["id"], "owner", evidence(job["id"], hotel_name=""), payload())
        self.assertEqual(result["hotel_name"], "原配置酒店")

    def test_changed_config_or_other_extension_cannot_finish_an_old_job(self):
        job = self.preview()
        with self.assertRaises(ValueError):
            self.imports.verify(job["id"], "other", evidence(job["id"]), payload())
        with patch("browser_cookie_import.config_digest", return_value="changed"):
            result = self.imports.verify(job["id"], "owner", evidence(job["id"]), payload())
        self.assertEqual(result["failure_code"], "ordinary_config_changed")


if __name__ == "__main__":
    unittest.main()
