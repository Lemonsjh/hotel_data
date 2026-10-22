"""Prevent new business work while code is being replaced or recovered."""
from pathlib import Path
import json
import time


def ensure_not_updating(*, check_cookie_import: bool = True) -> None:
    pending = Path(__file__).resolve().parent / "updater" / "pending.json"
    if pending.exists():
        raise RuntimeError("程序正在更新或等待更新恢复，暂不能启动业务任务")
    if not check_cookie_import:
        return
    lease = Path(__file__).resolve().parent / "OTA采集服务" / "state" / "browser_cookie_import.json"
    try:
        data = json.loads(lease.read_text(encoding="utf-8"))
        if float(data["expires"]) > time.time():
            raise RuntimeError("浏览器 Cookie 正在导入，请完成或取消后再启动业务任务")
    except FileNotFoundError:
        pass
