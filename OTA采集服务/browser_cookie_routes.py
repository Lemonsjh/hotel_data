"""Narrow extension bridge; no permissive CORS on existing panel APIs."""
from __future__ import annotations

import json
import re

from flask import Response, g, jsonify, render_template, request

import config_transaction as config_tx
import runner
from browser_cookie_auth import Pairing, require_loopback
from browser_cookie_import import CookieImport

API = "/api/browser-cookie/"
MAX_BODY = 512 * 1024


def register(app):
    auth = Pairing()
    imports = CookieImport()
    app.extensions["browser_cookie"] = (auth, imports)

    @app.before_request
    def protect_config_during_import():
        # Serialize mutating panel requests with import commits; GET monitoring remains available.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.path.startswith(API):
            gate = config_tx.transaction(runner.CONFIG_PATH)
            try:
                gate.__enter__()
                g.cookie_config_gate = gate
                config_tx.check_writable(runner.CONFIG_PATH)
            except (RuntimeError, OSError):
                return Response("Cookie 正在导入或配置正在使用，请完成或取消导入后再操作。", 409)

    @app.teardown_request
    def release_config_gate(error):
        gate = g.pop("cookie_config_gate", None)
        if gate:
            gate.__exit__(None, None, None)

    @app.after_request
    def bridge_headers(response):
        if request.path.startswith(API) or request.path == "/browser-cookie-import":
            response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
            origin = request.headers.get("Origin", "")
            if request.path.startswith(API) and re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin"
                response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Extension-ID"
        return response

    @app.get("/browser-cookie-import")
    def browser_cookie_page():
        try:
            require_loopback()
        except PermissionError:
            return Response("请在本机使用 http://127.0.0.1:8765/browser-cookie-import 访问。", 403)
        return render_template("browser_cookie_import.html", csrf=auth.page_nonce())

    @app.route(API + "<action>", methods=["POST", "OPTIONS"])
    def cookie_bridge(action):
        try:
            require_loopback()
            if request.method == "OPTIONS":
                if not re.fullmatch(r"chrome-extension://[a-p]{32}", request.headers.get("Origin", "")):
                    raise PermissionError("不允许的来源")
                return Response(status=204)
            if action not in {"code", "revoke", "pair", "preview", "verify", "status", "commit", "cancel"}:
                return jsonify(error="不支持的操作"), 404
            if request.mimetype != "application/json":
                return jsonify(error="仅接受 JSON 请求"), 415
            if action in {"code", "revoke"}:
                auth.require_local_page()
                if action == "code":
                    return jsonify(code=auth.create_code(), expires_in=180)
                imports.cancel()
                auth.revoke()
                return jsonify(message="扩展授权已撤销")
            # Authenticate before accepting any Cookie body.
            owner = None if action == "pair" else auth.require_extension()
            raw = request.stream.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                return jsonify(error="请求数据过大"), 413
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("请求格式错误")
            if action == "pair":
                return jsonify(token=auth.pair(data.get("code")))
            if action == "preview":
                return jsonify(imports.start(data, owner))
            if action == "status":
                return jsonify(imports.status(data.get("id"), owner))
            if action == "verify":
                return jsonify(imports.verify(data.get("id"), owner, data.get("evidence"), data))
            if action == "commit":
                return jsonify(imports.commit(data.get("id"), owner, data.get("confirmed")))
            imports.cancel(data.get("id") or "invalid", owner)
            return jsonify(message="导入已取消，原配置未修改")
        except PermissionError as exc:
            return jsonify(error=str(exc)), 403
        except (ValueError, RuntimeError) as exc:
            # Our validation messages are fixed; JSON parser errors can include input details.
            message = "请求格式错误" if isinstance(exc, (json.JSONDecodeError, UnicodeError)) else str(exc)
            return jsonify(error=message), 409
        except Exception:
            return jsonify(error="本机导入暂不可用，原配置未修改；请检查服务或重新配对"), 503
