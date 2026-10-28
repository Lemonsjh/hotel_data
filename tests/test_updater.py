from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "updater"))
import install_core as core
import package_format as fmt
import update
import update_guard
import build_release


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "requirements-portable.lock.txt").write_bytes(b"example==1\r\n")

    def package(self, extras=None, manifest_changes=None):
        content = {
            "mysql_connection.py": b"# new\n", "ota_mysql_writer.py": b"# new\n",
            "OTA采集服务/runner.py": b"# new\n", "OTA采集服务/panel.py": b"# new\n",
        }
        content.update(extras or {})
        manifest = {
            "format": 1, "repository": fmt.REPOSITORY, "version": "v1.0.0",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "dependency_sha256": fmt.dependency_digest(b"example==1\n"),
            "files": {name: fmt.digest(data) for name, data in content.items()}, "removed": [], "schema": [],
        }
        manifest.update(manifest_changes or {})
        path = self.root / "test.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in content.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest))
        return path, manifest, content

    def test_version_and_line_ending_independent_dependencies(self):
        self.assertLess(fmt.version("v1.9.0"), fmt.version("v1.10.0"))
        for value in ("main", "v1.0.0-rc1", "01.0.0", "1.2"):
            with self.assertRaises(ValueError):
                fmt.version(value)
        path, manifest, content = self.package()
        self.assertEqual(fmt.validate_package(path, self.root, "v1.0.0"), (manifest, content))

    def test_protected_and_unsafe_paths(self):
        for name in (
            "../evil.py", "/evil.py", "OTA采集服务/../evil.py", "OTA采集服务\\runner.py",
            "OTA采集服务/config/settings.json", "OTA采集服务/state/x.py", "runtime/python.exe",
            "updater/installed.json", "updater/backups/x.py", "OTA采集服务/CON.py", "updater/file.py:evil",
            "updater/name. /evil.py", "requirements-portable.lock.txt", "tests/x.py",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                fmt.allowed_path(name)

    def test_case_collisions_and_unlisted_entries(self):
        path, _, _ = self.package({"OTA采集服务/Runner.py": b"# collision"})
        with self.assertRaises(ValueError):
            fmt.validate_package(path, self.root)

    def test_reject_file_directory_collision(self):
        path, _, _ = self.package({"updater/file.py": b"# file", "updater/file.py/child.py": b"# child"})
        with self.assertRaises(ValueError):
            fmt.validate_package(path, self.root)
        path, _, _ = self.package()
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("hidden.txt", "not listed")
        with self.assertRaises(ValueError):
            fmt.validate_package(path, self.root)

    def test_wrong_checksum_runtime_and_release_version(self):
        for changes in ({"dependency_sha256": "bad"}, {"python": "0.0"}, {"version": "v2.0.0"}):
            path, _, _ = self.package(manifest_changes=changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                fmt.validate_package(path, self.root, "v1.0.0")
        path, _, _ = self.package(manifest_changes={"files": {"fake.py": "bad"}})
        with self.assertRaises(ValueError):
            fmt.validate_package(path, self.root)

    def test_reject_zip_symlink(self):
        path, _, _ = self.package()
        entry = zipfile.ZipInfo("updater/link.py")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(entry, "../../secret")
        with self.assertRaises(ValueError):
            fmt.validate_package(path, self.root)

    def test_invalid_python_is_rejected_before_install(self):
        path, _, _ = self.package({"updater/broken.py": b"def broken("})
        with self.assertRaises(SyntaxError):
            fmt.validate_package(path, self.root)

    def test_transaction_restores_previous_files_and_preserves_settings(self):
        _, manifest, content = self.package()
        original = self.root / "mysql_connection.py"
        original.write_bytes(b"# original")
        config = self.root / "OTA采集服务/config/settings.json"
        config.parent.mkdir(parents=True)
        config.write_bytes(b'{"private":"do not overwrite"}')
        backup = self.root / "updater/backups/test"
        plan = core.prepare(self.root, backup, manifest, content)
        core.apply_files(self.root, manifest, content, plan)
        self.assertEqual(original.read_bytes(), content["mysql_connection.py"])
        core.restore(self.root, backup, plan)
        self.assertEqual(original.read_bytes(), b"# original")
        self.assertFalse((self.root / "ota_mysql_writer.py").exists())
        self.assertEqual(config.read_bytes(), b'{"private":"do not overwrite"}')

    def test_removal_only_managed_and_unmodified_files(self):
        _, manifest, content = self.package()
        old = self.root / "OTA采集服务/old.py"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"# old")
        manifest["removed"] = ["OTA采集服务/old.py"]
        backup = self.root / "updater/backups/first"
        plan = core.prepare(self.root, backup, manifest, content)
        core.apply_files(self.root, manifest, content, plan)
        self.assertTrue(old.exists())
        installed = self.root / "updater/installed.json"
        installed.write_text(json.dumps({"files": {"OTA采集服务/old.py": fmt.digest(b"# old")}}))
        plan = core.prepare(self.root, self.root / "updater/backups/second", manifest, content)
        core.apply_files(self.root, manifest, content, plan)
        self.assertFalse(old.exists())

    def test_local_modifications_are_not_overwritten(self):
        _, manifest, content = self.package()
        (self.root / "mysql_connection.py").write_bytes(b"# local edits")
        (self.root / "updater").mkdir()
        (self.root / "updater/installed.json").write_text(json.dumps({"files": {"mysql_connection.py": fmt.digest(b"# old")}}))
        with self.assertRaises(ValueError):
            core.prepare(self.root, self.root / "updater/backups/test", manifest, content)

    def test_code_only_updater_rejects_database_migrations(self):
        for changes in ({"schema": [{"table": "anything"}]}, {"migrations": ["some.sql"]}):
            path, _, _ = self.package(manifest_changes=changes)
            with self.assertRaises(ValueError):
                fmt.validate_package(path, self.root)

    def test_update_pending_blocks_business_entry(self):
        pending = self.root / "updater/pending.json"
        pending.parent.mkdir()
        pending.write_text("{}")
        with patch.object(update_guard, "__file__", str(self.root / "update_guard.py")), self.assertRaises(RuntimeError):
            update_guard.ensure_not_updating()

    def test_latest_release_requires_stable_complete_assets(self):
        base = {"tag_name": "v1.0.0", "assets": [{"name": fmt.ASSET_NAME}, {"name": fmt.ASSET_NAME.replace('.zip', '.sha256')}]}
        with patch.object(update, "download", return_value=json.dumps(base).encode()):
            self.assertEqual(update.latest_release()["tag_name"], "v1.0.0")
        for changes in ({"draft": True}, {"prerelease": True}, {"assets": []}):
            with patch.object(update, "download", return_value=json.dumps({**base, **changes}).encode()), self.assertRaises(ValueError):
                update.latest_release()

    def test_api_rate_limit_uses_fixed_repository_stable_redirect(self):
        response = MagicMock()
        response.__enter__.return_value.url = f"https://github.com/{fmt.REPOSITORY}/releases/tag/v1.2.3"
        limited = urllib.error.HTTPError("https://api.github.com/", 403, "rate limit", {}, None)
        context = update.https_context()
        with patch.object(update, "download", side_effect=limited), patch.object(update.urllib.request, "urlopen", return_value=response) as open_url, \
                patch.object(update, "https_context", return_value=context):
            self.assertEqual(update.latest_release()["tag_name"], "v1.2.3")
        self.assertIs(open_url.call_args.kwargs['context'], context)

    def test_https_context_adds_bundle_to_existing_system_trust(self):
        context = MagicMock()
        with patch.object(update.ssl, 'create_default_context', return_value=context) as create:
            self.assertIs(update.https_context(), context)
        create.assert_called_once_with()
        context.load_verify_locations.assert_called_once_with(cafile=update.certifi.where())
        with patch.object(update.certifi, 'where', return_value=str(self.root / 'missing.pem')):
            with self.assertRaises(OSError):
                update.https_context()

    def test_https_context_works_without_system_roots_and_keeps_verification(self):
        context = update.ssl.SSLContext(update.ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(context.cert_store_stats()['x509_ca'], 0)
        with patch.object(update.ssl, 'create_default_context', return_value=context):
            self.assertIs(update.https_context(), context)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)
        self.assertEqual(context.verify_mode, update.ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_download_uses_verified_context_and_preserves_limits(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.url, response.read.return_value = 'https://example.test/asset', b'abc'
        context = update.https_context()
        with patch.object(update, 'https_context', return_value=context), \
                patch.object(update.urllib.request, 'urlopen', return_value=response) as open_url:
            self.assertEqual(update.download(response.url, 3), b'abc')
            self.assertIs(open_url.call_args.kwargs['context'], context)
            response.read.assert_called_once_with(4)
            response.read.return_value = b'abcd'
            with self.assertRaises(ValueError):
                update.download(response.url, 3)
            response.url = 'http://example.test/asset'
            with self.assertRaises(ValueError):
                update.download('https://example.test/asset', 3)

    def test_certificate_failure_does_not_retry_without_verification(self):
        error = urllib.error.URLError(update.ssl.SSLCertVerificationError('untrusted issuer'))
        with patch.object(update.urllib.request, 'urlopen', side_effect=error) as open_url:
            with self.assertRaises(urllib.error.URLError):
                update.download('https://example.test/asset', 3)
        open_url.assert_called_once()
        context = open_url.call_args.kwargs['context']
        self.assertEqual(context.verify_mode, update.ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def lifecycle_fixture(self):
        path, manifest, content = self.package()
        updater = self.root / "updater"
        updater.mkdir()
        for name in ("service_guard.ps1", "smoke_check.py"):
            (updater / name).write_text("# fixture")
        work = updater / "work/test"
        work.mkdir(parents=True)
        (self.root / "mysql_connection.py").write_bytes(b"# original")
        return path, work

    def test_simulated_install_updates_version_without_database_or_config_writes(self):
        path, work = self.lifecycle_fixture()
        with patch.object(update, "service") as service, patch.object(update, "panel_health"), patch.object(update.subprocess, "run"):
            update.apply_update(self.root, path, "v1.0.0", work)
        self.assertEqual(core.read_json(self.root / "updater/installed.json")["version"], "v1.0.0")
        self.assertFalse((self.root / "updater/pending.json").exists())
        self.assertEqual([call.args[-1] for call in service.call_args_list], ["Inspect", "Suspend", "Panel"])

    def test_simulated_panel_failure_restores_code_and_version(self):
        path, work = self.lifecycle_fixture()
        with patch.object(update, "service"), patch.object(update, "panel_health", side_effect=RuntimeError("health failed")), \
             patch.object(update.subprocess, "run"), self.assertRaises(RuntimeError):
            update.apply_update(self.root, path, "v1.0.0", work)
        self.assertEqual((self.root / "mysql_connection.py").read_bytes(), b"# original")
        self.assertFalse((self.root / "updater/installed.json").exists())
        self.assertFalse((self.root / "updater/pending.json").exists())

    def test_busy_service_never_changes_program_files(self):
        path, work = self.lifecycle_fixture()
        with patch.object(update, "service", side_effect=RuntimeError("busy")), self.assertRaises(RuntimeError):
            update.apply_update(self.root, path, "v1.0.0", work)
        self.assertEqual((self.root / "mysql_connection.py").read_bytes(), b"# original")
        self.assertFalse((self.root / "updater/pending.json").exists())

    def extension_files(self):
        files = {name: b"// extension fixture" for name in fmt.BROWSER_EXTENSION_FILES}
        files["extensions/meituan-login-sync/manifest.json"] = json.dumps({
            "manifest_version": 3, "version": "0.2.2",
        }).encode()
        return files

    def test_extension_allowlist_does_not_open_arbitrary_json_or_directories(self):
        for name in fmt.BROWSER_EXTENSION_FILES:
            self.assertEqual(fmt.allowed_path(name), name)
        for name in ("extensions/other/manifest.json", "extensions/meituan-login-sync/settings.json",
                     "extensions/meituan-login-sync/private.js", "extensions/meituan-login-sync/subdir/worker.js",
                     "OTA采集服务/credentials.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                fmt.allowed_path(name)

    def test_extension_package_is_complete_and_manifest_is_valid(self):
        files = self.extension_files()
        path, manifest, content = self.package(files)
        self.assertEqual(fmt.validate_package(path, self.root), (manifest, content))
        incomplete = dict(files)
        incomplete.pop("extensions/meituan-login-sync/worker.js")
        for changes in (incomplete, {**files, "extensions/meituan-login-sync/manifest.json": b"{}"},
                        {**files, "extensions/meituan-login-sync/manifest.json": b"invalid-json"}):
            path, _, _ = self.package(changes)
            with self.assertRaises(ValueError):
                fmt.validate_package(path, self.root)

    def test_extension_install_and_restore_preserve_browser_and_local_data(self):
        _, manifest, content = self.package(self.extension_files())
        original = self.root / "extensions/meituan-login-sync/worker.js"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"// previous extension")
        private = self.root / "extensions/meituan-login-sync/settings.json"
        private.write_bytes(b"local data")
        backup = self.root / "updater/backups/extension"
        plan = core.prepare(self.root, backup, manifest, content)
        core.apply_files(self.root, manifest, content, plan)
        self.assertEqual(original.read_bytes(), content["extensions/meituan-login-sync/worker.js"])
        core.restore(self.root, backup, plan)
        self.assertEqual(original.read_bytes(), b"// previous extension")
        self.assertEqual(private.read_bytes(), b"local data")
        self.assertFalse((original.parent / "manifest.json").exists())

    def test_transition_package_contains_no_extension_or_removals(self):
        path, manifest, content = self.package({"updater/package_format.py": b"# new allowlist"})
        fmt.validate_package(path, self.root)
        self.assertFalse(fmt.BROWSER_EXTENSION_FILES & content.keys())
        self.assertEqual(manifest["removed"], [])

    def test_publisher_includes_only_tracked_program_files(self):
        required = ["mysql_connection.py", "ota_mysql_writer.py", "OTA采集服务/runner.py", "OTA采集服务/panel.py",
                    "meituan_pms/collector.py"]
        for name in required:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# valid program")
        private = self.root / "OTA采集服务/config/settings.json"
        private.parent.mkdir()
        private.write_text('{"password":"not-for-release"}')
        extension = self.extension_files()
        for name, body in extension.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", *required, *extension, "requirements-portable.lock.txt", "OTA采集服务/config/settings.json"], cwd=self.root, check=True)
        path = build_release.build(self.root, self.root / "dist", "v1.0.0", [])
        with zipfile.ZipFile(path) as archive:
            self.assertNotIn("OTA采集服务/config/settings.json", archive.namelist())
            self.assertEqual(set(archive.namelist()), {*required, *extension, "manifest.json"})
        path = build_release.build(self.root, self.root / "transition", "v1.0.1", [], include_browser_extension=False)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), {*required, "manifest.json"})
        (self.root / "extensions/meituan-login-sync/manifest.json").unlink()
        with self.assertRaises(ValueError):
            build_release.build(self.root, self.root / "incomplete", "v1.0.2", [])


if __name__ == "__main__":
    unittest.main()
