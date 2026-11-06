"""Local update API, detached jobs and version pinning without real upgrades."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'OTA采集服务'), str(ROOT / 'updater'), str(ROOT)]
import program_update_routes as routes
import panel_worker as worker
import update


class ProgramUpdateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sys, "argv", sys.argv.copy()))
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'OTA采集服务/state/program_update.json'
        self.state.parent.mkdir(parents=True)
        (self.root / 'updater').mkdir()
        self.app = Flask(__name__, template_folder=str(ROOT / 'OTA采集服务/templates'))
        routes.register(self.app)
        self.client = self.app.test_client()
        self.headers = {'Origin': 'http://localhost', 'X-Update-Token': self.app.extensions['program_update_token']}
        for name, value in [('ROOT', self.root / 'OTA采集服务'), ('PROJECT_ROOT', self.root)]:
            self.enterContext(patch.object(routes.runner, name, value))
        self.enterContext(patch.object(routes.runner, 'load_settings', return_value={}))
        self.enterContext(patch.object(routes.runner, 'python_path', return_value=Path(sys.executable)))

    def installed(self, value='v1.0.0'):
        (self.root / 'updater/installed.json').write_text(json.dumps({'version': value}))

    def ready(self):
        self.state.write_text(json.dumps({'status': 'ready', 'latest': 'v1.0.2', 'checked_at': '2026-11-06 10:00:00'}))

    def test_current_version_is_local_and_unknown_is_not_guessed(self):
        with patch.object(routes.updater, 'latest_release') as release:
            self.assertEqual(self.client.get('/api/program-update').json['current'], '未记录')
            self.installed()
            self.assertEqual(self.client.get('/api/program-update').json['current'], 'v1.0.0')
        release.assert_not_called()

    def test_remote_origin_and_missing_confirmation_cannot_start_update(self):
        with patch.object(routes.subprocess, 'Popen') as launch:
            self.assertEqual(self.client.post('/api/program-update', json={'action': 'check'}).status_code, 403)
            self.assertEqual(self.client.post('/api/program-update', json={'action': 'check'},
                headers={**self.headers, 'Origin': 'https://evil.test'}).status_code, 403)
            self.assertEqual(self.client.get('/api/program-update', environ_base={'REMOTE_ADDR': '192.0.2.1'}).status_code, 403)
            self.assertEqual(self.client.get('/api/program-update', headers={'Host': 'evil.test'}).status_code, 403)
            self.assertEqual(self.client.post('/api/program-update', json={'action': 'apply'}, headers=self.headers).status_code, 409)
        launch.assert_not_called()

    def test_detached_update_requires_checked_version_and_blocks_duplicate(self):
        self.installed(); self.ready()
        with patch.object(routes.subprocess, 'Popen') as launch:
            response = self.client.post('/api/program-update', json={'action': 'apply', 'target': 'v1.0.2', 'confirmed': True}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['busy'])
            command = launch.call_args.args[0]
            self.assertEqual(command[1], str(self.root / 'updater/panel_worker.py'))
            self.assertEqual(command[-2:], ['--target', 'v1.0.2'])
            self.assertEqual(launch.call_args.kwargs['stdin'], routes.subprocess.DEVNULL)
            second = self.client.post('/api/program-update', json={'action': 'check'}, headers=self.headers)
            self.assertEqual(second.status_code, 409)
            self.assertEqual(launch.call_count, 1)

    def test_outdated_confirmation_and_cli_lock_are_rejected(self):
        self.installed(); self.ready()
        with patch.object(routes.subprocess, 'Popen') as launch:
            response = self.client.post('/api/program-update', json={'action': 'apply', 'target': 'v1.0.1', 'confirmed': True}, headers=self.headers)
            self.assertEqual(response.status_code, 409)
            (self.root / 'updater/update.lock').write_text('fixture')
            response = self.client.post('/api/program-update', json={'action': 'check'}, headers=self.headers)
            self.assertEqual(response.status_code, 409)
        launch.assert_not_called()

    def test_dead_worker_is_reported_instead_of_spinning_forever(self):
        self.state.write_text(json.dumps({'status': 'updating', 'pid': 123, 'started': time.time()}))
        with patch.object(routes.runner, 'process_alive', return_value=False):
            self.assertEqual(self.client.get('/api/program-update').json['status'], 'error')
        self.assertIn('进程已退出', json.loads(self.state.read_text(encoding="utf-8"))['message'])

    def test_update_blocks_business_mutations_but_not_read_only_monitoring(self):
        self.app.add_url_rule('/business', 'business', lambda: 'started', methods=['POST'])
        self.state.write_text(json.dumps({'status': 'updating', 'pid': 123, 'started': time.time()}))
        with patch.object(routes.runner, 'process_alive', return_value=True):
            self.assertEqual(self.client.post('/business').status_code, 409)
            self.assertEqual(self.client.get('/api/program-update').status_code, 200)
        self.state.write_text(json.dumps({'status': 'checking', 'started': time.time()}))
        self.assertEqual(self.client.post('/business').status_code, 200)
        (self.root / 'updater/pending.json').write_text('{}')
        self.assertEqual(self.client.post('/business').status_code, 409)

    def test_restart_reads_install_record_and_refreshes_page_token(self):
        self.installed('v1.0.2')
        self.state.write_text(json.dumps({'status': 'success', 'latest': 'v1.0.2', 'message': '更新完成'}))
        restarted = Flask('restarted')
        routes.register(restarted)
        response = restarted.test_client().get('/api/program-update')
        self.assertEqual(response.json['current'], 'v1.0.2')
        self.assertFalse(response.json['available'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotEqual(response.json['token'], self.headers['X-Update-Token'])

    def test_worker_check_does_not_install_and_worker_apply_uses_pinned_version(self):
        self.state.write_text(json.dumps({'job': 'fixture'}))
        with patch.object(worker, 'STATE', self.state), patch.object(worker, 'ROOT', self.root), \
                patch.object(worker.update, 'latest_release', return_value={'tag_name': 'v1.0.2'}), \
                patch.object(worker.update, 'main') as install:
            self.assertEqual(worker.run('check', 'fixture'), 0)
            install.assert_not_called()
            def fake_install():
                self.assertEqual(sys.argv[-3:], ['--yes', '--expected-version', 'v1.0.2'])
                worker.update.PROGRESS('正在安装')
                self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))['status'], 'updating')
            install.side_effect = fake_install
            self.assertEqual(worker.run('apply', 'fixture', 'v1.0.2'), 0)
            self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))['status'], 'success')
            self.assertIsNone(worker.update.PROGRESS)

    def test_worker_failure_does_not_report_success(self):
        self.state.write_text(json.dumps({'job': 'fixture'}))
        with patch.object(worker, 'STATE', self.state), patch.object(worker, 'ROOT', self.root), \
                patch.object(worker.update, 'main', side_effect=ValueError('checksum mismatch')):
            self.assertEqual(worker.run('apply', 'fixture', 'v1.0.2'), 1)
        data = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(data['status'], 'error')
        self.assertIn('checksum mismatch', data['message'])

    def test_bundled_python_can_launch_worker_from_its_own_updater_directory(self):
        self.state.write_text(json.dumps({'job': 'fixture'}))
        for name in ('panel_worker.py', 'update.py', 'install_core.py', 'package_format.py'):
            shutil.copyfile(ROOT / 'updater' / name, self.root / 'updater' / name)
        with (self.root / 'updater/update.py').open('a', encoding='utf-8') as stream:
            stream.write("\ndef latest_release(): return {'tag_name': 'v1.0.2'}\n")
        result = subprocess.run([sys.executable, str(self.root / 'updater/panel_worker.py'), 'check', 'fixture'],
                                cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        data = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['latest'], 'v1.0.2')

    def test_confirmed_target_change_is_rejected_before_lock_and_download(self):
        with patch.object(update, 'ROOT', self.root), patch.object(sys, 'argv', ['update.py', '--apply', '--yes', '--expected-version', 'v1.0.2']), \
                patch.object(update, 'latest_release', return_value={'tag_name': 'v1.0.3'}), \
                patch.object(update, 'download') as download, self.assertRaisesRegex(ValueError, '版本已变化'):
            update.main()
        download.assert_not_called()
        self.assertFalse((self.root / 'updater/update.lock').exists())

    def test_panel_confirmation_runs_installer_without_console_input(self):
        data = b'fixture package'
        release = {'tag_name': 'v1.0.2', 'assets': [
            {'name': update.ASSET_NAME, 'browser_download_url': 'https://gitee.com/attach/package.zip'},
            {'name': update.CHECKSUM_NAME, 'browser_download_url': 'https://gitee.com/attach/package.sha256'}]}
        with patch.object(update, 'ROOT', self.root), \
                patch.object(update, 'os', SimpleNamespace(name='nt', getpid=lambda: 123)), \
                patch.object(sys, 'argv', ['update.py', '--apply', '--yes', '--expected-version', 'v1.0.2']), \
                patch.object(update, 'latest_release', return_value=release), \
                patch.object(update, 'download', side_effect=[update.digest(data).encode(), data]), \
                patch('builtins.input', side_effect=AssertionError('Unexpected console prompt')), \
                patch.object(update, 'apply_update') as install:
            self.assertEqual(update.main(), 0)
            install.assert_called_once()


if __name__ == '__main__':
    unittest.main()
