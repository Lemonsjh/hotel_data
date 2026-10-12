from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SERVICE_DIR = Path(__file__).resolve().parents[1] / "OTA采集服务"
sys.path.insert(0, str(SERVICE_DIR))
import panel_common


class PanelCommonPlatformTests(unittest.TestCase):
    def test_import_without_windows_api(self):
        spec = importlib.util.spec_from_file_location(
            "panel_common_posix_test", SERVICE_DIR / "panel_common.py"
        )
        module = importlib.util.module_from_spec(spec)
        with patch.object(os, "name", "posix"), patch.object(
            ctypes, "WinDLL", create=True, side_effect=AssertionError("Windows API loaded")
        ) as win_dll:
            spec.loader.exec_module(module)
        win_dll.assert_not_called()
        self.assertIsNone(module.KERNEL32)
        self.assertFalse(module.process_alive(123))

    def test_windows_process_handle_is_closed(self):
        kernel32 = Mock()
        kernel32.OpenProcess.return_value = 1234
        with patch.object(panel_common, "KERNEL32", kernel32):
            self.assertTrue(panel_common.process_alive("123"))
        kernel32.OpenProcess.assert_called_once_with(0x1000, False, 123)
        kernel32.CloseHandle.assert_called_once_with(1234)

    def test_missing_process_has_no_handle_to_close(self):
        kernel32 = Mock()
        kernel32.OpenProcess.return_value = None
        with patch.object(panel_common, "KERNEL32", kernel32):
            self.assertFalse(panel_common.process_alive(123))
        kernel32.CloseHandle.assert_not_called()

    def test_invalid_pid_is_safe(self):
        kernel32 = Mock()
        with patch.object(panel_common, "KERNEL32", kernel32):
            for pid in (None, "", "not-a-pid", object()):
                self.assertFalse(panel_common.process_alive(pid))
        kernel32.OpenProcess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
