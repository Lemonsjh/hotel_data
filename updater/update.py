"""Explicit, interactive GitHub Releases updater for Windows deployments."""
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


def https_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # 补充便携环境的公共 CA，保留系统/企业证书和主机名校验。
    context.load_verify_locations(cafile=certifi.where())
    return context


def download(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "HotelCollector-Updater/1", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30, context=https_context()) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure download redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds size limit")
    return data


def latest_release() -> dict:
    try:
        release = json.loads(download(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("No official Release is available yet. Existing service was not changed.") from None
        if exc.code in {403, 429}:
            # Official stable-release redirect avoids anonymous API rate limits.
            request = urllib.request.Request(f"https://github.com/{REPOSITORY}/releases/latest",
                                             headers={"User-Agent": "HotelCollector-Updater/1"})
            with urllib.request.urlopen(request, timeout=30, context=https_context()) as response:
                target = urlparse(response.url)
            prefix = f"/{REPOSITORY}/releases/tag/"
            if target.scheme != "https" or target.netloc != "github.com" or not target.path.startswith(prefix):
                raise ValueError("No stable Release redirect is available") from None
            tag = target.path[len(prefix):]
            version(tag)
            release = {"tag_name": tag, "assets": [{"name": ASSET_NAME},
                       {"name": ASSET_NAME.replace(".zip", ".sha256")}]}
        else:
            raise
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Not a stable release")
    version(release["tag_name"])
    names = [item["name"] for item in release.get("assets", [])]
    if ASSET_NAME not in names or ASSET_NAME.replace(".zip", ".sha256") not in names:
        raise ValueError("Release update assets are not ready; service was not changed")
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
    args = parser.parse_args()
    current = read_json(ROOT / "updater" / "installed.json").get("version", "0.0.0")
    if not args.recover:
        release = latest_release()
        tag = release["tag_name"]
        print(f"Installed: {current}; latest official Release: {tag}")
        if version(tag) <= version(current):
            print("Already up to date; no downgrade will be performed.")
            return 0
        if not args.apply:
            print("Check only; pass --apply to install.")
            return 0
    if os.name != "nt":
        raise ValueError("Automatic installation supports Windows deployments only")
    if input("Back up and update program files? Configuration/data are kept. [y/N] ").strip().lower() != "y":
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
            base = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
            checksum = download(base + ASSET_NAME.replace(".zip", ".sha256"), 256).decode("ascii").split()[0]
            data = download(base + ASSET_NAME, MAX_BYTES)
            if digest(data) != checksum:
                raise ValueError("Release asset checksum mismatch; existing service was not changed")
            package = work / ASSET_NAME
            package.write_bytes(data)
            try:
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
