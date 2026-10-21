"""美团人工登录的被动、限量诊断；不改请求、不重试、不保存敏感正文。"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from login_diagnostic_safety import (
    EVENTS, PHASES, business_codes, environment_fields, network_error, request_metadata, safe_fields,
)


MAX_EVENTS = 300
MAX_BYTES = 128 * 1024
MAX_BODY_BYTES = 64 * 1024
ENVIRONMENT_JS = "() => ({webdriver: navigator.webdriver, ua: navigator.userAgent})"


class LoginDiagnostics:
    def __init__(self, log_dir: Path, *, enabled: bool, switch_account: bool):
        self.path = None
        self.phase_name = "starting"
        self.started = time.monotonic()
        self.closed = False
        self.truncated = False
        self.events = self.bytes = self.sequence = self.body_reads = self.samples = 0
        self.pending = {}
        self.listeners = []
        self.pages = []
        self.environment = {}
        if not enabled:
            return
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"{stamp}_meituan_login_diagnostic_{os.getpid()}.log"
            with path.open("x", encoding="utf-8"):
                pass
            self.path = path
            self.environment = safe_fields({"playwright_version": version("playwright")})
            self.emit("started", switch_account=switch_account, **self.environment)
        except Exception:
            self.path = None

    def emit(self, event: str, *, force: bool = False, **fields) -> None:
        if self.path is None or self.closed or event not in EVENTS:
            return
        if not force and (self.truncated or self.events >= MAX_EVENTS or self.bytes >= MAX_BYTES - 1024):
            if not self.truncated:
                self.truncated = True
                self.emit("limit_reached", force=True)
            return
        record = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "event": event, "phase": self.phase_name,
                  "elapsed_ms": min(int((time.monotonic() - self.started) * 1000), 86400000)}
        record.update(safe_fields(fields))
        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            size = len(line.encode("utf-8"))
            if self.bytes + size <= MAX_BYTES:
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
                self.bytes += size
                self.events += 1
        except Exception:
            self.path = None

    def phase(self, name: str, **fields) -> None:
        if name in PHASES:
            self.phase_name = name
            self.emit("phase", **fields)

    def _listen(self, emitter, event: str, callback) -> None:
        def guarded(*args):
            if self.closed or self.path is None or self.truncated:
                return
            try:
                callback(*args)
            except Exception:
                self.emit("observation_failed")
        emitter.on(event, guarded)
        self.listeners.append((emitter, event, guarded))

    def attach(self, context) -> None:
        if self.path is None:
            return
        try:
            browser = context.browser
            self.environment.update(safe_fields({"browser_version": browser.version if browser else None}))
            self.emit("environment", **self.environment)
            for event, callback in (("request", self._request), ("response", self._response),
                                    ("requestfinished", self._finished), ("requestfailed", self._failed),
                                    ("page", self._page)):
                self._listen(context, event, callback)
            for page in context.pages:
                self._page(page)
        except Exception:
            self.emit("observation_failed")

    def _page(self, page) -> None:
        if page in self.pages or len(self.pages) >= 8:
            return
        self.pages.append(page)
        index = len(self.pages)
        self._listen(page, "domcontentloaded", lambda *_: self._sample(page, index))
        self._listen(page, "pageerror", self._script_error)

    def _sample(self, page, index: int) -> None:
        if self.samples >= 20:
            return
        self.samples += 1
        fields = environment_fields(page.evaluate(ENVIRONMENT_JS))
        self.environment.update(fields)
        self.emit("environment", page_index=index, **fields)

    def _script_error(self, error) -> None:
        name = str(error).split(":", 1)[0]
        self.emit("script_error", script_error=name if name in {"TypeError", "ReferenceError", "SyntaxError", "SecurityError"} else "other")

    def _metadata(self, request) -> dict:
        return request_metadata(request.url, request.method, request.resource_type)

    def _request(self, request) -> None:
        if len(self.pending) >= 256:
            return
        if metadata := self._metadata(request):
            self.sequence += 1
            self.pending[request] = {**metadata, "request_id": self.sequence}

    def _response(self, response) -> None:
        metadata = self.pending.get(response.request)
        if metadata:
            self.emit("http", **metadata, http_status=response.status)

    def _finished(self, request) -> None:
        metadata = self.pending.pop(request, None)
        if not metadata or self.body_reads >= 30 or metadata["resource_type"] not in {"xhr", "fetch"}:
            return
        if metadata["endpoint"] not in {"login", "captcha_init", "captcha_verify", "verification"}:
            return
        response = request.response()
        if response is None or "json" not in (response.header_value("content-type") or "").lower():
            return
        length = response.header_value("content-length")
        if length and (not length.isdecimal() or int(length) > MAX_BODY_BYTES):
            return
        self.body_reads += 1
        body = response.body()
        if len(body) <= MAX_BODY_BYTES:
            codes = business_codes(json.loads(body))
            if codes:
                self.emit("business", **metadata, codes=codes)

    def _failed(self, request) -> None:
        if metadata := self.pending.pop(request, None):
            self.emit("network_failed", **metadata, network_error=network_error(request.failure))

    def finish(self, outcome: str) -> None:
        if self.closed:
            return
        for emitter, event, callback in self.listeners:
            try:
                emitter.remove_listener(event, callback)
            except Exception:
                pass
        self.listeners.clear()
        self.pages.clear()
        self.pending.clear()
        self.emit("finished", force=True, outcome=outcome, truncated=self.truncated, **self.environment)
        self.closed = True
