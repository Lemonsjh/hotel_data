from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "正式数据抓取-PMS（别样红）" / "ota调价" / "ctrip_change_price.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ctrip_price_verification_target", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load ctrip_change_price.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CtripPriceVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_current_price_ignores_related_product_price(self):
        data = {
            "roomPriceSettingMap": {
                "1335461005": {
                    "priceRelationRoomPriceSettingMap": {
                        "1335461006": {"firstDayPriceInfo": {"price": 154}}
                    },
                    "firstDayPriceInfo": {"price": 144},
                    "priceInfo": [{"price": 144}],
                }
            }
        }

        self.assertEqual(self.module.get_current_price(data, "1335461005"), 144.0)


if __name__ == "__main__":
    unittest.main()
