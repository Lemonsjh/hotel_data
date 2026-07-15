from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
CONFIG_PATH = ROOT / "config" / "settings.json"
STATUS_PATH = ROOT / "state" / "status.json"
LOG_DIR = ROOT / "logs"


TASKS = {
    "meituan_business": ("meituan", "bussiness_data.py", []),
    "meituan_joined_rights": ("meituan", "meituan_joined_rights_data.py", []),
    "meituan_promotion_status": ("meituan", "meituan_promotion_status_data.py", []),
    "meituan_video_upload_status": ("meituan", "meituan_video_upload_status_data.py", []),
    "meituan_promotion_finance": ("meituan", "meituan_promotion_finance_detail.py", []),
    "meituan_exposure_source": ("meituan", "meituan_exposure_source_data.py", []),
    "meituan_order_loss": ("meituan", "meituan_order_loss_data.py", []),
    "meituan_scan_order": ("meituan", "meituan_scan_order_data.py", []),
    "meituan_user_source": ("meituan", "meituan_user_source_data.py", []),
    "meituan_review": ("meituan", "meituan_review_data.py", []),
    "meituan_review_detail": ("meituan", "meituan_review_detail_data.py", []),
    "meituan_promotion": ("meituan", "meituan_promotion_data.py", []),
    "meituan_goods_price": ("meituan", "meituan_goods_price_mapping.py", []),
    "meituan_nearby_event": ("meituan", "meituan_nearby_event_data.py", []),
    "ctrip_business": ("ctrip", "ctrip_business_data.py", []),
    "ctrip_review": ("ctrip", "ctrip_review_data.py", ["--sync-db"]),
    "ctrip_review_detail": ("ctrip", "ctrip_review_detail_data.py", ["--sync-db"]),
    "ctrip_promotion": ("ctrip", "ctrip_promotion_data.py", ["--sync-db"]),
    "ctrip_goods_price": ("ctrip", "ctrip_goods_price_mapping.py", []),
    "pms_fetch": ("pms", "fetch_main.py", []),
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        file.write(content)
        temporary = Path(file.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_settings() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing config: {CONFIG_PATH}")
    return load_json(CONFIG_PATH, {})


def project_path(value: Any, default: str | Path = ".") -> Path:
    path = Path(str(value or default))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def python_path(settings: dict[str, Any]) -> Path:
    configured = project_path(settings.get("python_path"), "runtime/python.exe")
    if configured.exists():
        return configured
    bundled = PROJECT_ROOT / "runtime" / "python.exe"
    return bundled if bundled.exists() else Path(sys.executable)


def output_path(settings: dict[str, Any]) -> Path:
    return project_path((settings.get("paths") or {}).get("output_dir"), "OTA数据")


def load_status() -> dict[str, Any]:
    return load_json(
        STATUS_PATH,
        {
            "last_run_started_at": "",
            "last_run_finished_at": "",
            "last_run_status": "never_run",
            "tasks": {},
        },
    )


def enabled_tasks(settings: dict[str, Any]) -> list[str]:
    flags = settings.get("tasks") or {}
    result: list[str] = []
    for name, (platform, _script, _args) in TASKS.items():
        if flags.get(name, True) and (settings.get(platform) or {}).get("enabled", True):
            result.append(name)
    return result


def put_if(env: dict[str, str], key: str, value: Any) -> None:
    if value is not None and str(value).strip():
        env[key] = str(value).strip()


def build_env(settings: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    hotel = settings.get("hotel") or {}
    mysql = settings.get("mysql") or {}
    meituan = settings.get("meituan") or {}
    ctrip = settings.get("ctrip") or {}
    pms = settings.get("pms") or {}

    put_if(env, "HOTEL_OTA_PROJECT_ROOT", PROJECT_ROOT)
    put_if(env, "HOTEL_OTA_OUTPUT_DIR", output_path(settings))
    browser_dir = PROJECT_ROOT / "runtime" / "playwright-browsers"
    if browser_dir.exists():
        put_if(env, "PLAYWRIGHT_BROWSERS_PATH", browser_dir)
    put_if(env, "HOTEL_ID", hotel.get("hotel_id"))
    put_if(env, "HOTEL_OTA_MYSQL_HOST", mysql.get("host"))
    put_if(env, "HOTEL_OTA_MYSQL_PORT", mysql.get("port"))
    put_if(env, "HOTEL_OTA_MYSQL_USER", mysql.get("user"))
    put_if(env, "HOTEL_OTA_MYSQL_PASSWORD", mysql.get("password"))
    put_if(env, "HOTEL_OTA_MYSQL_DATABASE", mysql.get("database"))
    put_if(env, "MYSQL_HOST", mysql.get("host"))
    put_if(env, "MYSQL_PORT", mysql.get("port"))
    put_if(env, "MYSQL_USER", mysql.get("user"))
    put_if(env, "MYSQL_PASSWORD", mysql.get("password"))
    put_if(env, "MYSQL_DATABASE", mysql.get("database"))
    put_if(env, "PMS_USERNAME", pms.get("username"))
    put_if(env, "PMS_PASSWORD", pms.get("password"))

    put_if(env, "MEITUAN_HOTEL_NAME", meituan.get("hotel_name"))
    put_if(env, "MEITUAN_POI_ID", meituan.get("poi_id"))
    put_if(env, "MEITUAN_PARTNER_ID", meituan.get("partner_id"))
    put_if(env, "MEITUAN_BIZ_ACCOUNT_ID", meituan.get("biz_account_id"))
    put_if(env, "MEITUAN_EB_COOKIE", meituan.get("eb_cookie"))
    put_if(env, "MEITUAN_ME_COOKIE", meituan.get("me_cookie"))
    put_if(env, "MEITUAN_REVIEW_CONTRAST_URL", meituan.get("review_contrast_url"))
    put_if(env, "MEITUAN_DIANPING_REVIEW_CONTRAST_URL", meituan.get("dianping_review_contrast_url"))
    put_if(env, "MEITUAN_REVIEW_RANKING_URL", meituan.get("review_ranking_url"))
    put_if(env, "MEITUAN_REVIEW_DETAIL_URL", meituan.get("review_detail_url"))
    put_if(env, "MEITUAN_DIANPING_REVIEW_DETAIL_URL", meituan.get("dianping_review_detail_url"))
    put_if(env, "MEITUAN_GOODS_QUERY_URL", meituan.get("goods_query_url"))
    put_if(env, "MEITUAN_CALC_PRICE_URL", meituan.get("calc_price_url"))
    put_if(env, "MEITUAN_PRICE_STATUS_URL", meituan.get("price_status_url"))
    put_if(env, "MEITUAN_PROMOTION_FINANCE_URL", meituan.get("promotion_finance_url"))
    put_if(env, "MEITUAN_PRICE_STATUS_PAYLOAD_FILE", meituan.get("price_status_payload_file"))

    put_if(env, "CTRIP_HOTEL_NAME", ctrip.get("hotel_name"))
    put_if(env, "CTRIP_HOTEL_ID", ctrip.get("hotel_id"))
    put_if(env, "CTRIP_COOKIE", ctrip.get("cookie"))
    put_if(env, "CTRIP_GET_HOTEL_RATING_URL", ctrip.get("rating_url"))
    put_if(env, "CTRIP_GET_COMMENT_LIST_URL", ctrip.get("comment_list_url"))
    put_if(env, "CTRIP_GET_PRO_BATCH_URL", ctrip.get("promotion_url"))
    put_if(env, "CTRIP_GOODS_QUERY_URL", ctrip.get("goods_query_url"))
    return env


def secret_values(settings: dict[str, Any]) -> list[str]:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                is_signed_url = "url" in key.lower() and isinstance(item, str) and "mtgsig=" in item
                if any(word in key.lower() for word in ("cookie", "password", "token")) or is_signed_url:
                    if isinstance(item, str) and len(item) >= 8:
                        values.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(settings)
    return sorted(set(values), key=len, reverse=True)


def sanitize(text: str, settings: dict[str, Any]) -> str:
    for value in secret_values(settings):
        text = text.replace(value, "[REDACTED]")
    return text


def script_path(settings: dict[str, Any], platform: str, filename: str) -> Path:
    paths = settings.get("paths") or {}
    if platform == "pms":
        pms = settings.get("pms") or {}
        code_dir = project_path(pms.get("code_dir"), "正式数据抓取-PMS（别样红）/PMS登录")
        return code_dir / (pms.get("entry_script") or filename)
    key = "meituan_code_dir" if platform == "meituan" else "ctrip_code_dir"
    default = "美团OTA数据采集代码" if platform == "meituan" else "携程OTA数据采集代码"
    return project_path(paths.get(key), default) / filename


def task_timeout(settings: dict[str, Any], platform: str) -> int:
    if platform == "pms":
        return int((settings.get("pms") or {}).get("timeout_seconds") or 900)
    return int((settings.get("service") or {}).get("timeout_seconds") or 300)


def run_task(name: str, settings: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    if name not in TASKS:
        raise KeyError(f"Unknown task: {name}")

    platform, filename, extra_args = TASKS[name]
    py = str(python_path(settings))
    script = script_path(settings, platform, filename)
    timeout = task_timeout(settings, platform)
    base_dir = project_path((settings.get("paths") or {}).get("base_dir"), ".")
    started = now_text()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now():%Y%m%d}_{name}.log"

    result = {
        "name": name,
        "status": "running",
        "started_at": started,
        "finished_at": "",
        "duration_seconds": 0,
        "return_code": None,
        "error_summary": "",
        "log_path": str(log_path),
    }
    status.setdefault("tasks", {})[name] = result
    save_json(STATUS_PATH, status)

    if not script.exists():
        result.update(status="failed", finished_at=now_text(), error_summary=f"Script not found: {script}")
        save_json(STATUS_PATH, status)
        return result

    command = [py, str(script), *extra_args]
    begin = datetime.now()
    try:
        completed = subprocess.run(
            command,
            cwd=str(script.parent if platform == "pms" else base_dir),
            env=build_env(settings),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = sanitize((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""), settings)
        log_path.write_text(output, encoding="utf-8")
        result["return_code"] = completed.returncode
        if completed.returncode == 0:
            result["status"] = "success"
            enrich_room_type_ids(name, settings, log_path)
        else:
            result["status"] = "failed"
            result["error_summary"] = first_error_line(output) or f"return_code={completed.returncode}"
    except subprocess.TimeoutExpired as exc:
        output = sanitize((exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else ""), settings)
        log_path.write_text(output, encoding="utf-8")
        result["status"] = "failed"
        result["error_summary"] = f"timeout after {timeout}s"
    except Exception as exc:
        result["status"] = "failed"
        result["error_summary"] = str(exc)

    result["finished_at"] = now_text()
    result["duration_seconds"] = round((datetime.now() - begin).total_seconds(), 2)
    status.setdefault("tasks", {})[name] = result
    save_json(STATUS_PATH, status)
    return result


def first_error_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    markers = ("error:", "exception", "traceback", "失败", "错误", "timeout")
    for text in reversed(lines):
        lowered = text.lower()
        if any(marker in lowered for marker in markers) and not text.startswith("File "):
            return text[:300]
    for text in reversed(lines):
        if text.startswith("- ") or text == "Call log:":
            continue
        if text:
            return text[:300]
    return ""


def enrich_room_type_ids(name: str, settings: dict[str, Any], log_path: Path) -> None:
    try:
        import room_type_enrichment

        stats = room_type_enrichment.enrich_for_task(settings, name)
        if not stats:
            return
        matched = sum(
            item["matched_by_product"] + item["matched_by_name"] for item in stats.values()
        )
        message = f"\n[room_type_id] tables={len(stats)} matched={matched}\n"
    except Exception as exc:
        message = f"\n[room_type_id] warning: {exc}\n"
    with log_path.open("a", encoding="utf-8") as file:
        file.write(message)


def pending_result(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "pending",
        "started_at": "",
        "finished_at": "",
        "duration_seconds": 0,
        "return_code": None,
        "error_summary": "",
        "log_path": "",
    }


def run_once(task_names: list[str] | None = None) -> dict[str, Any]:
    settings = load_settings()
    status = load_status()
    tasks = task_names or enabled_tasks(settings)
    status["last_run_started_at"] = now_text()
    status["last_run_finished_at"] = ""
    status["last_run_status"] = "running"
    status["last_run_tasks"] = tasks
    for name in tasks:
        status.setdefault("tasks", {})[name] = pending_result(name)
    save_json(STATUS_PATH, status)

    results = [run_task(name, settings, status) for name in tasks]
    status = load_status()
    status["last_run_finished_at"] = now_text()
    status["last_run_status"] = "success" if all(item["status"] == "success" for item in results) else "partial_failed"
    save_json(STATUS_PATH, status)
    return status


def config_warnings(settings: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    py = python_path(settings)
    if not py.exists():
        warnings.append(f"Python path not found: {py}")
    for platform, key in (("meituan", "meituan_code_dir"), ("ctrip", "ctrip_code_dir")):
        default = "美团OTA数据采集代码" if platform == "meituan" else "携程OTA数据采集代码"
        code_dir = project_path((settings.get("paths") or {}).get(key), default)
        if not code_dir.exists():
            warnings.append(f"{platform} code dir not found: {code_dir}")
    pms = settings.get("pms") or {}
    if pms.get("enabled", True):
        pms_script = script_path(settings, "pms", "fetch_main.py")
        if not pms_script.exists():
            warnings.append(f"pms entry script not found: {pms_script}")
        if not pms.get("username"):
            warnings.append("PMS username is empty.")
        if not pms.get("password"):
            warnings.append("PMS password is empty.")
    if not (settings.get("meituan") or {}).get("me_cookie"):
        warnings.append("MEITUAN_ME_COOKIE is empty; Meituan collection cannot run.")
    if not (settings.get("ctrip") or {}).get("cookie"):
        warnings.append("CTRIP_COOKIE is empty; Ctrip collection cannot run.")
    if not (settings.get("mysql") or {}).get("password"):
        warnings.append("MySQL password is empty; original script fallback or environment may be used.")
    return warnings


def print_status() -> None:
    settings = load_settings()
    data = load_status()
    data["config_warnings"] = config_warnings(settings)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Hotel OTA collection runner.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once")
    task_parser = sub.add_parser("run-task")
    task_parser.add_argument("task", choices=sorted(TASKS))
    sub.add_parser("status")
    args = parser.parse_args()

    if args.command == "run-once":
        result = run_once()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("last_run_status") == "success" else 2
    elif args.command == "run-task":
        result = run_once([args.task])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("last_run_status") == "success" else 2
    elif args.command == "status":
        print_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
