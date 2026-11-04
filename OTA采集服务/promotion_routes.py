from __future__ import annotations

from datetime import datetime
from flask import abort, redirect, request, url_for

import promotion_controls
import ctrip_promotion_controls
import promotion_queue
import runner
from panel_common import esc, page


STATUS_LABELS = {"RUNNING": "推广中", "PAUSED": "已暂停"}
CONTROLLERS = {'meituan': promotion_controls, 'ctrip': ctrip_promotion_controls}
PLATFORM_LABELS = {'meituan': '美团推广管理', 'ctrip': '携程金字塔推广管理'}


def render_platform_tabs(selected: str) -> str:
    links = []
    for key, label in (('meituan', '美团推广通'), ('ctrip', '携程金字塔')):
        active = key == selected
        current = " aria-current='page'" if active else ''
        links.append(f"<a class='promotion-platform{' active' if active else ''}' "
                     f"href='/promotion-controls?platform={key}'{current}>{label}</a>")
    return "<nav class='promotion-platforms' aria-label='推广平台'>" + ''.join(links) + '</nav>'


def register(app) -> None:
    @app.get("/promotion-controls")
    def promotion_control_page() -> str:
        platform = request.args.get('platform', 'meituan')
        if platform not in CONTROLLERS:
            abort(400)
        settings = runner.load_settings()
        notice = request.args.get("notice", "")
        error = request.args.get("error", "")
        messages = ""
        if notice:
            messages += f"<div class='success'>{esc(notice)}</div>"
        if error:
            messages += f"<div class='warning'>{esc(error)}</div>"
        try:
            promotions = CONTROLLERS[platform].current_promotions(settings)
        except Exception as exc:
            promotions = []
            messages += f"<div class='warning'>{esc(exc)}</div>"

        rows = "".join(render_promotion_row(item, platform) for item in promotions)
        try:
            task_rows = "".join(render_task_row(item) for item in promotion_queue.recent(settings, platform))
        except Exception as exc:
            task_rows = f"<tr><td colspan='5'>{esc(exc)}</td></tr>"
        empty = ("<tr><td colspan='4'>暂无推广快照。请先运行“携程近30天推广效果”采集。</td></tr>" if platform == 'ctrip' else
                 "<tr><td colspan='5'>暂无推广快照。请先运行“美团近30天推广效果”采集。</td></tr>")
        name_headers = '<th>推广名称</th>' if platform == 'ctrip' else '<th>计划名称</th><th>推广名称</th>'
        body = f"""
        {messages}
        <section class='panel promotion-toolbar'>
          {render_platform_tabs(platform)}
          <a class='button secondary promotion-refresh' href='/promotion-controls?platform={platform}'>刷新状态</a>
        </section>
        <section class='panel promotion-plans'>
          <h2 class='promotion-section-title'>{PLATFORM_LABELS[platform]}</h2>
          <div class='table-wrap'>
            <table class='promotion-table'>
              <tr>{name_headers}<th>状态</th><th>最近快照</th><th>操作</th></tr>
              {rows or empty}
            </table>
          </div>
        </section>
        <section class='panel'>
          <h2 style='margin-top:0'>最近控制任务</h2>
          <div class='table-wrap'>
            <table>
              <tr><th>ID</th><th>推广</th><th>操作</th><th>状态</th><th>执行信息</th></tr>
              {task_rows or "<tr><td colspan='5'>暂无控制任务</td></tr>"}
            </table>
          </div>
        </section>
        """
        return page("推广管理", body, "promotions")

    @app.post("/promotion-controls/<launch_id>/<action>")
    @app.post("/promotion-controls/<platform>/<launch_id>/<action>")
    def control_promotion(launch_id: str, action: str, platform: str = 'meituan'):
        if platform not in CONTROLLERS:
            abort(400)
        expected = "暂停" if action == "pause" else "恢复" if action == "recover" else ""
        if not expected or request.form.get("confirmation", "").strip() != expected:
            return redirect(url_for("promotion_control_page", platform=platform, error=f"请输入“{expected or '确认'}”后再执行"))
        try:
            settings = runner.load_settings()
            promotion = CONTROLLERS[platform].find_promotion(settings, launch_id)
            task_id = promotion_queue.enqueue(settings, platform, promotion, action)
            return redirect(url_for("promotion_control_page", platform=platform, notice=f"已创建推广控制任务 #{task_id}，等待定时检查执行"))
        except Exception as exc:
            return redirect(url_for("promotion_control_page", platform=platform, error=str(exc)))


def render_promotion_row(item: dict[str, object], platform: str = 'meituan') -> str:
    launch_id = str(item.get("launch_id") or "")
    status = str(item.get("promotion_status") or "UNKNOWN").upper()
    if status == "RUNNING":
        action, action_label, css = "pause", "暂停", "danger"
    elif status == "PAUSED":
        action, action_label, css = "recover", "恢复", "secondary"
    else:
        action = action_label = css = ""
    actions = ""
    if action:
        actions = (
            f"<form method='post' action='/promotion-controls/{platform}/{esc(launch_id)}/{action}' class='inline-form'>"
            f"<input name='confirmation' placeholder='输入 {action_label}' autocomplete='off' required>"
            f"<button class='{css}' title='加入{action_label}队列'>{action_label}</button></form>"
        )
    title = (item.get('promotion_name') if platform == 'ctrip' else
             item.get('plan_name') or item.get('launch_name')) or launch_id
    detail = item.get("launch_name") or item.get("promotion_name") or ""
    detail_cell = f'<td>{esc(detail)}</td>' if platform == 'meituan' else ''
    label = STATUS_LABELS.get(status, status)
    status_css = 'good' if status == 'RUNNING' else 'idle'
    if platform == 'ctrip':
        label = {'RUNNING': '已开启', 'PAUSED': '已关闭'}.get(status, status)
        if item.get('campaign_status') in (7, '7'):
            label, status_css = '账户余额不足', 'warn'
    id_label = 'campaign_id' if platform == 'ctrip' else 'launch_id'
    snapshot = item.get('snapshot_time')
    if isinstance(snapshot, datetime):
        snapshot = snapshot.strftime('%Y-%m-%d %H:%M:%S')
    return (
        "<tr>"
        f"<td><strong>{esc(title)}</strong><div class='task-key'>{id_label}: {esc(launch_id)}</div></td>"
        f"{detail_cell}"
        f"<td><span class='pill {status_css}'>{esc(label)}</span></td>"
        f"<td>{esc(snapshot or '-')}</td><td>{actions or '-'}</td>"
        "</tr>"
    )


def render_task_row(item: dict[str, object]) -> str:
    status = str(item.get("status") or "")
    labels = {"pending": "待执行", "processing": "执行中", "success": "成功", "failed": "失败", "cancelled": "已取消"}
    css = "good" if status == "success" else "danger" if status == "failed" else "warn" if status == "processing" else "idle"
    action = "暂停" if item.get("action") == "pause" else "恢复"
    message = item.get("error_message") or item.get("executed_at") or item.get("created_at") or "-"
    return (
        "<tr>"
        f"<td>{esc(item.get('id'))}</td><td>{esc(item.get('launch_id'))}</td><td>{action}</td>"
        f"<td><span class='pill {css}'>{esc(labels.get(status, status))}</span></td>"
        f"<td>{esc(message)}</td></tr>"
    )
