"""Build an update asset from tracked program files, never local configuration."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_format import ASSET_NAME, BROWSER_EXTENSION_FILES, REPOSITORY, allowed_path, dependency_digest, digest, validate_package, version


def build(root: Path, output: Path, release_version: str, removed: list[str], *, include_browser_extension: bool = False) -> Path:
    version(release_version)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    files = {}
    for relative in filter(None, tracked):
        if relative in BROWSER_EXTENSION_FILES and not include_browser_extension:
            continue
        try:
            allowed_path(relative)
        except ValueError:
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("Tracked program file missing or symlink: " + relative)
        files[relative] = path.read_bytes()
    manifest = {
        "format": 1, "repository": REPOSITORY, "version": release_version,
        "python": "3.12", "dependency_sha256": dependency_digest((root / "requirements-portable.lock.txt").read_bytes()),
        "files": {name: digest(data) for name, data in sorted(files.items())},
        "removed": removed,
    }
    output.mkdir(parents=True, exist_ok=True)
    path = output / ASSET_NAME
    if path.exists():
        raise FileExistsError(path)
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    validate_package(path, root, release_version)
    path.with_suffix(".sha256").write_text(digest(path.read_bytes()) + "  " + ASSET_NAME + "\n", encoding="ascii")
    print(f"Built {path.name}: version={release_version}, files={len(files)}")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--remove", action="append", default=[])
    parser.add_argument("--include-browser-extension", action="store_true",
                        help="Only after deployed updaters support extension paths; omitted for the transition release")
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.output, args.version, args.remove,
          include_browser_extension=args.include_browser_extension)
