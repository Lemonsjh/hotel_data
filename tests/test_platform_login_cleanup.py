from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"
sys.path.insert(0, str(SERVICE_DIR))
import platform_login


class PlatformLoginCleanupTests(unittest.TestCase):
    def test_profile_edge_roots_reads_only_returned_pids(self):
        with patch.object(platform_login.subprocess, "run", return_value=SimpleNamespace(stdout="60192\n42380\n")):
            self.assertEqual(platform_login.profile_edge_root_pids("meituan"), [60192, 42380])

    def test_cleanup_kills_only_profile_roots(self):
        with patch.object(platform_login, "profile_edge_root_pids", return_value=[60192]), patch.object(platform_login.subprocess, "run") as run:
            platform_login.stop_profile_edge_processes("meituan")
        self.assertEqual(run.call_args_list[0].args[0], ["taskkill", "/PID", "60192", "/T", "/F"])


if __name__ == "__main__":
    unittest.main()
