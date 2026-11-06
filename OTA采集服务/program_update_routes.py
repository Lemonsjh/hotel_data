"""Local status-page controls for the detached release updater."""
import ipaddress
import secrets
import subprocess
import threading
import time
from urllib.parse import urlparse

from flask import current_app, jsonify, render_template, request

import runner
from updater import update as updater

ACTIVE = {'checking', 'updating'}
_gate = threading.Lock()


def state_path():
    return runner.ROOT / 'state' / 'program_update.json'


def public_status():
    data = runner.load_json(state_path(), {})
    installed = runner.load_json(runner.PROJECT_ROOT / 'updater/installed.json', {})
    current = installed.get('version') or '0.0.0'
    if data.get('status') in ACTIVE:
        alive = runner.process_alive(data['pid']) if data.get('pid') else time.time() - data.get('started', 0) < 15
        if not alive:
            data.update(status='error', message='更新进程已退出，请检查更新结果后重新操作。')
            runner.save_json(state_path(), data)
    available = False
    try:
        available = updater.version(data.get('latest', '')) > updater.version(current)
    except (ValueError, TypeError):
        pass
    locked = (runner.PROJECT_ROOT / 'updater/update.lock').exists() or (runner.PROJECT_ROOT / 'updater/pending.json').exists()
    return {'current': current if installed.get('version') else '未记录',
            'latest': data.get('latest') or '待检查', 'checked_at': data.get('checked_at') or '',
            'status': data.get('status', 'idle'), 'message': data.get('message', '点击检查更新获取最新版本。'),
            'available': available and data.get('status') in {'ready', 'success'},
            'busy': data.get('status') in ACTIVE or locked, 'locked': locked}


def update_card():
    return render_template('program_update.html', update_token=current_app.extensions.get('program_update_token', ''))


def require_local():
    try:
        local = ipaddress.ip_address(request.remote_addr or '').is_loopback
    except ValueError:
        local = False
    if (not local or urlparse(request.host_url).hostname not in {'localhost', '127.0.0.1', '::1'}
            or request.headers.get('Forwarded') or request.headers.get('X-Forwarded-For')):
        raise PermissionError('程序更新请在运行服务的电脑上操作。')
    if request.method == 'POST':
        token = current_app.extensions['program_update_token']
        if (request.headers.get('Origin') != request.host_url.rstrip('/')
                or not secrets.compare_digest(request.headers.get('X-Update-Token', ''), token)):
            raise PermissionError('请刷新本机状态页后重试。')


def launch(action, target=''):
    with _gate:
        status = public_status()
        if status['busy']:
            raise ValueError('已有检查或更新正在进行；恢复未完成的更新请使用一键更新工具。')
        if action == 'apply' and (not status['available'] or target != status['latest']):
            raise ValueError('请先检查更新，再确认页面显示的目标版本。')
        job = secrets.token_hex(16)
        old = runner.load_json(state_path(), {})
        data = {key: old[key] for key in ('latest', 'checked_at') if key in old}
        data.update(job=job, started=time.time(), status='checking' if action == 'check' else 'updating',
                    message='正在检查 Gitee 最新版本…' if action == 'check' else '正在准备更新…')
        runner.save_json(state_path(), data)
        command = [str(runner.python_path(runner.load_settings())),
                   str(runner.PROJECT_ROOT / 'updater/panel_worker.py'), action, job]
        if target:
            command.extend(['--target', target])
        try:
            with state_path().with_suffix('.log').open('ab') as log:
                subprocess.Popen(command, cwd=str(runner.PROJECT_ROOT), stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                 | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        except OSError:
            data.update(status='error', message='无法启动更新进程，请检查便携运行环境。')
            runner.save_json(state_path(), data)
            raise ValueError(data['message']) from None


def register(app):
    app.extensions['program_update_token'] = secrets.token_urlsafe(32)

    @app.before_request
    def pause_panel_writes_during_update():
        if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'} or request.path == '/api/program-update':
            return None
        data = runner.load_json(state_path(), {})
        updating = data.get('status') == 'updating' and public_status()['status'] == 'updating'
        if updating or any((runner.PROJECT_ROOT / 'updater' / name).exists() for name in ('update.lock', 'pending.json')):
            return jsonify(error='程序正在更新，暂不能启动任务、登录或修改配置。'), 409

    @app.route('/api/program-update', methods=['GET', 'POST'])
    def program_update_status():
        try:
            require_local()
            if request.method == 'POST':
                data = request.get_json(silent=True) or {}
                if not isinstance(data, dict):
                    raise ValueError('更新请求格式无效。')
                action = data.get('action')
                if not isinstance(action, str) or action not in {'check', 'apply'}:
                    raise ValueError('未知更新操作。')
                if action == 'apply' and data.get('confirmed') is not True:
                    raise ValueError('请先确认备份、更新和重启操作。')
                launch(action, data.get('target', '') if action == 'apply' else '')
            response = jsonify({**public_status(), 'token': app.extensions['program_update_token']})
            response.headers['Cache-Control'] = 'no-store'
            return response
        except PermissionError as exc:
            return jsonify(error=str(exc)), 403
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 409
