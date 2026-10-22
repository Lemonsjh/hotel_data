import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))
import browser_cookie_routes
import runner
from tests.test_browser_cookie_import import identity, payload
from tests.test_browser_session_evidence import evidence

EXT = "a" * 32
ORIGIN = "chrome-extension://" + EXT
BASE = "http://127.0.0.1:8765"


class CookieBridgeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "config" / "settings.json"
        guard = patch.object(runner, "CONFIG_PATH", path)
        guard.start()
        self.addCleanup(guard.stop)
        runner.save_json(path, {"meituan": {"poi_id": "123"}, "tasks": {"pms_fetch": False}})
        self.app = Flask(__name__, template_folder=str(ROOT / "OTA采集服务/templates"))
        browser_cookie_routes.register(self.app)
        self.auth, self.imports = self.app.extensions["browser_cookie"]
        self.imports.probe = lambda _: identity()
        self.imports.idle = lambda _: None
        self.addCleanup(self.imports.cancel)
        self.client = self.app.test_client()
        self.thread = patch("browser_cookie_import.start_worker", lambda target, args: target(*args))
        self.thread.start()
        self.addCleanup(self.thread.stop)

    def post(self, action, data=None, headers=None, **kwargs):
        return self.client.post("/api/browser-cookie/" + action, base_url=BASE, json=data or {},
                                headers=headers or {}, **kwargs)

    def local_headers(self):
        response = self.client.get("/browser-cookie-import", base_url=BASE)
        self.assertEqual(response.status_code, 200)
        nonce = re.search(r'name="bridge-csrf" content="([^"]+)"', response.text)[1]
        return {"Origin": BASE, "X-Bridge-CSRF": nonce}

    def pair(self):
        code = self.post("code", headers=self.local_headers()).json["code"]
        headers = {"Origin": ORIGIN, "X-Extension-ID": EXT}
        response = self.post("pair", {"code": code}, headers)
        self.assertEqual(response.status_code, 200, response.text)
        headers["Authorization"] = "Bearer " + response.json["token"]
        return headers

    def test_pair_import_commit_never_returns_credentials(self):
        headers = self.pair()
        preview = self.post("preview", payload(), headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertNotIn("cookie-fake", preview.text)
        result = self.post("commit", {"id": preview.json["id"], "confirmed": True}, headers)
        self.assertEqual(result.json["state"], "saved")
        stored = runner.load_json(runner.CONFIG_PATH, {})
        self.assertEqual(stored["tasks"], {"pms_fetch": False})
        self.assertIn("me_cookie-fake", stored["meituan"]["me_cookie"])

    def test_ordinary_browser_verify_is_required_before_commit(self):
        self.imports.probe = None
        headers = self.pair()
        preview = self.post("preview", {**payload(), "protocol":"ordinary_browser_v1"}, headers).json
        self.assertEqual(preview["state"], "checking")
        self.assertEqual(self.post("commit", {"id":preview["id"], "confirmed":True}, headers).status_code, 409)
        verified = self.post("verify", {**payload(), "id":preview["id"], "evidence":evidence(preview["id"])}, headers)
        self.assertEqual(verified.json["state"], "ready")
        self.assertNotIn("mtgsig", verified.text)

    def test_pair_code_single_use_and_revocation(self):
        local = self.local_headers()
        code = self.post("code", headers=local).json["code"]
        ext = {"Origin": ORIGIN, "X-Extension-ID": EXT}
        paired = self.post("pair", {"code": code}, ext)
        self.assertEqual(self.post("pair", {"code": code}, ext).status_code, 403)
        token = paired.json["token"]
        record = (runner.CONFIG_PATH.parent.parent / "state/browser_cookie_pair.json").read_text()
        self.assertNotIn(token, record)
        ext["Authorization"] = "Bearer " + token
        self.assertEqual(self.post("revoke", headers=local).status_code, 200)
        self.assertEqual(self.post("preview", payload(), ext).status_code, 403)

    def test_five_failed_pair_attempts_require_new_code(self):
        code = self.post("code", headers=self.local_headers()).json["code"]
        ext = {"Origin": ORIGIN, "X-Extension-ID": EXT}
        for _ in range(5):
            self.assertEqual(self.post("pair", {"code": "bad"}, ext).status_code, 403)
        self.assertEqual(self.post("pair", {"code": code}, ext).status_code, 403)

    def test_cross_origin_auth_and_body_limits(self):
        self.assertEqual(self.post("code").status_code, 403)
        headers = self.pair()
        self.assertEqual(self.post("preview", payload(), dict(headers, Origin="https://evil.example")).status_code, 403)
        self.assertEqual(self.post("preview", payload(), dict(headers, **{"X-Extension-ID": "b" * 32})).status_code, 403)
        self.assertEqual(self.post("preview", payload(), headers, environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)
        oversized = self.client.post("/api/browser-cookie/preview", base_url=BASE, headers=headers,
                                     content_type="application/json", data=b"x" * (512 * 1024 + 1))
        self.assertEqual(oversized.status_code, 413)

    def test_options_cors_is_not_a_wildcard_or_existing_route(self):
        good = self.client.options("/api/browser-cookie/preview", base_url=BASE, headers={"Origin": ORIGIN})
        self.assertEqual(good.status_code, 204)
        self.assertEqual(good.headers["Access-Control-Allow-Origin"], ORIGIN)
        bad = self.client.options("/api/browser-cookie/preview", base_url=BASE, headers={"Origin": "https://evil.example"})
        self.assertEqual(bad.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", bad.headers)
        self.assertEqual(self.client.get("/browser-cookie-import", base_url="http://evil.example").status_code, 403)

    def test_page_no_secrets_and_no_store_frame_protection(self):
        result = self.client.get("/browser-cookie-import", base_url=BASE)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(result.headers["X-Frame-Options"], "DENY")
        self.assertNotIn("old-main", result.text)

    def test_failed_preview_does_not_log_arbitrary_exception(self):
        self.imports.probe = lambda _: (_ for _ in ()).throw(RuntimeError("PRIVATE_COOKIE"))
        result = self.post("preview", payload(), self.pair())
        self.assertEqual(result.json["state"], "failed")
        self.assertNotIn("PRIVATE_COOKIE", result.text)
        self.assertTrue(all(result.json["groups"].values()))
        self.assertEqual(result.json["failure_code"], "probe_failed")

    def test_mutating_panel_routes_are_blocked_until_cancel(self):
        @self.app.post("/config")
        def save_fake():
            return "changed"
        headers = self.pair()
        preview = self.post("preview", payload(), headers)
        self.assertEqual(self.client.post("/config", base_url=BASE).status_code, 409)
        self.post("cancel", {"id": preview.json["id"]}, headers)
        self.assertEqual(self.client.post("/config", base_url=BASE).status_code, 200)


if __name__ == "__main__":
    unittest.main()
