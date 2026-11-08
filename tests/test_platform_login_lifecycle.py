"""Closing the final login window exits even if Edge still reports connected."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'OTA采集服务'))
import platform_login as login


class LoginWindowLifecycleTests(unittest.TestCase):
    def test_closed_last_window_releases_profile_lock_despite_stale_connection(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            folder = Path(directory)
            browser = MagicMock()
            browser.is_connected.return_value = True
            context = SimpleNamespace(browser=browser, pages=[])
            with patch.object(login, 'stop_path', return_value=folder / 'stop'), \
                 patch.object(login.time, 'sleep', side_effect=AssertionError('must not wait without windows')), \
                 patch('meituan_page_capture.profile_path', return_value=folder):
                with login.browser_profile_lock():
                    self.assertEqual((folder / '.capture.lock').read_text(), str(os.getpid()))
                    login.keep_open(context, 'meituan')
                self.assertFalse((folder / '.capture.lock').exists())

    def test_closing_one_page_keeps_another_window_and_final_close_exits(self):
        first, second = MagicMock(), MagicMock()
        browser = MagicMock()
        browser.is_connected.return_value = True
        context = SimpleNamespace(browser=browser, pages=[first, second])

        def close_second(_):
            context.pages = [first]
            raise login.PlaywrightError('Target closed')

        second.wait_for_timeout.side_effect = close_second
        first.wait_for_timeout.side_effect = lambda _: context.pages.clear()
        with patch.object(login, 'stop_path') as stop:
            stop.return_value.exists.return_value = False
            login.keep_open(context, 'meituan')
        second.wait_for_timeout.assert_called_once_with(500)
        first.wait_for_timeout.assert_called_once_with(500)

    def test_stop_request_exits_even_with_an_open_page(self):
        page = MagicMock()
        context = SimpleNamespace(browser=MagicMock(), pages=[page])
        with patch.object(login, 'stop_path') as stop:
            stop.return_value.exists.return_value = True
            login.keep_open(context, 'meituan')
        page.wait_for_timeout.assert_not_called()


if __name__ == '__main__':
    unittest.main()
