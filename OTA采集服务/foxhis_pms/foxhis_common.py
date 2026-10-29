#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Foxhis X5 采集公共模块

职责：只读查询、结果解析、业务日闸门、MySQL 连接及输出文件。

设计约束
  · 查询护栏仅防止代码误用，不能替代源数据库的只读权限
  · 纯标准库 + pymysql（不新增 pip 依赖，可跑在便携 runtime 上）
  · 各报表按酒店、来源及日期事务替换快照
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from foxhis_config import Config, DEFAULT_CHARSET, FoxhisError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from OTA采集服务.pms_history_retention import cleanup_rows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE_PLATFORM = "PMS（Foxhis）"
DEFAULT_OUTPUT_DIR = "OTA数据"
DAILY_REPORTS = {"jl01", "jy01", "jy03", "jl11"}
DAILY_STATE_FILE = "foxhis_daily_runs.json"


def cleanup_history(cursor, table, rows, config):
    cleanup_rows(cursor, table, rows, config.hotel_id, SOURCE_PLATFORM)


class ReadonlyViolation(FoxhisError):
    pass


# ---------------------------------------------------------------------------
# 前置检查与业务日
# ---------------------------------------------------------------------------


def check_reachable(config: Config, timeout: float = 3.0) -> tuple[bool, str]:
    """TCP 可达性自检：跨网段依赖，不通要快速失败而不是耗满超时。"""
    try:
        host, port = config.endpoint()
    except FoxhisError as exc:
        return False, str(exc)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} 可达"
    except OSError as exc:
        return False, f"{host}:{port} 不可达（{exc}）"


def business_date(config: Config) -> date:
    """业务日 = 当前日期 - 偏移。夜审后采的是「昨天」的账。"""
    override = os.environ.get("FOXHIS_BUSINESS_DATE", "").strip()
    if override:
        return date.fromisoformat(override)
    return (datetime.now() - timedelta(days=config.business_date_offset_days)).date()


def after_gate(config: Config) -> tuple[bool, str]:
    """
    业务日闸门：早于 fetch_after 直接跳过。

    现有调度是「每隔 N 分钟轮询」而不是定时点，夜审未完成时会采到空数据，
    空数据会被当成「当天为 0」写入，造成脏数据 —— 所以这道闸门必须放在采集器里。
    """
    if not config.fetch_after:
        return True, ""
    try:
        hour, minute = (int(part) for part in config.fetch_after.split(":", 1))
    except ValueError:
        return True, f"fetch_after={config.fetch_after} 格式无法识别，已忽略闸门"
    now = datetime.now()
    if (now.hour, now.minute) < (hour, minute):
        return False, f"当前 {now:%H:%M} 早于 {config.fetch_after}（夜审后采集窗口），本次跳过"
    return True, ""


# ---------------------------------------------------------------------------
# isql 调用与解析
# ---------------------------------------------------------------------------


def assert_readonly(sql: str) -> None:
    stripped = re.sub(r"'(?:''|[^'])*'", "''", sql)
    stripped = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/", " ", stripped).strip().lower()
    unsafe = r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|into|grant|revoke|dbcc|shutdown)\b"
    if not re.match(r"^\(*\s*select\b", stripped) or ";" in stripped.rstrip(";") or re.search(unsafe, stripped):
        raise ReadonlyViolation(f"只读护栏拦截了非查询语句：{sql.strip()[:80]}")


def run_isql(sql: str, config: Config, timeout: int = 300) -> str:
    """执行一次只读查询，返回 isql 原始输出。"""
    assert_readonly(sql)
    if not config.username:
        raise FoxhisError("未配置 Foxhis 数据库账号（只读账号），无法采集。")
    if not Path(config.isql_path).exists():
        raise FoxhisError(f"isql 不存在：{config.isql_path}")

    # 老版 Sybase 不识别 cp936。现场库以 GBK 字节保存中文，需禁用
    # 服务端转换，再由 Python 解码；客户端字符集名与输出编码不是同一概念。
    raw_gbk = config.charset.lower() in {"cp936", "gbk", "gb2312"}
    client_charset = "iso_1" if raw_gbk else config.charset
    output_encoding = "cp936" if raw_gbk else {"iso_1": "latin1", "eucgb": "gb2312"}.get(config.charset, config.charset)
    command = [
        config.isql_path,
        "-S", config.server,
        "-U", config.username,
        "-P", config.password,
        "-I", config.interfaces_file,
        "-J", client_charset,
        "-s", "|",
        "-w", "4000",
        "-l", "10",
    ]
    if config.database:
        command += ["-D", config.database]

    env = os.environ.copy()
    env["SYBASE"] = str(Path(config.isql_path).parent.parent)
    env["LANG"] = "enu"
    env.pop("LC_ALL", None)

    try:
        completed = subprocess.run(
            command,
            input="set nocount on\n" + ("set char_convert off\n" if raw_gbk else "") + f"{sql}\ngo\nquit\n",
            capture_output=True,
            text=True,
            encoding=output_encoding,
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise FoxhisError(f"isql 执行超时（{timeout}s）") from exc

    output = (completed.stdout or "") + (completed.stderr or "")
    lowered = output.lower()
    if "login failed" in lowered:
        raise FoxhisError("数据库登录失败（Login failed），请检查只读账号与密码。")
    if "not found" in lowered and "server name" in lowered:
        raise FoxhisError(f"服务名 {config.server} 未在接口文件中解析到可连接的地址。")
    if completed.returncode or re.search(r"\bmsg\s+\d+", lowered) or "context allocation routine failed" in lowered:
        raise FoxhisError("isql 返回错误：" + output.strip()[:300])

    return output


def parse_isql_table(text: str, separator: str = "|", *, required_columns: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """把 isql 的对齐表输出解析成 [dict]；第一行视为表头。"""
    raw_rows: list[list[str]] = []
    for line in text.splitlines():
        if separator not in line:
            continue
        cells = [cell.strip() for cell in line.split(separator)]
        # 本机 isql 每列前加分隔符（包括第一列），有些版本还追加尾分隔符。
        if line.lstrip().startswith(separator):
            cells = cells[1:]
        if line.rstrip().endswith(separator):
            cells = cells[:-1]
        if any(cell for cell in cells) is False:
            continue
        # 分隔线（----|----）
        if all(set(cell) <= {"-", "+", " "} for cell in cells if cell):
            continue
        raw_rows.append(cells)

    if not raw_rows:
        if required_columns:
            raise FoxhisError("查询结果缺少有效表头，无法确认是否为正常空结果。")
        return []
    header = [cell or f"col{index}" for index, cell in enumerate(raw_rows[0])]
    if not set(required_columns).issubset(header):
        raise FoxhisError("查询结果表头缺少必需字段，无法解析。")
    rows: list[dict[str, str]] = []
    for cells in raw_rows[1:]:
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(
            {
                key: ("" if value.lower() in {"null", "(null)"} else value)
                for key, value in zip(header, cells[: len(header)])
            }
        )
    return rows


def query(sql: str, config: Config, timeout: int = 300, *, required_columns: tuple[str, ...] = ()) -> list[dict[str, str]]:
    return parse_isql_table(run_isql(sql, config, timeout), required_columns=required_columns)


def resolve_hotel_name(config: Config) -> str:
    if not config.hotel_name.strip():
        rows = query("select value as hotel_name from sysoption where catalog='hotel' and item='name'",
                     config, timeout=30, required_columns=("hotel_name",))
        if len(rows) != 1 or not (rows[0].get("hotel_name") or "").strip():
            raise FoxhisError("西软酒店名称未配置，且源库未返回唯一有效名称。")
        config.hotel_name = rows[0]["hotel_name"].strip()
    return config.hotel_name


# ---------------------------------------------------------------------------
# 库结构自省（等只读账号到位后的第一步）
# ---------------------------------------------------------------------------


def db_version(config: Config) -> str:
    rows = parse_isql_table(run_isql("select @@version as version", config, timeout=60))
    return rows[0].get("version", "") if rows else ""


def list_user_tables(config: Config) -> list[str]:
    rows = query("select name from sysobjects where type = 'U' order by name", config, timeout=120)
    return [row.get("name", "") for row in rows if row.get("name")]


def table_columns(config: Config, table: str) -> list[str]:
    sql = (
        "select c.name as column_name, t.name as type_name "
        "from syscolumns c join systypes t on c.usertype = t.usertype "
        f"where c.id = object_id('{table}') order by c.colid"
    )
    rows = query(sql, config, timeout=120)
    return [row.get("column_name", "") for row in rows if row.get("column_name")]


def require_columns(config: Config, table: str, needed: Iterable[str]) -> None:
    actual = set(table_columns(config, table))
    if not actual:
        raise FoxhisError(f"表 {table} 不存在或不可见；请先运行 --introspect 查看实际库结构。")
    missing = [name for name in needed if name not in actual]
    if missing:
        raise FoxhisError(
            f"表 {table} 缺少字段 {missing}；实际字段：{sorted(actual)}。"
            "Foxhis 各版本表结构存在差异，请据此调整 foxhis_sql.py 中的 SQL。"
        )


# ---------------------------------------------------------------------------
# 输出与落库
# ---------------------------------------------------------------------------


def save_output(filename: str, payload: dict[str, Any], config: Config) -> Path:
    directory = Path(config.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target


def mysql_connection(config: Config):
    import pymysql

    settings = dict(config.mysql)
    if not settings.get("host"):
        raise FoxhisError("未配置 MySQL 连接信息，无法落库。")
    return pymysql.connect(**settings)


# ---------------------------------------------------------------------------
# 报表脚本的统一入口
# ---------------------------------------------------------------------------


def report_main(report_key: str, collect: Callable[[Config, date, datetime], dict[str, Any]], *, realtime: bool = False) -> int:
    """
    报表脚本的统一外壳：闸门 → 配置校验 → 日结去重 → 连通性自检 → 采集 → 输出。

    退出码：0 成功；0 但打印 SKIP 表示闸门跳过（不算失败）；其他为失败。
    """
    config = Config.from_env()

    allowed, message = (True, "") if realtime else after_gate(config)
    if not allowed:
        print(f"SKIP [{report_key}] {message}")
        return 0

    for name, value in (
        ("FOXHIS_SERVER", config.server),
        ("FOXHIS_DATABASE", config.database),
        ("FOXHIS_USERNAME", config.username),
    ):
        if not value:
            raise FoxhisError(f"缺少配置 {name}；请在配置页完成 Foxhis 连接参数与只读账号。")

    resolve_hotel_name(config)
    snapshot_time = datetime.now()
    day = snapshot_time.date() if realtime else business_date(config)
    daily_state, record = {}, None
    if report_key in DAILY_REPORTS:
        record = {"business_date": day.isoformat(), "source_endpoint": list(config.endpoint()),
                  "source_database": config.database, "source_user": config.username,
                  "hotel_id": config.hotel_id, "hotel_name": config.hotel_name,
                  "target_host": config.mysql.get("host"), "target_port": config.mysql.get("port"),
                  "target_database": config.mysql.get("database")}
        try:
            state = json.loads((Path(config.output_dir) / DAILY_STATE_FILE).read_text(encoding="utf-8"))
            if isinstance(state, dict):
                daily_state = state
        except (OSError, ValueError):
            pass
        if daily_state.get(report_key) == record and not any(flag in sys.argv for flag in ("--force", "--dry-run", "--initial")):
            print(f"SKIP [{report_key}] business_date={day} 已成功采集；使用 --force 可重新采集")
            return 0

    reachable, detail = check_reachable(config)
    if not reachable:
        raise FoxhisError(f"数据库不可达：{detail}")

    result = collect(config, day, snapshot_time)
    if record is not None and "--dry-run" not in sys.argv and not result.get("dry_run") and result.get("written", 0) > 0:
        daily_state[report_key] = record
        save_output(DAILY_STATE_FILE, daily_state, config)
    print(
        f"OK [{report_key}] business_date={result.get('business_date')} "
        f"rows={result.get('row_count')} written={result.get('written')}"
    )
    return 0
