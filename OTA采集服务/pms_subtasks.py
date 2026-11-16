"""PMS 串行子采集：隔离单张报表故障，保留中断和整体失败语义。"""
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from process_runner import ProcessCancelledError

MEITUAN_DAILY_SCRIPTS = {
    "daily_report.py", "monthly_report.py", "performance_report.py", "classification_report.py",
    "room_performance_report.py", "revenue_report.py", "guest_report.py",
}
DAILY_SCRIPTS = {
    "bypms": {
        "bypms_channel_mapping_data.py", "bypms_daily_report_data.py", "bypms_jl11_data.py",
        "bypms_jy03_data.py", "bypms_jd02_data.py",
    },
    "meituan_pms": MEITUAN_DAILY_SCRIPTS,
    "meituan_rezen": MEITUAN_DAILY_SCRIPTS,
    "yolo": {
        "yolo_daily_report_data.py", "yolo_jl11_data.py", "yolo_rs01_data.py",
        "yolo_jy03_data.py", "yolo_jd02_data.py",
    },
    "foxhis": {"foxhis_jl01_data.py", "foxhis_jy01_data.py", "foxhis_jy03_data.py", "foxhis_jl11_data.py"},
}
SHANGHAI = timezone(timedelta(hours=8))


def daily_state(api, platform, env):
    keys = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "HOTEL_ID")
    if platform in {"meituan_pms", "meituan_rezen"}:
        keys += ("MEITUAN_PMS_ORG_ID", "MEITUAN_PMS_TAKEOVER_DATE")
    if platform == "meituan_rezen":
        keys += ("REZEN_BASE_URL", "REZEN_HOTEL_NAME")
    if platform == "yolo":
        keys += ("YOLO_SERVER", "YOLO_DATABASE")
    if platform == "pms":
        keys += ("PMS_USERNAME", "PMS_LOGIN_BASE_URL")
    if platform == "foxhis":
        keys += ("FOXHIS_SERVER", "FOXHIS_DATABASE", "FOXHIS_USERNAME", "FOXHIS_INTERFACES_FILE",
                 "FOXHIS_BUSINESS_DATE_OFFSET_DAYS", "FOXHIS_REPORTS")
    scope = [env.get(key, "") for key in keys]
    if platform == "foxhis":
        accounts = json.loads(env.get("FOXHIS_ACCOUNTS") or "[]")
        source_keys = ("server", "database", "username", "interfaces_file", "business_date_offset_days", "reports")
        scope.extend([account.get(key, "") for key in source_keys] for account in accounts)
    key = sha256(json.dumps(scope).encode()).hexdigest()
    return api.STATUS_PATH.parent / f"{platform}_daily_{key}.json"


def run_pms_subtasks(specs, settings, status, result, python, base_dir, log_path, api):
    children = [{"script": script.name, "status": "pending", "return_code": None, "error_summary": ""}
                for _platform, script, _args in specs]
    result["subtasks"] = children
    for index, (platform, script, args) in enumerate(specs):
        if api.run_stop_requested():
            raise ProcessCancelledError("")
        child = children[index]
        child["status"] = "running"
        api.save_json(api.STATUS_PATH, status)
        with log_path.open("w" if index == 0 else "a", encoding="utf-8") as log:
            log.write(f"=== PMS 子采集：{script.name} ===\n")
        try:
            if not script.exists():
                raise FileNotFoundError(f"Script not found: {script}")
            env = api.build_env(settings, platform)
            if platform == "pms" and not args:
                env["PMS_DAILY_CHECKPOINT"] = "1"
            if script.name in DAILY_SCRIPTS.get(platform, ()):
                daily_path = daily_state(api, platform, env)
                day = datetime.now(SHANGHAI).date().isoformat()
                daily = api.load_json(daily_path, {})
                if daily.get(script.name) == day:
                    child["status"] = "skipped"
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write("今日已成功采集，本轮跳过。\n")
                    api.save_json(api.STATUS_PATH, status)
                    continue
            else:
                daily_path = None
            completed = api.run_streamed(
                [python, str(script), *args],
                cwd=str(script.parent if platform == "pms" else base_dir),
                env=env, timeout=api.task_timeout(settings, platform),
                log_path=log_path, append=True,
                transform=lambda line: api.sanitize(line, settings), should_cancel=api.run_stop_requested,
            )
            child["return_code"] = completed.return_code
            if completed.return_code:
                raise RuntimeError(api.first_error_line(completed.output_tail) or f"return_code={completed.return_code}")
            if platform in {"yolo", "foxhis"} and any(
                line.startswith("SKIP [") for line in completed.output_tail.splitlines()
            ):
                child["status"] = "skipped"
                api.save_json(api.STATUS_PATH, status)
                continue
            if daily_path is not None:
                daily[script.name] = day
                api.save_json(daily_path, daily)
            if platform == "bypms" and script.name == "bypms_daily_report_data.py":
                if api.backfill_bypms_hotel_name_from_summary(settings):
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write("ByPMS hotel name identified and saved to local configuration.\n")
            child["status"] = "success"
        except ProcessCancelledError:
            child.update(status="cancelled", error_summary="已由用户中断")
            raise
        except Exception as exc:
            message = api.sanitize(str(exc), settings)
            child.update(status="failed", error_summary=message)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[子采集失败] {script.name}: {message}\n")
        api.save_json(api.STATUS_PATH, status)
    failures = [child for child in children if child["status"] == "failed"]
    succeeded = any(child["status"] == "success" for child in children)
    result["status"] = ("partial_failed" if succeeded else "failed") if failures else "success"
    result["return_code"] = (failures[0]["return_code"] or 1) if failures else 0
    result["error_summary"] = "; ".join(f"{child['script']}: {child['error_summary']}" for child in failures)
    if succeeded:
        api.enrich_room_type_ids("pms_fetch", settings, log_path)
