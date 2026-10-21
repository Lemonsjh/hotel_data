"""西软连接配置；源数据库必须使用受数据库权限约束的只读账号。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CHARSET = "cp936"
DEFAULT_OUTPUT_DIR = "OTA数据"


class FoxhisError(RuntimeError):
    pass


@dataclass
class Config:
    isql_path: str = ""
    interfaces_file: str = ""
    server: str = ""
    database: str = ""
    username: str = ""
    password: str = ""
    charset: str = DEFAULT_CHARSET
    hotel_id: str = ""
    hotel_name: str = ""
    output_dir: str = DEFAULT_OUTPUT_DIR
    fetch_after: str = ""
    business_date_offset_days: int = 1
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

        offset_raw = env("FOXHIS_BUSINESS_DATE_OFFSET_DAYS", default="1")
        try:
            offset = int(offset_raw)
        except ValueError:
            offset = 1

        mysql = {
            "host": env("HOTEL_OTA_MYSQL_HOST", "MYSQL_HOST"),
            "port": int(env("HOTEL_OTA_MYSQL_PORT", "MYSQL_PORT", default="3306") or 3306),
            "user": env("HOTEL_OTA_MYSQL_USER", "MYSQL_USER"),
            "password": env("HOTEL_OTA_MYSQL_PASSWORD", "MYSQL_PASSWORD"),
            "database": env("HOTEL_OTA_MYSQL_DATABASE", "MYSQL_DATABASE"),
            "charset": "utf8mb4",
        }

        return cls(
            isql_path=env("FOXHIS_ISQL_PATH", default=r"C:\SYBASE\BIN\isql.exe"),
            interfaces_file=env("FOXHIS_INTERFACES_FILE", default=r"C:\SYBASE\ini\SQL.INI"),
            server=env("FOXHIS_SERVER"),
            database=env("FOXHIS_DATABASE"),
            username=env("FOXHIS_USERNAME"),
            password=env("FOXHIS_PASSWORD"),
            charset=env("FOXHIS_CHARSET", default=DEFAULT_CHARSET),
            hotel_id=env("HOTEL_ID"),
            hotel_name=env("FOXHIS_HOTEL_NAME"),
            output_dir=env("HOTEL_OTA_OUTPUT_DIR", default=DEFAULT_OUTPUT_DIR),
            fetch_after=env("FOXHIS_FETCH_AFTER"),
            business_date_offset_days=offset,
            mysql=mysql,
        )

    def endpoint(self) -> tuple[str, int]:
        """从接口文件解析当前服务名对应的 host/port。"""
        path = Path(self.interfaces_file)
        if not path.exists():
            raise FoxhisError(f"接口文件不存在：{path}")
        current = ""
        for raw in path.read_text(encoding="gbk", errors="replace").splitlines():
            line = raw.strip()
            if not line or line[0] in ";#":
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                continue
            if current == self.server and "=" in line:
                parts = [part.strip() for part in line.split("=", 1)[1].split(",")]
                if len(parts) >= 3 and parts[2].isdigit():
                    return parts[1], int(parts[2])
        raise FoxhisError(f"接口文件里找不到服务名 {self.server} 的地址定义")
