"""Explicit, interactive Gitee Releases updater for Windows deployments."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

import certifi

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_core import apply_files, atomic_write, finish, prepare, read_json, restore
from package_format import ASSET_NAME, MAX_BYTES, REPOSITORY, digest, validate_package, version

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "OTA采集服务"
LOCK_NAME = "update.lock"
GITEE_API = f"https://gitee.com/api/v5/repos/{REPOSITORY}"
CHECKSUM_NAME = ASSET_NAME.replace(".zip", ".sha256")
PROGRESS = None


def progress(message: str) -> None:
    if PROGRESS:
        PROGRESS(message)


def https_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # 补充便携环境的公共 CA，保留系统/企业证书和主机名校验。
    context.load_verify_locations(cafile=certifi.where())
    return context


def download(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "HotelCollector-Updater/1", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30, context=https_context()) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure download redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds size limit")
    return data


def release_asset_urls(release: dict) -> dict[str, str]:
    required = {ASSET_NAME, CHECKSUM_NAME}
    urls = {}
    for item in release.get("assets", []):
        name = item.get("name")
        if name not in required:
            continue
        if name in urls:
            raise ValueError("Duplicate Gitee update attachment: " + name)
        url = item.get("browser_download_url", "")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 443)
                or not (host == "gitee.com" or host.endswith(".gitee.com"))):
            raise ValueError("Invalid Gitee attachment URL: " + name)
        urls[name] = url
    if urls.keys() != required:
        raise ValueError("Gitee release update attachments are not ready: " + ", ".join(sorted(required - urls.keys())))
    return urls


def latest_release() -> dict:
    try:
        release = json.loads(download(f"{GITEE_API}/releases/latest", 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("No public Gitee Release is available yet. Existing service was not changed.") from None
        raise
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise ValueError("Not a stable Gitee release")
    version(release["tag_name"])
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("Invalid Gitee release assets")
    required = {ASSET_NAME, CHECKSUM_NAME}
    if not required <= {item.get("name") for item in assets}:
        release_id = release.get("id")
        if type(release_id) is not int or release_id <= 0:
            raise ValueError("Gitee release update attachments are not ready")
        assets = list(assets)
        for page in range(1, 11):
            url = f"{GITEE_API}/releases/{release_id}/attach_files?page={page}&per_page=100"
            attachments = json.loads(download(url, 1024 * 1024))
            if not isinstance(attachments, list):
                raise ValueError("Invalid Gitee release attachments")
            assets.extend(attachments)
            if required <= {item.get("name") for item in assets} or len(attachments) < 100:
                break
        release["assets"] = assets
    release_asset_urls(release)
    return release


def service(root: Path, guard: Path, snapshot: Path, action: str) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(guard),
         "-Root", str(root), "-ServiceDir", SERVICE, "-Snapshot", str(snapshot), "-Action", action],
        check=True, timeout=50,
    )


def panel_health(root: Path, snapshot: Path) -> None:
    if not any(item["script"] == "panel.py" for item in read_json(snapshot).get("processes", [])):
        return
    settings = read_json(root / SERVICE / "config" / "settings.json")
    port = int((settings.get("service") or {}).get("panel_port", 8765))
    url = f"http://127.0.0.1:{port}/config"
    for _ in range(10):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError("Updated panel health check failed")


def apply_update(root: Path, package: Path, tag: str, work: Path) -> None:
    manifest, content = validate_package(package, root, tag)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = root / "updater" / "backups" / stamp
    backup.mkdir(parents=True)
    snapshot = backup / "services.json"
    # Freeze lifecycle scripts before replacing updater code itself.
    guard = work / "service_guard.ps1"
    smoke = work / "smoke_check.py"
    shutil.copy2(root / "updater" / guard.name, guard)
    shutil.copy2(root / "updater" / smoke.name, smoke)
    service(root, guard, snapshot, "Inspect")
    plan = prepare(root, backup, manifest, content)
    pending = root / "updater" / "pending.json"
    atomic_write(pending, json.dumps({"backup": stamp}).encode("ascii"))
    atomic_write(work / "resume.json", json.dumps({"snapshot": str(snapshot)}).encode("utf-8"))
    suspended = False
    applied = False
    try:
        suspended = True  # Suspend may stop some components before reporting failure.
        progress("正在停止面板、安装代码并重新启动…")
        service(root, guard, snapshot, "Suspend")
        applied = True
        apply_files(root, manifest, content, plan)
        subprocess.run([sys.executable, str(smoke), str(root)], check=True, timeout=30)
        service(root, guard, snapshot, "Panel")
        panel_health(root, snapshot)
        finish(root, backup, manifest, plan)
    except Exception:
        if suspended:
            service(root, guard, snapshot, "Undo")
        if applied:
            restore(root, backup, plan)
        if suspended:
            service(root, guard, snapshot, "Panel")
        pending.unlink(missing_ok=True)
        # Caller releases maintenance lock before restoring schedulers.
        raise
    pending.unlink(missing_ok=True)
    print(f"Update installed: {manifest['version']}; backup: updater/backups/{stamp}")
    if any(name.startswith("extensions/meituan-login-sync/") for name in content):
        print("Browser extension files updated. Reload the extension in Edge/Chrome; first-time installation still requires Load unpacked.")


def recover(root: Path, work: Path) -> Path:
    pending = root / "updater" / "pending.json"
    stamp = read_json(pending).get("backup", "")
    if not stamp or any(char not in "0123456789-" for char in stamp):
        raise ValueError("Invalid recovery record")
    backup = root / "updater" / "backups" / stamp
    plan = read_json(backup / "plan.json")
    guard = work / "service_guard.ps1"
    shutil.copy2(root / "updater" / guard.name, guard)
    snapshot = backup / "services.json"
    service(root, guard, snapshot, "Undo")
    restore(root, backup, plan)
    service(root, guard, snapshot, "Panel")
    pending.unlink()
    print("Recovered program files. Database and local configuration were not changed.")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Ask before installing the latest official Release")
    parser.add_argument("--recover", action="store_true", help="Recover files after an interrupted update")
    parser.add_argument("--yes", action="store_true", help="Use a confirmation already obtained by the local panel")
    parser.add_argument("--expected-version", help="Refuse if the release changed since panel confirmation")
    args = parser.parse_args()
    current = read_json(ROOT / "updater" / "installed.json").get("version", "0.0.0")
    if not args.recover:
        release = latest_release()
        tag = release["tag_name"]
        if args.expected_version and tag != args.expected_version:
            raise ValueError("最新版本已变化，请重新检查并确认更新。")
        print(f"Installed: {current}; latest official Gitee Release: {tag}")
        if version(tag) <= version(current):
            print("Already up to date; no downgrade will be performed.")
            return 0
        if not args.apply:
            print("Check only; pass --apply to install.")
            return 0
    if os.name != "nt":
        raise ValueError("Automatic installation supports Windows deployments only")
    if not args.yes and input("Back up and update program files? Configuration/data are kept. [y/N] ").strip().lower() != "y":
        return 0
    lock = ROOT / "updater" / LOCK_NAME
    # Exclusive file lock; a crash leaves a visible lock requiring explicit recovery.
    if args.recover and lock.exists():
        raise ValueError("Confirm no updater is running, then remove updater/update.lock before --recover")
    with lock.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    work = ROOT / "updater" / "work" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work.mkdir(parents=True)
    resume = None
    try:
        if args.recover:
            resume = recover(ROOT, work)
        else:
            if (ROOT / "updater" / "pending.json").exists():
                raise ValueError("An interrupted update exists; use --recover first")
            urls = release_asset_urls(release)
            progress("正在下载并校验更新包…")
            checksum = download(urls[CHECKSUM_NAME], 256).decode("ascii").split()[0]
            data = download(urls[ASSET_NAME], MAX_BYTES)
            if digest(data) != checksum:
                raise ValueError("Release asset checksum mismatch; existing service was not changed")
            package = work / ASSET_NAME
            package.write_bytes(data)
            try:
                progress("正在检查任务空闲状态并备份代码…")
                apply_update(ROOT, package, tag, work)
            finally:
                # Only resume if rollback/install fully completed, never during pending recovery.
                resume_path = read_json(work / "resume.json").get("snapshot")
                if resume_path and not (ROOT / "updater" / "pending.json").exists():
                    resume = Path(resume_path)
    finally:
        lock.unlink(missing_ok=True)
        if resume:
            service(ROOT, work / "service_guard.ps1", resume, "Schedulers")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Update failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        raise SystemExit(1)
