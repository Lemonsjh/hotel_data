"""自动建表只操作本任务所需的缺表，不连接真实数据库。"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'OTA采集服务'))
import collection_schema as schema
import runner
from process_runner import ProcessResult


class CollectionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = []
        self.config = {'database': 'fake_hotel', 'host': 'example.test'}
        self.connect = patch.object(schema, 'connect_mysql', return_value=self.connection).start()
        self.addCleanup(patch.stopall)

    def test_all_registered_tasks_and_reports_have_standard_definitions(self):
        self.assertEqual(set(schema.TASK_TABLES) | {'pms_fetch'}, set(runner.TASKS))
        definitions = schema.table_definitions()
        self.assertEqual(len(definitions), schema.SCHEMA_PATH.read_text(encoding='utf-8-sig').count('CREATE TABLE IF NOT EXISTS `'))
        for tables in (*schema.TASK_TABLES.values(), *schema.REPORT_TABLES.values()):
            self.assertTrue(set(tables) <= definitions.keys())
        self.assertIn('hotel_room_type_mapping', definitions)
        for name, ddl in definitions.items():
            self.assertTrue(ddl.startswith(f'CREATE TABLE IF NOT EXISTS `{name}` ('))
            self.assertNotIn('CREATE OR REPLACE VIEW', ddl)
            self.assertNotIn('FOREIGN_KEY_CHECKS', ddl)

    def test_existing_tables_never_execute_create_or_change_columns(self):
        table = 'ctrip_ota_business_metrics'
        self.cursor.fetchall.return_value = [(table,)]
        self.assertEqual(schema.ensure_tables(self.config, [table]), [])
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.assertIn('TABLE_SCHEMA=DATABASE()', self.cursor.execute.call_args.args[0])
        self.connect.assert_called_once_with(self.config, autocommit=True, attempts=1)
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_only_requested_missing_table_uses_exact_standard_ddl(self):
        tables = ['pms_room_type_forecast', 'pms_room_type_hourly_status']
        self.cursor.fetchall.return_value = [(tables[0],)]
        self.assertEqual(schema.ensure_tables(self.config, tables), [tables[1]])
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.assertEqual(self.cursor.execute.call_args.args, (schema.table_definitions()[tables[1]],))
        self.assertEqual(self.cursor.execute.call_args_list[0].args[1], tables)

    def test_next_task_checks_again_but_does_not_recreate_existing_table(self):
        table = 'jy01_hotel_statistics_daily'
        self.cursor.fetchall.side_effect = [[], [(table,)]]
        self.assertEqual(schema.ensure_tables(self.config, [table]), [table])
        self.assertEqual(schema.ensure_tables(self.config, [table]), [])
        self.assertEqual(self.cursor.execute.call_count, 3)
        self.assertEqual(self.connection.close.call_count, 2)

    def test_unknown_or_injected_table_is_rejected_before_connection(self):
        for name in ('unknown_table', 'pms_room_type_forecast; DROP DATABASE fake_hotel'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                schema.ensure_tables(self.config, [name])
        self.connect.assert_not_called()

    def test_empty_request_and_unconfigured_database_do_not_connect(self):
        self.assertEqual(schema.ensure_tables(self.config, []), [])
        schema.ensure_task_tables('pms_fetch', {'mysql': {'database': ''}}, [])
        self.connect.assert_not_called()

    def test_create_permission_failure_closes_connection_and_does_not_continue(self):
        self.cursor.execute.side_effect = [None, PermissionError('CREATE denied')]
        with self.assertRaisesRegex(PermissionError, 'CREATE denied'):
            schema.ensure_tables(self.config, ['pms_room_type_forecast', 'pms_room_type_hourly_status'])
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.connection.close.assert_called_once()

    def test_ota_tasks_only_select_their_own_tables(self):
        tables = schema.required_tables('ctrip_business', [])
        self.assertEqual(tables, {'ctrip_ota_business_metrics', 'ctrip_ota_business_metrics_hourly'})
        for task in ('ctrip_goods_price', 'meituan_goods_price'):
            self.assertIn('hotel_room_type_mapping', schema.required_tables(task, []))

    def test_byh_defaults_do_not_create_paused_or_other_provider_tables(self):
        tables = schema.required_tables('pms_fetch', [('pms', Path('fetch_main.py'), [])])
        self.assertIn('jl02_hotel_performance_daily', tables)
        self.assertIn('hotel_room_type_mapping', tables)
        self.assertNotIn('kf11_room_status_snapshot', tables)
        self.assertNotIn('jd04_inhouse_extension', tables)
        self.assertNotIn('bypms_channel_unit_mapping_snapshot', tables)

    def test_manual_report_selection_only_creates_requested_report(self):
        specs = [('pms', Path('fetch_main.py'), ['--reports', 'JD04', '--jy03-backfill'])]
        self.assertEqual(schema.required_tables('pms_fetch', specs),
                         {'hotel_room_type_mapping', 'jd04_inhouse_extension'})

    def test_pms_subtasks_follow_actual_provider_and_selected_reports(self):
        for platform, script, expected in (
            ('foxhis', 'foxhis_jd01_data.py', {'jd01_booking_detail'}),
            ('bypms', 'bypms_daily_report_data.py', {'jl01_room_type_performance_daily', 'jy01_hotel_statistics_daily'}),
            ('meituan_pms', 'collector.py', {'pms_room_type_forecast', 'pms_room_type_hourly_status'}),
            ('meituan_pms', 'daily_report.py', {'jy01_hotel_statistics_daily'}),
            ('meituan_pms', 'monthly_report.py', {'jy03_hotel_statistics_month'}),
            ('meituan_pms', 'performance_report.py', {'jl02_hotel_performance_daily'}),
            ('meituan_pms', 'booking_report.py', {'jd01_booking_detail'}),
            ('meituan_pms', 'classification_report.py', {'jl11_room_type_classification'}),
            ('meituan_pms', 'room_performance_report.py', {'jl01_room_type_performance_daily'}),
            ('meituan_pms', 'revenue_report.py', {'rs01_room_revenue_daily'}),
        ):
            with self.subTest(platform=platform):
                self.assertEqual(schema.required_tables('pms_fetch', [(platform, Path(script), [])]),
                                 expected | {'hotel_room_type_mapping'})


class CollectionSchemaRunnerTests(unittest.TestCase):
    def run_task(self, ready, enabled=True):
        settings = {'meituan': {'enabled': enabled}, 'mysql': {'database': 'fake_hotel'}}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.object(runner, 'LOG_DIR', Path(directory)), \
                patch.object(runner, 'save_json'), patch.object(runner, 'enrich_room_type_ids'), \
                patch.object(runner, 'ensure_task_tables', side_effect=ready) as ensure, \
                patch.object(runner, 'run_streamed', side_effect=lambda *a, **kw:
                             self.assertTrue(ensure.called) or ProcessResult(0, 'ok')) as process:
            result = runner.run_task('meituan_business', settings, {})
        return result, ensure, process

    def test_preflight_finishes_before_collector_process_starts(self):
        ready = Mock()
        result, ensure, process = self.run_task(ready)
        self.assertEqual(result['status'], 'success')
        ensure.assert_called_once()
        self.assertEqual(ensure.call_args.args[0], 'meituan_business')
        self.assertEqual(ensure.call_args.args[2][0][1].name, 'bussiness_data.py')
        process.assert_called_once()

    def test_preflight_failure_prevents_all_collection_writes(self):
        result, ensure, process = self.run_task(PermissionError('CREATE denied'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_summary'], 'CREATE denied')
        process.assert_not_called()

    def test_disabled_task_does_not_check_or_create_tables(self):
        result, ensure, process = self.run_task(None, enabled=False)
        self.assertEqual(result['status'], 'failed')
        ensure.assert_not_called()
        process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
