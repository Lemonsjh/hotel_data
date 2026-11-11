"""汇总采集只在状态文件记录哪一侧查询成功，不写入业务数据。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def state_path() -> Path:
    root = Path(os.environ.get("HOTEL_OTA_PROJECT_ROOT") or Path(__file__).resolve().parents[1])
    return root / "OTA采集服务" / "state" / "pms_dual.json"


def remember(side: str, report: str = "room_status", path: Path | None = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    bucket = data.setdefault(report, {})
    if not isinstance(bucket, dict):
        bucket = {}
        data[report] = bucket
    bucket[side] = {"queried_at": datetime.now().replace(microsecond=0).isoformat(sep=" ")}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
