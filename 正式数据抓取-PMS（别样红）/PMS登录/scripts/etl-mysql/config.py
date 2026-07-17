# -*- coding: utf-8 -*-
"""
ETL 配置文件 - 统一管理酒店信息和数据库连接
酒店名称和组织 ID 优先从环境变量或当前会话获取。
"""

import json
import os
from pathlib import Path


_OUTPUT_DIR = str(Path(__file__).resolve().parents[2] / "output")


def load_service_settings():
    path = Path(__file__).resolve().parents[4] / "OTA采集服务" / "config" / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


_service_settings = load_service_settings()
_pms_settings = _service_settings.get("pms") or {}
_configured_hotel_name = os.environ.get("PMS_HOTEL_NAME") or _pms_settings.get("hotel_name")

try:
    from pms_session import get_hotel_name_from_session, get_session_info
    _session_info = get_session_info() or {}
    _hotel_name = _configured_hotel_name or get_hotel_name_from_session()
except Exception as e:
    print(f"⚠️ 获取会话酒店名称失败: {e}")
    _session_info = {}
    _hotel_name = _configured_hotel_name or ""

if not _hotel_name:
    raise RuntimeError("PMS 酒店名称为空，请先刷新登录会话或配置 pms.hotel_name")


def session_org_id() -> str:
    configured = os.environ.get("PMS_ORG_ID", "").strip()
    if configured:
        return configured
    for key in ("jl11_payload", "jl02_payload", "jl01_payload", "jd01_payload", "jd04_payload", "kf11_payload"):
        value = str((_session_info.get(key) or {}).get("orgId") or "").strip()
        if value:
            return value
    return str((_session_info.get("cookies") or {}).get("LoginOrgId") or "").strip()


# =========================================================
# 🏨 酒店信息配置
# =========================================================
HOTEL_CONFIG = {
    "name": _hotel_name,
    "source_platform": "PMS（别样红）",
    "short_name": _hotel_name.replace("（", "(").replace("）", ")").split("（")[0].split("(")[0],
    "org_id": session_org_id(),
}

# =========================================================
# 📊 输出路径配置
# =========================================================
OUTPUT_CONFIG = {
    "base_dir": _OUTPUT_DIR,
    "json_dir": _OUTPUT_DIR,
}

# =========================================================
# 🗄️ MySQL 数据库配置
# =========================================================
def mysql_value(config, key, env_name, default=""):
    return os.environ.get(env_name) or config.get(key) or default


_mysql = _service_settings.get("mysql") or {}
_hotel_id = os.environ.get("HOTEL_ID") or str((_service_settings.get("hotel") or {}).get("hotel_id") or "")
HOTEL_CONFIG["id"] = _hotel_id.strip()
DB_CONFIG = {
    "host": mysql_value(_mysql, "host", "HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
    "port": int(mysql_value(_mysql, "port", "HOTEL_OTA_MYSQL_PORT", 3306)),
    "user": mysql_value(_mysql, "user", "HOTEL_OTA_MYSQL_USER"),
    "password": mysql_value(_mysql, "password", "HOTEL_OTA_MYSQL_PASSWORD"),
    "database": mysql_value(_mysql, "database", "HOTEL_OTA_MYSQL_DATABASE"),
    "charset": "utf8mb4",
}

# =========================================================
# 📈 报表配置
# =========================================================
REPORT_CONFIG = {
    "rs01_file": "RS01.json",
    "jd01_file": "JD01.json",
    "jd04_file": "JD04.json",
    "jy01_file": "JY01.json",
    "jy03_file": "JY03.json",
    "jl01_file": "JL01.json",
    "jl02_file": "JL02.json",
    "jl11_file": "JL11.json",
    "forecast_file": "FORECAST.json",
    "hourly_status_file": "ROOM_STATUS.json",
    "kf11_file": "KF11.json",
}
