# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable configuration adapter for the unified ``goo`` CLI.

The authoritative file is :mod:`goo_bridge.config` (``GOO Suite``).  This
module keeps the historical dictionary based API used by the NPR pipeline and
adds the optional ``./.goo/config.json`` project override.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from goo_bridge import config as suite_config

from .errors import ConfigError
from .util import json_dumps

CONFIG_DIRNAME = "GOO Suite"
LOCAL_DIRNAME = ".goo"
CONFIG_FILENAME = "config.json"

DEFAULTS: dict[str, Any] = {
    "blender": None,
    "addon_path": None,
    "output_dir": None,
    "samples": None,
    "format": "exr",
    "resolution_percentage": None,
}

KNOWN_KEYS = tuple(DEFAULTS)


def home_directory() -> Path:
    return suite_config.home_directory()


def user_config_path() -> Path:
    return suite_config.config_path()


def local_config_path(cwd: Optional[os.PathLike[str] | str] = None) -> Path:
    return Path(cwd or os.getcwd()) / LOCAL_DIRNAME / CONFIG_FILENAME


def config_paths(cwd: Optional[os.PathLike[str] | str] = None) -> list[Path]:
    return [local_config_path(cwd), user_config_path()]


def _to_dict(config: suite_config.Config) -> dict[str, Any]:
    return {
        "blender": config.blender or None,
        "addon_path": config.addon_path or None,
        "output_dir": config.output_dir or None,
        "samples": config.samples,
        "format": config.format,
        "resolution_percentage": config.resolution_percentage,
    }


def load_config(cwd: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    """Load merged configuration (defaults + user + local)."""
    config = suite_config.effective_config(Path(cwd) if cwd else None)
    return _to_dict(config)


def save_config(
    updates: dict[str, Any],
    *,
    scope: str = "user",
    cwd: Optional[os.PathLike[str] | str] = None,
) -> Path:
    """Merge *updates* into one config file and return its path."""
    if scope not in ("user", "local"):
        raise ConfigError(f"unknown config scope {scope!r}", hint="Use --scope user or --scope local")
    for key in updates:
        if key not in KNOWN_KEYS:
            raise ConfigError(f"unknown config key {key!r}", hint=f"Known keys: {', '.join(KNOWN_KEYS)}")

    if scope == "local":
        path = local_config_path(cwd)
        current: dict[str, Any] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise ConfigError(f"cannot read config file {path}: {exc}") from exc
            if isinstance(data, dict):
                current = data
        for key, value in updates.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        _validate_paths(current)
        path.parent.mkdir(parents=True, exist_ok=True)
        suite_config.atomic_write_text(path, json.dumps(current, indent=2, sort_keys=True) + "\n")
        return path

    config = suite_config.load_config()
    for key, value in updates.items():
        if value is None:
            setattr(config, key, "" if key in {"blender", "addon_path", "output_dir"} else None)
        else:
            setattr(config, key, value)
    _validate_config(config)
    return suite_config.save_config(config)


def _validate_paths(data: dict[str, Any]) -> None:
    blender = data.get("blender")
    if blender and not Path(blender).is_file():
        raise ConfigError(
            f"cannot store blender path {blender!r}: file does not exist",
            hint="Pass the full path to blender.exe / 请给出 blender.exe 的完整路径",
        )
    addon_path = data.get("addon_path")
    if addon_path and not Path(addon_path).exists():
        raise ConfigError(f"cannot store addon_path {addon_path!r}: directory does not exist")


def _validate_config(config: suite_config.Config) -> None:
    _validate_paths({"blender": config.blender, "addon_path": config.addon_path})


def describe(config: Optional[dict[str, Any]] = None) -> str:
    cfg = config if config is not None else load_config()
    return json_dumps(cfg, ensure_ascii=False)
