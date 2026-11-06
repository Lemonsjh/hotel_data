"""面板里的正式版检查。确认之后才调用现有更新器，不在这里改安装目录。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import runner


def classify_update_text(text: str) -> str:
    """把更新器的英文结果收成店员能看的一句或几句中文。"""
    folded = text.lower()
    sentences: list[str] = []

    def add(sentence: str) -> None:
        if sentence not in sentences:
            sentences.append(sentence)

    if "already up to date" in folded:
        add("已是最新。")
    if "update installed:" in folded or "backup: updater/backups/" in folded:
        stamp = ""
        marker = "backup: updater/backups/"
        index = folded.find(marker)
        if index >= 0:
            stamp = text[index + len(marker):].split()[0].strip(".;，。")
        folder = f"updater/backups/{stamp}" if stamp else "updater/backups/"
        add(f"已安装，备份在 {folder}。")
    if "locally modified program file" in folded:
        add("程序文件被改过，更新已停止。")
    if (
        "python runtime differs" in folded
        or "dependencies differ" in folded
        or "portable runtime" in folded
    ):
        add("需要另换便携运行环境，现有配置不会被覆盖。")
    if "interrupted update" in folded or "use --recover" in folded:
        add("上次更新中断，要先恢复。")
    if "recovered program files" in folded:
        add("已恢复上次更新。数据库和本地配置没有改动。")
    if "browser extension files updated" in folded or "reload the extension" in folded:
        add("扩展文件有变化，请到 Edge 扩展页手动重新加载。")
    if (
        "try again when idle" in folded
        or "task status is active" in folded
        or "scheduled task targets" in folded
        or "refuse rollback" in folded
    ):
        add("现在正在采集或有任务占用，更新已停止。请等空闲后再试。")
    if sentences:
        return "".join(sentences)
    if "no official release" in folded:
        return "还没有可安装的正式版，本机程序没有改动。"
    if "update failed" in folded:
        return "更新没有完成，现有配置不会被覆盖。"
    stripped = text.strip()
    return stripped or "没有得到更新结果。"


def notice_path() -> Path:
    return runner.ROOT / "state" / "panel_update_notice.json"


def script_result_path() -> Path:
    return runner.ROOT / "state" / "update_result.json"


def store_notice(payload: dict[str, Any]) -> None:
    body = dict(payload)
    body["at"] = runner.now_text()
    runner._save_json(notice_path(), body)


def current_notice() -> dict[str, Any]:
    panel_path = notice_path()
    script_path = script_result_path()
    panel_data = runner.load_json(panel_path, {})
    script_data = runner.load_json(script_path, {})
    if script_path.is_file() and (
        not panel_path.is_file() or script_path.stat().st_mtime >= panel_path.stat().st_mtime
    ):
        text = str(script_data.get("text") or "")
        if text:
            return {"kind": "result", "message": classify_update_text(text)}
    return panel_data if isinstance(panel_data, dict) else {}


def _updater():
    path = str(runner.PROJECT_ROOT / "updater")
    if path not in sys.path:
        sys.path.insert(0, path)
    import update
    return update


def installed_version() -> str:
    path = runner.PROJECT_ROOT / "updater" / "installed.json"
    if not path.is_file():
        return "0.0.0"
    try:
        return str(json.loads(path.read_text(encoding="utf-8-sig")).get("version") or "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def update_pending() -> bool:
    return (runner.PROJECT_ROOT / "updater" / "pending.json").exists()


def check_release() -> dict[str, str]:
    if update_pending():
        return {"kind": "interrupted", "message": "上次更新中断，要先恢复。"}
    updater = _updater()
    current = installed_version()
    try:
        release = updater.latest_release()
    except Exception as exc:
        return {"kind": "error", "message": classify_update_text(f"Update failed ({type(exc).__name__}): {exc}")}
    tag = str(release.get("tag_name") or "")
    if updater.version(tag) <= updater.version(current):
        return {"kind": "current", "message": "已是最新。"}
    return {
        "kind": "available",
        "message": f"发现正式版 {tag}，当前安装的是 {current}。确认后才会安装，不会覆盖配置，也不会迁移数据库或更换运行环境。",
        "tag": tag,
    }


def _run_updater(argument: str) -> dict[str, str]:
    if update_pending() and argument == "--apply":
        return {"kind": "interrupted", "message": "上次更新中断，要先恢复。"}
    completed = subprocess.run(
        [sys.executable, str(runner.PROJECT_ROOT / "updater" / "update.py"), argument],
        input="y\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(runner.PROJECT_ROOT),
        timeout=900,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    return {"kind": "result", "message": classify_update_text(text)}


def apply_confirmed() -> dict[str, str]:
    return _run_updater("--apply")


def recover_confirmed() -> dict[str, str]:
    return _run_updater("--recover")
