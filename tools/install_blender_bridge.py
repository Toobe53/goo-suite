# SPDX-License-Identifier: GPL-3.0-or-later
"""Download and unpack Blender 5.2.3 for CI or local testing.

Usage::

    python tools/install_blender.py --dest /tmp/blender --json

The script prints JSON with ``path`` (the executable), ``version`` and
``archive``.  It never modifies an existing Blender installation.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "5.2.3"
URLS = {
    "windows": f"https://download.blender.org/release/Blender5.2/blender-{VERSION}-windows-x64.zip",
    "linux": f"https://download.blender.org/release/Blender5.2/blender-{VERSION}-linux-x64.tar.xz",
    "macos-arm64": f"https://download.blender.org/release/Blender5.2/blender-{VERSION}-macos-arm64.dmg",
    "macos-x64": f"https://download.blender.org/release/Blender5.2/blender-{VERSION}-macos-x64.dmg",
}


def platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "macos-x64"
    return "linux"


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)


def _find(root: Path, name: str) -> Path:
    # ``rglob("blender")`` also matches the add-on directory
    # ``.../io_scene_gltf2/blender``; only regular files are executables.
    matches = sorted(path for path in root.rglob(name) if path.is_file())
    if not matches:
        raise RuntimeError(f"{name} not found under {root}")
    return matches[0]


def unpack(archive: Path, dest: Path, key: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    if key == "windows":
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(dest)
        return _find(dest, "blender.exe")
    if key == "linux":
        # The official archive contains symlinked add-on payloads; the system
        # tar preserves those links reliably (Python's tarfile has hit
        # permission errors on some Blender 5.2 archives).
        tar = shutil.which("tar")
        if tar:
            subprocess.run([tar, "-xJf", str(archive), "-C", str(dest)], check=True)
        else:
            with tarfile.open(archive, "r:xz") as handle:
                handle.extractall(dest)
        return _find(dest, "blender")
    if key.startswith("macos"):
        mount = Path(tempfile.mkdtemp(prefix="goo-bridge-dmg-"))
        try:
            subprocess.run(
                ["hdiutil", "attach", str(archive), "-nobrowse", "-mountpoint", str(mount)],
                check=True,
                capture_output=True,
            )
            apps = sorted(mount.glob("Blender*.app"))
            if not apps:
                raise RuntimeError("no Blender.app in dmg")
            target = dest / apps[0].name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(apps[0], target)
            return target / "Contents" / "MacOS" / "Blender"
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], check=False)
    raise RuntimeError(f"unsupported platform: {key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-archive", action="store_true")
    args = parser.parse_args()
    key = platform_key()
    dest = Path(args.dest).expanduser().resolve()
    archive = dest.parent / f"blender-{VERSION}-{key}{'.zip' if key == 'windows' else '.tar.xz' if key == 'linux' else '.dmg'}"
    try:
        if not archive.is_file():
            download(URLS[key], archive)
        executable = unpack(archive, dest, key)
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=60)
        version = ""
        for line in (result.stdout or "").splitlines():
            if line.startswith("Blender "):
                version = line.split()[1]
                break
        payload = {"ok": True, "path": str(executable), "version": version, "archive": str(archive)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=True))
        else:
            print(executable)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
    finally:
        if not args.keep_archive and archive.is_file():
            try:
                archive.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
