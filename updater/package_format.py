"""Strict code-only release format shared by publisher and updater."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "Lemonsjh/hotel_data"
ASSET_NAME = "hotel-collector-update.zip"
MAX_BYTES = 50 * 1024 * 1024
CODE_DIRS = {
    "OTA采集服务", "美团OTA数据采集代码", "携程OTA数据采集代码",
    "宝寓PMS数据采集代码", "别样红PMS数据采集代码", "正式数据抓取-PMS（别样红）", "updater",
}
ROOT_FILES = {"mysql_connection.py", "ota_mysql_writer.py", "update_guard.py", "一键更新.bat"}
BLOCKED = {"config", "state", "logs", "output", "__pycache__", "browser_profiles", "user-data", "backups", "work"}
EXTENSIONS = {".py", ".ps1", ".bat", ".sql", ".css", ".html", ".js"}
BROWSER_EXTENSION_FILES = {
    "extensions/meituan-login-sync/" + name for name in (
        "manifest.json", "README.md", "browser_verify.js", "cookies.js",
        "popup.css", "popup.html", "popup.js", "worker.js",
    )
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dependency_digest(data: bytes) -> str:
    return digest(b"\n".join(data.splitlines()) + b"\n")


def version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if not match:
        raise ValueError("Version must be vMAJOR.MINOR.PATCH")
    return tuple(map(int, match.groups()))


def allowed_path(value: str) -> str:
    if not isinstance(value, str) or "\\" in value or value != str(PurePosixPath(value)):
        raise ValueError("Noncanonical package path")
    parts = PurePosixPath(value).parts
    if not parts or any(
        part in {".", ".."} or part.endswith((".", " ")) or
        re.search(r'[<>:"|?*\x00-\x1f]', part) or
        re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise ValueError("Unsafe package path")
    if any(part.lower() in BLOCKED for part in parts):
        raise ValueError("Protected deployment data path")
    if value not in ROOT_FILES | BROWSER_EXTENSION_FILES and not (
        len(parts) > 1 and parts[0] in CODE_DIRS and PurePosixPath(value).suffix in EXTENSIONS
    ):
        raise ValueError("Path is not an allowed program file: " + value)
    return value


def destination(root: Path, relative: str) -> Path:
    allowed_path(relative)
    root = root.resolve()
    target = root.joinpath(*PurePosixPath(relative).parts)
    for path in (target, *target.parents):
        if path == root:
            break
        if path.exists() and (path.is_symlink() or getattr(path, "is_junction", lambda: False)()):
            raise ValueError("Symlink/junction deployment path is not supported")
        if path.exists() and getattr(path.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse-point deployment path is not supported")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Package escaped deployment root")
    return target


def validate_package(path: Path, root: Path, expected_version: str | None = None) -> tuple[dict, dict[str, bytes]]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Update package too large")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError("Duplicate/case-colliding archive paths")
        if sum(item.file_size for item in entries) > MAX_BYTES:
            raise ValueError("Expanded package too large")
        if any(item.flag_bits & 1 or stat.S_ISLNK(item.external_attr >> 16) for item in entries):
            raise ValueError("Encrypted/link archive entry")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != 1 or manifest.get("repository") != REPOSITORY:
            raise ValueError("Unsupported update manifest")
        if manifest.get("schema") or manifest.get("migrations"):
            raise ValueError("Code-only updater does not accept database migrations")
        version(manifest["version"])
        if expected_version and version(manifest["version"]) != version(expected_version):
            raise ValueError("Release tag and package version disagree")
        if manifest.get("python") != f"{sys.version_info.major}.{sys.version_info.minor}":
            raise ValueError("Python runtime differs; install a new portable runtime first")
        lock = root / "requirements-portable.lock.txt"
        if not lock.is_file() or dependency_digest(lock.read_bytes()) != manifest.get("dependency_sha256"):
            raise ValueError("Dependencies differ; install a new portable runtime first")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or set(names) != {"manifest.json", *files}:
            raise ValueError("Package file list differs from manifest")
        required = {"OTA采集服务/runner.py", "OTA采集服务/panel.py", "mysql_connection.py", "ota_mysql_writer.py"}
        if not required <= files.keys():
            raise ValueError("Incomplete program package")
        removed = manifest.get("removed", [])
        if not isinstance(removed, list) or len(set(removed)) != len(removed):
            raise ValueError("Invalid removal list")
        all_paths = [*files, *removed]
        if len(set(p.casefold() for p in all_paths)) != len(all_paths):
            raise ValueError("File/removal collision")
        folded = {name.casefold() for name in all_paths}
        if any(str(parent).casefold() in folded for name in all_paths for parent in PurePosixPath(name).parents):
            raise ValueError("File/directory prefix collision")
        for relative in all_paths:
            destination(root, relative)
        content = {}
        for relative, checksum in files.items():
            data = archive.read(relative)
            if digest(data) != checksum:
                raise ValueError("File checksum mismatch: " + relative)
            if relative.endswith(".py"):
                compile(data, relative, "exec")
            content[relative] = data
        extension_files = BROWSER_EXTENSION_FILES & content.keys()
        if extension_files:
            if extension_files != BROWSER_EXTENSION_FILES:
                raise ValueError("Incomplete browser extension package")
            extension = json.loads(content["extensions/meituan-login-sync/manifest.json"])
            if extension.get("manifest_version") != 3 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", str(extension.get("version", ""))):
                raise ValueError("Invalid browser extension manifest")
        return manifest, content
