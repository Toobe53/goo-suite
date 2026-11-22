# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the goo_npr add-on into Blender's user add-ons directory without the GUI.

The CLI runs this inside Blender because only Blender knows where the user
scripts directory lives and how to write user preferences::

    blender --factory-startup --background --python-exit-code 4 \
        --python install_addon.py -- job.json

Job keys::

    addon_root   repository root containing ``goo_npr/``
    target       optional explicit add-ons directory
    dry_run      bool, only report what would happen
    enable       bool, enable in user preferences (default true)
"""

from __future__ import annotations

import os
import shutil
import sys
import traceback

# Blender's --python does not add the script directory to sys.path; do it so
# the sibling _common module is importable in every invocation style.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.pdb", ".git", ".hg", "*.zip")


def main(job: dict) -> None:
    import bpy
    import addon_utils

    timer = C.Timer()
    warnings: list[str] = []
    source_root = C.addon_root(job)
    if not source_root:
        raise RuntimeError("job is missing 'addon_root' / 缺少 addon_root")
    source = os.path.join(source_root, "goo_npr")
    if not os.path.isdir(source):
        raise RuntimeError(f"add-on source directory not found: {source} / 找不到插件源码目录")
    if not os.path.isfile(os.path.join(source, "__init__.py")):
        raise RuntimeError(f"{source} is not a Python package (__init__.py missing)")

    target_root = job.get("target") or bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
    target = os.path.abspath(os.path.join(str(target_root), "goo_npr"))
    dry_run = bool(job.get("dry_run"))

    installed_before = _is_installed()
    _, enabled_before = addon_utils.check("goo_npr")
    files, total_bytes = _scan(source)
    payload: dict = {
        "ok": True,
        "tool": "install_addon",
        "dry_run": dry_run,
        "source": source,
        "target": target,
        "blender": {"version": bpy.app.version_string, "binary": bpy.app.binary_path},
        "user_addons_dir": str(target_root),
        "files": files,
        "bytes": total_bytes,
        "installed_before": bool(installed_before),
        "enabled_before": bool(enabled_before),
        "copied": False,
        "replaced": os.path.isdir(target),
        "enabled": bool(enabled_before),
        "saved_preferences": False,
        "warnings": warnings,
    }
    payload["addon_version"] = _addon_version(source)

    if dry_run:
        C.log(f"[dry-run] would copy {files} file(s) ({total_bytes} bytes) to {target}")
        payload["seconds"] = round(timer.elapsed, 3)
        C.emit(payload, job)
        return

    C.log(f"copying {source} -> {target} ({files} files, {total_bytes} bytes)")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=IGNORE)
    except OSError as exc:
        C.fail(f"could not copy the add-on: {exc}", "Close Blender instances using it, or run as administrator / 请关闭占用该目录的 Blender 实例后重试", job)
        return
    payload["copied"] = True

    # Make Blender notice the new files.
    for step in (_refresh_script_paths, _refresh_modules):
        try:
            step()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{step.__name__} failed: {exc}")

    enable = job.get("enable", True)
    if enable:
        try:
            result = bpy.ops.preferences.addon_enable(module="goo_npr")
            payload["enabled"] = "FINISHED" in result
        except Exception as exc:  # noqa: BLE001
            payload["enabled"] = False
            payload["enable_error"] = f"{type(exc).__name__}: {exc}"
            warnings.append(
                "add-on was copied but could not be enabled: "
                f"{payload['enable_error']}. Enable it in Preferences > Add-ons / 请在首选项的插件面板手动启用"
            )
            C.log(traceback.format_exc())
        try:
            bpy.ops.wm.save_userpref()
            payload["saved_preferences"] = True
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not save user preferences: {exc}")
    else:
        warnings.append("add-on not enabled (--no-enable)")

    # Verify what Blender now sees.
    try:
        payload["installed_after"] = _is_installed()
        _, enabled_after = addon_utils.check("goo_npr")
        payload["enabled_after"] = bool(enabled_after)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"could not re-check add-on state: {exc}")

    C.log(f"add-on installed at {target} (enabled={payload['enabled']})")
    payload["seconds"] = round(timer.elapsed, 3)
    C.emit(payload, job)


def _installed_names() -> set[str]:
    try:
        import addon_utils

        return {module.__name__ for module in addon_utils.modules()}
    except Exception:  # noqa: BLE001
        return set()


def _is_installed() -> bool:
    return "goo_npr" in _installed_names()


def _refresh_script_paths() -> None:
    import bpy

    bpy.utils.refresh_script_paths()


def _refresh_modules() -> None:
    import addon_utils

    addon_utils.modules_refresh()


def _scan(source: str) -> tuple[int, int]:
    files = 0
    total = 0
    for root, dirs, names in os.walk(source):
        dirs[:] = [name for name in dirs if name not in ("__pycache__", ".git", ".hg")]
        for name in names:
            if name.endswith((".pyc", ".pyo", ".pdb")):
                continue
            files += 1
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return files, total


def _addon_version(source: str) -> str | None:
    path = os.path.join(source, "__init__.py")
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    import ast
    import re

    match = re.search(r"ADDON_VERSION\s*=\s*\(([^)]*)\)", text)
    if match:
        parts = [part.strip().strip("\"'") for part in match.group(1).split(",") if part.strip()]
        if parts and all(part.isdigit() for part in parts):
            return ".".join(parts)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "ADDON_VERSION" for target in node.targets):
            if isinstance(node.value, ast.Tuple):
                values = [getattr(element, "value", None) for element in node.value.elts]
                if all(isinstance(value, int) for value in values):
                    return ".".join(str(value) for value in values)
    return None


if __name__ == "__main__":
    C.run_main(main, C.load_job())
