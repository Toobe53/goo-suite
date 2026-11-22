# SPDX-License-Identifier: GPL-3.0-or-later
"""Find and drive a local Blender installation.

Blender is treated as an external command.  Nothing in this module imports
``bpy``; scripts that need Blender run in a child process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence


class BlenderError(RuntimeError):
    """Raised when Blender cannot be found or exits unsuccessfully."""


@dataclass
class BlenderInfo:
    path: Path
    version: str = ""
    major: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "version": self.version,
            "major": list(self.major),
        }


def _candidate_paths(explicit: Optional[str] = None) -> Iterable[Path]:
    if explicit:
        yield Path(explicit).expanduser()
    for env_name in ("GOO_BRIDGE_BLENDER", "BLENDER"):
        value = os.environ.get(env_name)
        if value:
            yield Path(value).expanduser()

    if sys.platform == "win32":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramW6432", r"C:\Program Files")),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        ]
        for root in roots:
            if not str(root):
                continue
            base = root / "Blender Foundation"
            if base.is_dir():
                for child in sorted(base.iterdir(), reverse=True):
                    if child.is_dir():
                        yield child / "blender.exe"
        yield Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    elif sys.platform == "darwin":
        yield Path("/Applications/Blender.app/Contents/MacOS/Blender")
        home = Path.home() / "Applications"
        if home.is_dir():
            for child in sorted(home.glob("Blender*.app"), reverse=True):
                yield child / "Contents" / "MacOS" / "Blender"
    else:
        found = shutil.which("blender")
        if found:
            yield Path(found)
        for pattern in ("/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"):
            yield Path(pattern)
        opt = Path("/opt")
        if opt.is_dir():
            for child in sorted(opt.glob("blender*"), reverse=True):
                yield child / "blender"


def _read_version(path: Path) -> str:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (result.stdout or "").splitlines():
        match = re.search(r"Blender\s+([0-9][0-9A-Za-z.\-]*)", line)
        if match:
            return match.group(1)
    return ""


def find_blender(explicit: Optional[str] = None) -> BlenderInfo:
    """Return the first usable Blender executable.

    The version check both validates the executable and gives callers a
    chance to reject an old Blender before a render starts.
    """
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            version = _read_version(candidate)
            if version:
                parts = tuple(int(part) for part in re.findall(r"\d+", version)[:2])
                return BlenderInfo(candidate.resolve(), version, parts)
        raise BlenderError(f"Blender not found or not executable: {explicit}")

    tried: list[str] = []
    for candidate in _candidate_paths(None):
        if not candidate:
            continue
        tried.append(str(candidate))
        if not candidate.is_file():
            continue
        version = _read_version(candidate)
        if version:
            parts = tuple(int(part) for part in re.findall(r"\d+", version)[:2])
            return BlenderInfo(candidate.resolve(), version, parts)
    raise BlenderError(
        "Blender 5.2 was not found. Set GOO_BRIDGE_BLENDER to blender.exe. "
        "Tried: " + ", ".join(tried[:8])
    )


def require_blender_5(info: BlenderInfo, minimum: tuple[int, int] = (5, 2)) -> None:
    if info.major and info.major < minimum:
        raise BlenderError(
            f"Blender {info.version} is too old; GOO Bridge requires "
            f"{minimum[0]}.{minimum[1]} or newer"
        )


def run_blender(
    blender: BlenderInfo | Path | str,
    arguments: Sequence[str],
    *,
    timeout: int = 3600,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess[str]:
    path = blender.path if isinstance(blender, BlenderInfo) else Path(blender)
    command = [str(path), *arguments]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BlenderError(f"Blender timed out after {timeout}s: {' '.join(command)}") from exc
    except OSError as exc:
        raise BlenderError(f"could not start Blender: {exc}") from exc


def blender_python(blender: BlenderInfo) -> Optional[Path]:
    """Return Blender's bundled Python executable when it can be located."""
    path = blender.path
    if sys.platform == "win32":
        candidate = path.parent / f"{blender.major[0]}.{blender.major[1]}" / "python" / "bin" / "python.exe"
        if candidate.is_file():
            return candidate
    elif sys.platform == "darwin":
        candidate = path.parent.parent / "Resources" / f"{blender.major[0]}.{blender.major[1]}" / "python" / "bin" / "python3"
        if candidate.is_file():
            return candidate
    else:
        candidate = path.parent / f"{blender.major[0]}.{blender.major[1]}" / "python" / "bin" / "python3"
        if candidate.is_file():
            return candidate
    return None
