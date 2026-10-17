"""Recoverable code-file transaction. Configuration/runtime are never managed."""
from __future__ import annotations

import json
import os
from pathlib import Path

from package_format import destination, digest


def read_json(path: Path, default: dict | None = None) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else (default or {})


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".update-tmp")
    if temporary.exists():
        raise FileExistsError("Stale update temporary file: " + str(temporary))
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(root: Path, backup: Path, manifest: dict, content: dict[str, bytes]) -> dict:
    installed_path = root / "updater" / "installed.json"
    previous = read_json(installed_path)
    managed = previous.get("files", {})
    # A removal may only target files managed by an earlier successful update.
    removed = [name for name in manifest.get("removed", []) if name in managed]
    skipped = set(manifest.get("removed", [])) - set(removed)
    if skipped:
        print(f"Skipped {len(skipped)} removals not owned by a previous update.")
    plan = {"version": manifest["version"], "files": {}, "previous_installed": previous,
            "complete": False}
    for relative in [*content, *removed]:
        path = destination(root, relative)
        original = path.read_bytes() if path.exists() else None
        if relative in managed and original is not None and digest(original) != managed[relative]:
            raise ValueError("Locally modified program file; refusing overwrite: " + relative)
        plan["files"][relative] = digest(original) if original is not None else None
        if original is not None:
            copy = backup / "files" / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(original)
    atomic_write(backup / "plan.json", json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8"))
    return plan


def apply_files(root: Path, manifest: dict, content: dict[str, bytes], plan: dict) -> None:
    for relative, data in content.items():
        atomic_write(destination(root, relative), data)
    for relative in manifest.get("removed", []):
        if relative in plan["files"]:
            destination(root, relative).unlink(missing_ok=True)


def restore(root: Path, backup: Path, plan: dict) -> None:
    for relative, checksum in plan["files"].items():
        path = destination(root, relative)
        if checksum is None:
            path.unlink(missing_ok=True)
        else:
            original = (backup / "files" / relative).read_bytes()
            if digest(original) != checksum:
                raise ValueError("Backup checksum mismatch: " + relative)
            atomic_write(path, original)
    installed = root / "updater" / "installed.json"
    if plan["previous_installed"]:
        atomic_write(installed, json.dumps(plan["previous_installed"]).encode("utf-8"))
    else:
        installed.unlink(missing_ok=True)


def finish(root: Path, backup: Path, manifest: dict, plan: dict) -> None:
    installed = {"version": manifest["version"], "files": manifest["files"], "backup": backup.name}
    atomic_write(root / "updater" / "installed.json", json.dumps(installed, ensure_ascii=False).encode("utf-8"))
    plan["complete"] = True
    atomic_write(backup / "plan.json", json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8"))
