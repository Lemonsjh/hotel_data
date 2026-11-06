"""Exercise real Windows stop/install/restart against a disposable HTTP panel."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'updater'))
import package_format as fmt
import update

PANEL = '''import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
BUILD = "old"
port = json.loads((Path(__file__).parent / "config/settings.json").read_text())["service"]["panel_port"]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(BUILD.encode())
    def log_message(self, *args): pass
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@unittest.skipUnless(os.name == 'nt', 'Real service lifecycle requires Windows')
class RealPanelRestartTests(unittest.TestCase):
    def test_real_panel_restarts_with_updated_code_and_preserves_configuration(self):
        with tempfile.TemporaryDirectory(prefix='updater-restart-', dir=ROOT / 'exports') as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to((ROOT / 'exports').resolve()))
            service = root / 'OTA采集服务'
            (service / 'config').mkdir(parents=True)
            (root / 'updater').mkdir()
            work = root / 'updater/work/fixture'
            work.mkdir(parents=True)
            shutil.copyfile(ROOT / 'updater/service_guard.ps1', root / 'updater/service_guard.ps1')
            (root / 'updater/smoke_check.py').write_text("print('isolated fixture smoke check passed')\n")
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            config = service / 'config/settings.json'
            config.write_text(json.dumps({'service': {'panel_port': port}}))
            config_bytes = config.read_bytes()
            (service / 'panel.py').write_text(PANEL)
            (root / 'requirements-portable.lock.txt').write_bytes(b'example==1\n')
            content = {'OTA采集服务/panel.py': PANEL.replace('BUILD = "old"', 'BUILD = "new"').encode(),
                       'OTA采集服务/runner.py': b'# fixture runner\n',
                       'mysql_connection.py': b'# fixture connector\n', 'ota_mysql_writer.py': b'# fixture writer\n'}
            manifest = {'format': 1, 'repository': fmt.REPOSITORY, 'version': 'v9.0.0',
                        'python': f'{sys.version_info.major}.{sys.version_info.minor}',
                        'dependency_sha256': fmt.dependency_digest(b'example==1\n'),
                        'files': {name: fmt.digest(data) for name, data in content.items()}}
            package = work / 'fixture.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                for name, data in content.items():
                    archive.writestr(name, data)
                archive.writestr('manifest.json', json.dumps(manifest))
            process = subprocess.Popen([sys.executable, str(service / 'panel.py')], cwd=service,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            url = f'http://127.0.0.1:{port}/config'
            try:
                for _ in range(40):
                    try:
                        with urllib.request.urlopen(url, timeout=1) as response:
                            self.assertEqual(response.read(), b'old')
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    self.fail('Fixture panel did not start')
                update.apply_update(root, package, 'v9.0.0', work)
                with urllib.request.urlopen(url, timeout=2) as response:
                    self.assertEqual(response.read(), b'new')
                new_pid = int((service / 'state/panel.pid').read_text().strip())
                self.assertNotEqual(new_pid, process.pid)
                self.assertEqual(config.read_bytes(), config_bytes)
                self.assertEqual(update.read_json(root / 'updater/installed.json')['version'], 'v9.0.0')
                self.assertFalse((root / 'updater/pending.json').exists())
            finally:
                snapshots = list((root / 'updater/backups').glob('*/services.json'))
                if snapshots:
                    update.service(root, work / 'service_guard.ps1', snapshots[0], 'Undo')
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
