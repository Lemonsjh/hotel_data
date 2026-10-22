"""Bounded in-memory preview; only explicit confirmation writes existing settings."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

import config_transaction as config_tx
import platform_login
import runner
from browser_cookie_payload import HOSTS, headers_from_payload, scoped_cookies_from_payload, verified_identity
from browser_cookie_diagnostics import ImportFailure, ProbeDiagnostics
from browser_session_evidence import browser_session_result, SOURCE


class CookieHeaders(dict):
    """Legacy collector headers plus short-lived scoped Cookies for verification."""


def config_digest():
    return hashlib.sha256(runner.CONFIG_PATH.read_bytes()).hexdigest()


def require_idle(settings):
    from update_guard import ensure_not_updating
    ensure_not_updating(check_cookie_import=False)
    config_tx.check_writable(runner.CONFIG_PATH)
    if runner.load_status().get("last_run_status") in {"starting", "running", "stopping"}:
        raise ValueError("采集仍在运行，请等待结束后导入")
    for section, key in (("service", "scheduler_enabled"), ("price_scheduler", "enabled"),
                         ("reply_scheduler", "enabled"), ("promotion_scheduler", "enabled")):
        if (settings.get(section) or {}).get(key):
            raise ValueError("请先暂停自动采集、调价、评价回复及推广控制调度，再导入 Cookie")
    for platform in platform_login.PLATFORMS:
        status = platform_login.read_status(platform)
        if runner.process_alive(status.get("pid")):
            raise ValueError("请先关闭登录助手，再使用普通浏览器扩展导入")
    # A stopping scheduler may still hold an old settings object; wait for process exit.
    state = runner.CONFIG_PATH.parent.parent / "state"
    for name in ("manual", "price", "review_reply", "promotion"):
        status = runner.load_json(state / f"{name}_scheduler_status.json", {})
        if runner.process_alive(status.get("pid")):
            raise ValueError("后台调度进程尚未退出，请稍后重试")
    if (platform_login.profile_path("meituan") / ".capture.lock").exists():
        raise ValueError("美团浏览器正被使用或锁未释放，请先检查正在执行的操作")


def probe_current(headers):
    # In-process worker: do not expose credentials via child-process command lines.
    from hotel_name_probe import probe
    diagnostics = ProbeDiagnostics()
    try:
        return probe("meituan", headers["me_cookie"], headers["eb_cookie"], timeout_ms=30000,
                     require_authenticated=True, diagnostics=diagnostics,
                     scoped_cookies=getattr(headers, "scoped_cookies", None))
    except Exception as exc:
        raise diagnostics.exception(exc) from None


def start_worker(target, args):
    threading.Thread(target=target, args=args, daemon=True).start()


class CookieImport:
    def __init__(self, probe=None, idle=None):
        self.probe = probe
        self.idle = idle or require_idle
        self.lock = threading.RLock()
        self.job = None
        self.timer = None

    def expire(self):
        if self.job and self.job["expires"] <= time.time():
            config_tx.release(runner.CONFIG_PATH, self.job["id"])
            self.job = None

    def _expire_due(self):
        with self.lock:
            self.expire()

    def start(self, payload, owner):
        if self.probe is None and payload.get("protocol") != SOURCE:
            raise ValueError("请在扩展管理页重新加载 0.2.0 或更新版本，再读取 Cookie")
        headers = CookieHeaders(headers_from_payload(payload))
        headers.scoped_cookies = scoped_cookies_from_payload(payload)
        with self.lock, config_tx.transaction(runner.CONFIG_PATH):
            self.expire()
            if self.job:
                raise ValueError("已有导入正在核对或等待确认，请先完成或取消")
            settings = runner.load_json(runner.CONFIG_PATH, {})
            self.idle(settings)
            job = {"id": secrets.token_urlsafe(24), "owner": owner, "state": "checking",
                   "expires": time.time() + 180, "headers": headers,
                   "groups": {key: bool(value) for key, value in headers.items()},
                   "config_digest": config_digest(), "message": "正在普通浏览器中核对酒店，尚未保存",
                   "verification_mode": SOURCE}
            config_tx.reserve(runner.CONFIG_PATH, job["id"])
            self.job = job
            self.timer = threading.Timer(181, self._expire_due)
            self.timer.daemon = True
            self.timer.start()
            if self.probe:
                start_worker(self._check, (job, settings))
            return self._public(job)

    def verify(self, identifier, owner, evidence, payload):
        with self.lock, config_tx.transaction(runner.CONFIG_PATH):
            job = self.get(identifier, owner)
            if job["state"] != "checking":
                raise ValueError("本轮核对已经结束，请重新读取")
            try:
                if config_digest() != job["config_digest"]:
                    raise ImportFailure("ordinary_config_changed")
                settings = runner.load_json(runner.CONFIG_PATH, {})
                result = browser_session_result(evidence, job["id"], settings)
                headers = headers_from_payload(payload)
                identity = verified_identity(result, settings, headers)
                job.update(state="ready", headers=headers, identity=identity,
                           groups={key: bool(value) for key, value in headers.items()},
                           message="普通浏览器核对成功；请确认酒店及额外后台账号后保存")
            except ImportFailure as exc:
                job.update(state="failed", **exc.public())
                job.pop("headers", None)
                config_tx.release(runner.CONFIG_PATH, job["id"])
            except (ValueError, TypeError):
                failure = ImportFailure("ordinary_evidence_invalid")
                job.update(state="failed", **failure.public())
                job.pop("headers", None)
                config_tx.release(runner.CONFIG_PATH, job["id"])
            return self._public(job)

    def _check(self, job, settings):
        failure = None
        try:
            identity = verified_identity(self.probe(job["headers"]), settings, job["headers"])
            with self.lock:
                self.expire()
                if self.job is job:
                    job.update(state="ready", identity=identity, message="酒店身份已识别；额外后台仅完成读取，未验证登录")
        except ImportFailure as exc:
            failure = exc
        except Exception:
            # No arbitrary exception/probe result can leak Cookie, URLs or response data.
            failure = ImportFailure("probe_failed")
        if failure:
            with self.lock:
                if self.job is job:
                    job.update(state="failed", **failure.public())
                    job.pop("headers", None)
                    config_tx.release(runner.CONFIG_PATH, job["id"])

    def _public(self, job):
        identity = job.get("identity", {})
        result = {"id": job["id"], "state": job["state"], "message": job["message"],
                "hotel_name": identity.get("hotel_name", ""), "poi_id": identity.get("poi_id", ""),
                "groups": dict(job["groups"]),
                "expires": job["expires"], "verification_mode": job["verification_mode"]}
        for key in ("failure_code", "failure_details"):
            if key in job:
                result[key] = job[key]
        return result

    def get(self, identifier, owner):
        self.expire()
        if not self.job or self.job["id"] != identifier or self.job["owner"] != owner:
            raise ValueError("导入已取消、过期或不属于当前扩展，请重新读取")
        return self.job

    def status(self, identifier, owner):
        with self.lock:
            return self._public(self.get(identifier, owner))

    def commit(self, identifier, owner, confirmed):
        with self.lock, config_tx.transaction(runner.CONFIG_PATH):
            job = self.get(identifier, owner)
            if job["state"] != "ready" or confirmed is not True:
                raise ValueError("请先完成核对，并确认五类后台属于同一账号和目标酒店")
            if not config_tx.active_lease(runner.CONFIG_PATH):
                raise ValueError("导入保护已过期，请重新读取")
            if config_digest() != job["config_digest"]:
                raise ValueError("配置已变化，请取消并重新读取；本次未覆盖配置")
            settings = runner.load_json(runner.CONFIG_PATH, {})
            # Own lease blocks new work; busy conditions still checked immediately before writing.
            with config_tx.import_write(job["id"]):
                self.idle(settings)
                current = settings.setdefault("meituan", {})
                current.update({key: value for key, value in job["headers"].items() if value})
                current.update(job["identity"])
                runner.save_json(runner.CONFIG_PATH, settings)
            result = self._public(job)
            result.update(state="saved", message="登录配置已保存；未启动采集，未修改任务或数据库配置")
            config_tx.release(runner.CONFIG_PATH, job["id"])
            self.job = None
            if self.timer:
                self.timer.cancel()
            return result

    def cancel(self, identifier=None, owner=None):
        with self.lock, config_tx.transaction(runner.CONFIG_PATH):
            if identifier:
                self.get(identifier, owner)
            if self.job:
                config_tx.release(runner.CONFIG_PATH, self.job["id"])
                self.job = None
            if self.timer:
                self.timer.cancel()
