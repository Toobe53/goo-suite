# SPDX-License-Identifier: GPL-3.0-or-later
"""Small dependency free helpers shared by the CLI modules."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

INVALID_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
SIZE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def json_dumps(data: Any, *, pretty: bool = False, ensure_ascii: bool = True) -> str:
    """Deterministic JSON (sorted keys, fixed separators)."""
    if pretty:
        return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=ensure_ascii, default=str)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii, default=str)


def read_json(path: os.PathLike[str] | str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: os.PathLike[str] | str, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


def parse_version(text: str) -> Optional[tuple[int, ...]]:
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def version_text(version: Optional[tuple[int, ...]]) -> str:
    if not version:
        return "unknown"
    return ".".join(str(part) for part in version)


def read_addon_version(addon_dir: os.PathLike[str] | str) -> Optional[str]:
    """Read ``ADDON_VERSION`` from ``goo_npr/__init__.py`` without importing it.

    Importing the add-on requires ``bpy``, so the version is parsed from the AST
    instead.  Returns a dotted string such as ``"0.1.0"`` or ``None``.
    """
    init_py = Path(addon_dir) / "__init__.py"
    if not init_py.is_file():
        return None
    try:
        tree = ast.parse(init_py.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "ADDON_VERSION":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
                if isinstance(value, (tuple, list)):
                    return ".".join(str(part) for part in value)
                return str(value)
    return None


def safe_filename(name: str, fallback: str = "default") -> str:
    """Turn a light-group name into a portable file name stem."""
    original = (name or "").strip()
    cleaned = INVALID_FILENAME.sub("_", original).strip("._-")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if not cleaned:
        cleaned = fallback
    if cleaned != original:
        # keep a short stable suffix so ``Key Light`` and ``Key_Light`` do not clash
        digest = 0
        for char in original:
            digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
        cleaned = f"{cleaned}_{digest % 0xFFFF:04x}"
    return cleaned[:96]


def unique_names(names: list[str]) -> list[str]:
    """Resolve case-insensitive duplicates (Windows file systems)."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        key = name.lower()
        count = seen.get(key, 0)
        seen[key] = count + 1
        result.append(name if count == 0 else f"{name}_{count + 1}")
    return result


def human_bytes(size: float) -> str:
    value = float(size)
    for unit in SIZE_UNITS:
        if abs(value) < 1024.0 or unit == SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TiB"


def is_windows() -> bool:
    return os.name == "nt"


def platform_name() -> str:
    if sys.platform.startswith("win"):
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


def shorten_path(path: os.PathLike[str] | str, width: int = 64) -> str:
    text = str(path)
    if len(text) <= width:
        return text
    keep = max(8, width - 3)
    front = keep // 3
    back = keep - front
    return f"{text[:front]}...{text[-back:]}"


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 1.0:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60.0:
        return f"{seconds:.2f} s"
    minutes, rest = divmod(seconds, 60.0)
    if minutes < 60.0:
        return f"{int(minutes)}m {rest:04.1f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h {minutes:02d}m"


def ensure_dir(path: os.PathLike[str] | str) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_text(text: str) -> str:
    """Replace characters that the legacy Windows console cannot encode."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural_form or singular + 's'}"
