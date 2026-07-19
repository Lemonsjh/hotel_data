from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


POSITIVE_NUMBERS = (
    "service.interval_minutes",
    "service.timeout_seconds",
    "price_scheduler.interval_minutes",
    "price_scheduler.startup_delay_seconds",
    "price_scheduler.max_tasks_per_run",
    "pms.timeout_seconds",
    "pms.navigation_timeout_ms",
    "pms.action_timeout_ms",
    "pms.api_timeout_seconds",
)
URL_FIELDS = (
    "pms.login_base_url",
    "pms.report_base_url",
    "pms.service_api_base_url",
    "pms.forecast_api_base_url",
)
PATH_FIELDS = (
    "python_path",
    "paths.base_dir",
    "paths.output_dir",
    "paths.meituan_code_dir",
    "paths.ctrip_code_dir",
    "pms.code_dir",
    "pms.entry_script",
)


def get_path(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def validate_settings(settings: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(settings, dict):
        return ["配置根节点必须是 JSON 对象"]

    for section in ("service", "price_scheduler", "hotel", "paths", "pms", "mysql", "meituan", "ctrip", "tasks"):
        value = settings.get(section)
        if value is not None and not isinstance(value, dict):
            errors.append(f"{section} 必须是 JSON 对象")

    for dotted in POSITIVE_NUMBERS:
        value = get_path(settings, dotted)
        if value in (None, ""):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{dotted} 必须是大于 0 的数字")

    port = get_path(settings, "mysql.port")
    if port not in (None, "") and (
        isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535
    ):
        errors.append("mysql.port 必须是 1 到 65535 的整数")

    for dotted in URL_FIELDS:
        value = get_path(settings, dotted)
        if value in (None, ""):
            continue
        parsed = urlsplit(str(value))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"{dotted} 必须是有效的 HTTP(S) 地址")

    for dotted in PATH_FIELDS:
        value = get_path(settings, dotted)
        if value is not None and not isinstance(value, str):
            errors.append(f"{dotted} 必须是字符串路径")

    tasks = settings.get("tasks") or {}
    if isinstance(tasks, dict):
        for name, enabled in tasks.items():
            if not isinstance(enabled, bool):
                errors.append(f"tasks.{name} 必须是布尔值")

    for platform in ("pms", "meituan", "ctrip"):
        enabled = get_path(settings, f"{platform}.enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append(f"{platform}.enabled 必须是布尔值")

    return errors


def require_valid_settings(settings: Any, source: str | Path = "settings.json") -> dict[str, Any]:
    errors = validate_settings(settings)
    if errors:
        details = "; ".join(errors)
        raise ValueError(f"配置文件 {source} 无效: {details}")
    return settings
