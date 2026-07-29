from __future__ import annotations

from flask import redirect, url_for

import runner
from price_routes import PRICE_STOP_PATH, price_scheduler_status, start_price_scheduler
from review_reply_routes import REPLY_STOP_PATH, reply_scheduler_status, start_reply_scheduler
from panel_common import (
    esc,
    manual_scheduler_status,
    ota_scope_panel,
    page,
    request_manual_scheduler_stop,
    run_background,
    start_manual_scheduler,
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
        price_scheduler = price_scheduler_status()
        reply_scheduler = reply_scheduler_status()
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
        is_running = current_status in {"starting", "running", "stopping"}
        warnings = runner.config_warnings(settings)
        warning_html = "".join(f"<div class='warning'>{esc(w)}</div>" for w in warnings)
        cards = []
        for name in runner.TASKS:
            item = tasks.get(name, {})
            state = item.get("status", "never_run")
            platform = runner.TASKS[name][0]
            task_enabled = bool((settings.get("tasks") or {}).get(name, False)) and bool(
                (settings.get(platform) or {}).get("enabled", True)
            )
            config_state = "已启用" if task_enabled else "已禁用"
            log = item.get("log_path", "")
            log_link = f"<a class='button secondary' href='/log?path={esc(log)}'>日志</a>" if log else ""
            error = item.get("error_summary") or "无"
            cards.append(
                "<article class='task-card'>"
                f"<div><div class='task-title'>{esc(task_label(name))}</div><div class='task-key'>{esc(name)}</div></div>"
                f"<span class='pill {status_class(state)}'>{esc(status_label(state))}</span>"
                f"<div class='meta'>配置：{config_state}<br>开始：{esc(item.get('started_at', '-'))}<br>"
                f"耗时：{esc(item.get('duration_seconds', '-'))} s<br>"
                f"错误：{esc(error)}</div>"
                f"<div class='actions'><form method='post' action='/run/{esc(name)}'><button {'disabled' if is_running else ''}>运行</button></form>{log_link}</div>"
                "</article>"
            )
        running_names = [task_label(name) for name in run_names if tasks.get(name, {}).get("status") == "running"]
        current_task = running_names[0] if running_names else "-"
        scheduler_state = scheduler.get("scheduler_status", "stopped")
        price_scheduler_state = price_scheduler.get("scheduler_status", "stopped")
        reply_scheduler_state = reply_scheduler.get("scheduler_status", "stopped")
        collection_enabled = bool((settings.get("service") or {}).get("scheduler_enabled", True))
        price_enabled = bool((settings.get("price_scheduler") or {}).get("enabled", True))
        reply_enabled = bool((settings.get("reply_scheduler") or {}).get("enabled", False))
        scheduler_labels = {
            "collecting": "正在采集",
            "waiting": "定时等待",
            "running": "已启动",
            "stopping": "待当前采集完成后停止",
            "failed": "启动失败",
            "stopped": "未启动",
            "paused": "已暂停",
        }
        price_scheduler_labels = {
            "waiting": "定时等待",
            "executing": "正在调价",
            "stopping": "待当前调价完成后停止",
            "failed": "启动失败",
            "stopped": "未启动",
            "paused": "已暂停",
        }
        reply_scheduler_labels = {
            "waiting": "定时等待",
            "executing": "正在回复",
            "stopping": "待当前回复完成后停止",
            "failed": "启动失败",
            "stopped": "未启动",
            "paused": "已暂停",
        }
        collection_display_state = scheduler_state if collection_enabled or scheduler.get("alive") else "paused"
        price_display_state = price_scheduler_state if price_enabled or price_scheduler.get("alive") else "paused"
        reply_display_state = reply_scheduler_state if reply_enabled or reply_scheduler.get("alive") else "paused"
        scheduler_class = (
            "danger"
            if collection_display_state == "failed"
            else ("warn" if collection_display_state in {"collecting", "stopping"} else ("good" if collection_display_state in {"waiting", "running"} else "idle"))
        )
        price_scheduler_class = (
            "danger"
            if price_display_state == "failed"
            else ("warn" if price_display_state in {"executing", "stopping"} else ("good" if price_display_state == "waiting" else "idle"))
        )
        reply_scheduler_class = (
            "danger"
            if reply_display_state == "failed"
            else ("warn" if reply_display_state in {"executing", "stopping"} else ("good" if reply_display_state == "waiting" else "idle"))
        )
        collection_action = (
            "<form method='post' action='/scheduler/collection/stop'><button class='secondary'>暂停定时采集</button></form>"
            if scheduler.get("alive")
            else "<form method='post' action='/scheduler/collection/start'><button>开启定时采集</button></form>"
        )
        price_action = (
            "<form method='post' action='/scheduler/price/stop'><button class='secondary'>暂停定时调价</button></form>"
            if price_scheduler.get("alive")
            else "<form method='post' action='/scheduler/price/start'><button>开启定时调价</button></form>"
        )
        reply_action = (
            "<form method='post' action='/scheduler/reply/stop'><button class='secondary'>暂停定时回复</button></form>"
            if reply_scheduler.get("alive")
            else "<form method='post' action='/scheduler/reply/start'><button>开启定时回复</button></form>"
        )
        should_refresh = (
            is_running
            or scheduler_state in {"collecting", "stopping"}
            or price_scheduler_state in {"executing", "stopping"}
            or reply_scheduler_state in {"executing", "stopping"}
        )
        refresh_script = "<script>setTimeout(() => location.reload(), 2000);</script>" if should_refresh else ""
        scheduler_html = f"""
<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px">
  <section class="panel" style="padding:16px 20px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div><strong>定时采集任务</strong><div class="muted">下次执行：{esc(scheduler.get('next_run_at') or '-')}</div></div>
      <span class="pill {scheduler_class}">{esc(scheduler_labels.get(collection_display_state, collection_display_state))}</span>
      {collection_action}
    </div>
  </section>
  <section class="panel" style="padding:16px 20px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div><strong>定时调价任务</strong><div class="muted">下次检查：{esc(price_scheduler.get('next_run_at') or '-')}</div></div>
      <span class="pill {price_scheduler_class}">{esc(price_scheduler_labels.get(price_display_state, price_display_state))}</span>
      {price_action}
    </div>
  </section>
  <section class="panel" style="padding:16px 20px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div><strong>定时评论回复任务</strong><div class="muted">下次检查：{esc(reply_scheduler.get('next_run_at') or '-')}</div></div>
      <span class="pill {reply_scheduler_class}">{esc(reply_scheduler_labels.get(reply_display_state, reply_display_state))}</span>
      {reply_action}
    </div>
  </section>
</div>"""
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
    <div class="actions">
      <form method="post" action="/run/all"><button {'disabled' if is_running else ''}>运行全部</button></form>
      {"<form method='post' action='/run/stop'><button class='secondary'>中断当前采集</button></form>" if is_running else ""}
    </div>
  </div>
  <div class="grid">{''.join(cards)}</div>
</section>
{refresh_script}"""
        return page("酒店数据采集控制台", body, "status")

    @app.post("/run/all")
    def run_all():
        run_background(["run-once"])
        return redirect(url_for("index"))

    @app.post("/run/<task>")
    def run_task(task: str):
        if task in runner.TASKS:
            run_background(["run-task", task])
        return redirect(url_for("index"))

    @app.post("/run/stop")
    def stop_current_run():
        runner.request_run_stop()
        return redirect(url_for("index"))

    @app.post("/scheduler/collection/start")
    def start_collection_scheduler():
        settings = runner.load_settings()
        settings.setdefault("service", {})["scheduler_enabled"] = True
        runner.save_json(runner.CONFIG_PATH, settings)
        start_manual_scheduler(settings)
        return redirect(url_for("index"))

    @app.post("/scheduler/collection/stop")
    def stop_collection_scheduler():
        settings = runner.load_settings()
        settings.setdefault("service", {})["scheduler_enabled"] = False
        runner.save_json(runner.CONFIG_PATH, settings)
        request_manual_scheduler_stop()
        return redirect(url_for("index"))

    @app.post("/scheduler/price/start")
    def start_price_scheduler_from_status():
        settings = runner.load_settings()
        settings.setdefault("price_scheduler", {})["enabled"] = True
        runner.save_json(runner.CONFIG_PATH, settings)
        start_price_scheduler(settings)
        return redirect(url_for("index"))

    @app.post("/scheduler/price/stop")
    def stop_price_scheduler_from_status():
        settings = runner.load_settings()
        settings.setdefault("price_scheduler", {})["enabled"] = False
        runner.save_json(runner.CONFIG_PATH, settings)
        PRICE_STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRICE_STOP_PATH.write_text("stop", encoding="utf-8")
        return redirect(url_for("index"))

    @app.post("/scheduler/reply/start")
    def start_reply_scheduler_from_status():
        settings = runner.load_settings()
        settings.setdefault("reply_scheduler", {})["enabled"] = True
        runner.save_json(runner.CONFIG_PATH, settings)
        start_reply_scheduler(settings)
        return redirect(url_for("index"))

    @app.post("/scheduler/reply/stop")
    def stop_reply_scheduler_from_status():
        settings = runner.load_settings()
        settings.setdefault("reply_scheduler", {})["enabled"] = False
        runner.save_json(runner.CONFIG_PATH, settings)
        REPLY_STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPLY_STOP_PATH.write_text("stop", encoding="utf-8")
        return redirect(url_for("index"))
