from __future__ import annotations

from flask import redirect, url_for

import runner
from panel_common import (
    esc,
    manual_scheduler_status,
    ota_scope_panel,
    page,
    run_background,
    status_class,
    status_label,
    task_label,
)


def register(app) -> None:
    @app.get("/")
    def index() -> str:
        status = runner.load_status()
        settings = runner.load_settings()
        scheduler = manual_scheduler_status()
        tasks = status.get("tasks") or {}
        run_names = status.get("last_run_tasks") or runner.enabled_tasks(settings)
        run_items = [tasks.get(name, {}) for name in run_names]
        success_count = sum(1 for item in run_items if item.get("status") == "success")
        failed_count = sum(1 for item in run_items if item.get("status") == "failed")
        running_count = sum(1 for item in run_items if item.get("status") == "running")
        pending_count = sum(1 for item in run_items if item.get("status") == "pending")
        completed_count = success_count + failed_count
        total = len(run_names)
        current_status = status.get("last_run_status", "never_run")
        is_running = current_status == "running"
        warnings = runner.config_warnings(settings)
        warning_html = "".join(f"<div class='warning'>{esc(w)}</div>" for w in warnings)
        cards = []
        for name in runner.TASKS:
            item = tasks.get(name, {})
            state = item.get("status", "never_run")
            log = item.get("log_path", "")
            log_link = f"<a class='button secondary' href='/log?path={esc(log)}'>日志</a>" if log else ""
            error = item.get("error_summary") or "无"
            cards.append(
                "<article class='task-card'>"
                f"<div><div class='task-title'>{esc(task_label(name))}</div><div class='task-key'>{esc(name)}</div></div>"
                f"<span class='pill {status_class(state)}'>{esc(status_label(state))}</span>"
                f"<div class='meta'>开始：{esc(item.get('started_at', '-'))}<br>"
                f"耗时：{esc(item.get('duration_seconds', '-'))} s<br>"
                f"错误：{esc(error)}</div>"
                f"<div class='actions'><form method='post' action='/run/{esc(name)}'><button {'disabled' if is_running else ''}>运行</button></form>{log_link}</div>"
                "</article>"
            )
        running_names = [task_label(name) for name in run_names if tasks.get(name, {}).get("status") == "running"]
        current_task = running_names[0] if running_names else "-"
        scheduler_state = scheduler.get("scheduler_status", "stopped")
        scheduler_labels = {
            "collecting": "正在采集",
            "waiting": "定时等待",
            "running": "已启动",
            "stopping": "待当前采集完成后停止",
            "failed": "启动失败",
            "stopped": "未启动",
        }
        scheduler_class = (
            "danger"
            if scheduler_state == "failed"
            else ("warn" if scheduler_state in {"collecting", "stopping"} else ("good" if scheduler_state in {"waiting", "running"} else "idle"))
        )
        should_refresh = is_running or scheduler_state in {"collecting", "stopping"}
        refresh_script = "<script>setTimeout(() => location.reload(), 2000);</script>" if should_refresh else ""
        scheduler_html = f"""
<section class="panel" style="padding:16px 20px">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
    <div><strong>手动定时采集</strong><div class="muted">启动：{esc(scheduler.get('started_at') or '-')}</div></div>
    <span class="pill {scheduler_class}">{esc(scheduler_labels.get(scheduler_state, scheduler_state))}</span>
    <div class="muted">下次执行：{esc(scheduler.get('next_run_at') or '-')}</div>
  </div>
</section>"""
        body = f"""
{scheduler_html}
{ota_scope_panel(settings, "/")}
<section class="summary">
  <div class="metric"><div class="label">总状态</div><div class="value"><span class="pill {status_class(current_status)}">{esc(status_label(current_status))}</span></div></div>
  <div class="metric"><div class="label">执行进度</div><div class="value">{completed_count}/{total}</div></div>
  <div class="metric"><div class="label">成功 / 失败</div><div class="value">{success_count} / {failed_count}</div></div>
  <div class="metric"><div class="label">运行中 / 待执行</div><div class="value">{running_count} / {pending_count}</div><div class="muted">{esc(current_task)}</div></div>
</section>
{warning_html}
<section class="panel">
  <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px">
    <div>
      <h2 style="margin:0">采集任务</h2>
      <div class="muted">最近运行：{esc(status.get('last_run_started_at') or '-')}</div>
    </div>
    <form method="post" action="/run/all"><button {'disabled' if is_running else ''}>运行全部</button></form>
  </div>
  <div class="grid">{''.join(cards)}</div>
</section>
{refresh_script}"""
        return page("OTA 采集控制台", body, "status")

    @app.post("/run/all")
    def run_all():
        run_background(["run-once"])
        return redirect(url_for("index"))

    @app.post("/run/<task>")
    def run_task(task: str):
        if task in runner.TASKS:
            run_background(["run-task", task])
        return redirect(url_for("index"))
