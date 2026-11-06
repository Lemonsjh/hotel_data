"""Gitee release discovery, named attachments and verified download routing."""
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'updater'))
import update


class GiteeUpdaterTests(unittest.TestCase):
    def assets(self):
        return [
            {'name': update.ASSET_NAME, 'browser_download_url': 'https://gitee.com/attach_file/123?attname=update.zip'},
            {'name': update.CHECKSUM_NAME, 'browser_download_url': 'https://foruda.gitee.com/attach_file/456?attname=update.sha256'},
        ]

    def release(self):
        return {'id': 42, 'tag_name': 'v1.0.2', 'prerelease': False,
                'assets': [{'name': 'v1.0.2.zip', 'browser_download_url': 'https://gitee.com/archive/source.zip'}]}

    def test_real_gitee_shape_loads_separate_attachment_list(self):
        with patch.object(update, 'download', side_effect=[json.dumps(self.release()).encode(), json.dumps(self.assets()).encode()]) as download:
            release = update.latest_release()
        self.assertEqual(release['tag_name'], 'v1.0.2')
        self.assertEqual(update.release_asset_urls(release)[update.ASSET_NAME], self.assets()[0]['browser_download_url'])
        self.assertEqual([call.args[0] for call in download.call_args_list], [
            f'{update.GITEE_API}/releases/latest',
            f'{update.GITEE_API}/releases/42/attach_files?page=1&per_page=100'])

    def test_source_archives_are_not_code_update_attachments(self):
        with patch.object(update, 'download', side_effect=[json.dumps(self.release()).encode(), b'[]']), \
                self.assertRaisesRegex(ValueError, 'attachments are not ready'):
            update.latest_release()

    def test_existing_named_assets_do_not_require_another_api_call(self):
        release = {**self.release(), 'assets': self.assets()}
        with patch.object(update, 'download', return_value=json.dumps(release).encode()) as download:
            self.assertEqual(update.latest_release(), release)
        self.assertEqual(download.call_count, 1)

    def test_attachments_on_next_page_are_found(self):
        unrelated = [{'name': f'other-{i}'} for i in range(100)]
        with patch.object(update, 'download', side_effect=[json.dumps(self.release()).encode(),
                json.dumps(unrelated).encode(), json.dumps(self.assets()).encode()]) as download:
            self.assertEqual(update.latest_release()['tag_name'], 'v1.0.2')
        self.assertIn('page=2&per_page=100', download.call_args.args[0])

    def test_404_explains_public_release_is_missing(self):
        error = urllib.error.HTTPError(update.GITEE_API, 404, 'missing', {}, None)
        with patch.object(update, 'download', side_effect=error), \
                self.assertRaisesRegex(ValueError, 'No public Gitee Release'):
            update.latest_release()

    def test_invalid_release_and_attachment_metadata_fail(self):
        for metadata in ({**self.release(), 'id': None}, {**self.release(), 'assets': {}},
                         {**self.release(), 'prerelease': True}, {**self.release(), 'tag_name': 'v1.0.2-rc1'}):
            with self.subTest(metadata=metadata), patch.object(update, 'download', return_value=json.dumps(metadata).encode()), \
                    self.assertRaises(ValueError):
                update.latest_release()
        with patch.object(update, 'download', side_effect=[json.dumps(self.release()).encode(), b'{}']), \
                self.assertRaisesRegex(ValueError, 'Invalid Gitee release attachments'):
            update.latest_release()

    def test_untrusted_attachment_locations_are_rejected(self):
        for url in ('http://gitee.com/a', 'https://github.com/a', 'https://gitee.com.evil.test/a',
                    'https://user:password@gitee.com/a', 'https://gitee.com:444/a', '',
                    'https://gitee.com/a#fragment'):
            assets = self.assets()
            assets[0]['browser_download_url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                update.release_asset_urls({'assets': assets})

    def test_duplicate_or_missing_required_attachment_is_rejected(self):
        for assets in (self.assets() + [self.assets()[0]], self.assets()[:1]):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                update.release_asset_urls({'assets': assets})

    def run_main(self, root, checksum):
        release = {**self.release(), 'assets': self.assets()}
        data = b'fixture package bytes'
        with patch.object(update, 'ROOT', root), patch.object(update, 'latest_release', return_value=release), \
                patch.object(update, 'os', SimpleNamespace(name='nt', getpid=lambda: 123)), \
                patch.object(sys, 'argv', ['update.py', '--apply']), patch('builtins.input', return_value='y'), \
                patch.object(update, 'download', side_effect=[checksum, data]) as download, \
                patch.object(update, 'apply_update') as apply:
            try:
                result = update.main()
            except ValueError:
                self.assertFalse(apply.called)
                raise
            self.assertEqual(result, 0)
            self.assertEqual([call.args[0] for call in download.call_args_list], [
                self.assets()[1]['browser_download_url'], self.assets()[0]['browser_download_url']])
            apply.assert_called_once()
            self.assertEqual(apply.call_args.args[1].read_bytes(), data)
        self.assertFalse((root / 'updater/update.lock').exists())

    def test_main_downloads_named_gitee_urls_and_checks_sha256(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            (root / 'updater').mkdir()
            self.run_main(root, (update.digest(b'fixture package bytes') + '  update.zip').encode())

    def test_checksum_mismatch_does_not_install(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            root = Path(temporary)
            (root / 'updater').mkdir()
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.run_main(root, b'0' * 64)
            self.assertFalse((root / 'updater/update.lock').exists())


if __name__ == '__main__':
    unittest.main()
