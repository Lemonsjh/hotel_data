from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platform_login
import runner


DEFAULT_SETTINGS_PATH = runner.ROOT / "config" / "settings.example.json"
PMS_SESSION_PATH = (
    runner.PROJECT_ROOT
    / "正式数据抓取-PMS（别样红）"
    / "PMS登录"
    / "pms_session_playwright.json"
)
SCHEDULER_STOP_PATHS = (
    runner.ROOT / "state" / "manual_scheduler.stop",
    runner.ROOT / "state" / "price_scheduler.stop",
    runner.ROOT / "state" / "review_reply_scheduler.stop",
    runner.ROOT / "state" / "promotion_scheduler.stop",
)


def is_collection_running() -> bool:
    status = runner.load_status()
    return status.get("last_run_status") in {"starting", "running", "stopping"}


def default_settings() -> dict[str, Any]:
    if not DEFAULT_SETTINGS_PATH.exists():
        raise FileNotFoundError(f"Missing default settings: {DEFAULT_SETTINGS_PATH}")

    settings = runner.load_json(DEFAULT_SETTINGS_PATH, {})
    settings.setdefault("service", {})["scheduler_enabled"] = False
    settings.setdefault("price_scheduler", {})["enabled"] = False
    settings.setdefault("reply_scheduler", {})["enabled"] = False
    settings.setdefault("promotion_scheduler", {})["enabled"] = False
    settings["tasks"] = {name: False for name in runner.TASKS}
    return settings


def stop_login_helpers() -> None:
    for platform in platform_login.PLATFORMS:
        status = platform_login.read_status(platform)
        platform_login.stop_login_process(status.get("pid"))
        platform_login.stop_path(platform).unlink(missing_ok=True)
        platform_login.status_path(platform).unlink(missing_ok=True)


def clear_login_sessions() -> None:
    PMS_SESSION_PATH.unlink(missing_ok=True)
    for platform in platform_login.PLATFORMS:
        profile = platform_login.profile_path(platform)
        if profile.exists():
            shutil.rmtree(profile)


def request_scheduler_stop() -> None:
    runner.request_run_stop()
    for path in SCHEDULER_STOP_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reset", encoding="utf-8")


def reset_local_configuration() -> None:
    if is_collection_running():
        raise RuntimeError("当前采集仍在运行，请先停止并等待任务结束后再重置")

    request_scheduler_stop()
    stop_login_helpers()
    clear_login_sessions()
    runner.save_json(runner.CONFIG_PATH, default_settings())
