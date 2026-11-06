from __future__ import annotations

import os
import re
import subprocess
import time

from flask import redirect, request, url_for

import panel_update
import runner
from price_routes import PRICE_STOP_PATH, price_scheduler_status, start_price_scheduler
from promotion_scheduler import STOP_PATH as PROMOTION_STOP_PATH, scheduler_status as promotion_scheduler_status, start_scheduler as start_promotion_scheduler
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


_LEGACY_CACHE = {"at": 0.0, "value": False}


def needs_setup(settings: dict) -> bool:
    hotel_id = str((settings.get("hotel") or {}).get("hotel_id") or "").strip()
    database = str((settings.get("mysql") or {}).get("database") or "").strip()
    return not hotel_id or not database


def benign_empty_booking(text: str) -> bool:
    """预订或订单 0 条是正常结果，不能当成失败文案。"""
    normalized = str(text or "").replace(" ", "")
    if "0条" not in normalized:
        return False
    if "预订" not in text and "订单" not in text:
        return False
    return not any(word in text for word in ("失败", "无效", "拒绝", "错误"))


def hotel_sentence(settings: dict) -> str:
    hotel_id = str((settings.get("hotel") or {}).get("hotel_id") or "").strip()
    names = []
    meituan_name = str((settings.get("meituan") or {}).get("hotel_name") or "").strip()
    ctrip_name = str((settings.get("ctrip") or {}).get("hotel_name") or "").strip()
    if meituan_name:
        names.append(f"美团：{meituan_name}")
    if ctrip_name:
        names.append(f"携程：{ctrip_name}")
    if hotel_id and names:
        return f"{hotel_id}（{'，'.join(names)}）"
    if hotel_id:
        return hotel_id
    if names:
        return "，".join(names)
    return "尚未填写酒店"


def collection_sentence(settings: dict, status: dict) -> str:
    tasks = status.get("tasks") or {}
    run_names = list(status.get("last_run_tasks") or [])
    running = [name for name in run_names if (tasks.get(name) or {}).get("status") == "running"]
    current = status.get("last_run_status") or "never_run"
    if running or current in {"starting", "running", "stopping"}:
        if running:
            return task_label(running[0])
        if current == "stopping":
            return "正在停止采集"
        return "正在采集"
    if not bool((settings.get("service") or {}).get("scheduler_enabled", True)):
        return "未开启自动采集"
    if current == "success":
        return "正常"
    if current in {"failed", "partial_failed"}:
        failed = [
            str((tasks.get(name) or {}).get("error_summary") or "")
            for name in run_names
            if (tasks.get(name) or {}).get("status") in {"failed", "partial_failed"}
        ]
        if failed and all(benign_empty_booking(text) for text in failed):
            return "正常"
        return "最近一轮没有全部成功"
    if current == "cancelled":
        return "上一轮已中断"
    if current == "never_run":
        return "尚未开始采集"
    return status_label(current)


def unique_todo(settings: dict) -> dict | None:
    if needs_setup(settings):
        return {"text": "请完成酒店和数据库配置", "href": "/config?view=installer", "label": "去填写"}
    meituan = settings.get("meituan") or {}
    ctrip = settings.get("ctrip") or {}
    if meituan.get("enabled", True) and not str(meituan.get("me_cookie") or "").strip():
        return {"text": "请重新登录美团", "href": "/config?view=staff", "label": "去登录"}
    if ctrip.get("enabled", True) and not str(ctrip.get("cookie") or "").strip():
        return {"text": "请重新登录携程", "href": "/config?view=staff", "label": "去登录"}
    return None


def build_conclusion(settings: dict, status: dict) -> dict:
    return {
        "hotel": hotel_sentence(settings),
        "collection": collection_sentence(settings, status),
        "todo": unique_todo(settings),
    }


def _query_legacy_task() -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["schtasks", "/Query", "/TN", "HotelOTACollector", "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    text = f"{completed.stdout}\n{completed.stderr}"
    if "已禁用" in text or re.search(r"(?i)\bDisabled\b", text):
        return False
    return True


def legacy_collector_enabled() -> bool:
    now = time.time()
    if now - _LEGACY_CACHE["at"] < 60:
        return bool(_LEGACY_CACHE["value"])
    value = _query_legacy_task()
    _LEGACY_CACHE["at"] = now
    _LEGACY_CACHE["value"] = value
    return value


def conclusion_html(settings: dict, status: dict) -> str:
    summary = build_conclusion(settings, status)
    todo = summary["todo"]
    if not todo:
        todo_html = "<strong>没有待办</strong>"
    elif todo.get("href"):
        todo_html = (
            f"<strong>{esc(todo['text'])}</strong>"
            f"<div class='actions'><a class='button' href='{esc(todo['href'])}'>{esc(todo['label'])}</a></div>"
        )
    else:
        todo_html = (
            f"<strong>{esc(todo['text'])}</strong>"
            "<form method='post' action='" + esc(todo["action"]) + "'>"
            "<input type='hidden' name='return_to' value='/'>"
            f"<button>{esc(todo['label'])}</button></form>"
        )
    legacy_html = ""
    if legacy_collector_enabled():
        legacy_html = (
            "<p>计划任务 HotelOTACollector 仍在启用，会和手动启动冲突。"
            "<a href='/legacy-task'>查看卸载脚本</a>。本页不会自动删除该任务。</p>"
        )
    return f"""
<section class="panel conclusion">
  <div class="conclusion-grid">
    <div><div class="label">酒店</div><strong>{esc(summary["hotel"])}</strong></div>
    <div><div class="label">采集</div><strong>{esc(summary["collection"])}</strong></div>
    <div><div class="label">待办</div>{todo_html}</div>
  </div>
  <p class="muted">服务在后台运行，关闭浏览器不会停止。停止请用停止脚本，或在本页暂停定时采集。</p>
  {legacy_html}
</section>"""


def update_panel_html() -> str:
    notice = panel_update.current_notice()
    message = str(notice.get("message") or "")
    kind = str(notice.get("kind") or "")
    extra = ""
    if kind == "available":
        extra = (
            "<form method='post' action='/update/apply'>"
            "<input type='hidden' name='confirm' value='yes'>"
            "<button onclick=\"return confirm('确认安装正式版代码？不会覆盖配置、Cookie、日志和运行环境，也不会迁移数据库。')\">确认安装</button>"
            "</form>"
        )
    elif kind == "interrupted":
        extra = (
            "<form method='post' action='/update/recover'>"
            "<input type='hidden' name='confirm' value='yes'>"
            "<button>先恢复上次更新</button></form>"
        )
    message_html = f"<div class='warning'>{esc(message)}</div>" if message else ""
    return f"""
<section class="panel">
  <div class="section-save">
    <div>
      <strong>检查更新</strong>
      <div class="muted">只查看 GitHub 正式版。确认后才会安装，不会覆盖配置，也不会更换运行环境。</div>
      {message_html}
    </div>
    <div class="save-actions">
      <form method="post" action="/update/check"><button type="submit" class="secondary">检查更新</button></form>
      {extra}
    </div>
  </div>
</section>"""


def register(app) -> None:
    @app.get("/")
    def index() -> str:
        status = runner.reconcile_stale_run()
        settings = runner.load_settings()
        show_tasks = request.args.get("tasks") == "1"
        scheduler = manual_scheduler_status()
        price_scheduler = price_scheduler_status()
        reply_scheduler = reply_scheduler_status()
        promotion_scheduler = promotion_scheduler_status()
        tasks = status.get("tasks") or {}
        run_names = status.get("last_run_tasks") or runner.enabled_tasks(settings)
        run_items = [tasks.get(name, {}) for name in run_names]
        success_count = sum(1 for item in run_items if item.get("status") == "success")
        failed_count = sum(1 for item in run_items if item.get("status") in {"failed", "partial_failed"})
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
            unavailable_reason = runner.task_unavailable_reason(name, platform, settings)
            task_enabled = bool((settings.get("tasks") or {}).get(name, False)) and not unavailable_reason
            config_state = "已启用" if task_enabled else "已禁用"
            log = item.get("log_path", "")
            log_link = f"<a class='button secondary' href='/log?path={esc(log)}'>日志</a>" if log else ""
            error = item.get("error_summary") or "无"
            children = item.get("subtasks") or []
            child_html = ""
            if len(children) > 1:
                succeeded = sum(child.get("status") == "success" for child in children)
                failed = sum(child.get("status") == "failed" for child in children)
                child_html = (
                    f"<details class='meta'><summary>子采集：{succeeded} 成功 / {failed} 失败</summary>"
                    + "".join(f"<div>{esc(child.get('script'))}：{esc(status_label(child.get('status')))}</div>" for child in children)
                    + "</details>"
                )
            cards.append(
                "<article class='task-card'>"
                f"<div><div class='task-title'>{esc(task_label(name))}</div><div class='task-key'>{esc(name)}</div></div>"
                f"<span class='pill {status_class(state)}'>{esc(status_label(state))}</span>"
                f"<div class='meta'>配置：{config_state}<br>开始：{esc(item.get('started_at', '-'))}<br>"
                f"耗时：{esc(item.get('duration_seconds', '-'))} s<br>"
                f"错误：{esc(error)}</div>"
                f"{child_html}"
                f"<div class='actions'><form method='post' action='/run/{esc(name)}'>"
                f"<button {'disabled' if is_running or unavailable_reason else ''} "
                f"title='{esc(unavailable_reason)}'>运行</button></form>{log_link}</div>"
                "</article>"
            )
        running_names = [task_label(name) for name in run_names if tasks.get(name, {}).get("status") == "running"]
        current_task = running_names[0] if running_names else "-"
        scheduler_state = scheduler.get("scheduler_status", "stopped")
        price_scheduler_state = price_scheduler.get("scheduler_status", "stopped")
        reply_scheduler_state = reply_scheduler.get("scheduler_status", "stopped")
        promotion_scheduler_state = promotion_scheduler.get("scheduler_status", "stopped")
        collection_enabled = bool((settings.get("service") or {}).get("scheduler_enabled", True))
        price_enabled = bool((settings.get("price_scheduler") or {}).get("enabled", True))
        reply_enabled = bool((settings.get("reply_scheduler") or {}).get("enabled", False))
        promotion_enabled = bool((settings.get("promotion_scheduler") or {}).get("enabled", False))
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
        promotion_scheduler_labels = {
            "waiting": "定时等待",
            "executing": "正在执行推广控制",
            "stopping": "待当前推广操作完成后停止",
            "failed": "启动失败",
            "stopped": "未启动",
            "paused": "已暂停",
        }
        collection_display_state = scheduler_state if collection_enabled or scheduler.get("alive") else "paused"
        price_display_state = price_scheduler_state if price_enabled or price_scheduler.get("alive") else "paused"
        reply_display_state = reply_scheduler_state if reply_enabled or reply_scheduler.get("alive") else "paused"
        promotion_display_state = promotion_scheduler_state if promotion_enabled or promotion_scheduler.get("alive") else "paused"
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
        promotion_scheduler_class = (
            "danger"
            if promotion_display_state == "failed"
            else ("warn" if promotion_display_state in {"executing", "stopping"} else ("good" if promotion_display_state == "waiting" else "idle"))
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
        promotion_action = (
            "<form method='post' action='/scheduler/promotion/stop'><button class='secondary'>暂停推广控制</button></form>"
            if promotion_scheduler.get("alive")
            else "<form method='post' action='/scheduler/promotion/start'><button>开启推广控制</button></form>"
        )
        should_refresh = (
            is_running
            or scheduler_state in {"collecting", "stopping"}
            or price_scheduler_state in {"executing", "stopping"}
            or reply_scheduler_state in {"executing", "stopping"}
            or promotion_scheduler_state in {"executing", "stopping"}
        )
        refresh_delay = 2000 if should_refresh else 15000 if scheduler.get("alive") and scheduler_state == "waiting" else 0
        refresh_script = f"<script>setTimeout(() => location.reload(), {refresh_delay});</script>" if refresh_delay else ""
        scheduler_html = f"""
<div class="grid scheduler-grid">
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
  <section class="panel" style="padding:16px 20px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div><strong>定时推广控制任务</strong><div class="muted">下次检查：{esc(promotion_scheduler.get('next_run_at') or '-')}</div></div>
      <span class="pill {promotion_scheduler_class}">{esc(promotion_scheduler_labels.get(promotion_display_state, promotion_display_state))}</span>
      {promotion_action}
    </div>
  </section>
</div>"""
        notice = request.args.get("notice", "")
        error = request.args.get("error", "")
        message_html = ""
        if notice:
            message_html += f"<div class='success'>{esc(notice)}</div>"
        if error:
            message_html += f"<div class='warning'>{esc(error)}</div>"
        tasks_link = (
            "<p><a class='button secondary' href='/?tasks=1'>查看全部任务</a></p>"
            if not show_tasks
            else "<p><a class='button secondary' href='/'>返回简要状态</a></p>"
        )
        detail_html = ""
        if show_tasks:
            detail_html = f"""
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
</section>"""
        body = f"""
{message_html}
{conclusion_html(settings, status)}
{update_panel_html()}
{scheduler_html}
{tasks_link}
{detail_html}
{refresh_script}"""
        return page("酒店数据采集控制台", body, "status")

    @app.post("/run/all")
    def run_all():
        run_background(["run-once"])
        return redirect(url_for("index"))

    @app.post("/run/<task>")
    def run_task(task: str):
        if task in runner.TASKS and runner.task_is_available(task, runner.TASKS[task][0], runner.load_settings()):
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

    @app.post("/scheduler/promotion/start")
    def start_promotion_scheduler_from_status():
        settings = runner.load_settings()
        settings.setdefault("promotion_scheduler", {})["enabled"] = True
        runner.save_json(runner.CONFIG_PATH, settings)
        start_promotion_scheduler(settings)
        return redirect(url_for("index"))

    @app.post("/scheduler/promotion/stop")
    def stop_promotion_scheduler_from_status():
        settings = runner.load_settings()
        settings.setdefault("promotion_scheduler", {})["enabled"] = False
        runner.save_json(runner.CONFIG_PATH, settings)
        PROMOTION_STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROMOTION_STOP_PATH.write_text("stop", encoding="utf-8")
        return redirect(url_for("index"))

    @app.post("/update/check")
    def update_check():
        try:
            notice = panel_update.check_release()
        except Exception as exc:
            notice = {"kind": "error", "message": f"检查更新没有完成：{exc}"}
        panel_update.store_notice(notice)
        return redirect(url_for("index"))

    @app.post("/update/apply")
    def update_apply():
        if request.form.get("confirm") != "yes":
            panel_update.store_notice({"kind": "error", "message": "还没有确认，没有安装更新。"})
            return redirect(url_for("index"))
        try:
            notice = panel_update.apply_confirmed()
        except Exception as exc:
            notice = {"kind": "error", "message": panel_update.classify_update_text(str(exc))}
        panel_update.store_notice(notice)
        return redirect(url_for("index"))

    @app.post("/update/recover")
    def update_recover():
        if request.form.get("confirm") != "yes":
            panel_update.store_notice({"kind": "interrupted", "message": "还没有确认，没有恢复。"})
            return redirect(url_for("index"))
        try:
            notice = panel_update.recover_confirmed()
        except Exception as exc:
            notice = {"kind": "error", "message": panel_update.classify_update_text(str(exc))}
        panel_update.store_notice(notice)
        return redirect(url_for("index"))

    @app.get("/legacy-task")
    def legacy_task_page() -> str:
        script = runner.ROOT / "uninstall_task.bat"
        body = f"""
<section class="panel">
  <h2>卸载旧的计划任务</h2>
  <p>计划任务 HotelOTACollector 和现在的手动启动会冲突。请手动运行下面的卸载脚本，本页不会自动删除它。</p>
  <p><code>{esc(script)}</code></p>
  <p>脚本会请求管理员权限。卸载之后，再用启动脚本打开本页。</p>
  <p><a class="button secondary" href="/">返回状态页</a></p>
</section>"""
        return page("卸载旧计划任务", body, "status")
