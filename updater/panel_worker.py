"""Detached status-page job; survives the panel process restarting."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'OTA采集服务/state/program_update.json'


def save(job, **changes):
    data = update.read_json(STATE)
    if data.get('job') != job:
        raise RuntimeError('更新任务已被替换')
    data.update(changes, pid=os.getpid())
    update.atomic_write(STATE, json.dumps(data, ensure_ascii=False).encode('utf-8'))


def run(action, job, target=''):
    try:
        save(job, message='正在检查 Gitee 最新版本…')
        if action == 'check':
            release = update.latest_release()
            save(job, status='ready', latest=release['tag_name'],
                 checked_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), message='版本检查完成。')
            return 0
        installed = update.read_json(ROOT / 'updater/installed.json').get('version') or '0.0.0'
        if update.version(target) <= update.version(installed):
            save(job, status='ready', message='当前版本已更新，请刷新版本信息。')
            return 0
        update.PROGRESS = lambda message: save(job, status='updating', message=message)
        sys.argv = ['update.py', '--apply', '--yes', '--expected-version', target]
        update.main()
        save(job, status='success', message='更新完成，面板已重新启动，原运行的调度器已恢复。')
        return 0
    except Exception as exc:
        message = ('更新前检查或重启失败，请确认采集、调价和登录助手已空闲，再查看本地更新日志。'
                   if isinstance(exc, subprocess.CalledProcessError) else str(exc))
        save(job, status='error', message='更新失败：' + message[:500])
        return 1
    finally:
        update.PROGRESS = None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'apply'])
    parser.add_argument('job')
    parser.add_argument('--target', default='')
    args = parser.parse_args()
    raise SystemExit(run(args.action, args.job, args.target))
