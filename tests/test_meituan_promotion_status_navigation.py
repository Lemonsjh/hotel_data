from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(MEITUAN_DIR))
    path = MEITUAN_DIR / "meituan_promotion_status_data.py"
    spec = importlib.util.spec_from_file_location("meituan_promotion_navigation_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeLocator:
    def __init__(self, visible: bool):
        self.visible = visible
        self.waits = []
        self.clicks = 0

    @property
    def first(self):
        return self

    def is_visible(self, timeout):
        return self.visible

    def wait_for(self, **kwargs):
        self.waits.append(kwargs)
        self.visible = True

    def click(self, **_kwargs):
        self.clicks += 1


class FakePage:
    def __init__(self, locators):
        self.locators = locators

    def get_by_text(self, text, exact):
        self.assert_exact = exact
        return self.locators[text]


class PromotionStatusNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_expands_parent_before_clicking_hidden_child(self):
        parent, child = FakeLocator(True), FakeLocator(False)
        self.module.click_workbench_menu(FakePage({"促销推广": parent, "公益流量": child}), "促销推广", "公益流量")

        self.assertEqual(parent.clicks, 1)
        self.assertEqual(child.clicks, 1)
        self.assertEqual(child.waits[-1]["state"], "visible")

    def test_does_not_collapse_an_already_visible_child(self):
        parent, child = FakeLocator(True), FakeLocator(True)
        self.module.click_workbench_menu(FakePage({"信息管理": parent, "酒店亮点": child}), "信息管理", "酒店亮点")

        self.assertEqual(parent.clicks, 0)
        self.assertEqual(child.clicks, 1)


if __name__ == "__main__":
    unittest.main()
