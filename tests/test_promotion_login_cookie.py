from contextlib import nullcontext
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'OTA采集服务'))
import promotion_login_cookie as module


class PromotionLoginCookieTests(unittest.TestCase):
    def setUp(self):
        self.context = MagicMock()
        self.settings = {'meituan': {'me_cookie': 'fake-me', 'eb_cookie': 'fake-eb', 'promotion_cookie': 'manual'}}
        self.response = MagicMock(url='https://ebmidas.dianping.com/shopdiy/common/ajax/queryFinanceInfo', status=200)
        self.response.json.return_value = {'code': 200, 'msg': {'balance': '0.00'}}
        self.load = patch.object(module.runner, 'load_settings', return_value=self.settings).start()
        self.save = patch.object(module.runner, 'save_json').start()
        patch.object(module.runner.config_transaction, 'transaction', side_effect=lambda *args: nullcontext()).start()
        self.addCleanup(patch.stopall)

    def run_capture(self):
        self.context.reset_mock()
        cookie_header, callback = MagicMock(return_value='fresh-promotion'), MagicMock()
        with module.watch_promotion_cookie(self.context, cookie_header, callback):
            self.context.on.call_args.args[1](self.response)
        self.context.remove_listener.assert_called_once()
        return cookie_header, callback

    def test_successful_manual_visit_overwrites_old_manual_cookie(self):
        header, callback = self.run_capture()
        self.assertEqual(self.settings['meituan']['promotion_cookie'], 'fresh-promotion')
        header.assert_called_once_with(self.context, self.response.url)
        self.save.assert_called_once()
        callback.assert_called_once()

    def test_empty_failed_or_unrelated_response_keeps_previous_cookie(self):
        for payload in ({'code': 0, 'msg': {}}, {'code': 200, 'msg': {}}, []):
            self.response.json.return_value = payload
            self.run_capture()
        self.save.assert_not_called()
        self.assertEqual(self.settings['meituan']['promotion_cookie'], 'manual')

    def test_changed_main_account_does_not_mix_cookies(self):
        other = {'meituan': {'me_cookie': 'other', 'eb_cookie': 'other', 'promotion_cookie': 'keep'}}
        self.load.side_effect = [self.settings, other]
        self.run_capture()
        self.save.assert_not_called()
        self.assertEqual(other['meituan']['promotion_cookie'], 'keep')
