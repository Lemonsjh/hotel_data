"""别样红仅账号密码登录；旧手动配置不再启用浏览器会话模式。"""
import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'OTA采集服务'
PMS = ROOT / '别样红PMS数据采集代码/PMS登录'
sys.path[:0] = [str(SERVICE), str(PMS / 'scripts')]
import panel
import platform_login
import runner
import pms_utils


class PmsPasswordLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('password_only_pms_fetch', PMS / 'fetch_main.py')
        cls.fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fetch)

    def session(self, hours=0):
        return {'login_time': (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S'),
                'account_fingerprint': pms_utils.account_fingerprint('account')}

    def test_login_address_is_plain_input_without_manual_controls(self):
        html = panel.config_control({'pms': {'login_base_url': 'https://example.test:8101'}},
                                    'pms.login_base_url', '登录地址', False)
        self.assertIn("name='pms.login_base_url'", html)
        self.assertIn("value='https://example.test:8101'", html)
        self.assertNotIn('<button', html)
        self.assertNotIn('/platform-login/pms', html)

    def test_pms_helper_routes_are_disabled_without_saving_configuration(self):
        client = panel.app.test_client()
        with patch.object(runner, 'load_settings') as load, patch.object(runner, 'save_json') as save:
            self.assertEqual(client.post('/platform-login/pms').status_code, 302)
            self.assertEqual(client.post('/platform-login/pms/cancel').status_code, 302)
            self.assertEqual(client.get('/api/platform-login/pms').status_code, 404)
        load.assert_not_called()
        save.assert_not_called()

    def test_other_platform_login_helpers_are_unchanged(self):
        self.assertEqual(set(platform_login.PLATFORMS), {'meituan', 'ctrip', 'bypms', 'meituan_pms'})
        self.assertFalse((SERVICE / 'pms_manual_login.py').exists())

    def test_defaults_have_no_login_mode_and_old_flag_does_not_skip_credentials(self):
        settings = json.loads((SERVICE / 'config/settings.example.json').read_text(encoding='utf-8-sig'))
        self.assertNotIn('login_mode', settings['pms'])
        settings['pms'].update(enabled=True, provider='byh', login_mode='manual', username='', password='')
        warnings = runner.config_warnings(settings)
        self.assertIn('PMS username is empty.', warnings)
        self.assertIn('PMS password is empty.', warnings)

    def test_old_mode_setting_no_longer_clears_password(self):
        settings = {'pms': {'login_mode': 'manual', 'username': 'account', 'password': 'fake-password'}}
        with patch.dict(os.environ, {}, clear=True):
            env = runner.build_env(settings, 'pms')
        self.assertEqual(env['PMS_USERNAME'], 'account')
        self.assertEqual(env['PMS_PASSWORD'], 'fake-password')
        self.assertNotIn('PMS_LOGIN_MODE', env)

    def test_password_session_keeps_twelve_hour_lifetime_and_account_binding(self):
        for hours, account, expected in ((0, 'account', False), (13, 'account', True), (0, 'other', True)):
            with self.subTest(hours=hours, account=account), \
                    patch.object(pms_utils, 'read_session', return_value=self.session(hours)):
                self.assertEqual(self.fetch.need_login(username=account), expected)

    def test_old_manual_session_requires_password_login_without_deleting_it(self):
        session = {**self.session(), 'login_mode': 'manual'}
        with patch.object(pms_utils, 'read_session', return_value=session), \
                patch.object(pms_utils, 'delete_session') as delete:
            self.assertTrue(self.fetch.need_login(username='account'))
        self.assertEqual(session['login_mode'], 'manual')
        delete.assert_not_called()

    def test_old_environment_flag_does_not_enable_manual_authentication(self):
        login = Mock(return_value=False)
        with patch.dict(os.environ, {'PMS_LOGIN_MODE': 'manual', 'PMS_USERNAME': 'account', 'PMS_PASSWORD': 'fake'}), \
                patch.dict(sys.modules, {'login': SimpleNamespace(login=login)}), \
                patch.object(sys, 'argv', ['fetch_main.py']), \
                patch.object(self.fetch, 'need_login', return_value=True), \
                patch.object(self.fetch, 'run_fetch_jobs') as reports:
            self.assertEqual(self.fetch.main(), 1)
        login.assert_called_once_with('account', 'fake')
        reports.assert_not_called()

    def test_missing_password_stops_before_login_or_report_execution(self):
        login = Mock()
        with patch.dict(os.environ, {'PMS_USERNAME': 'account', 'PMS_PASSWORD': ''}), \
                patch.dict(sys.modules, {'login': SimpleNamespace(login=login)}), \
                patch.object(sys, 'argv', ['fetch_main.py']), \
                patch.object(self.fetch, 'need_login', return_value=True), \
                patch.object(self.fetch, 'run_fetch_jobs') as reports:
            self.assertEqual(self.fetch.main(), 1)
        login.assert_not_called()
        reports.assert_not_called()

    def test_paused_reports_are_excluded_by_default_but_can_be_selected_manually(self):
        for args, expected in (([], set()), (['--reports', 'KF11'], {'KF11'}),
                               (['--reports', 'JD04'], {'JD04'})):
            with self.subTest(args=args), \
                    patch.object(sys, 'argv', ['fetch_main.py', *args]), \
                    patch.object(self.fetch, 'need_login', return_value=False), \
                    patch.object(pms_utils, 'ensure_hotel_name', return_value=''), \
                    patch.object(self.fetch, 'run_fetch_jobs', side_effect=RuntimeError('stop before ETL')) as jobs:
                with self.assertRaisesRegex(RuntimeError, 'stop before ETL'):
                    self.fetch.main()
                codes = [job[0] for job in jobs.call_args.args[0]]
                self.assertEqual(set(codes) & {'KF11', 'JD04'}, expected)
                self.assertEqual(jobs.call_args.kwargs['enable_circuit_breaker'], not bool(args))
                if not args:
                    self.assertEqual(len(codes), 9)


if __name__ == '__main__':
    unittest.main()
