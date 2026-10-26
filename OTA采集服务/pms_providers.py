#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMS provider 注册表

把「PMS 厂商二选一」收敛成一处定义，供 runner / panel / config_schema 共同读取。

新增平台只需在这里加一个条目，其余三处不再散落 if/else。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent          # OTA采集服务
PROJECT_ROOT = ROOT.parent

DEFAULT_PROVIDER = "byh"
FOXHIS_DEFAULTS = {
    "enabled": False, "hotel_name": "", "username": "", "password": "",
    "code_dir": "OTA采集服务/foxhis_pms", "isql_path": "", "interfaces_file": "",
    "server": "", "database": "", "charset": "cp936", "fetch_after": "03:30",
    "business_date_offset_days": 1,
    "reports": "room_status,room_revenue,jl01,jy01,jy03,jl11,jd01",
    "timeout_seconds": 600,
}


def project_path(value: Any, default: str | Path = ".") -> Path:
    path = Path(str(value or default))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def byh_path(value: Any = None) -> Path:
    """兼容本项目旧目录配置；不改写配置，不重定向外部自定义目录。"""
    path = project_path(str(value).replace("\\", "/") if value else None,
                        "别样红PMS数据采集代码/PMS登录")
    legacy = PROJECT_ROOT / "正式数据抓取-PMS（别样红）"
    current = PROJECT_ROOT / "别样红PMS数据采集代码"
    for base in (current, legacy):
        if path.is_relative_to(base):
            relative = path.relative_to(base)
            break
    else:
        return path  # 不重定向项目之外的自定义代码目录。
    installed = PROJECT_ROOT / "updater" / "installed.json"
    managed = json.loads(installed.read_text(encoding="utf-8-sig")).get("files", {}) if installed.is_file() else {}
    for base in (current, legacy):
        entry = base / "PMS登录" / "fetch_main.py"
        if entry.is_file() and entry.relative_to(PROJECT_ROOT).as_posix() in managed:
            return base / relative
    return (current if current.is_dir() else legacy) / relative


# ---------------------------------------------------------------------------
# 注册表
#
# scripts=None  → 该平台只有一个入口脚本（别样红）
# scripts=[...] → 一个 pms_fetch 任务展开成多张报表脚本（宝寓 / Foxhis）
#
# cwd_from_script=True  → 子进程工作目录用脚本所在目录（别样红的既有行为）
# ---------------------------------------------------------------------------

PMS_PROVIDERS: dict[str, dict[str, Any]] = {
    "byh": {
        "label": "别样红 PMS",
        "short_label": "别样红 PMS",
        "platform": "pms",
        "config_key": "pms",
        "card_key": "pms",
        "badge": "PMS",
        "default_code_dir": "别样红PMS数据采集代码/PMS登录",
        "cwd_from_script": True,
        "entry_script": "fetch_main.py",
        "entry_args": [],
        "timeout_seconds": 900,
        "scripts": None,
        "probe_action": "",
        "channel": "http",
    },
    "bypms": {
        "label": "宝寓 PMS",
        "short_label": "宝寓 PMS",
        "platform": "bypms",
        "config_key": "bypms",
        "card_key": "bypms",
        "badge": "BYPMS",
        "default_code_dir": "宝寓PMS数据采集代码",
        "cwd_from_script": False,
        "entry_script": "",
        "entry_args": [],
        "timeout_seconds": 120,
        "scripts": [
            ("bypms", "bypms_room_status_data.py", []),
            ("bypms", "bypms_channel_mapping_data.py", []),
            ("bypms", "bypms_rs01_data.py", []),
            ("bypms", "bypms_daily_report_data.py", []),
            ("bypms", "bypms_jl11_data.py", []),
            ("bypms", "bypms_jy03_data.py", []),
            ("bypms", "bypms_jd01_data.py", []),
        ],
        "probe_action": "",
        "channel": "http",
    },
    "foxhis": {
        "label": "西软 Foxhis",
        "short_label": "Foxhis",
        "platform": "foxhis",
        "config_key": "foxhis",
        "card_key": "foxhis",
        "badge": "FOXHIS",
        "default_code_dir": "OTA采集服务/foxhis_pms",
        "cwd_from_script": False,
        # 多脚本平台必须留空：script_path 里 entry_script 优先级高于报表文件名，
        # 一旦填了就会把所有报表都解析到同一个入口脚本上。
        "entry_script": "",
        "entry_args": [],
        # 报表查询比 HTTP 慢，且跨网段，独立给更宽的超时
        "timeout_seconds": 600,
        "scripts": [
            ("foxhis", "foxhis_room_status_data.py", []),
            ("foxhis", "foxhis_room_revenue_data.py", []),
            ("foxhis", "foxhis_jl01_data.py", []),
            ("foxhis", "foxhis_jy01_data.py", []),
            ("foxhis", "foxhis_jy03_data.py", []),
            ("foxhis", "foxhis_jl11_data.py", []),
            ("foxhis", "foxhis_jd01_data.py", []),
        ],
        # 配置页上的「从本机客户端自动读取」按钮
        "probe_action": "/detect-foxhis",
        "channel": "tds",
    },
}


# ---------------------------------------------------------------------------
# 分发逻辑（与改造前的单平台行为保持一致）
# ---------------------------------------------------------------------------


def active_pms_provider(settings: dict[str, Any]) -> str:
    raw = str((settings.get("pms") or {}).get("provider") or DEFAULT_PROVIDER).strip().lower()
    return raw if raw in PMS_PROVIDERS else DEFAULT_PROVIDER


def normalize_provider(settings: dict[str, Any]) -> tuple[str, bool]:
    """
    规范化 pms.provider。

    这里必须与 active_pms_provider 区分开：后者对非法值一律回落 DEFAULT_PROVIDER，
    用在分发路径上；而本函数用于 load_settings 的「读档修正」，要保持原有语义——
    非法值时按各平台 enabled 推断，而不是无脑回落默认平台。

    返回 (provider, changed)。
    """
    raw = (settings.get("pms") or {}).get("provider")
    key = str(raw or "").strip().lower()
    if key in PMS_PROVIDERS:
        return key, False
    # 非法值：优先推断到显式启用了的非默认平台（保持改造前「bypms.enabled 则选 bypms」的行为）
    for candidate, spec in PMS_PROVIDERS.items():
        if candidate != DEFAULT_PROVIDER and (settings.get(spec["config_key"]) or {}).get("enabled"):
            return candidate, True
    return DEFAULT_PROVIDER, True


def enforce_exclusive_enable(settings: dict[str, Any], selected: str) -> bool:
    """同一时刻只允许一个 PMS 平台启用（沿用改造前的互斥语义）。返回是否有变更。"""
    changed = False
    for key, spec in PMS_PROVIDERS.items():
        if key == selected:
            continue
        section = settings.setdefault(spec["config_key"], {})
        if section.get("enabled"):
            section["enabled"] = False
            changed = True
    return changed


def provider_spec(provider: str) -> dict[str, Any]:
    return PMS_PROVIDERS.get(provider) or PMS_PROVIDERS[DEFAULT_PROVIDER]


def task_is_available(name: str, platform: str, settings: dict[str, Any]) -> bool:
    if name != "pms_fetch":
        return (settings.get(platform) or {}).get("enabled", True)
    provider = active_pms_provider(settings)
    spec = PMS_PROVIDERS[provider]
    # 与改造前一致：未显式配置 enabled 时，只有默认平台视为启用
    return (settings.get(spec["config_key"]) or {}).get("enabled", provider == DEFAULT_PROVIDER)


def task_unavailable_reason(name: str, platform: str, settings: dict[str, Any]) -> str:
    if task_is_available(name, platform, settings):
        return ""
    if name == "pms_fetch":
        label = PMS_PROVIDERS[active_pms_provider(settings)]["label"]
    else:
        label = {"meituan": "美团", "ctrip": "携程"}.get(platform, platform)
    return f"{label}采集已禁用，请先在配置页启用"


def task_specs(name: str, settings: dict[str, Any]) -> list[tuple[str, str, list[str]]] | None:
    """返回 None 表示不是 PMS 任务，交由调用方走原有注册表。"""
    if name != "pms_fetch":
        return None
    spec = PMS_PROVIDERS[active_pms_provider(settings)]
    if spec["scripts"]:
        scripts = spec["scripts"]
        if active_pms_provider(settings) == "foxhis":
            reports = (settings.get("foxhis") or {}).get("reports")
            if reports:
                selected = reports.split(",") if isinstance(reports, str) else reports
                names = {f"foxhis_{item.strip()}_data.py" for item in selected}
                scripts = [item for item in scripts if item[1] in names]
                if not scripts:
                    raise ValueError("西软未选择有效的采集报表")
        return [(platform, script, list(args)) for platform, script, args in scripts]
    return [(spec["platform"], spec["entry_script"], list(spec["entry_args"]))]


def script_path(settings: dict[str, Any], platform: str, filename: str) -> Path | None:
    """返回 None 表示不是 PMS 平台，交由调用方走原有解析。"""
    for spec in PMS_PROVIDERS.values():
        if spec["platform"] != platform:
            continue
        section = settings.get(spec["config_key"]) or {}
        code_dir = (byh_path(section.get("code_dir")) if platform == "pms"
                    else project_path(section.get("code_dir"), spec["default_code_dir"]))
        entry = section.get("entry_script") or spec["entry_script"] or filename
        return code_dir / entry
    return None


def task_timeout(settings: dict[str, Any], platform: str) -> int | None:
    for spec in PMS_PROVIDERS.values():
        if spec["platform"] != platform:
            continue
        section = settings.get(spec["config_key"]) or {}
        return int(section.get("timeout_seconds") or spec["timeout_seconds"])
    return None


def pms_platforms() -> set[str]:
    return {spec["platform"] for spec in PMS_PROVIDERS.values()}


def pms_env_prefixes() -> dict[str, str]:
    """平台 → 环境变量前缀，供 runner.build_env 批量注入。"""
    return {spec["platform"]: spec["config_key"].upper() for spec in PMS_PROVIDERS.values()}


# ---------------------------------------------------------------------------
# 面板侧
# ---------------------------------------------------------------------------


def provider_options() -> list[dict[str, str]]:
    return [
        {"value": key, "label": spec["label"], "card": spec["card_key"]}
        for key, spec in PMS_PROVIDERS.items()
    ]


def card_keys() -> list[str]:
    return [spec["card_key"] for spec in PMS_PROVIDERS.values()]


def apply_provider_selection(settings: dict[str, Any], provider: str) -> bool:
    """选中某个平台时，同步各平台的 enabled 开关。返回是否被接受。"""
    if provider not in PMS_PROVIDERS:
        return False
    settings.setdefault("pms", {})["provider"] = provider
    for key, spec in PMS_PROVIDERS.items():
        settings.setdefault(spec["config_key"], {})["enabled"] = key == provider
    return True
