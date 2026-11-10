"""西软业务库：一个酒店最多 8 个库，库名、账号、密码一起填写。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

MAX_ACCOUNTS = 8
LOGIN_FIELDS = ("database", "username", "password")
DISPLAY_FIELDS = ("hotel_name",)
OVERRIDE_FIELDS = (
    "server",
    "charset",
    "isql_path",
    "interfaces_file",
    "fetch_after",
    "business_date_offset_days",
    "timeout_seconds",
    "reports",
)
REPORTS = {"room_status", "room_revenue", "jl01", "jy01", "jy03", "jl11", "jd01"}
_BLANK = {"database": "", "username": "", "password": ""}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_account(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    item = {key: _text(raw.get(key)) for key in LOGIN_FIELDS}
    for key in (*DISPLAY_FIELDS, *OVERRIDE_FIELDS):
        value = _text(raw.get(key))
        if value:
            item[key] = value
    if not any(item.get(key) for key in (*LOGIN_FIELDS, *DISPLAY_FIELDS, *OVERRIDE_FIELDS)):
        return None
    return item


def _legacy_account(foxhis: Mapping[str, Any]) -> dict[str, str] | None:
    return normalize_account({
        "database": foxhis.get("database"),
        "username": foxhis.get("username"),
        "password": foxhis.get("password"),
    })


def iter_stored(foxhis: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """配置里真正要参与的库。没有 accounts 时，沿用原来的单个库登录。"""
    foxhis = foxhis or {}
    if isinstance(foxhis.get("accounts"), list):
        stored = []
        for raw in foxhis["accounts"]:
            item = normalize_account(raw)
            if item:
                stored.append(item)
        return stored
    legacy = _legacy_account(foxhis)
    return [legacy] if legacy else []


def complete_accounts(foxhis: Mapping[str, Any] | None) -> list[dict[str, str]]:
    return [
        item for item in iter_stored(foxhis)
        if item.get("database") and item.get("username") and item.get("password")
    ]


def effective_reports(foxhis: Mapping[str, Any] | None) -> Any:
    """报表范围。只有一个完整业务库且该库自己填了报表时，用这一格的范围。"""
    foxhis = foxhis or {}
    reports = foxhis.get("reports")
    if isinstance(foxhis.get("accounts"), list):
        complete = complete_accounts(foxhis)
        if len(complete) == 1 and complete[0].get("reports"):
            return complete[0]["reports"]
    return reports


def display_slots(foxhis: Mapping[str, Any] | None) -> list[dict[str, str]]:
    foxhis = foxhis or {}
    accounts = foxhis.get("accounts")
    if isinstance(accounts, list) and any(normalize_account(item) for item in accounts):
        slots = [item for item in (normalize_account(raw) for raw in accounts) if item]
    else:
        legacy = _legacy_account(foxhis)
        slots = [legacy] if legacy else []
    return (slots or [dict(_BLANK)])[:MAX_ACCOUNTS]


def _override_errors(item: Mapping[str, Any]) -> list[str]:
    errors = []
    offset = _text(item.get("business_date_offset_days"))
    if offset:
        try:
            if int(offset) < 0:
                raise ValueError
        except ValueError:
            errors.append("业务日偏移必须是非负整数。")
    timeout = _text(item.get("timeout_seconds"))
    if timeout:
        try:
            if int(timeout) <= 0:
                raise ValueError
        except ValueError:
            errors.append("超时必须是正整数。")
    gate = _text(item.get("fetch_after"))
    if gate:
        try:
            datetime.strptime(gate, "%H:%M")
        except ValueError:
            errors.append("采集时机必须是 HH:MM。")
    reports = _text(item.get("reports"))
    if reports:
        selected = [part.strip() for part in reports.split(",") if part.strip()]
        if any(part not in REPORTS for part in selected):
            errors.append("业务库的采集报表包含无效项。")
    return errors


def validate_accounts(accounts: Any) -> list[str]:
    if not isinstance(accounts, list):
        return ["业务库列表格式无效。"]
    errors: list[str] = []
    seen: list[str] = []
    count = 0
    for raw in accounts:
        if not isinstance(raw, dict):
            errors.append("业务库格式无效。")
            continue
        item = {key: _text(raw.get(key)) for key in LOGIN_FIELDS}
        for key in (*DISPLAY_FIELDS, *OVERRIDE_FIELDS):
            value = _text(raw.get(key))
            if value:
                item[key] = value
        if not any(item.get(key) for key in (*LOGIN_FIELDS, *DISPLAY_FIELDS, *OVERRIDE_FIELDS)):
            continue
        count += 1
        filled = [bool(item.get(key)) for key in LOGIN_FIELDS]
        if not all(filled):
            errors.append("业务库只填了一部分，库名、账号、密码要一起填。")
            continue
        if item["database"] in seen:
            errors.append(f"数据库名 {item['database']} 重复，不能保存。")
        else:
            seen.append(item["database"])
        errors.extend(_override_errors(item))
    if count > MAX_ACCOUNTS:
        errors.append(f"业务库最多 {MAX_ACCOUNTS} 个。")
    return errors


def _passwords_by_database(foxhis: Mapping[str, Any]) -> dict[str, str]:
    found = {}
    for item in iter_stored(foxhis):
        if item.get("database") and item.get("password"):
            found[item["database"]] = item["password"]
    database = _text(foxhis.get("database"))
    password = "" if foxhis.get("password") is None else str(foxhis.get("password")).strip()
    if database and password and database not in found:
        found[database] = password
    return found


def _read_slot(form: Mapping[str, Any], index: int, passwords: Mapping[str, str]) -> dict[str, str] | None:
    def field(name: str) -> str:
        value = form.get(f"foxhis_account_{index}_{name}")
        return "" if value is None else str(value).strip()

    database = field("database")
    password = field("password") or passwords.get(database, "")
    item = {"database": database, "username": field("username"), "password": password}
    for key in (*DISPLAY_FIELDS, *OVERRIDE_FIELDS):
        value = field(key)
        if value:
            item[key] = value
    if not any(item.get(key) for key in (*LOGIN_FIELDS, *DISPLAY_FIELDS, *OVERRIDE_FIELDS)):
        return None
    return item


def _coerce(item: Mapping[str, str]) -> dict[str, Any]:
    stored: dict[str, Any] = {key: item[key] for key in LOGIN_FIELDS}
    for key in DISPLAY_FIELDS:
        if item.get(key):
            stored[key] = item[key]
    for key in OVERRIDE_FIELDS:
        if not item.get(key):
            continue
        if key in {"business_date_offset_days", "timeout_seconds"}:
            stored[key] = int(item[key])
        elif key == "reports":
            stored[key] = ",".join(part.strip() for part in item[key].split(",") if part.strip())
        else:
            stored[key] = item[key]
    return stored


def apply_foxhis_accounts(settings: dict[str, Any], form: Mapping[str, Any]) -> str:
    """把配置页上的业务库写回设置。表单里没有这些格子时不动现有配置。"""
    if all(form.get(f"foxhis_account_0_{key}") is None for key in LOGIN_FIELDS):
        return ""
    foxhis = settings.get("foxhis") if isinstance(settings.get("foxhis"), dict) else {}
    parsed = []
    passwords = _passwords_by_database(foxhis)
    for index in range(MAX_ACCOUNTS):
        item = _read_slot(form, index, passwords)
        if item:
            parsed.append(item)
    errors = validate_accounts(parsed)
    if errors:
        return errors[0]
    target = settings.setdefault("foxhis", {})
    stored = [_coerce(item) for item in parsed]
    target["accounts"] = stored
    if stored:
        target["database"] = stored[0]["database"]
        target["username"] = stored[0]["username"]
        target["password"] = stored[0]["password"]
    else:
        target["database"] = ""
        target["username"] = ""
        target["password"] = ""
    return ""
