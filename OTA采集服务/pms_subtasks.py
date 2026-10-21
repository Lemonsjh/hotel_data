"""PMS 串行子采集：隔离单张报表故障，保留中断和整体失败语义。"""
from process_runner import ProcessCancelledError


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
            completed = api.run_streamed(
                [python, str(script), *args],
                cwd=str(script.parent if platform == "pms" else base_dir),
                env=api.build_env(settings, platform), timeout=api.task_timeout(settings, platform),
                log_path=log_path, append=True,
                transform=lambda line: api.sanitize(line, settings), should_cancel=api.run_stop_requested,
            )
            child["return_code"] = completed.return_code
            if completed.return_code:
                raise RuntimeError(api.first_error_line(completed.output_tail) or f"return_code={completed.return_code}")
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
