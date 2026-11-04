"""美团、携程共用的推广控制队列。"""
from datetime import datetime

import runner
from collection_schema import ensure_tables
from mysql_connection import connect_mysql

TABLE = 'ota_promotion_control_task'
PLATFORMS = ('meituan', 'ctrip')
TARGETS = {'pause': 'PAUSED', 'recover': 'RUNNING'}


def hotel_id(settings, platform):
    if platform not in PLATFORMS:
        raise ValueError('不支持的推广平台')
    value = runner.internal_hotel_id(settings, platform)
    if not value:
        raise ValueError('请先配置酒店 ID')
    return value


def connect_database(settings, *, autocommit=False):
    return connect_mysql(settings.get('mysql') or {}, autocommit=autocommit)


def enqueue(settings, platform, promotion, action):
    target = TARGETS.get(action)
    if not target:
        raise ValueError('不支持的推广操作')
    identifier = str(promotion.get('launch_id') or '')
    if not identifier.isdigit():
        raise ValueError('推广计划 ID 无效')
    status = promotion.get('promotion_status')
    if status not in TARGETS.values():
        raise ValueError('推广状态未知，无法操作')
    if status == target:
        raise ValueError('推广已处于目标状态，无需重复操作')
    scope = hotel_id(settings, platform)
    ensure_tables(settings.get('mysql') or {}, [TABLE])
    connection = connect_database(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT id FROM `{TABLE}` WHERE hotel_id=%s AND platform=%s '
                           "AND launch_id=%s AND action=%s AND status IN ('pending','processing') LIMIT 1",
                           (scope, platform, identifier, action))
            if cursor.fetchone():
                raise ValueError('相同推广操作已在待执行队列中')
            cursor.execute(f'INSERT INTO `{TABLE}` (hotel_id,platform,launch_id,action) VALUES (%s,%s,%s,%s)',
                           (scope, platform, identifier, action))
            task_id = int(cursor.lastrowid)
        connection.commit()
        return task_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def recent(settings, platform, limit=20):
    scope = hotel_id(settings, platform)
    connection = connect_database(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT id,launch_id,action,status,error_message,created_at,executed_at '
                           f'FROM `{TABLE}` WHERE hotel_id=%s AND platform=%s ORDER BY id DESC LIMIT %s',
                           (scope, platform, max(1, limit)))
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def claim(settings, platform, limit):
    scope = hotel_id(settings, platform)
    connection = connect_database(settings, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT id,launch_id,action FROM `{TABLE}` WHERE hotel_id=%s '
                           "AND platform=%s AND status='pending' ORDER BY id LIMIT %s",
                           (scope, platform, max(1, limit)))
            tasks = []
            for task_id, identifier, action in cursor.fetchall():
                cursor.execute(f"UPDATE `{TABLE}` SET status='processing',error_message=NULL "
                               "WHERE id=%s AND status='pending'", (task_id,))
                if cursor.rowcount:
                    tasks.append({'id': task_id, 'launch_id': str(identifier), 'action': action})
            return tasks
    finally:
        connection.close()


def finish(settings, platform, task_id, status, error=None):
    scope = hotel_id(settings, platform)
    connection = connect_database(settings, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'UPDATE `{TABLE}` SET status=%s,error_message=%s,executed_at=%s '
                           'WHERE id=%s AND hotel_id=%s AND platform=%s',
                           (status, None if status == 'success' else (error or '操作失败')[:500],
                            datetime.now(), task_id, scope, platform))
    finally:
        connection.close()


def fail_interrupted(settings, platform):
    scope = hotel_id(settings, platform)
    ensure_tables(settings.get('mysql') or {}, [TABLE])
    connection = connect_database(settings, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE `{TABLE}` SET status='failed',error_message=%s "
                           "WHERE hotel_id=%s AND platform=%s AND status='processing'",
                           ('上次推广操作在确认结果前中断，请核对平台状态', scope, platform))
    finally:
        connection.close()
