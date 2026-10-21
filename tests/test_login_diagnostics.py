from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "OTA采集服务"))
from login_diagnostics import ENVIRONMENT_JS, MAX_BODY_BYTES, MAX_BYTES, MAX_EVENTS, LoginDiagnostics


class Emitter:
    def __init__(self):
        self.handlers = {}

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.handlers[event].remove(callback)

    def fire(self, event, *args):
        for callback in list(self.handlers.get(event, [])):
            callback(*args)


class FakePage(Emitter):
    def __init__(self):
        super().__init__()
        self.reads = 0

    def evaluate(self, script):
        assert script == ENVIRONMENT_JS
        self.reads += 1
        return {"webdriver": False, "ua": "PRIVATE Chrome/148.0.1.2 Edg/155.0.2.3"}


class FakeContext(Emitter):
    def __init__(self):
        super().__init__()
        self.pages = [FakePage()]
        self.browser = SimpleNamespace(version="155.0.2.3")


class FakeRequest:
    def __init__(self, url="https://verify.meituan.com/v2/yoda/requestcode?token=PRIVATE"):
        self.url, self.method, self.resource_type = url, "POST", "xhr"
        self.failure = "net::ERR_CONNECTION_RESET https://PRIVATE"
        self.reply = FakeResponse(self)

    @property
    def headers(self):
        raise AssertionError("不能读取请求头")

    @property
    def post_data(self):
        raise AssertionError("不能读取请求体")

    def response(self):
        return self.reply


class FakeResponse:
    def __init__(self, request):
        self.request, self.status = request, 200
        self.payload = b'{"code":200,"error":{"code":121092,"message":"PRIVATE"},"token":"PRIVATE"}'
        self.content_type, self.length = "application/json", None
        self.reads = 0

    def header_value(self, name):
        assert name in {"content-type", "content-length"}
        return self.content_type if name == "content-type" else self.length

    def body(self):
        self.reads += 1
        return self.payload


class LoginDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".test_login_diag_", dir=ROOT)
        self.folder = Path(self.directory.name)
        self.diag = LoginDiagnostics(self.folder, enabled=True, switch_account=True)
        self.path = self.diag.path
        self.context = FakeContext()
        self.diag.attach(self.context)

    def tearDown(self):
        self.diag.finish("ended")
        self.directory.cleanup()

    def records(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def complete(self, request):
        self.context.fire("request", request)
        self.context.fire("response", request.reply)
        self.context.fire("requestfinished", request)

    def test_http_200_business_failure_and_environment_are_durable_and_redacted(self):
        self.diag.phase("waiting_main", had_session=False)
        self.context.pages[0].fire("domcontentloaded")
        request = FakeRequest()
        self.complete(request)
        records = self.records()  # 登录窗口还没关闭，事件也已经落盘。
        self.assertTrue(any(r.get("webdriver") is False for r in records))
        self.assertTrue(any(r.get("http_status") == 200 for r in records))
        business = next(r for r in records if r["event"] == "business")
        self.assertEqual(business["codes"], {"code": 200, "error.code": 121092})
        self.assertEqual(business["phase"], "waiting_main")
        self.assertEqual(request.reply.reads, 1)
        self.assertNotIn("PRIVATE", self.path.read_text(encoding="utf-8"))

    def test_failed_requests_script_errors_and_popup_are_observed(self):
        request = FakeRequest()
        self.context.fire("request", request)
        self.context.fire("requestfailed", request)
        popup = FakePage()
        self.context.fire("page", popup)
        popup.fire("domcontentloaded")
        popup.fire("pageerror", Exception("TypeError: account PRIVATE token PRIVATE"))
        self.assertEqual(next(r for r in self.records() if r["event"] == "network_failed")["network_error"], "ERR_CONNECTION_RESET")
        self.assertEqual(next(r for r in self.records() if r["event"] == "script_error")["script_error"], "TypeError")
        self.assertNotIn("PRIVATE", self.path.read_text(encoding="utf-8"))

    def test_no_body_read_for_unrelated_static_non_json_or_oversized_data(self):
        unrelated = FakeRequest("https://private.example/login")
        static = FakeRequest(); static.resource_type = "script"
        non_json = FakeRequest(); non_json.reply.content_type = "text/html"
        large = FakeRequest(); large.reply.length = str(MAX_BODY_BYTES + 1)
        for request in (unrelated, static, non_json, large):
            self.complete(request)
            self.assertEqual(request.reply.reads, 0)

    def test_large_or_malformed_body_never_appears_in_log(self):
        for body in (b"PRIVATE", b"PRIVATE" * MAX_BODY_BYTES):
            request = FakeRequest(); request.reply.payload = body
            self.complete(request)
        self.assertNotIn("PRIVATE", self.path.read_text(encoding="utf-8"))
        self.assertTrue(any(r["event"] == "observation_failed" for r in self.records()))

    def test_body_sampling_is_bounded(self):
        requests = [FakeRequest() for _ in range(35)]
        for request in requests:
            self.complete(request)
        self.assertEqual(sum(r.reply.reads for r in requests), 30)

    def test_record_limit_reserves_terminal_result_and_detaches(self):
        for _ in range(MAX_EVENTS + 30):
            self.diag.emit("http", http_status=200, message="PRIVATE")
        self.diag.finish("cancelled")
        self.diag.finish("failed")
        self.assertLessEqual(self.path.stat().st_size, MAX_BYTES)
        self.assertEqual(sum(r["event"] == "limit_reached" for r in self.records()), 1)
        self.assertEqual(self.records()[-1]["outcome"], "cancelled")
        self.assertEqual(self.records()[-1]["browser_version"], "155.0.2.3")
        before = self.path.read_bytes()
        self.context.pages[0].fire("domcontentloaded")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(all(not callbacks for callbacks in self.context.handlers.values()))

    def test_diagnostic_io_and_callback_errors_do_not_escape(self):
        with patch.object(self.context.pages[0], "evaluate", side_effect=RuntimeError("PRIVATE")):
            self.context.pages[0].fire("domcontentloaded")
        with patch.object(Path, "open", side_effect=OSError("PRIVATE")):
            self.diag.emit("phase", phase="saving")
            self.diag.finish("failed")
            failed = LoginDiagnostics(self.folder, enabled=True, switch_account=False)
        self.assertIsNone(failed.path)
        self.assertNotIn("PRIVATE", self.path.read_text(encoding="utf-8"))

    def test_disabled_diagnostic_creates_no_files_or_listeners(self):
        before = list(self.folder.iterdir())
        disabled = LoginDiagnostics(self.folder, enabled=False, switch_account=False)
        context = FakeContext()
        disabled.attach(context); disabled.phase("waiting_main"); disabled.finish("success")
        self.assertEqual(list(self.folder.iterdir()), before)
        self.assertEqual(context.handlers, {})

    def test_environment_sampling_is_bounded_and_does_not_modify_pages(self):
        page = self.context.pages[0]
        for _ in range(40):
            page.fire("domcontentloaded")
        self.assertEqual(page.reads, 20)

    def test_existing_log_page_lists_and_reads_the_diagnostic(self):
        from flask import Flask
        import log_routes

        app = Flask("diagnostic-log-test")
        log_routes.register(app)
        with patch.object(log_routes.runner, "LOG_DIR", self.folder), \
             patch.object(log_routes, "page", side_effect=lambda title, body, *args: body):
            client = app.test_client()
            listing = client.get("/logs")
            detail = client.get("/log", query_string={"path": str(self.path)})
        self.assertEqual(listing.status_code, 200)
        self.assertIn(self.path.name.encode(), listing.data)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"started", detail.data)


if __name__ == "__main__":
    unittest.main()
