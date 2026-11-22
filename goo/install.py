# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on installation and packaging (``goo install`` / ``goo bundle``).

Installing is done by a small Blender script: only Blender knows the user
scripts directory and can persist the add-on state into user preferences, so the
CLI never copies files itself.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path
from typing import Optional

from . import REPO_ROOT, addon_root
from . import blender as blender_module
from .errors import UsageError
from .util import ensure_dir, read_addon_version

#: Files/directories never packed into the distributable zip.
_BUNDLE_EXCLUDES = {
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
}
_BUNDLE_SUFFIXES = {".pyc", ".pyo", ".pdb", ".blend", ".blend1", ".zip"}
#: Fixed timestamp so repeated bundles are byte-identical.
_FIXED_DATE = (2020, 1, 1, 0, 0, 0)


def install(
    blender: Optional[str] = None,
    *,
    dry_run: bool = False,
    target: Optional[str] = None,
    enable: bool = True,
    addon_path: Optional[str] = None,
    timeout: float = 300.0,
) -> dict:
    """Install ``goo_npr`` into the Blender user add-ons directory.

    Returns the script payload (``target``, ``files``, ``enabled`` ...).
    """
    install_path = blender_module.discover(blender)
    source_root = Path(addon_path) if addon_path else addon_root()
    package = source_root / "goo_npr"
    if not package.is_dir():
        raise UsageError(
            f"add-on package not found: {package}",
            hint="Run goo from a full source checkout, or pass --addon / 请在完整源码目录中运行，或使用 --addon 指定路径",
        )
    job = {
        "addon_root": str(source_root),
        "dry_run": bool(dry_run),
        "enable": bool(enable),
    }
    if target:
        job["target"] = str(target)
    result = blender_module.run_script(
        "install_addon.py",
        args=job,
        blender=install_path,
        timeout=timeout,
        hide_window=True,
    )
    return blender_module.ensure_ok(result, "add-on installation")


def bundle(output: Optional[str] = None, *, addon_path: Optional[str] = None) -> dict:
    """Zip ``goo_npr`` into ``dist/goo_npr-<version>.zip`` for manual install."""
    source_root = Path(addon_path) if addon_path else addon_root()
    package = source_root / "goo_npr"
    if not package.is_dir():
        raise UsageError(f"add-on package not found: {package}")
    version = read_addon_version(package) or "0.0.0"
    target = Path(output) if output else REPO_ROOT / "dist" / f"goo_npr-{version}.zip"
    if target.is_dir():
        raise UsageError(f"output path is a directory: {target}")
    ensure_dir(str(target.parent))
    files = _collect(package)
    if not files:
        raise UsageError(f"nothing to bundle in {package}")
    temporary = target.with_suffix(target.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(package).as_posix()
            info = zipfile.ZipInfo(f"goo_npr/{relative}", date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())
    os.replace(temporary, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "ok": True,
        "tool": "bundle",
        "path": str(target),
        "version": version,
        "files": len(files),
        "bytes": target.stat().st_size,
        "sha256": digest,
        "entries": [path.relative_to(package).as_posix() for path in files],
    }


def source_bundle(output: Optional[str] = None, *, root: Optional[str] = None) -> dict:
    """Zip the source tree (README, package, docs, tests) reproducibly."""
    source_root = Path(root) if root else REPO_ROOT
    package = source_root / "goo_npr"
    if not package.is_dir():
        raise UsageError(
            f"add-on package not found: {package}",
            hint="Source bundles need a full checkout; install from a wheel to use only the add-on / 源码包需要完整源码目录",
        )
    for required in ("README.md", "pyproject.toml"):
        if not (source_root / required).is_file():
            raise UsageError(f"source bundle input missing: {source_root / required}")
    version = read_addon_version(package) or "0.0.0"
    target = Path(output) if output else REPO_ROOT / "dist" / f"goo-suite-{version}-src.zip"
    if target.is_dir():
        raise UsageError(f"output path is a directory: {target}")
    files = _collect_source(source_root)
    if not files:
        raise UsageError(f"nothing to bundle in {source_root}")
    ensure_dir(str(target.parent))
    temporary = target.with_suffix(target.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(source_root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())
    os.replace(temporary, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "ok": True,
        "tool": "source-bundle",
        "path": str(target),
        "version": version,
        "files": len(files),
        "bytes": target.stat().st_size,
        "sha256": digest,
        "entries": [path.relative_to(source_root).as_posix() for path in files],
    }


_SOURCE_TOP_FILES = ("README.md", "README.zh-CN.md", "pyproject.toml", "LICENSE")
_SOURCE_DIRS = ("goo", "goo_npr", "goo_bridge", "docs", "tests", "tools", ".github")
_SOURCE_EXCLUDES = _BUNDLE_EXCLUDES | {"_work", "dist", "build"}


def _collect_source(source_root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in _SOURCE_TOP_FILES:
        path = source_root / name
        if path.is_file():
            paths.append(path)
    for name in _SOURCE_DIRS:
        directory = source_root / name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(source_root).parts
            if any(part in _SOURCE_EXCLUDES for part in relative_parts):
                continue
            if any(part.endswith(".egg-info") for part in relative_parts):
                continue
            if path.suffix.lower() in {".pyc", ".pyo", ".zip"}:
                continue
            paths.append(path)
    return sorted(set(paths), key=lambda item: item.relative_to(source_root).as_posix())


def _collect(package: Path) -> list[Path]:
    collected: list[Path] = []
    for path in sorted(package.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(package).parts
        if any(part in _BUNDLE_EXCLUDES for part in relative_parts):
            continue
        if path.suffix.lower() in _BUNDLE_SUFFIXES:
            continue
        collected.append(path)
    return collected
