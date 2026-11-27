from __future__ import annotations

import subprocess
from typing import Any

import runner
from panel_common import process_alive
from review_reply_scheduler import PID_PATH as REPLY_PID_PATH, STOP_PATH as REPLY_STOP_PATH, load_status


def reply_scheduler_status() -> dict[str, Any]:
    data = load_status()
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
