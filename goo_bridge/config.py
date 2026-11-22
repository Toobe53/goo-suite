# SPDX-License-Identifier: GPL-3.0-or-later
"""Unified configuration and platform paths for GOO Suite.

One JSON file is shared by the CLI, the GUI, the render queue and the remote
transport.  Existing GOO Bridge installs are read transparently; the next
explicit save migrates them to the new location.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text

APP_NAME = "GOO Suite"
CONFIG_VERSION = 2
LEGACY_APP_NAME = "GOO Bridge"


def _home_path() -> Path:
    return Path.home()


def _platform_home(app_name: str) -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / app_name
        return _home_path() / "AppData" / "Roaming" / app_name
    if sys.platform == "darwin":
        return _home_path() / "Library" / "Application Support" / app_name
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / app_name.lower().replace(" ", "-")
    return _home_path() / ".config" / app_name.lower().replace(" ", "-")


def home_directory() -> Path:
    """Return the per-user GOO Suite state directory.

    ``GOO_SUITE_HOME`` takes precedence.  ``GOO_BRIDGE_HOME`` remains supported
    for existing scripts and for tests that isolate the old state directory.
    """
    override = os.environ.get("GOO_SUITE_HOME") or os.environ.get("GOO_BRIDGE_HOME")
    if override:
        return Path(override).expanduser()
    return _platform_home(APP_NAME)


def config_path() -> Path:
    return home_directory() / "config.json"


def legacy_home_directory() -> Path:
    override = os.environ.get("GOO_BRIDGE_HOME")
    if override:
        return Path(override).expanduser()
    return _platform_home(LEGACY_APP_NAME)


def legacy_config_path() -> Path:
    return legacy_home_directory() / "config.json"


def _as_port(value: Any, default: int = 22) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class RemoteProfile:
    """A remote render host as stored in ``config.json``."""

    name: str
    host: str
    user: str
    port: int = 22
    key: str = ""
    blender: str = "blender"
    workspace: str = "~/.goo-suite"
    python: str = "python3"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteProfile":
        return cls(
            name=str(data.get("name", "")),
            host=str(data.get("host", "")),
            user=str(data.get("user", "")),
            port=_as_port(data.get("port", 22)),
            key=str(data.get("key", "")),
            blender=str(data.get("blender", "blender")),
            workspace=str(data.get("workspace", "~/.goo-suite")),
            python=str(data.get("python", "python3")),
        )


@dataclass
class Config:
    blender: str = ""
    addon_path: str = ""
    output_dir: str = ""
    samples: int | None = None
    format: str = "exr"
    resolution_percentage: int | None = None
    language: str = "en_US"
    log_level: str = "INFO"
    remote: list[RemoteProfile] = field(default_factory=list)
    version: int = CONFIG_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["remote"] = [profile.to_dict() for profile in self.remote]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        profiles = [
            RemoteProfile.from_dict(item)
            for item in data.get("remote", [])
            if isinstance(item, dict) and item.get("name")
        ]
        try:
            version = int(data.get("version", CONFIG_VERSION))
        except (TypeError, ValueError):
            version = CONFIG_VERSION
        fmt = str(data.get("format", "exr")).lower()
        if fmt not in {"exr", "png"}:
            fmt = "exr"
        return cls(
            blender=str(data.get("blender", "") or ""),
            addon_path=str(data.get("addon_path", "") or ""),
            output_dir=str(data.get("output_dir", "") or ""),
            samples=_as_optional_int(data.get("samples")),
            format=fmt,
            resolution_percentage=_as_optional_int(data.get("resolution_percentage")),
            language=str(data.get("language", "en_US") or "en_US"),
            log_level=str(data.get("log_level", "INFO") or "INFO").upper(),
            remote=profiles,
            version=max(version, 1),
        )

    def profile(self, name: str) -> RemoteProfile:
        for item in self.remote:
            if item.name == name:
                return item
        raise KeyError(f"unknown remote profile: {name!r}")

    def set_profile(self, profile: RemoteProfile) -> None:
        for index, item in enumerate(self.remote):
            if item.name == profile.name:
                self.remote[index] = profile
                return
        self.remote.append(profile)


def load_config(path: Path | None = None) -> Config:
    """Load configuration from *path*.

    When no explicit path is given the new ``GOO Suite`` file is preferred.
    If it does not exist yet the legacy GOO Bridge file is read, which keeps
    existing installations working without a mandatory migration step.
    """
    if path is not None:
        target = Path(path)
    else:
        target = config_path()
        if not target.is_file():
            legacy = legacy_config_path()
            if legacy.is_file():
                target = legacy
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    except OSError:
        return Config()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return Config()
    if not isinstance(data, dict):
        return Config()
    return Config.from_dict(data)


def effective_config(cwd: Path | None = None) -> Config:
    """Load user configuration, then apply ``./.goo/config.json`` overrides."""
    base = load_config()
    local = Path(cwd or os.getcwd()) / ".goo" / "config.json"
    if not local.is_file():
        return base
    try:
        data = json.loads(local.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return base
    if not isinstance(data, dict):
        return base
    merged = base.to_dict()
    merged.update({key: value for key, value in data.items() if key in merged})
    return Config.from_dict(merged)


def save_config(config: Config, path: Path | None = None) -> Path:
    target = path or config_path()
    atomic_write_text(
        target,
        json.dumps(config.to_dict(), indent=2, ensure_ascii=True) + "\n",
    )
    return target


def migrate_legacy() -> Path:
    """Copy the legacy GOO Bridge config to the GOO Suite location.

    Returns the new config path.  An existing new config is never overwritten.
    """
    target = config_path()
    if target.is_file():
        return target
    legacy = legacy_config_path()
    if not legacy.is_file():
        return target
    config = load_config(legacy)
    return save_config(config, target)
