import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))
import config_transaction as tx
import runner
from browser_cookie_import import CookieImport, require_idle
from browser_cookie_payload import HOSTS, headers_from_payload, scoped_cookies_from_payload, verified_identity


def payload():
    return {"groups": {key: [{"name": {"me_cookie":"mebsid", "eb_cookie":"ebbsid"}.get(key, "session"), "value": key + "-fake", "domain": host,
                                "hostOnly": True, "path": "/", "storeId": "0"}]
                       for key, host in HOSTS.items()}}


def identity(poi="123"):
    return {"ok": True, "authenticated": True, "hotel_name": "测试酒店", "poi_id": poi, "partner_id": "456",
            "biz_account_id": "789", "review_detail_url":
            f"https://me.meituan.com/api/gw/v1/base/comments/queryGeneralCommentInfo?poiId={poi}&partnerId=456&bizAccountId=789&mtgsig=fake"}


class CookiePayloadTests(unittest.TestCase):
    def test_all_five_headers_and_http_only_values(self):
        data = payload()
        data["groups"]["me_cookie"][0]["httpOnly"] = True
        headers = headers_from_payload(data)
        self.assertEqual(set(headers), set(HOSTS))
        self.assertEqual(headers["me_cookie"], "mebsid=me_cookie-fake")

    def test_parent_domain_and_identical_duplicate_are_allowed(self):
        data = payload()
        item = data["groups"]["me_cookie"][0]
        item.update(domain=".meituan.com", hostOnly=False)
        data["groups"]["me_cookie"].append(dict(item, path="/api"))
        self.assertEqual(headers_from_payload(data)["me_cookie"], "mebsid=me_cookie-fake")

    def test_probe_cookies_keep_original_domain_path_and_flags(self):
        data = payload()
        shared = {"name": "shared", "value": "fake-parent", "domain": ".meituan.com",
                  "hostOnly": False, "path": "/ebooking", "storeId": "0",
                  "secure": True, "httpOnly": True, "sameSite": "lax"}
        data["groups"]["me_cookie"].append(shared)
        data["groups"]["eb_cookie"].append(shared.copy())
        headers_from_payload(data)
        cookies = scoped_cookies_from_payload(data)
        self.assertEqual(len(cookies), 3)
        self.assertIn({"name": "shared", "value": "fake-parent", "domain": ".meituan.com",
                       "path": "/ebooking", "secure": True, "httpOnly": True,
                       "sameSite": "Lax"}, cookies)
        self.assertIn({"name": "mebsid", "value": "me_cookie-fake",
                       "domain": "me.meituan.com", "path": "/"}, cookies)
        self.assertNotIn({"name": "mebsid", "value": "me_cookie-fake",
                          "domain": ".eb.meituan.com", "path": "/"}, cookies)

    def test_invalid_cookie_forms_rejected_without_echoing_values(self):
        mutations = [
            {"domain": "evil.example"}, {"domain": "tdc.meituan.com"},
            {"name": "bad;name"}, {"value": "SECRET\r\nCookie: bad"},
            {"value": "SECRET;bad=1"}, {"partitionKey": {}},
            {"expirationDate": 0}, {"expirationDate": float("nan")},
            {"path": "invalid"}, {"storeId": ""},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = payload()
                data["groups"]["me_cookie"][0].update(mutation)
                with self.assertRaises(ValueError) as caught:
                    headers_from_payload(data)
                self.assertNotIn("SECRET", str(caught.exception))

    def test_duplicate_different_values_or_mixed_stores_rejected(self):
        for change in ({"value": "other"}, {"storeId": "1"}):
            data = payload()
            data["groups"]["me_cookie"].append(dict(data["groups"]["me_cookie"][0], **change))
            with self.assertRaises(ValueError):
                headers_from_payload(data)

    def test_required_groups_and_main_sessions(self):
        for key in ("me_cookie", "eb_cookie"):
            data = payload()
            data["groups"][key] = []
            with self.assertRaises(ValueError):
                headers_from_payload(data)
        data = payload()
        data["groups"]["unexpected_cookie"] = []
        with self.assertRaises(ValueError):
            headers_from_payload(data)

    def test_tracking_only_cookie_is_not_a_login_or_optional_overwrite(self):
        data = payload()
        data["groups"]["me_cookie"][0]["name"] = "uuid"
        with self.assertRaisesRegex(ValueError, "mebsid"):
            headers_from_payload(data)
        data = payload()
        data["groups"]["highlights_cookie"][0]["name"] = "uuid"
        self.assertEqual(headers_from_payload(data)["highlights_cookie"], "")

    def test_failed_or_wrong_hotel_does_not_pass(self):
        headers = headers_from_payload(payload())
        for result in ({"ok": False}, identity("999"), dict(identity(), review_detail_url="https://evil.example/?mtgsig=fake")):
            with self.assertRaises(ValueError):
                verified_identity(result, {"meituan": {"poi_id": "123"}}, headers)


class CookieImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "config" / "settings.json"
        self.config_patch = patch.object(runner, "CONFIG_PATH", self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.settings = {"meituan": {"poi_id": "123", "me_cookie": "old-main", "promotion_cookie": "old-promo"},
                         "mysql": {"database": "fake-db"}, "tasks": {"pms_fetch": False},
                         "pms": {"provider": "foxhis"}}
        runner.save_json(self.config, self.settings)
        self.thread = patch("browser_cookie_import.start_worker", lambda target, args: target(*args))
        self.thread.start()
        self.addCleanup(self.thread.stop)
        self.imports = CookieImport(probe=lambda _: identity(), idle=lambda _: None)
        self.addCleanup(self.imports.cancel)

    def preview(self, data=None):
        return self.imports.start(data or payload(), "owner")

    def test_preview_does_not_save_commit_is_narrow_and_one_time(self):
        before = self.config.read_bytes()
        job = self.preview()
        self.assertEqual(job["state"], "ready")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertNotIn("fake", json.dumps(job))
        result = self.imports.commit(job["id"], "owner", True)
        saved = runner.load_json(self.config, {})
        self.assertEqual(result["state"], "saved")
        self.assertEqual(saved["meituan"]["me_cookie"], "mebsid=me_cookie-fake")
        for section in ("tasks", "mysql", "pms"):
            self.assertEqual(saved[section], self.settings[section])
        self.assertFalse(tx.active_lease(self.config))
        with self.assertRaises(ValueError):
            self.imports.commit(job["id"], "owner", True)

    def test_preview_passes_scoped_cookies_without_persisting_them(self):
        seen = []
        self.imports.probe = lambda headers: (seen.extend(headers.scoped_cookies), identity())[1]
        job = self.preview()
        self.assertEqual(job["state"], "ready")
        self.assertEqual(len(seen), 2)  # Only ME/EB are used for identity verification.
        self.assertNotIn("scoped_cookies", runner.load_json(self.config, {}))

    def test_missing_optional_does_not_clear_same_hotel_cookie(self):
        data = payload()
        data["groups"]["promotion_cookie"] = []
        job = self.preview(data)
        self.imports.commit(job["id"], "owner", True)
        self.assertEqual(runner.load_json(self.config, {})["meituan"]["promotion_cookie"], "old-promo")

    def test_wrong_hotel_or_probe_exception_keeps_original_and_redacts(self):
        before = self.config.read_bytes()
        for probe in (lambda _: identity("999"), lambda _: (_ for _ in ()).throw(RuntimeError("SECRET Cookie=leak"))):
            self.imports.probe = probe
            job = self.preview()
            self.assertEqual(job["state"], "failed")
            self.assertNotIn("SECRET", json.dumps(job))
            self.assertNotIn("headers", self.imports.job)
            self.assertEqual(job["groups"], dict.fromkeys(HOSTS, True))
            self.assertEqual(self.imports.status(job["id"], "owner")["groups"], job["groups"])
            self.assertEqual(self.config.read_bytes(), before)
            self.assertFalse(tx.active_lease(self.config))
            self.imports.cancel()

    def test_business_failure_preserves_read_summary_but_refuses_save(self):
        self.imports.probe = lambda _: {**identity(), "authenticated": False,
            "probe_diagnostics": {"response_seen": True, "http_status": 200, "business_code": 303}}
        job = self.preview()
        self.assertEqual(job["failure_code"], "review_business_rejected")
        self.assertEqual(job["failure_details"]["business_code"], "303")
        self.assertEqual(job["groups"], dict.fromkeys(HOSTS, True))
        self.assertNotIn("headers", self.imports.job)
        self.assertNotIn("me_cookie-fake", json.dumps(job))
        with self.assertRaises(ValueError):
            self.imports.commit(job["id"], "owner", True)
        self.assertEqual(runner.load_json(self.config, {}), self.settings)

    def test_owner_confirmation_and_config_revision_required(self):
        job = self.preview()
        for owner, confirmation in (("other", True), ("owner", False)):
            with self.assertRaises(ValueError):
                self.imports.commit(job["id"], owner, confirmation)
        with self.assertRaises(RuntimeError):
            runner.save_json(self.config, self.settings)
        # Simulate a non-cooperating external editor; still never overwrite its change.
        self.config.write_text(json.dumps({**self.settings, "external_edit": True}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.imports.commit(job["id"], "owner", True)
        self.assertTrue(runner.load_json(self.config, {})["external_edit"])

    def test_expiration_and_cancellation_release_reservation(self):
        job = self.preview()
        self.imports.cancel(job["id"], "owner")
        self.assertFalse(tx.active_lease(self.config))
        job = self.preview()
        self.imports.job["expires"] = time.time() - 1
        with self.assertRaises(ValueError):
            self.imports.status(job["id"], "owner")
        self.assertFalse(tx.active_lease(self.config))

    def test_commit_io_failure_preserves_old_atomic_file(self):
        before = self.config.read_bytes()
        job = self.preview()
        with patch("runner.os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.imports.commit(job["id"], "owner", True)
        self.assertEqual(self.config.read_bytes(), before)

    def test_active_work_is_checked_again_before_commit(self):
        job = self.preview()
        self.imports.idle = lambda _: (_ for _ in ()).throw(ValueError("busy"))
        with self.assertRaises(ValueError):
            self.imports.commit(job["id"], "owner", True)
        self.assertEqual(runner.load_json(self.config, {}), self.settings)

    def test_task_and_scheduler_checks_use_no_real_state(self):
        with patch("platform_login.read_status", return_value={}), \
             patch("runner.load_status", return_value={"last_run_status": "running"}):
            with self.assertRaises(ValueError):
                require_idle({})
        with patch("runner.load_status", return_value={}), self.assertRaises(ValueError):
            require_idle({"service": {"scheduler_enabled": True}})


if __name__ == "__main__":
    unittest.main()
