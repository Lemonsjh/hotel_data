"""Prevent new business work while code is being replaced or recovered."""
from pathlib import Path


def ensure_not_updating() -> None:
    pending = Path(__file__).resolve().parent / "updater" / "pending.json"
    if pending.exists():
        raise RuntimeError("程序正在更新或等待更新恢复，暂不能启动业务任务")
