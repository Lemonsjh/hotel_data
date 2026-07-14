from __future__ import annotations

import subprocess
from datetime import date
from typing import Any

from flask import redirect, request, url_for

import price_tasks
import runner
from panel_common import esc, get_path, page, process_alive


PRICE_STATUS_PATH = runner.ROOT / "state" / "price_scheduler_status.json"
PRICE_PID_PATH = runner.ROOT / "state" / "price_scheduler.pid"
PRICE_STOP_PATH = runner.ROOT / "state" / "price_scheduler.stop"


def price_scheduler_status() -> dict[str, Any]:
    data = runner.load_json(PRICE_STATUS_PATH, {})
    alive = process_alive(data.get("pid"))
    state = data.get("scheduler_status", "stopped") if alive else "stopped"
    if alive and PRICE_STOP_PATH.exists():
        state = "stopping"
    data["scheduler_status"] = state
    data["alive"] = alive
    return data


def start_price_scheduler(settings: dict[str, Any]) -> None:
    status = price_scheduler_status()
    if status["alive"]:
        return
    PRICE_PID_PATH.unlink(missing_ok=True)
    PRICE_STOP_PATH.unlink(missing_ok=True)
    subprocess.Popen(
        [str(runner.python_path(settings)), str(runner.ROOT / "price_scheduler.py")],
        cwd=str(runner.ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def price_status_class(status: str) -> str:
    return {"PENDING": "warn", "EXECUTING": "warn", "SUCCESS": "good", "FAILED": "danger"}.get(status, "idle")


def product_options(products: list[dict[str, Any]]) -> str:
    options = []
    for item in products:
        product_id = item.get("ota_product_id", "")
        name = item.get("display_name", product_id)
        price = item.get("ota_sale_price")
        price_text = f" - 当前采集价 ¥{price}" if price not in (None, "") else ""
        options.append(f"<option value='{esc(product_id)}'>{esc(name)} [{esc(product_id)}]{esc(price_text)}</option>")
    return "".join(options)


def register(app) -> None:
    @app.get("/price-tasks")
    def price_task_page() -> str:
        settings = runner.load_settings()
        scheduler = price_scheduler_status()
        scheduler_cfg = settings.get("price_scheduler") or {}
        notice = request.args.get("notice", "")
        error = request.args.get("error", "")
        messages = ""
        if notice:
            messages += f"<div class='success'>{esc(notice)}</div>"
        if error:
            messages += f"<div class='warning'>{esc(error)}</div>"

        scheduler_state = scheduler["scheduler_status"]
        scheduler_labels = {
            "waiting": "定时等待",
            "executing": "正在执行调价",
            "stopping": "当前任务完成后停止",
            "failed": "启动失败",
            "stopped": "已停用",
        }
        scheduler_class = "danger" if scheduler_state == "failed" else (
            "warn" if scheduler_state in {"executing", "stopping"} else ("good" if scheduler_state == "waiting" else "idle")
        )
        if scheduler["alive"]:
            scheduler_action = "<form method='post' action='/price-scheduler/stop'><button class='secondary'>停用定时调价</button></form>"
        else:
            scheduler_action = "<form method='post' action='/price-scheduler/start'><button>启用定时调价</button></form>"
        scheduler_html = f"""
        <section class="panel">
          <div class="panel-heading">
            <div>
              <h2 style="margin:0">定时调价执行器</h2>
              <div class="muted">检测到未过期的 PENDING 任务后自动执行；每 {esc(scheduler_cfg.get('interval_minutes', 5))} 分钟检查一次</div>
            </div>
            <span class="pill {scheduler_class}">{esc(scheduler_labels.get(scheduler_state, scheduler_state))}</span>
          </div>
          <div class="panel-heading" style="margin-top:14px">
            <div class="muted">下次检查：{esc(scheduler.get('next_run_at') or '-')}<br>最近完成：{esc(scheduler.get('last_run_finished_at') or '-')}</div>
            {scheduler_action}
          </div>
        </section>
        """

        try:
            products, tasks = price_tasks.page_data(settings, force=request.args.get("refresh") == "1")
        except Exception as exc:
            products = {"meituan": [], "ctrip": []}
            tasks = []
            messages += f"<div class='warning'>{esc(exc)}</div>"

        create_panels = []
        for platform, info in price_tasks.PLATFORMS.items():
            hotel_name = get_path(settings, f"{platform}.hotel_name")
            create_panels.append(
                f"""
                <section class="panel">
                  <div class="panel-heading">
                    <h2 style="margin:0">{esc(info['label'])}调价任务</h2>
                    <span class="pill idle">{esc(hotel_name or '未配置酒店')}</span>
                  </div>
                  <form method="post" action="/price-tasks/create" class="form-grid" style="margin-top:16px">
                    <input type="hidden" name="platform" value="{esc(platform)}">
                    <div class="field"><label>商品 / 房型</label>
                      <select name="product_id" required>{product_options(products[platform])}</select>
                    </div>
                    <div class="field"><label>调价日期</label>
                      <input type="date" name="business_date" value="{date.today().isoformat()}" min="{date.today().isoformat()}" required>
                    </div>
                    <div class="field"><label>目标售价（元）</label>
                      <input type="number" name="target_price" min="1" step="0.01" required>
                    </div>
                    <div class="field" style="display:flex;align-items:end"><button type="submit">创建并立即调价</button></div>
                  </form>
                </section>
                """
            )

        task_rows = []
        for item in tasks:
            platform = item["platform"]
            task_id = int(item["id"])
            status = str(item.get("execute_status") or "")
            actions = "-"
            if status == "PENDING":
                actions = f"""
                <div class="actions" style="margin:0;flex-wrap:wrap">
                  <form method="post" action="/price-tasks/{esc(platform)}/{task_id}/preview">
                    <button class="secondary">预览</button>
                  </form>
                  <form method="post" action="/price-tasks/{esc(platform)}/{task_id}/cancel">
                    <button class="secondary">取消</button>
                  </form>
                  <form method="post" action="/price-tasks/{esc(platform)}/{task_id}/execute" class="inline-form">
                    <input name="confirmation" placeholder="输入 确认执行" autocomplete="off" required>
                    <button class="danger">真实执行</button>
                  </form>
                </div>
                """
            task_rows.append(
                "<tr>"
                f"<td>{task_id}</td><td>{esc(item['platform_label'])}</td><td>{esc(item.get('hotel_name'))}</td>"
                f"<td>{esc(item.get('room_type_name'))}<div class='task-key'>{esc(item.get('ota_product_id'))}</div></td>"
                f"<td>{esc(item.get('business_date'))}</td><td>¥{esc(item.get('target_sale_price'))}</td>"
                f"<td><span class='pill {price_status_class(status)}'>{esc(status)}</span></td>"
                f"<td>{actions}</td></tr>"
            )

        body = f"""
        {messages}
        {scheduler_html}
        <div class="detect-bar" style="justify-content:flex-end;margin-bottom:12px">
          <a class="button secondary" href="/price-tasks?refresh=1">刷新数据库数据</a>
        </div>
        <section class="summary">
          <div class="metric"><div class="label">待执行</div><div class="value">{sum(1 for x in tasks if x.get('execute_status') == 'PENDING')}</div></div>
          <div class="metric"><div class="label">已成功</div><div class="value">{sum(1 for x in tasks if x.get('execute_status') == 'SUCCESS')}</div></div>
          <div class="metric"><div class="label">执行失败</div><div class="value">{sum(1 for x in tasks if x.get('execute_status') == 'FAILED')}</div></div>
          <div class="metric"><div class="label">执行中</div><div class="value">{sum(1 for x in tasks if x.get('execute_status') == 'EXECUTING')}</div></div>
        </section>
        {''.join(create_panels)}
        <section class="panel">
          <h2 style="margin-top:0">调价任务记录</h2>
          <div class="table-wrap">
            <table>
              <tr><th>ID</th><th>平台</th><th>酒店</th><th>商品 / 房型</th><th>日期</th><th>目标价</th><th>执行</th><th>操作</th></tr>
              {''.join(task_rows) or '<tr><td colspan="8">暂无调价任务</td></tr>'}
            </table>
          </div>
        </section>
        """
        refresh_script = "<script>setTimeout(() => location.reload(), 2000);</script>" if scheduler_state in {"executing", "stopping"} else ""
        return page("调价任务", body + refresh_script, "prices")

    @app.post("/price-scheduler/start")
    def start_price_scheduler_route():
        try:
            settings = runner.load_settings()
            settings.setdefault("price_scheduler", {})["enabled"] = True
            runner.save_json(runner.CONFIG_PATH, settings)
            start_price_scheduler(settings)
            return redirect(url_for("price_task_page", notice="定时调价执行器已启动"))
        except Exception as exc:
            return redirect(url_for("price_task_page", error=str(exc)))

    @app.post("/price-scheduler/stop")
    def stop_price_scheduler_route():
        settings = runner.load_settings()
        settings.setdefault("price_scheduler", {})["enabled"] = False
        runner.save_json(runner.CONFIG_PATH, settings)
        PRICE_STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRICE_STOP_PATH.write_text("stop", encoding="utf-8")
        return redirect(url_for("price_task_page", notice="已请求停止定时调价执行器"))

    @app.post("/price-tasks/create")
    def create_price_task():
        try:
            settings = runner.load_settings()
            platform = request.form.get("platform", "")
            task_id = price_tasks.create_task(
                settings,
                platform,
                request.form.get("product_id", ""),
                request.form.get("business_date", ""),
                request.form.get("target_price", ""),
            )
            log = price_tasks.launch_task(settings, platform, task_id, dry_run=False)
            return redirect(url_for("price_task_page", notice=f"任务 #{task_id} 已创建并开始调价：{log.name}"))
        except Exception as exc:
            return redirect(url_for("price_task_page", error=str(exc)))

    @app.post("/price-tasks/<platform>/<int:task_id>/preview")
    def preview_price_task(platform: str, task_id: int):
        try:
            log = price_tasks.launch_task(runner.load_settings(), platform, task_id, dry_run=True)
            return redirect(url_for("price_task_page", notice=f"预览已启动：{log.name}"))
        except Exception as exc:
            return redirect(url_for("price_task_page", error=str(exc)))

    @app.post("/price-tasks/<platform>/<int:task_id>/execute")
    def execute_price_task(platform: str, task_id: int):
        if request.form.get("confirmation", "").strip() != "确认执行":
            return redirect(url_for("price_task_page", error="请输入“确认执行”"))
        try:
            log = price_tasks.launch_task(runner.load_settings(), platform, task_id, dry_run=False)
            return redirect(url_for("price_task_page", notice=f"真实调价已启动：{log.name}"))
        except Exception as exc:
            return redirect(url_for("price_task_page", error=str(exc)))

    @app.post("/price-tasks/<platform>/<int:task_id>/cancel")
    def cancel_price_task(platform: str, task_id: int):
        try:
            changed = price_tasks.cancel_task(runner.load_settings(), platform, task_id)
            message = f"已取消任务 #{task_id}" if changed else "任务不存在或已执行"
            return redirect(url_for("price_task_page", notice=message))
        except Exception as exc:
            return redirect(url_for("price_task_page", error=str(exc)))
