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

    def is_visible(self, timeout=None):
        return self.visible

    def wait_for(self, **kwargs):
        self.waits.append(kwargs)
        self.visible = True

    def click(self, **_kwargs):
        self.clicks += 1


class FakeLocatorGroup:
    def __init__(self, locators):
        self.locators = locators

    def count(self):
        return len(self.locators)

    def nth(self, index):
        return self.locators[index]


class FakePage:
    def __init__(self, locators):
        self.locators = locators

    def get_by_text(self, text, exact):
        self.assert_exact = exact
        locators = self.locators[text]
        return FakeLocatorGroup(locators if isinstance(locators, list) else [locators])

    def wait_for_timeout(self, _timeout):
        for locators in self.locators.values():
            for locator in locators if isinstance(locators, list) else [locators]:
                locator.visible = True


class FakeRedirectPage:
    def __init__(self):
        self.urls = ["https://eb.meituan.com/ebooking/new-workbench/index.html", "https://me.meituan.com/ebooking/merchant/ebIframe"]
        self.index = 0

    @property
    def url(self):
        return self.urls[self.index]

    def wait_for_timeout(self, _timeout):
        self.index = min(self.index + 1, len(self.urls) - 1)


class PromotionStatusNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_expands_parent_before_clicking_hidden_child(self):
        parent, child = FakeLocator(True), FakeLocator(False)
        self.module.click_workbench_menu(FakePage({"促销推广": parent, "公益流量": child}), "促销推广", "公益流量")

        self.assertEqual(parent.clicks, 1)
        self.assertTrue(child.visible)
        self.assertEqual(child.clicks, 1)

    def test_does_not_collapse_an_already_visible_child(self):
        parent, child = FakeLocator(True), FakeLocator(True)
        self.module.click_workbench_menu(FakePage({"信息管理": parent, "酒店亮点": child}), "信息管理", "酒店亮点")

        self.assertEqual(parent.clicks, 0)
        self.assertEqual(child.clicks, 1)

    def test_uses_visible_parent_when_same_text_has_hidden_copy(self):
        hidden_parent, visible_parent, child = FakeLocator(False), FakeLocator(True), FakeLocator(False)
        page = FakePage({"促销推广": [hidden_parent, visible_parent], "公益流量": child})
        self.module.click_workbench_menu(page, "促销推广", "公益流量")

        self.assertEqual(hidden_parent.clicks, 0)
        self.assertEqual(visible_parent.clicks, 1)
        self.assertEqual(child.clicks, 1)

    def test_waits_for_workbench_wrapper_before_navigation(self):
        page = FakeRedirectPage()
        self.module.wait_for_workbench_wrapper(page, 1_000)

        self.assertEqual(page.url, "https://me.meituan.com/ebooking/merchant/ebIframe")


if __name__ == "__main__":
    unittest.main()
