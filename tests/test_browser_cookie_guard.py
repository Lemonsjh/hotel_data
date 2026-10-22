import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "OTA采集服务")]
import config_transaction as tx
import hotel_name_probe
import runner
import update_guard
from browser_cookie_payload import headers_from_payload, verified_identity
from tests.test_browser_cookie_import import payload, identity


class ConfigTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "OTA采集服务/config/settings.json"
        self.patch = patch.object(runner, "CONFIG_PATH", self.config)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        runner.save_json(self.config, {"tasks": {"pms_fetch": False}})

    def test_reservation_blocks_other_writers_but_authorizes_only_owner(self):
        with tx.transaction(self.config):
            tx.reserve(self.config, "owner")
        with self.assertRaises(RuntimeError):
            runner.save_json(self.config, {"bad": True})
        with tx.import_write("wrong"), self.assertRaises(RuntimeError):
            runner.save_json(self.config, {"bad": True})
        with tx.import_write("owner"):
            runner.save_json(self.config, {"narrow": True})
        tx.release(self.config, "wrong")
        self.assertTrue(tx.active_lease(self.config))
        tx.release(self.config, "owner")
        self.assertFalse(tx.active_lease(self.config))

    def test_cross_process_lock_prevents_simultaneous_config_transaction(self):
        code = ("import sys; from pathlib import Path; "
                "sys.path.insert(0,sys.argv[1]); import config_transaction as tx; "
                "gate=tx.transaction(Path(sys.argv[2])); gate.__enter__()")
        with tx.transaction(self.config):
            result = subprocess.run([sys.executable, "-c", code, str(ROOT / "OTA采集服务"), str(self.config)],
                                    capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        with tx.transaction(self.config):
            tx.check_writable(self.config)

    def test_business_guard_rejects_import_and_honors_update_marker(self):
        with patch.object(update_guard, "__file__", str(self.root / "update_guard.py")):
            tx.reserve(self.config, "owner")
            with self.assertRaises(RuntimeError):
                update_guard.ensure_not_updating()
            update_guard.ensure_not_updating(check_cookie_import=False)
            pending = self.root / "updater/pending.json"
            pending.parent.mkdir()
            pending.write_text("{}")
            with self.assertRaises(RuntimeError):
                update_guard.ensure_not_updating(check_cookie_import=False)

    def test_expired_reservation_does_not_block_or_delete_new_owner(self):
        tx.reserve(self.config, "old")
        lease = tx.lease_path(self.config)
        value = json.loads(lease.read_text())
        value["expires"] = 0
        lease.write_text(json.dumps(value))
        self.assertFalse(tx.active_lease(self.config))
        tx.reserve(self.config, "new")
        tx.release(self.config, "old")
        self.assertTrue(tx.active_lease(self.config))

    def test_reset_revokes_extension_pairing_without_touching_real_files(self):
        import local_reset
        from browser_cookie_auth import pairing_path
        runner.save_json(pairing_path(), {"token_hash": "fake"})
        with patch.object(local_reset, "is_collection_running", return_value=False), \
             patch.object(local_reset, "request_scheduler_stop"), \
             patch.object(local_reset, "stop_login_helpers"), \
             patch.object(local_reset, "clear_login_sessions"), \
             patch.object(local_reset, "default_settings", return_value={"tasks": {}}):
            local_reset.reset_local_configuration()
        self.assertFalse(pairing_path().exists())


class StrictProbeTests(unittest.TestCase):
    def run_probe(self, code, response_status=200, different_signature=False):
        with patch.object(hotel_name_probe, "sync_playwright") as factory:
            browser = factory.return_value.__enter__.return_value.chromium.launch.return_value
            page = browser.new_context.return_value.new_page.return_value
            page.url = hotel_name_probe.MEITUAN_URL
            page.locator.return_value.inner_text.return_value = "测试酒店"
            page.evaluate.return_value = []
            page.title.return_value = "酒店后台"
            handlers = {}
            page.on.side_effect = lambda name, callback: handlers.update({name: callback})
            request_url = identity()["review_detail_url"]
            response_url = request_url + "-new" if different_signature else request_url

            def loaded(*args, **kwargs):
                handlers["request"](SimpleNamespace(url=request_url))
                handlers["response"](SimpleNamespace(url=response_url, status=response_status,
                    json=lambda: {"code": code, "data": {"poiName": "测试酒店"}}))

            page.goto.side_effect = loaded
            return hotel_name_probe.probe("meituan", "session=fake", "eb=fake", require_authenticated=True)

    def test_successful_protected_response_is_required(self):
        self.assertTrue(self.run_probe(10000)["authenticated"])
        for code, status in ((303, 200), (10000, 403), (None, 200)):
            result = self.run_probe(code, status)
            self.assertFalse(result["authenticated"])
            with self.assertRaises(ValueError):
                verified_identity(result, {"meituan": {"poi_id": "123"}}, headers_from_payload(payload()))

    def test_fresh_successful_signature_used_instead_of_failed_first_request(self):
        result = self.run_probe(10000, different_signature=True)
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["review_detail_url"].endswith("-new"))


if __name__ == "__main__":
    unittest.main()
