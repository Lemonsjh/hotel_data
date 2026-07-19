from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from mysql_connection import connect_mysql as connect_with_retry


PROJECT_ROOT = Path(os.environ.get("HOTEL_OTA_PROJECT_ROOT") or Path(__file__).resolve().parent)
OUTPUT_DIR = Path(os.environ.get("HOTEL_OTA_OUTPUT_DIR") or PROJECT_ROOT / "OTA数据")

DB_CONFIG = {
    "host": os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
    "user": os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
    "password": os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
    "database": os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
    "charset": "utf8mb4",
}

NUMERIC_TYPES = {
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "integer",
    "bigint",
    "decimal",
    "numeric",
    "float",
    "double",
}
DATE_TYPES = {"date", "datetime", "timestamp"}


class MysqlSyncError(RuntimeError):
    pass


def connect_mysql(*, autocommit: bool = False):
    return connect_with_retry(DB_CONFIG, autocommit=autocommit)


def delete_scope(cursor, table_name: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """Replace only the affected hotel/platform snapshot, preserving other scopes."""
    if not rows or "hotel_id" not in headers:
        cursor.execute(f"DELETE FROM `{table_name}`")
        return

    hotel_index = headers.index("hotel_id")
    if "platform_scope" not in headers:
        hotel_ids = sorted({str(row[hotel_index] or "").strip() for row in rows if row[hotel_index]})
        marks = ", ".join(["%s"] * len(hotel_ids))
        cursor.execute(f"DELETE FROM `{table_name}` WHERE hotel_id IN ({marks})", hotel_ids)
        return

    scope_index = headers.index("platform_scope")
    scopes = sorted(
        {
            (str(row[hotel_index] or "").strip(), str(row[scope_index] or "").strip())
            for row in rows
            if row[hotel_index] and row[scope_index]
        }
    )
    if not scopes:
        raise MysqlSyncError(f"Table {table_name} requires non-empty hotel_id and platform_scope")
    conditions = " OR ".join("(hotel_id=%s AND platform_scope=%s)" for _ in scopes)
    cursor.execute(f"DELETE FROM `{table_name}` WHERE {conditions}", [value for scope in scopes for value in scope])


def sync_table(
    table_name: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    allow_empty_replace: bool = False,
) -> None:
    if not table_name.replace("_", "").isalnum():
        raise MysqlSyncError(f"Invalid table name: {table_name}")
    if not rows and not allow_empty_replace:
        raise MysqlSyncError(f"Refusing to replace {table_name} with empty rows")

    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            if (
                table_name == "meituan_ota_activity_product_detail"
                and "ota_room_type_id" in column_types
                and "ota_product_id" not in column_types
            ):
                cursor.execute(
                    "ALTER TABLE `meituan_ota_activity_product_detail` "
                    "RENAME COLUMN `ota_room_type_id` TO `ota_product_id`"
                )
                column_types = load_column_types(cursor, table_name)
            effective_headers = list(headers)
            synthetic_values = build_synthetic_values(table_name, column_types, effective_headers)
            effective_headers.extend(synthetic_values)
            insert_headers = [header for header in effective_headers if header in column_types]
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")

            delete_scope(cursor, table_name, headers, rows)
            if rows:
                placeholders = ", ".join(["%s"] * len(insert_headers))
                columns = ", ".join(f"`{header}`" for header in insert_headers)
                sql = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
                cursor.executemany(
                    sql,
                    [
                        tuple(
                            convert_value(
                                value_for_header(header, row, headers, synthetic_values),
                                column_types[header],
                            )
                            for header in insert_headers
                        )
                        for row in rows
                    ],
                )
        connection.commit()
        print(f"DB synced: {table_name} rows={len(rows)}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_metric_history_table(
    table_name: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    key_columns: set[str],
    retention_days: int | None,
) -> None:
    if not rows:
        raise MysqlSyncError(f"Refusing to sync empty metric history: {table_name}")
    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")
            columns = list(headers)
            missing_keys = key_columns.difference(columns)
            if missing_keys:
                raise MysqlSyncError(f"Metric history keys missing: {', '.join(sorted(missing_keys))}")
            updates = [column for column in columns if column not in key_columns]
            placeholders = ", ".join(["%s"] * len(columns))
            sql = (
                f"INSERT INTO `{table_name}` ({', '.join(f'`{column}`' for column in columns)}) "
                f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE "
                + ", ".join(f"`{column}`=VALUES(`{column}`)" for column in updates)
            )
            cursor.executemany(
                sql,
                [tuple(convert_value(value, column_types[column]) for column, value in zip(columns, row)) for row in rows],
            )
            hotel_ids = sorted({str(row[columns.index("hotel_id")]) for row in rows if row[columns.index("hotel_id")]})
            if retention_days is not None and hotel_ids:
                marks = ", ".join(["%s"] * len(hotel_ids))
                cursor.execute(
                    f"DELETE FROM `{table_name}` WHERE hotel_id IN ({marks}) AND business_date<%s",
                    (*hotel_ids, date.today() - timedelta(days=retention_days)),
                )
                deleted = cursor.rowcount
            else:
                deleted = 0
        connection.commit()
        print(f"DB synced: {table_name} rows={len(rows)} retention_deleted={deleted}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_metric_history(headers: Sequence[str], rows: Sequence[Sequence[Any]], retention_days: int = 30) -> None:
    """美团经营指标按营业日和指标编码增量保存。"""
    sync_metric_history_table(
        "meituan_ota_business_metrics",
        headers,
        rows,
        {"hotel_id", "business_date", "metric_code"},
        retention_days,
    )


def sync_ctrip_metric_history(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], retention_days: int = 30
) -> None:
    """携程经营指标按营业日和指标编码增量保存。"""
    sync_metric_history_table(
        "ctrip_ota_business_metrics",
        headers,
        rows,
        {"hotel_id", "platform_scope", "business_date", "metric_code"},
        retention_days,
    )


def sync_monthly_history(
    table_name: str, headers: Sequence[str], rows: Sequence[Sequence[Any]], retention_days: int = 30
) -> None:
    """按统计窗口结束日期保留近30天指标快照。"""
    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")
            updates = [column for column in headers if column not in {"hotel_id", "business_date"}]
            columns = ", ".join(f"`{column}`" for column in headers)
            values = ", ".join(["%s"] * len(headers))
            changed = ", ".join(f"`{column}`=VALUES(`{column}`)" for column in updates)
            cursor.executemany(
                f"INSERT INTO `{table_name}` ({columns}) VALUES ({values}) ON DUPLICATE KEY UPDATE {changed}",
                [tuple(convert_value(value, column_types[column]) for column, value in zip(headers, row)) for row in rows],
            )
            hotel_ids = sorted({str(row[0]) for row in rows if row and row[0]})
            deleted = 0
            if hotel_ids:
                marks = ", ".join(["%s"] * len(hotel_ids))
                cursor.execute(
                    f"DELETE FROM `{table_name}` WHERE hotel_id IN ({marks}) AND business_date < %s",
                    (*hotel_ids, date.today() - timedelta(days=retention_days)),
                )
                deleted = cursor.rowcount
        connection.commit()
        print(f"DB synced: {table_name} rows={len(rows)} retention_deleted={deleted}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_latest_row(table_name: str, headers: Sequence[str], row: Sequence[Any]) -> None:
    """Replace the current hotel's row while leaving other hotels untouched."""
    if "hotel_id" not in headers:
        raise MysqlSyncError("Latest-row sync requires hotel_id")
    hotel_id = str(row[headers.index("hotel_id")] or "").strip()
    if not hotel_id:
        raise MysqlSyncError("Latest-row sync requires a non-empty hotel_id")
    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")
            columns = ", ".join(f"`{header}`" for header in headers)
            values = ", ".join(["%s"] * len(headers))
            cursor.execute(f"DELETE FROM `{table_name}` WHERE hotel_id=%s", (hotel_id,))
            cursor.execute(
                f"INSERT INTO `{table_name}` ({columns}) VALUES ({values})",
                tuple(convert_value(value, column_types[header]) for header, value in zip(headers, row)),
            )
        connection.commit()
        print(f"DB synced: {table_name} rows=1 mode=latest")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_user_source_history(headers: Sequence[str], rows: Sequence[Sequence[Any]], retention_days: int = 30) -> None:
    sync_monthly_history("meituan_ota_user_source_monthly", headers, rows, retention_days)


def sync_exposure_source_history(headers: Sequence[str], rows: Sequence[Sequence[Any]], retention_days: int = 30) -> None:
    sync_monthly_history("meituan_ota_exposure_source_monthly", headers, rows, retention_days)


def sync_exposure_source_daily_history(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], retention_days: int = 30
) -> None:
    sync_monthly_history("meituan_ota_exposure_source_daily", headers, rows, retention_days)


def sync_meituan_scan_orders(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], hotel_id: str, retention_start: date
) -> None:
    table_name = "meituan_ota_scan_order_detail"
    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")
            columns = ", ".join(f"`{column}`" for column in headers)
            values = ", ".join(["%s"] * len(headers))
            updates = ", ".join(
                f"`{column}`=VALUES(`{column}`)" for column in headers if column not in {"hotel_id", "order_id"}
            )
            if rows:
                cursor.executemany(
                    f"INSERT INTO `{table_name}` ({columns}) VALUES ({values}) ON DUPLICATE KEY UPDATE {updates}",
                    [tuple(convert_value(value, column_types[column]) for column, value in zip(headers, row)) for row in rows],
                )
            if hotel_id:
                cursor.execute(
                    f"""DELETE FROM `{table_name}`
                        WHERE hotel_id=%s
                          AND (scan_time < %s OR (scan_time IS NULL AND collected_at < %s))""",
                    (hotel_id, retention_start, retention_start),
                )
                deleted = cursor.rowcount
            else:
                deleted = 0
        connection.commit()
        print(f"DB synced: {table_name} rows={len(rows)} retention_deleted={deleted}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_order_loss_snapshot(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], *, allow_empty_replace: bool = False
) -> None:
    """覆写保存当前美团近30天流失订单竞争酒店快照。"""
    table_name = "meituan_ota_order_loss_monthly"
    if not rows and not allow_empty_replace:
        raise MysqlSyncError(f"Refusing to replace {table_name} with empty rows")

    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")
            columns = ", ".join(f"`{column}`" for column in headers)
            values = ", ".join(["%s"] * len(headers))
            cursor.execute(f"DELETE FROM `{table_name}`")
            if rows:
                cursor.executemany(
                    f"INSERT INTO `{table_name}` ({columns}) VALUES ({values})",
                    [tuple(convert_value(value, column_types[column]) for column, value in zip(headers, row)) for row in rows],
                )
        connection.commit()
        print(f"DB synced: {table_name} rows={len(rows)} mode=overwrite")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_joined_rights_snapshot(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], *, allow_empty_replace: bool = False
) -> None:
    """覆写保存美团当前已报名权益快照。"""
    table_name = "meituan_ota_joined_rights"
    if not rows and not allow_empty_replace:
        raise MysqlSyncError(f"Refusing to replace {table_name} with empty rows")

    connection = connect_mysql(autocommit=False)
    try:
        with connection.cursor() as cursor:
            column_types = load_column_types(cursor, table_name)
            missing = [header for header in headers if header not in column_types]
            if missing:
                raise MysqlSyncError(f"Table {table_name} missing columns: {', '.join(missing)}")
            columns = ", ".join(f"`{column}`" for column in headers)
            values = ", ".join(["%s"] * len(headers))
            cursor.execute(f"DELETE FROM `{table_name}`")
            if rows:
                cursor.executemany(
                    f"INSERT INTO `{table_name}` ({columns}) VALUES ({values})",
                    [tuple(convert_value(value, column_types[column]) for column, value in zip(headers, row)) for row in rows],
                )
        connection.commit()
        print(f"DB synced: {table_name} rows={len(rows)} mode=overwrite")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_column_types(cursor: Any, table_name: str) -> dict[str, str]:
    cursor.execute(
        """
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (DB_CONFIG["database"], table_name),
    )
    columns = {name: data_type.lower() for name, data_type in cursor.fetchall()}
    if not columns:
        raise MysqlSyncError(f"Table {table_name} does not exist in database {DB_CONFIG['database']}")
    return columns


def build_synthetic_values(table_name: str, column_types: dict[str, str], headers: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "source_platform" in column_types and "source_platform" not in headers:
        values["source_platform"] = default_source_platform(table_name)
    if "hotel_name" in column_types and "hotel_name" not in headers:
        values["hotel_name"] = default_hotel_name(table_name)
    return values


def value_for_header(header: str, row: Sequence[Any], headers: Sequence[str], synthetic_values: dict[str, Any]) -> Any:
    if header in synthetic_values:
        return synthetic_values[header]
    try:
        index = list(headers).index(header)
    except ValueError:
        return None
    return row[index] if index < len(row) else None


def default_source_platform(table_name: str) -> str:
    if table_name.startswith("meituan_"):
        return "美团"
    if table_name.startswith("ctrip_"):
        return "携程"
    if table_name.startswith(("jd", "jy", "kf", "rs", "v_openclaw")):
        return "PMS（别样红）"
    return ""


def default_hotel_name(table_name: str) -> str:
    if table_name.startswith("meituan_"):
        return os.environ.get("MEITUAN_HOTEL_NAME", "").strip()
    if table_name.startswith("ctrip_"):
        return os.environ.get("CTRIP_HOTEL_NAME", "").strip()
    return os.environ.get("PMS_HOTEL_NAME", "").strip()


def convert_value(value: Any, data_type: str) -> Any:
    if value in (None, "", "-", "--"):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if data_type in NUMERIC_TYPES:
        return to_number(value)
    if data_type in DATE_TYPES:
        return str(value).strip() or None
    return str(value) if not isinstance(value, (int, float, Decimal)) else value


def to_number(value: Any) -> Any:
    if isinstance(value, (int, float, Decimal)):
        return value
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return int(number) if number == number.to_integral_value() else number
