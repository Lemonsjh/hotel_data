from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务'), str(ROOT / '美团OTA数据采集代码')]
import meituan_promotion_balance_data as task
import promotion_balance as storage


class MeituanBalanceBrowserTests(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.page = self.context.pages[0]
        self.response = MagicMock(url='https://ebmidas.dianping.com' + task.API_PATH, status=200)
        self.response.json.return_value = {'code': 200, 'msg': {'balance': '0.00'}}

    def send_response(self, *args, **kwargs):
        self.context.on.call_args.args[1](self.response)

    def test_captures_page_response_and_releases_browser(self):
        self.page.goto.side_effect = self.send_response
        with patch.object(task, 'promotion_browser', return_value=nullcontext(self.context)):
            self.assertEqual(task.fetch_balance(), self.response.json.return_value)
        self.context.remove_listener.assert_called_once()
        self.page.goto.assert_called_once_with(task.PAGE_URL, wait_until='domcontentloaded', timeout=60_000)

    def test_no_request_keeps_previous_database_snapshot(self):
        with patch.object(task, 'promotion_browser', return_value=nullcontext(self.context)), \
             patch.object(task, 'page_access_issue', return_value=None), \
             patch.object(storage, 'sync_table') as sync:
            with self.assertRaisesRegex(RuntimeError, '未发出余额请求'):
                storage.main('meituan', task.fetch_balance)
        sync.assert_not_called()
        self.context.remove_listener.assert_called_once()

    def test_failed_http_response_does_not_return_data(self):
        self.page.goto.side_effect = self.send_response
        self.response.status = 403
        with patch.object(task, 'promotion_browser', return_value=nullcontext(self.context)):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                task.fetch_balance()


if __name__ == '__main__':
    unittest.main()
