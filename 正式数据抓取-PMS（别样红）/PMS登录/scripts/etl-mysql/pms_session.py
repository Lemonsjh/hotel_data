# -*- coding: utf-8 -*-
"""ETL 侧的 PMS 会话兼容入口。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pms_utils


def get_hotel_name_from_session(default_name: str = "") -> str:
    return pms_utils.get_hotel_name_from_session(default_name)


def get_session_info() -> dict[str, Any] | None:
    return pms_utils.read_session(quiet=True)
