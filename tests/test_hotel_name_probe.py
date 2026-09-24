from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "OTA采集服务"
sys.path.insert(0, str(SERVICE_DIR))

import hotel_name_probe


class HotelNameProbeTests(unittest.TestCase):
    def test_ctrip_page_module_title_is_not_a_hotel_name(self):
        self.assertFalse(hotel_name_probe.looks_like_hotel("酒店点评分"))

    def test_hotel_name_remains_a_valid_candidate(self):
        self.assertTrue(hotel_name_probe.looks_like_hotel("贵阳智町·栖筑优品酒店"))


if __name__ == "__main__":
    unittest.main()
