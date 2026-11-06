"""User-source page values: report abnormal platform values without guessing."""
import sys
import unittest
from pathlib import Path

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

    def test_abnormal_platform_pair_reports_values_and_refuses_write(self):
        values = {'local_user_pct': 31.82, 'nonlocal_user_pct': 68.18,
                  'new_user_pct': 101.31, 'returning_user_pct': -1.31}
        with self.assertRaises(RuntimeError) as caught:
            source.validate(values)
        self.assertIn('新客占比=101.31%', str(caught.exception))
        self.assertIn('老客占比=-1.31%', str(caught.exception))
        self.assertIn('未写入数据库，保留原值', str(caught.exception))

    def test_normal_pair_and_zero_boundary_are_valid(self):
        for new, returning in [(88.14, 11.86), (100, 0), (0, 100)]:
            source.validate({'local_user_pct': 31.82, 'nonlocal_user_pct': 68.18,
                             'new_user_pct': new, 'returning_user_pct': returning})

    def test_pair_consistency_is_still_required(self):
        with self.assertRaisesRegex(RuntimeError, '新客与老客占比之和异常'):
            source.validate({'local_user_pct': 31.82, 'nonlocal_user_pct': 68.18,
                             'new_user_pct': 88.14, 'returning_user_pct': 5})


if __name__ == '__main__':
    unittest.main()
