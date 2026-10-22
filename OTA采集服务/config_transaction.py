"""Short cross-process config lock and bounded Cookie import reservation."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

_local = threading.local()
_mutex = threading.RLock()


def lease_path(config: Path) -> Path:
    return config.parent.parent / "state" / "browser_cookie_import.json"


def active_lease(config: Path) -> dict:
    path = lease_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if float(data["expires"]) > time.time() else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("Cookie 导入保护状态异常，请检查本地 state 文件") from None


def check_writable(config: Path) -> None:
    lease = active_lease(config)
    allowed = getattr(_local, "import_owner", "")
    if lease and (not allowed or lease.get("owner") != allowed):
        raise RuntimeError("浏览器 Cookie 正在核对或等待确认，请先完成、取消导入或等待过期")


@contextmanager
def transaction(config: Path):
    """Serialize cooperating writers, including threads in this panel process."""
    with _mutex:
        if getattr(_local, "depth", 0):
            _local.depth += 1
            try:
                yield
            finally:
                _local.depth -= 1
            return
        config.parent.mkdir(parents=True, exist_ok=True)
        with (config.parent / ".settings.lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _local.depth = 1
            try:
                yield
            finally:
                _local.depth = 0
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def reserve(config: Path, token: str) -> None:
    check_writable(config)
    path = lease_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(json.dumps({"owner": hashlib.sha256(token.encode()).hexdigest(),
                               "expires": time.time() + 240}))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def release(config: Path, token: str) -> None:
    lease = active_lease(config)
    if lease.get("owner") == hashlib.sha256(token.encode()).hexdigest():
        lease_path(config).unlink(missing_ok=True)


@contextmanager
def import_write(token: str):
    previous = getattr(_local, "import_owner", "")
    _local.import_owner = hashlib.sha256(token.encode()).hexdigest()
    try:
        yield
    finally:
        _local.import_owner = previous
