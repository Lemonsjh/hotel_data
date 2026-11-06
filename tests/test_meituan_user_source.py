"""User-source percentages retain platform values without clamping or guessing."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '美团OTA数据采集代码'))
import meituan_user_source_data as source


class UserSourceTests(unittest.TestCase):
    def test_reads_rendered_positive_zero_and_signed_percentages(self):
        for text, expected in [('老客占比\n12.50%', 12.5), ('老客占比\n0%', 0),
                               ('老客占比\n-1.31%', -1.31), ('老客占比 +1.31 %', 1.31)]:
            with self.subTest(text=text):
                self.assertEqual(source.read_percent(text, '老客占比'), expected)

    def test_missing_value_does_not_use_peer_value_or_zero(self):
        for text in ('老客占比\n--\n同行均值 11.86%', '暂无数据'):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, '找到字段：老客占比'):
                source.read_percent(text, '老客占比')

    def collect_page(self, text):
        playwright = MagicMock()
        browser = playwright.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        page.locator.return_value.inner_text.return_value = text
        self.enterContext(patch.object(source, 'MEITUAN_EB_COOKIE', 'fake=value'))
        self.enterContext(patch.object(source, 'MEITUAN_ME_COOKIE', 'fake=value'))
        factory = self.enterContext(patch.object(source, 'sync_playwright'))
        factory.return_value.__enter__.return_value = playwright
        return browser, page

    def test_platform_values_reach_writer_unchanged(self):
        for new, returning in [(101.31, -1.31), (88.14, 5), (100, 0), (0, 100)]:
            with self.subTest(new=new, returning=returning):
                text = f'本地用户占比 31.82%\n异地用户占比 68.18%\n新客占比 {new}%\n老客占比 {returning}%'
                browser, page = self.collect_page(text)
                with patch.object(sys, 'argv', ['meituan_user_source_data.py']), \
                     patch.object(source, 'write_output') as output, \
                     patch.object(source, 'sync_user_source_history') as writer:
                    self.assertEqual(source.main(), 0)
                row = writer.call_args.args[1][0]
                self.assertEqual(row[-4:], [31.82, 68.18, new, returning])
                output.assert_called_once_with(row)
                browser.close.assert_called_once()
                page.get_by_role.assert_called_once_with('button', name='近30天', exact=True)

    def test_missing_percentage_still_prevents_output_and_database_write(self):
        browser, _ = self.collect_page('本地用户占比 31.82%\n异地用户占比 68.18%\n新客占比 88.14%\n老客占比 --')
        with patch.object(sys, 'argv', ['meituan_user_source_data.py']), \
             patch.object(source, 'write_output') as output, \
             patch.object(source, 'sync_user_source_history') as writer:
            with self.assertRaisesRegex(RuntimeError, '找到字段：老客占比'):
                source.main()
        output.assert_not_called()
        writer.assert_not_called()
        browser.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
