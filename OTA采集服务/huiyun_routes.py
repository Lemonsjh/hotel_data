"""慧云配置页的只读客户端定位入口。"""
from flask import jsonify, request

from huiyun_pms.client_discovery import find_client


def register(app):
    @app.post('/api/huiyun/client-path')
    def find_huiyun_client():
        data = request.get_json(silent=True) or {}
        configured = data.get('client_path', '') if isinstance(data, dict) else ''
        path = find_client(configured if isinstance(configured, str) else '')
        if not path:
            return jsonify(ok=False, message='未找到，请先打开慧云客户端后重试，或手动填写路径。'), 404
        return jsonify(ok=True, path=path, message='已找到，保存配置后生效')
