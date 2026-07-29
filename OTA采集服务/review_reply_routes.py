from __future__ import annotations

import subprocess
from typing import Any

import runner
from panel_common import process_alive


REPLY_STATUS_PATH = runner.ROOT / "state" / "review_reply_scheduler_status.json"
REPLY_PID_PATH = runner.ROOT / "state" / "review_reply_scheduler.pid"
REPLY_STOP_PATH = runner.ROOT / "state" / "review_reply_scheduler.stop"


def reply_scheduler_status() -> dict[str, Any]:
    data = runner.load_json(REPLY_STATUS_PATH, {})
    alive = process_alive(data.get("pid"))
    state = data.get("scheduler_status", "stopped") if alive else "stopped"
    if alive and REPLY_STOP_PATH.exists():
        state = "stopping"
    data.update(scheduler_status=state, alive=alive)
    return data


def start_reply_scheduler(settings: dict[str, Any]) -> None:
    if reply_scheduler_status()["alive"]:
        return
    REPLY_PID_PATH.unlink(missing_ok=True)
    REPLY_STOP_PATH.unlink(missing_ok=True)
    subprocess.Popen(
        [str(runner.python_path(settings)), str(runner.ROOT / "review_reply_scheduler.py")],
        cwd=str(runner.ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
