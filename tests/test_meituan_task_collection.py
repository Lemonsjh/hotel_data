from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module(filename: str, module_name: str):
    sys.path.insert(0, str(MEITUAN_DIR))
    spec = importlib.util.spec_from_file_location(module_name, MEITUAN_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Frame:
    def __init__(self, text: str):
        self.text = text

    def locator(self, _selector):
        return self

    def inner_text(self, timeout):
        return self.text


class _Page:
    def __init__(self, *texts: str):
        self.frames = [_Frame(text) for text in texts]


class MeituanTaskCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = load_module("meituan_scan_order_data.py", "meituan_scan_order_test_target")
        cls.video = load_module("meituan_video_upload_status_data.py", "meituan_video_upload_test_target")

    def test_scan_order_accepts_successful_empty_data(self):
        response = _Response({"status": 0, "data": None})
        with patch.object(self.scan, "MEITUAN_EB_COOKIE", "test-cookie"), patch.object(
            self.scan.requests, "get", return_value=response
        ):
            data = self.scan.request_page(date(2026, 8, 1), date(2026, 8, 2), 1)
        self.assertEqual(data, {})

    def test_scan_order_failure_includes_platform_message(self):
        response = _Response({"status": 303, "msg": "登录状态失效", "data": None})
        with patch.object(self.scan, "MEITUAN_EB_COOKIE", "test-cookie"), patch.object(
            self.scan.requests, "get", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "status=303; message=登录状态失效"):
                self.scan.request_page(date(2026, 8, 1), date(2026, 8, 2), 1)

    def test_video_counts_are_read_from_the_loaded_video_frame(self):
        page = _Page(
            "页面框架",
            "待上传视频任务\n房型视频 8/12\n酒店预览视频 0/1\n房型预览视频 0/12",
        )
        self.assertEqual(
            self.video.video_rows_from_page(page),
            [("room_type_video", 8, 12), ("hotel_preview_video", 0, 1), ("room_type_preview_video", 0, 12)],
        )


if __name__ == "__main__":
    unittest.main()
