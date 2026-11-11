"""汇总采集只在状态文件记录哪一侧查询成功，不写入业务数据。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def state_path() -> Path:
    root = Path(os.environ.get("HOTEL_OTA_PROJECT_ROOT") or Path(__file__).resolve().parents[1])
    return root / "OTA采集服务" / "state" / "pms_dual.json"


def _modify(path: Path | None, update) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    update(data)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def remember(side: str, report: str = "room_status", path: Path | None = None) -> None:
    def update(data: dict) -> None:
        bucket = data.setdefault(report, {})
        if not isinstance(bucket, dict):
            bucket = {}
            data[report] = bucket
        bucket[side] = {"queried_at": datetime.now().replace(microsecond=0).isoformat(sep=" ")}

    _modify(path, update)


def _clean_names(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return sorted(result)


def remember_room_types(meituan, rezen, path: Path | None = None) -> None:
    """记住两侧合并前的原始房型名。合并写入后的「名称 / 名称」不能再拆回两侧。"""
    meituan_names, rezen_names = _clean_names(meituan), _clean_names(rezen)

    def update(data: dict) -> None:
        data["room_types"] = {
            "meituan": meituan_names,
            "rezen": rezen_names,
            "saved_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
        }

    _modify(path, update)
