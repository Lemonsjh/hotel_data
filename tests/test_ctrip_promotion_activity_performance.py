from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(CTRIP_DIR))
    path = CTRIP_DIR / "ctrip_promotion_activity_performance_data.py"
    spec = importlib.util.spec_from_file_location("ctrip_promotion_performance_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CtripPromotionActivityPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_uses_the_completed_30_day_window(self):
        self.assertEqual(
            self.module.rolling_30_day_range(date(2026, 9, 18)),
            (date(2026, 8, 19), date(2026, 9, 17)),
        )

    def test_builds_campaign_rows_with_shared_totals(self):
        payload = {"rcode": 0, "data": {
            "totalCampaignQuantity": 1044, "totalQuantity": 1044, "compAvgTotalQuantity": 123.68,
            "quantityYoy": 100.0, "compAvgQuantityYoy": 44.27, "promotionSwitch": "T",
            "promotionDetailBo": [{
                "campaignId": 5, "campaignName": "天天特价", "campaignQuantity": 1044,
                "quantityYoy": 100.0, "campaignGmv": 281495.0, "campaignGmvYoy": 100.0,
                "discountAmt": 14538.0,
            }],
        }}
        rows = self.module.rows_from_payload(
            payload, "hotel-1", datetime(2026, 9, 18, 12), date(2026, 8, 19), date(2026, 9, 17),
        )
        self.assertEqual(rows[0][0], "hotel-1")
        self.assertEqual(rows[0][3:5], [date(2026, 8, 19), date(2026, 9, 17)])
        self.assertEqual(rows[0][7:12], [1044, 1044, 123.68, 100.0, 44.27])
        self.assertEqual(rows[0][12:], [5, "天天特价", 1044, 100.0, 281495.0, 100.0, 14538.0])


if __name__ == "__main__":
    unittest.main()
