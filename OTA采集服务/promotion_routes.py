from __future__ import annotations

from flask import redirect, request, url_for

import promotion_controls
import runner
from panel_common import esc, page


STATUS_LABELS = {"RUNNING": "推广中", "PAUSED": "已暂停"}


def register(app) -> None:
    @app.get("/promotion-controls")
    def promotion_control_page() -> str:
        notice = request.args.get("notice", "")
        error = request.args.get("error", "")
        messages = ""
        if notice:
            messages += f"<div class='success'>{esc(notice)}</div>"
        if error:
            messages += f"<div class='warning'>{esc(error)}</div>"
        try:
            promotions = promotion_controls.current_promotions(runner.load_settings())
        except Exception as exc:
            promotions = []
            messages += f"<div class='warning'>{esc(exc)}</div>"

        rows = "".join(render_promotion_row(item) for item in promotions)
        try:
            task_rows = "".join(render_task_row(item) for item in promotion_controls.recent_control_tasks(runner.load_settings()))
        except Exception as exc:
            task_rows = f"<tr><td colspan='5'>{esc(exc)}</td></tr>"
        empty = "<tr><td colspan='6'>暂无推广快照。请先运行“美团近30天推广效果”采集。</td></tr>"
        body = f"""
        {messages}
        <section class='panel'>
          <div class='panel-heading'>
            <div>
              <h2 style='margin:0'>美团推广管理</h2>
              <div class='muted'>提交后进入待执行队列，由定时推广控制任务使用已登录的美团专用浏览器执行。</div>
            </div>
            <a class='button secondary' href='/promotion-controls'>刷新状态</a>
          </div>
        </section>
        <section class='panel'>
          <div class='table-wrap'>
            <table class='promotion-table'>
              <tr><th>计划名称</th><th>推广名称</th><th>状态</th><th>最近快照</th><th>操作</th></tr>
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
    def control_promotion(launch_id: str, action: str):
        expected = "暂停" if action == "pause" else "恢复" if action == "recover" else ""
        if not expected or request.form.get("confirmation", "").strip() != expected:
            return redirect(url_for("promotion_control_page", error=f"请输入“{expected or '确认'}”后再执行"))
        try:
            settings = runner.load_settings()
            promotion = promotion_controls.find_promotion(settings, launch_id)
            task_id = promotion_controls.enqueue_control_task(settings, promotion, action)
            return redirect(url_for("promotion_control_page", notice=f"已创建推广控制任务 #{task_id}，等待定时检查执行"))
        except Exception as exc:
            return redirect(url_for("promotion_control_page", error=str(exc)))


def render_promotion_row(item: dict[str, object]) -> str:
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
            f"<form method='post' action='/promotion-controls/{esc(launch_id)}/{action}' class='inline-form'>"
            f"<input name='confirmation' placeholder='输入 {action_label}' autocomplete='off' required>"
            f"<button class='{css}' title='加入{action_label}队列'>{action_label}</button></form>"
        )
    title = item.get("plan_name") or item.get("launch_name") or launch_id
    detail = item.get("launch_name") or item.get("promotion_name") or ""
    return (
        "<tr>"
        f"<td><strong>{esc(title)}</strong><div class='task-key'>launch_id: {esc(launch_id)}</div></td>"
        f"<td>{esc(detail)}</td>"
        f"<td><span class='pill {'good' if status == 'RUNNING' else 'idle'}'>{esc(STATUS_LABELS.get(status, status))}</span></td>"
        f"<td>{esc(item.get('snapshot_time') or '-')}</td><td>{actions or '-'}</td>"
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
