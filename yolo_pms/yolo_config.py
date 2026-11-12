"""余乐通 E8HotelMis 连接配置。

源库是酒店本机的 SQL Server（迷你 SQL2000，默认 8829 端口）。采集只读，
源库账号应由酒店方限定为只读权限；本模块的只读护栏只防代码误用。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CHARSET = "cp936"
DEFAULT_OUTPUT_DIR = "OTA数据"
DEFAULT_SERVER = "127.0.0.1,8829"
DEFAULT_DATABASE = "Yolo880_Hotel"

CLIENT_NAMES = ("SQLCMD.EXE", "sqlcmd.exe", "OSQL.EXE", "osql.exe")
CLIENT_SEARCH_ROOTS = (
    r"C:\Program Files\Microsoft SQL Server",
    r"C:\Program Files (x86)\Microsoft SQL Server",
    r"D:\Program Files\Microsoft SQL Server",
    r"D:\Program Files (x86)\Microsoft SQL Server",
)


class YoloError(RuntimeError):
    pass


def find_client(configured: str = "") -> str:
    """按 配置 → PATH → 常见安装目录 定位命令行客户端，找不到返回空串。"""
    if configured and Path(configured).is_file():
        return configured
    for name in CLIENT_NAMES:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    # 新版 SQL Server 把客户端装在 Tools\Binn 下，版本号目录不固定
    for root in CLIENT_SEARCH_ROOTS:
        if not Path(root).is_dir():
            continue
        for name in CLIENT_NAMES:
            for candidate in sorted(Path(root).glob(f"*/Tools/Binn/{name}"), reverse=True):
                if candidate.is_file():
                    return str(candidate)
    return ""


@dataclass
class Config:
    cli_path: str = ""
    server: str = DEFAULT_SERVER
    database: str = DEFAULT_DATABASE
    username: str = ""
    password: str = ""
    charset: str = DEFAULT_CHARSET
    hotel_id: str = ""
    hotel_name: str = ""
    output_dir: str = DEFAULT_OUTPUT_DIR
    fetch_after: str = ""
    business_date_offset_days: int = 1
    timeout_seconds: int = 300
    mysql: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Config":
        environ = os.environ if environ is None else environ

        def env(*names: str, default: str = "") -> str:
            for name in names:
                value = environ.get(name)
                if value and value.strip():
                    return value.strip()
            return default

        try:
            offset = int(env("YOLO_BUSINESS_DATE_OFFSET_DAYS", default="1"))
        except ValueError:
            offset = 1
        try:
            timeout = int(env("YOLO_TIMEOUT_SECONDS", default="300"))
        except ValueError:
            timeout = 300

        return cls(
            cli_path=env("YOLO_CLI_PATH"),
            server=env("YOLO_SERVER", default=DEFAULT_SERVER),
            database=env("YOLO_DATABASE", default=DEFAULT_DATABASE),
            username=env("YOLO_USERNAME"),
            password=env("YOLO_PASSWORD"),
            charset=env("YOLO_CHARSET", default=DEFAULT_CHARSET),
            hotel_id=env("HOTEL_ID"),
            hotel_name=env("YOLO_HOTEL_NAME"),
            output_dir=env("HOTEL_OTA_OUTPUT_DIR", default=DEFAULT_OUTPUT_DIR),
            fetch_after=env("YOLO_FETCH_AFTER"),
            business_date_offset_days=offset,
            timeout_seconds=timeout,
            mysql={
                "host": env("HOTEL_OTA_MYSQL_HOST", "MYSQL_HOST"),
                "port": int(env("HOTEL_OTA_MYSQL_PORT", "MYSQL_PORT", default="3306") or 3306),
                "user": env("HOTEL_OTA_MYSQL_USER", "MYSQL_USER"),
                "password": env("HOTEL_OTA_MYSQL_PASSWORD", "MYSQL_PASSWORD"),
                "database": env("HOTEL_OTA_MYSQL_DATABASE", "MYSQL_DATABASE"),
                "charset": "utf8mb4",
            },
        )

    def endpoint(self) -> tuple[str, int]:
        """把 `host,port` 形式的实例名解析成可做连通性探测的地址。"""
        raw = (self.server or "").strip()
        if not raw:
            raise YoloError("未配置余乐通数据库实例（形如 127.0.0.1,8829）。")
        host, _, port = raw.partition(",")
        host = host.strip()
        if "\\" in host:
            raise YoloError(f"暂不支持命名实例：{raw}；请填写 host,port 形式。")
        try:
            return host, int(port.strip() or 1433)
        except ValueError:
            raise YoloError(f"实例名端口无效：{raw}") from None
