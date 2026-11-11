"""检查已登录的 Rezen 能否读出当天房态。结果里不放会话。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "OTA采集服务"
DEFAULT_STATUS = ROOT / "state" / "rezen_connection.json"
FIELDS = (
    "status", "message", "checked_at", "room_types", "total_rooms",
    "available_rooms", "occupied_rooms", "overbooking_rooms",
)


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def status_path(path: Path | None = None) -> Path:
    override = os.environ.get("REZEN_CONNECTION_STATUS", "").strip()
    if path is not None:
        return Path(path)
    return Path(override) if override else DEFAULT_STATUS


def read_status(path: Path | None = None) -> dict:
    file = status_path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        data = None
    if not isinstance(data, dict):
        return {"status": "never", "message": "尚未检查"}
    result = {key: data[key] for key in FIELDS if key in data}
    result["status"] = str(result.get("status") or "never")
    result["message"] = str(result.get("message") or "尚未检查")
    return result


def _store(result: dict, path: Path | None) -> None:
    file = status_path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: result[key] for key in FIELDS if key in result}
    temporary = file.with_suffix(file.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(file)


def _collect(session_token: str, hotel_name: str, *, base_url: str, profile_dir: str,
             view_days: int, include_tax: str) -> list[dict]:
    from rezen_pms.collector import _tax, _view_days, collect
    return collect(
        session_token, "connection-check", hotel_name,
        base_url=base_url, profile_dir=profile_dir,
        view_days=_view_days(str(view_days)), include_tax=_tax(include_tax),
    )


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("Rezen 没有返回房型")
    total = sum(int(row["total_rooms"]) for row in rows)
    available = sum(int(row["available_rooms"]) for row in rows)
    sold = sum(int(row["occupied_rooms"]) for row in rows)
    overbooking = sum(int(row["overbooking_rooms"]) for row in rows)
    message = f"已连接，{len(rows)} 个房型，可售 {available}，已售 {sold}"
    if overbooking:
        message += f"，超售 {overbooking}"
    return {
        "status": "success", "message": message, "checked_at": _now(),
        "room_types": len(rows), "total_rooms": total, "available_rooms": available,
        "occupied_rooms": sold, "overbooking_rooms": overbooking,
    }


def run_check(settings: dict, *, collect=None, path: Path | None = None) -> dict:
    rezen = settings.get("rezen") if isinstance(settings.get("rezen"), dict) else {}
    meituan = settings.get("meituan_pms") if isinstance(settings.get("meituan_pms"), dict) else {}
    name = str(rezen.get("hotel_name") or meituan.get("hotel_name") or "").strip() or "连接检查"
    reader = collect or _collect
    try:
        rows = reader(
            str(rezen.get("session_token") or ""), name,
            base_url=str(rezen.get("base_url") or ""),
            profile_dir=str(rezen.get("profile_dir") or ""),
            view_days=rezen.get("view_days") or 15,
            include_tax=str(rezen.get("include_tax") or "1"),
        )
        result = _summarize(rows)
    except (ValueError, RuntimeError) as exc:
        result = {"status": "failed", "message": str(exc), "checked_at": _now()}
    _store(result, path)
    return result
