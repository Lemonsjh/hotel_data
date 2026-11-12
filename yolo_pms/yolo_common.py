"""余乐通采集公共模块。

职责：只读查询、结果解析、业务日闸门、连通性自检、MySQL 落库与输出。

设计约束
  · 查询护栏只防代码误用，真实隔离依赖源库的只读账号
  · 只用标准库 + pymysql，不新增 pip 依赖，可跑在便携 runtime 上
  · 采集失败一律抛错，绝不补零写脏数据
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
from typing import Any, Callable, Iterable, Sequence

from yolo_config import Config, YoloError, find_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from OTA采集服务.pms_history_retention import cleanup_rows  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE_PLATFORM = "PMS（余乐通）"
DEFAULT_OUTPUT_DIR = "OTA数据"
# 取列分隔符而非空白：备注等字段可能含空格，用空白无法还原列边界。
# 用 ASCII 单元分隔符（US），业务数据里不会出现；不用 RS/FS，
# 因为那几个字符属于 Python splitlines() 的行边界，会把一行拆散。
CELL_SEPARATOR = "\x1f"

_UNSAFE_SQL = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|"
    r"grant|revoke|shutdown|dbcc|backup|restore)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 只读护栏与查询
# ---------------------------------------------------------------------------


def assert_readonly(sql: str) -> None:
    """只允许单条 SELECT；注释与字符串字面量先剔除，避免误判。"""
    stripped = re.sub(r"'(?:''|[^'])*'", "''", sql)
    stripped = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/", " ", stripped).strip()
    if not re.match(r"^\(*\s*select\b", stripped, re.IGNORECASE):
        raise YoloError(f"只读护栏拦截了非查询语句：{sql.strip()[:80]}")
    if ";" in stripped.rstrip(";") or _UNSAFE_SQL.search(stripped):
        raise YoloError(f"只读护栏拦截了非查询语句：{sql.strip()[:80]}")


def flatten_sql(sql: str) -> str:
    """命令行客户端只吃单行 -Q：剥离 -- 行注释并把换行折叠成空格。

    余乐通报表 SQL 里有 `--` 注释（如 jd01 的备注字段说明），
    直接折叠换行会让注释吞掉后半条语句；字符串字面量内的字符原样保留。
    """
    out: list[str] = []
    index = 0
    in_string = False
    while index < len(sql):
        char = sql[index]
        if in_string:
            out.append(char)
            if char == "'":
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    out.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "-" and index + 1 < len(sql) and sql[index + 1] == "-":
            while index < len(sql) and sql[index] not in "\r\n":
                index += 1
            out.append(" ")
            continue
        out.append(" " if char in "\r\n\t" else char)
        index += 1
    return " ".join("".join(out).split())


def run_query(sql: str, config: Config, timeout: int | None = None) -> str:
    """执行一次只读查询，返回客户端原始输出。"""
    assert_readonly(sql)
    if not config.username:
        raise YoloError("未配置余乐通数据库只读账号，无法采集。")
    # 配置里的客户端路径只在「本机确实存在」时才采用：换机器部署时该路径
    # （例如某台机器专用的适配层脚本）可能不存在，此时回落到自动探测，
    # 避免整套采集被一个跟机器绑定的路径卡死。
    cli = find_client(config.cli_path)
    if not cli:
        raise YoloError("未找到 SQL Server 命令行客户端（SQLCMD/OSQL），请填写客户端路径。")
    # 只有经 .cmd/.bat 包装层调用时才需要压成单行：cmd.exe 在换行处会截断
    # 命令行，客户端只收到半条 SQL。原生 exe（SQLCMD）按原样发送，与历史版本一致。
    if cli.lower().endswith((".cmd", ".bat")):
        sql = flatten_sql(sql)

    command = [
        cli,
        "-S", config.server,
        "-U", config.username,
        "-d", config.database,
        "-s", CELL_SEPARATOR,  # 列分隔符
        # -y 0 会吞掉表头，8000 是变长类型上限，既不截断也保留表头；
        # -W 与 -y 互斥，尾随空格由解析端 strip。
        "-y", "8000",
        "-b",                  # 出错时返回非零退出码
        "-l", "15",            # 登录超时
        "-Q", f"SET NOCOUNT ON; {sql}",
    ]
    env = os.environ.copy()
    env["SQLCMDPASSWORD"] = config.password  # 密码不进命令行，避免出现在进程列表
    env["LANG"] = "enu"

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout or config.timeout_seconds,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise YoloError(f"查询超时（{timeout or config.timeout_seconds}s）") from exc

    output = completed.stdout.decode(config.charset, "replace")
    errors = completed.stderr.decode(config.charset, "replace")
    if completed.returncode:
        raise YoloError("查询失败：" + (errors or output).strip()[:400])
    return output


def parse_table(text: str) -> list[dict[str, str]]:
    """把客户端的列分隔输出解析成 [dict]；首行视为表头。

    单列结果里客户端不会插入列分隔符，因此不能拿分隔符判断「是不是数据行」；
    表头下的虚线行按字符内容剔除（SET NOCOUNT ON 已去掉「N 行受影响」尾注）。
    """
    rows: list[list[str]] = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(CELL_SEPARATOR)]
        if not any(cells):
            continue
        if all(set(cell) <= {"-", "+"} for cell in cells if cell):
            continue  # 表头下的分隔线
        rows.append(cells)

    if not rows:
        return []
    header = [cell or f"col{index}" for index, cell in enumerate(rows[0])]
    result: list[dict[str, str]] = []
    for cells in rows[1:]:
        cells += [""] * (len(header) - len(cells))
        result.append({
            key: "" if value.upper() == "NULL" else value
            for key, value in zip(header, cells)
        })
    return result


def query(sql: str, config: Config, timeout: int | None = None,
          *, required: Iterable[str] = ()) -> list[dict[str, str]]:
    rows = parse_table(run_query(sql, config, timeout))
    missing = [name for name in required if rows and name not in rows[0]]
    if missing:
        raise YoloError(f"查询结果缺少字段 {missing}，请核对余乐通库结构。")
    return rows


# ---------------------------------------------------------------------------
# 时间与连通性
# ---------------------------------------------------------------------------


def business_date(config: Config) -> date:
    """业务日 = 今天 - 偏移。夜审后采的是「昨天」的账。"""
    override = os.environ.get("YOLO_BUSINESS_DATE", "").strip()
    if override:
        return date.fromisoformat(override)
    return (datetime.now() - timedelta(days=config.business_date_offset_days)).date()


def after_gate(config: Config) -> tuple[bool, str]:
    """早于夜审完成时刻直接跳过，避免把空数据当成「当天为 0」写库。"""
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


def check_reachable(config: Config, timeout: float = 3.0) -> tuple[bool, str]:
    """TCP 可达性自检：不通要快速失败，而不是耗满查询超时。"""
    try:
        host, port = config.endpoint()
    except YoloError as exc:
        return False, str(exc)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} 可达"
    except OSError as exc:
        return False, f"{host}:{port} 不可达（{exc}）"


# ---------------------------------------------------------------------------
# 落库与输出
# ---------------------------------------------------------------------------


def mysql_connection(config: Config):
    import pymysql

    settings = dict(config.mysql)
    if not settings.get("host"):
        raise YoloError("未配置 MySQL 连接信息，无法落库。")
    return pymysql.connect(**settings)


def save_output(filename: str, payload: dict[str, Any], config: Config) -> Path:
    directory = Path(config.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return target


def replace_snapshot(table: str, columns: Sequence[str], rows: list[dict[str, Any]],
                     config: Config, scope: tuple[date, ...], *, unique_key: str = "") -> int:
    """按「酒店 + 来源 + 营业日」在事务里整段替换，避免半成品数据留在库里。

    写入前检查目标表是否已有更新批次，拒绝旧快照覆盖新数据。
    """
    if not rows:
        raise YoloError(f"{table} 无有效数据，已保留目标表原样。")
    hotel_id, stamp = config.hotel_id, rows[0]["snapshot_time"]
    if not hotel_id.strip():
        raise YoloError("酒店 ID 为空，无法落库。")
    if unique_key and len({tuple(row[key] for key in unique_key.split(",")) for row in rows}) != len(rows):
        raise YoloError(f"{table} 输出唯一键 {unique_key} 重复。")

    days = sorted(set(scope))
    marks = ", ".join(["%s"] * len(days))
    condition = f"hotel_id=%s AND source_platform=%s AND business_date IN ({marks})"
    args = (hotel_id, SOURCE_PLATFORM, *days)
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(f"`{name}`" for name in columns)

    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT MAX(snapshot_time) FROM `{table}` WHERE {condition}", args)
            existing = cursor.fetchone()
            if existing and existing[0] and existing[0] > stamp:
                raise YoloError(f"{table} 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{table}` WHERE {condition}", args)
            cursor.executemany(
                f"INSERT INTO `{table}` ({column_sql}) VALUES ({placeholders})",
                [tuple(row[name] for name in columns) for row in rows],
            )
            cleanup_rows(cursor, table, rows, hotel_id, SOURCE_PLATFORM)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def replace_batch(table: str, columns: Sequence[str], rows: list[dict[str, Any]],
                  config: Config, *, unique_key: str = "") -> int:
    """滚动汇总表整段替换：按「酒店 + 来源」删除后重写，库里只保留最新一批。

    jl11 这类「最近 N 天滚动汇总」没有逐日键，每次重算整批覆盖，
    不做历史累积，也不参与按日期的历史清理。
    """
    if not rows:
        raise YoloError(f"{table} 无有效数据，已保留目标表原样。")
    hotel_id, stamp = config.hotel_id, rows[0]["snapshot_time"]
    if not hotel_id.strip():
        raise YoloError("酒店 ID 为空，无法落库。")
    if unique_key and len({tuple(row[key] for key in unique_key.split(",")) for row in rows}) != len(rows):
        raise YoloError(f"{table} 输出唯一键 {unique_key} 重复。")

    args = (hotel_id, SOURCE_PLATFORM)
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(f"`{name}`" for name in columns)

    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT MAX(snapshot_time) FROM `{table}` "
                           "WHERE hotel_id=%s AND source_platform=%s", args)
            existing = cursor.fetchone()
            if existing and existing[0] and existing[0] > stamp:
                raise YoloError(f"{table} 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{table}` WHERE hotel_id=%s AND source_platform=%s", args)
            cursor.executemany(
                f"INSERT INTO `{table}` ({column_sql}) VALUES ({placeholders})",
                [tuple(row[name] for name in columns) for row in rows],
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def replace_periods(table: str, columns: Sequence[str], rows: list[dict[str, Any]],
                    config: Config, period_column: str, periods: Sequence[str], *,
                    unique_key: str = "") -> int:
    """按「酒店 + 来源 + 周期」在事务里整段替换，供月表（jy03）一类无逐日键的报表使用。

    与 replace_snapshot 的区别只有定位列：月表用 period_month（'2026-11'）而不是
    business_date（date），因此不能比日期大小，只能按等值集合删除。
    写入后在同一事务内按 pms_monthly_months 清理超期月份。
    """
    if not rows:
        raise YoloError(f"{table} 无有效数据，已保留目标表原样。")
    hotel_id, stamp = config.hotel_id, rows[0]["snapshot_time"]
    if not hotel_id.strip():
        raise YoloError("酒店 ID 为空，无法落库。")
    if unique_key and len({tuple(row[key] for key in unique_key.split(",")) for row in rows}) != len(rows):
        raise YoloError(f"{table} 输出唯一键 {unique_key} 重复。")

    values = sorted({str(value) for value in periods})
    if not values:
        raise YoloError(f"{table} 缺少可定位的周期值，拒绝落库。")
    marks = ", ".join(["%s"] * len(values))
    condition = f"hotel_id=%s AND source_platform=%s AND `{period_column}` IN ({marks})"
    args = (hotel_id, SOURCE_PLATFORM, *values)
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(f"`{name}`" for name in columns)

    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT MAX(snapshot_time) FROM `{table}` WHERE {condition}", args)
            existing = cursor.fetchone()
            if existing and existing[0] and existing[0] > stamp:
                raise YoloError(f"{table} 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{table}` WHERE {condition}", args)
            cursor.executemany(
                f"INSERT INTO `{table}` ({column_sql}) VALUES ({placeholders})",
                [tuple(row[name] for name in columns) for row in rows],
            )
            cleanup_rows(cursor, table, rows, hotel_id, SOURCE_PLATFORM)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


# ---------------------------------------------------------------------------
# 报表脚本统一外壳
# ---------------------------------------------------------------------------


def report_main(report_key: str, collect: Callable[[Config, datetime], dict[str, Any]], *,
                realtime: bool = False) -> int:
    """闸门 → 配置校验 → 连通性 → 采集 → 输出。退出码 0 成功，非 0 失败。"""
    config = Config.from_env()

    if not realtime:
        allowed, message = after_gate(config)
        if not allowed:
            print(f"SKIP [{report_key}] {message}")
            return 0

    for name, value in (("YOLO_SERVER", config.server), ("YOLO_USERNAME", config.username)):
        if not value:
            raise YoloError(f"缺少配置 {name}；请在配置页完成余乐通连接参数与只读账号。")

    reachable, detail = check_reachable(config)
    if not reachable:
        raise YoloError(f"数据库不可达：{detail}")

    result = collect(config, datetime.now().replace(microsecond=0))
    print(f"OK [{report_key}] business_date={result.get('business_date')} "
          f"rows={result.get('row_count')} written={result.get('written')}")
    return 0
