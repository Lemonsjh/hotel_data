"""统一采集入口按需补建标准表；不迁移字段，不接触业务数据。"""
from functools import lru_cache
from pathlib import Path
import re

from mysql_connection import connect_mysql

SCHEMA_PATH = Path(__file__).resolve().parent / 'database/create_all_tables.sql'
TASK_TABLES = {
    'meituan_business': ('meituan_ota_business_metrics', 'meituan_ota_business_metrics_hourly'),
    'meituan_flow_conversion': ('meituan_ota_flow_conversion_30d',),
    'meituan_joined_rights': ('meituan_ota_joined_rights',),
    'meituan_promotion_status': ('meituan_ota_promotion_status',),
    'meituan_video_upload_status': ('meituan_ota_video_upload_status',),
    'meituan_promotion_performance': ('meituan_ota_promotion_performance_30d',),
    'meituan_order_loss': ('meituan_ota_order_loss_monthly',),
    'meituan_scan_order': ('meituan_ota_scan_order_detail',),
    'meituan_order_detail': ('meituan_ota_order_detail',),
    'meituan_user_source': ('meituan_ota_user_source_monthly',),
    'meituan_review': ('meituan_ota_review_overview', 'meituan_ota_review_ranking'),
    'meituan_review_detail': ('meituan_ota_review_detail', 'meituan_ota_review_overview'),
    'meituan_promotion': ('meituan_ota_promotion_activity', 'meituan_ota_activity_product_detail'),
    'meituan_goods_price': ('meituan_ota_goods_price_mapping',),
    'meituan_nearby_event': ('meituan_ota_nearby_event',),
    'ctrip_business': ('ctrip_ota_business_metrics', 'ctrip_ota_business_metrics_hourly'),
    'ctrip_competition_metrics_30d': ('ctrip_ota_competition_metrics_30d',),
    'ctrip_flow_conversion': ('ctrip_ota_flow_conversion_30d',),
    'ctrip_order_detail': ('ctrip_ota_order_detail',),
    'ctrip_order_loss': ('ctrip_ota_order_loss_monthly',),
    'ctrip_joined_rights': ('ctrip_ota_joined_rights',),
    'ctrip_promotion_status': ('ctrip_ota_promotion_status',),
    'ctrip_video_upload_status': ('ctrip_ota_video_upload_status',),
    'ctrip_user_profile': ('ctrip_ota_userprofile_distribution',),
    'ctrip_psi_score': ('ctrip_ota_psi_score', 'ctrip_ota_psi_metric'),
    'ctrip_promotion_performance': ('ctrip_ota_promotion_performance_30d',),
    'ctrip_review': ('ctrip_ota_review_overview', 'ctrip_ota_review_ranking'),
    'ctrip_review_detail': ('ctrip_ota_review_detail',),
    'ctrip_promotion': ('ctrip_ota_promotion_activity', 'ctrip_ota_activity_product_detail',
                        'ctrip_ota_promotion_activity_performance'),
    'ctrip_goods_price': ('ctrip_ota_goods_price_mapping',),
}
REPORT_TABLES = {
    'rs01': ('rs01_room_revenue_daily',), 'room_revenue': ('rs01_room_revenue_daily',),
    'jd01': ('jd01_booking_detail',), 'jd04': ('jd04_inhouse_extension',),
    'jl01': ('jl01_room_type_performance_daily',), 'jl02': ('jl02_hotel_performance_daily',),
    'jl11': ('jl11_room_type_classification',), 'jy01': ('jy01_hotel_statistics_daily',),
    'jy03': ('jy03_hotel_statistics_month',), 'kf11': ('kf11_room_status_snapshot',),
    'forecast': ('pms_room_type_forecast',),
    'room_status': ('pms_room_type_forecast', 'pms_room_type_hourly_status'),
    'channel_mapping': ('bypms_channel_unit_mapping_snapshot',),
    'daily_report': ('jl01_room_type_performance_daily', 'jy01_hotel_statistics_daily'),
}


@lru_cache(maxsize=1)
def table_definitions():
    source = SCHEMA_PATH.read_text(encoding='utf-8-sig')
    pattern = r'^CREATE TABLE IF NOT EXISTS `([A-Za-z0-9_]+)` \([\s\S]*?^\) ENGINE=[^\r\n]+;'
    return {match.group(1): match.group(0) for match in re.finditer(pattern, source, re.MULTILINE)}


def required_tables(task, specs):
    if task != 'pms_fetch':
        tables = set(TASK_TABLES[task])
        if task in {'meituan_goods_price', 'ctrip_goods_price'}:
            tables.add('hotel_room_type_mapping')
        return tables
    tables = {'hotel_room_type_mapping'}
    for platform, script, args in specs:
        if platform == 'pms':
            reports = ['rs01', 'jd01', 'jy01', 'jy03', 'jl01', 'jl02', 'jl11', 'forecast', 'room_status']
            if '--reports' in args:
                reports = []
                for item in args[args.index('--reports') + 1:]:
                    if item.startswith('--'):
                        break
                    reports.append(item.lower())
        elif platform == 'meituan_pms':
            reports = {'collector.py': ['forecast', 'room_status'],
                       'daily_report.py': ['jy01'], 'monthly_report.py': ['jy03'],
                       'performance_report.py': ['jl02'], 'booking_report.py': ['jd01'],
                       'classification_report.py': ['jl11'], 'room_performance_report.py': ['jl01'],
                       'revenue_report.py': ['rs01']}[script.name]
        else:
            reports = [script.stem.removeprefix(platform + '_').removesuffix('_data')]
        for report in reports:
            tables.update(REPORT_TABLES[report])
    return tables


def ensure_tables(config, tables):
    """只在独立连接上执行缺表的标准 CREATE，避免隐式提交采集事务。"""
    tables = sorted(set(tables))
    if not tables:
        return []
    definitions = table_definitions()
    unknown = set(tables) - definitions.keys()
    if unknown:
        raise ValueError('标准 Schema 未定义采集表：' + ', '.join(sorted(unknown)))
    connection = connect_mysql(config, autocommit=True, attempts=1)
    try:
        with connection.cursor() as cursor:
            marks = ', '.join(['%s'] * len(tables))
            cursor.execute('SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES '
                           f'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ({marks})', tables)
            existing = {row[0] for row in cursor.fetchall()}
            missing = [table for table in tables if table not in existing]
            for table in missing:
                cursor.execute(definitions[table])
        return missing
    finally:
        connection.close()


def ensure_task_tables(task, settings, specs):
    config = settings.get('mysql') or {}
    if not config.get('database'):
        return  # 未配置目标数据库时，沿用采集器自身的配置校验。
    created = ensure_tables(config, required_tables(task, specs))
    if created:
        print('Created missing collection tables: ' + ', '.join(created))
