"""Loopback-only pairing. Cookie values never enter this persistence file."""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time

from flask import request

import runner

EXTENSION_ID = re.compile(r"[a-p]{32}")


def pairing_path():
    return runner.CONFIG_PATH.parent.parent / "state" / "browser_cookie_pair.json"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require_loopback():
    if (request.remote_addr != "127.0.0.1" or request.scheme != "http"
            or request.host.split(":")[0] != "127.0.0.1"
            or request.headers.get("X-Forwarded-For") or request.headers.get("Forwarded")):
        raise PermissionError("扩展导入只允许通过本机 http://127.0.0.1 访问")


def extension_id():
    value = request.headers.get("X-Extension-ID", "")
    origin = request.headers.get("Origin", "")
    if not EXTENSION_ID.fullmatch(value) or origin not in {"", "chrome-extension://" + value}:
        raise PermissionError("不允许的扩展来源")
    return value


class Pairing:
    def __init__(self):
        self.lock = threading.RLock()
        self.pages = {}
        self.code = ""
        self.expires = 0
        self.attempts = 0

    def page_nonce(self):
        with self.lock:
            now = time.time()
            self.pages = {k: v for k, v in self.pages.items() if v > now}
            if len(self.pages) >= 32:
                self.pages.clear()
            nonce = secrets.token_urlsafe(32)
            self.pages[nonce] = now + 900
            return nonce

    def require_local_page(self):
        require_loopback()
        if request.headers.get("Origin") != request.host_url.rstrip("/"):
            raise PermissionError("请从本机配对页面操作")
        nonce = request.headers.get("X-Bridge-CSRF", "")
        if not nonce or self.pages.get(nonce, 0) <= time.time():
            raise PermissionError("页面已过期，请刷新后重新操作")

    def create_code(self):
        with self.lock:
            self.code = secrets.token_hex(16).upper()
            self.expires = time.time() + 180
            self.attempts = 0
            return self.code

    def pair(self, code):
        with self.lock:
            ext = extension_id()
            self.attempts += 1
            if (self.attempts > 5 or time.time() >= self.expires or not self.code
                    or not isinstance(code, str) or not secrets.compare_digest(code, self.code)):
                raise PermissionError("配对码无效、已使用或已过期，请在面板重新生成")
            token = secrets.token_urlsafe(48)
            runner.save_json(pairing_path(), {
                "extension_id": ext, "token_hash": digest(token), "expires": time.time() + 30 * 86400,
            })
            self.code = ""
            return token

    def require_extension(self):
        require_loopback()
        ext = extension_id()
        data = runner.load_json(pairing_path(), {})
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if (not token or len(token) > 200 or data.get("extension_id") != ext
                or data.get("expires", 0) <= time.time()
                or not secrets.compare_digest(str(data.get("token_hash", "")), digest(token))):
            raise PermissionError("扩展未配对或授权已失效，请重新连接")
        return digest(token)

    def revoke(self):
        with self.lock:
            pairing_path().unlink(missing_ok=True)
            self.code = ""
            self.expires = 0
