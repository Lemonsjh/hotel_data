from __future__ import annotations

import ctypes
import html
import subprocess
import sys
from typing import Any

from flask import render_template

import runner


MANUAL_STATUS_PATH = runner.ROOT / "state" / "manual_scheduler_status.json"
MANUAL_STOP_PATH = runner.ROOT / "state" / "manual_scheduler.stop"

KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
KERNEL32.OpenProcess.restype = ctypes.c_void_p
KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]

TASK_LABELS = {
    "meituan_business": "美团经营",
    "meituan_flow_conversion": "美团近30天流量",
    "meituan_joined_rights": "美团已报名权益",
    "meituan_promotion_status": "美团促销开通状态",
    "meituan_video_upload_status": "美团视频上传情况",
    "meituan_promotion_performance": "美团近30天推广效果",
    "meituan_exposure_source": "美团曝光来源",
    "meituan_order_loss": "美团流失订单",
    "meituan_scan_order": "美团扫码订单明细",
    "meituan_user_source": "美团用户来源",
    "meituan_review": "美团评价",
    "meituan_review_detail": "美团评价明细",
    "meituan_promotion": "美团活动",
    "meituan_goods_price": "美团调价商品",
    "meituan_nearby_event": "美团周边事件",
    "ctrip_business": "携程经营",
    "ctrip_flow_conversion": "携程近30天流量",
    "ctrip_order_loss": "携程流失订单",
    "ctrip_joined_rights": "携程已报名权益",
    "ctrip_promotion_status": "携程活动开通状态",
    "ctrip_user_profile": "携程用户画像",
    "ctrip_psi_score": "携程PSI评分",
    "ctrip_promotion_performance": "携程近30天推广效果",
    "ctrip_review": "携程评价",
    "ctrip_review_detail": "携程评价明细",
    "ctrip_promotion": "携程活动",
    "ctrip_goods_price": "携程调价商品",
    "pms_fetch": "PMS 数据采集",
}

STATUS_LABELS = {
    "success": "成功",
    "failed": "失败",
    "pending": "待执行",
    "running": "运行中",
    "partial_failed": "部分失败",
    "never_run": "未运行",
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def status_class(value: str) -> str:
    if value == "success":
        return "good"
    if value in ("failed", "partial_failed"):
        return "danger"
    if value == "running":
        return "warn"
    return "idle"


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value, value or "未运行")


def task_label(name: str) -> str:
    return TASK_LABELS.get(name, name)


def get_path(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part, "")
    return cur


def set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    cur = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def page(title: str, body: str, active: str = "status") -> str:
    nav_items = [
        ("status", "/", "状态"),
        ("prices", "/price-tasks", "调价任务"),
        ("room_mappings", "/room-mappings", "房型映射"),
        ("config", "/config", "配置"),
        ("logs", "/logs", "日志"),
    ]
    return render_template("base.html", title=title, body=body, active=active, nav_items=nav_items)


def process_alive(pid: Any) -> bool:
    if not pid:
        return False
    handle = KERNEL32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    KERNEL32.CloseHandle(handle)
    return True


def manual_scheduler_status() -> dict[str, Any]:
    data = runner.load_json(MANUAL_STATUS_PATH, {})
    alive = process_alive(data.get("pid"))
    state = data.get("scheduler_status", "stopped") if alive else "stopped"
    if alive and MANUAL_STOP_PATH.exists():
        state = "stopping"
    data["scheduler_status"] = state
    return data


def run_background(args: list[str]) -> None:
    subprocess.Popen(
        [sys.executable, str(runner.ROOT / "runner.py"), *args],
        cwd=str(runner.ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ota_scope_panel(settings: dict[str, Any], return_to: str) -> str:
    meituan = bool((settings.get("meituan") or {}).get("enabled", True))
    ctrip = bool((settings.get("ctrip") or {}).get("enabled", True))
    current = "all" if meituan and ctrip else ("meituan" if meituan else ("ctrip" if ctrip else "none"))
    labels = {"all": "全部平台", "meituan": "仅美团", "ctrip": "仅携程", "none": "均已暂停"}
    options = [
        ("all", "全部平台", "美团 + 携程"),
        ("meituan", "仅美团", "暂停携程定时采集"),
        ("ctrip", "仅携程", "暂停美团定时采集"),
    ]
    buttons = []
    for value, title, hint in options:
        active = " active" if current == value else ""
        pressed = "true" if active else "false"
        buttons.append(
            "<form method='post' action='/config/ota-scope'>"
            f"<input type='hidden' name='scope' value='{value}'>"
            f"<input type='hidden' name='return_to' value='{esc(return_to)}'>"
            f"<button class='ota-scope-button{active}' aria-pressed='{pressed}'>"
            f"<strong>{title}</strong><small>{hint}</small></button></form>"
        )
    state_class = "warn" if current == "none" else "good"
    return f"""
    <section class="panel ota-scope-panel">
      <div class="ota-scope-header">
        <div><h2>OTA 采集范围</h2><div class="muted">选择“运行全部”和下一轮定时任务需要采集的平台。</div></div>
        <span class="pill {state_class}">当前：{labels[current]}</span>
      </div>
      <div class="ota-scope-options">{''.join(buttons)}</div>
      <div class="muted" style="margin-top:10px">修改后立即生效，不中断正在运行的任务；各任务仍可单独手动运行。</div>
    </section>"""
