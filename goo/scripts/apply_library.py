# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless "build the library + sync light groups + save" job for ``goo apply``.

The add-on is imported from the repository (or an installed copy under
``GOO_ADDON_PATH``), registered, its node groups rebuilt and the resulting file
saved.  Nothing here touches UI state that only exists with a window.
"""

from __future__ import annotations

import os
import sys
import traceback

# Blender's --python does not add the script directory to sys.path; do it so
# the sibling _common module is importable in every invocation style.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402


def main(job: dict) -> None:
    import bpy

    timer = C.Timer()
    warnings: list[str] = []
    errors: list[str] = []

    out_path = job.get("out_path") or job.get("blend")
    if not out_path:
        raise RuntimeError("job is missing 'out_path'")
    out_path = os.path.abspath(str(out_path))
    C.ensure_dir(os.path.dirname(out_path))
    blend_in = bpy.data.filepath or None
    C.log(f"blend in: {blend_in or '<startup scene>'}")
    C.log(f"blend out: {out_path}")

    goo_npr = C.import_addon(job, required=True)
    addon_version = getattr(goo_npr, "ADDON_VERSION", None)
    version_text = None
    if addon_version:
        version_text = ".".join(str(part) for part in addon_version)

    registered = False
    register_error = None
    try:
        goo_npr.register()
        registered = True
    except Exception as exc:  # noqa: BLE001
        register_error = traceback.format_exc()
        errors.append(f"goo_npr.register() failed: {type(exc).__name__}: {exc}")
        C.log(errors[-1])
        C.log(register_error)
    if not registered:
        # Without registration the node properties/operators do not exist; still
        # try to build trees so users can see the failure in the payload.
        warnings.append("add-on registration failed; node library may be incomplete")

    keys = C.goo_keys()
    groups_before = C.goo_groups()
    build_requested = bool(job.get("build_library", True))
    built: dict = {}
    if build_requested:
        built = _build_library(goo_npr, warnings, errors)
    groups_after = C.goo_groups()
    protected = _protect_groups(warnings)

    sync_enabled = bool(job.get("sync_light_groups", True))
    light_groups = _sync_light_groups(goo_npr, sync_enabled, warnings, errors)

    saved = _save(out_path, errors)
    payload = {
        "ok": bool(saved) and not errors,
        "tool": "apply_library",
        "blend_in": blend_in,
        "blend_out": out_path,
        "saved": saved,
        "addon": {
            "version": version_text,
            "root": C.addon_root(job),
            "registered": registered,
            "build_requested": build_requested,
            "register_error": register_error,
        },
        "node_groups": {
            "before": [group.name for group in groups_before],
            "after": [group.name for group in groups_after],
            "created": len(built.get("created", [])),
            "total_goo": len(groups_after),
            "fake_user_protected": protected,
            "goo_keys": keys,
        },
        "build": built,
        "light_groups": light_groups,
        "warnings": warnings,
        "errors": errors,
        "seconds": round(timer.elapsed, 3),
    }
    if not payload["ok"]:
        C.fail("; ".join(errors) if errors else "could not save the blend file", job.get("hint"), job)
    C.log(f"applied library: {len(groups_after)} GOO group(s), saved={saved}")
    C.emit(payload, job)


def _build_library(goo_npr, warnings: list[str], errors: list[str]) -> dict:
    result: dict = {"requested": True, "created": [], "counts": {}}
    if hasattr(goo_npr, "reload_node_groups"):
        try:
            built = goo_npr.reload_node_groups(force=True)
            result["created"] = _normalise_built(built)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reload_node_groups(force=True) failed: {type(exc).__name__}: {exc}")
            C.log(traceback.format_exc())
    else:  # pragma: no cover - older add-on layout
        warnings.append("goo_npr.reload_node_groups() missing; using nodes.registry.ensure_all()")
        try:
            from goo_npr.nodes import registry

            built = registry.ensure_all(force=True)
            result["created"] = _normalise_built(built)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"nodes.registry.ensure_all(force=True) failed: {type(exc).__name__}: {exc}")
            C.log(traceback.format_exc())
    try:
        from goo_npr.nodes import registry

        specs = registry.summary()
        result["counts"] = {"specs": len(specs)}
        result["categories"] = sorted({str(spec.get("category")) for spec in specs if isinstance(spec, dict)})
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"could not read registry summary: {type(exc).__name__}: {exc}")
    C.log(f"node library: {len(result['created'])} created")
    return result


def _protect_groups(warnings: list[str]) -> int:
    """Keep the standalone GOO library in the saved .blend.

    The registry creates node groups with zero users, and Blender purges
    zero-user datablocks when saving; without a fake user the whole library
    silently disappears from the file we just wrote.
    """
    import bpy

    keys = C.goo_keys()
    names = C.registry_names()
    protected = 0
    for group in bpy.data.node_groups:
        try:
            is_goo = C.is_goo_group(group, keys, names)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not inspect node group {group.name!r}: {exc}")
            continue
        if is_goo and not group.use_fake_user:
            group.use_fake_user = True
            protected += 1
    if protected:
        C.log(f"marked {protected} GOO node group(s) with a fake user so they survive saving")
    return protected


def _normalise_built(built) -> list[str]:
    if isinstance(built, dict):
        return sorted(str(key) for key in built)
    if isinstance(built, (list, tuple, set)):
        names = []
        for item in built:
            names.append(str(getattr(item, "name", item)))
        return sorted(names)
    return []


def _sync_light_groups(goo_npr, enabled: bool, warnings: list[str], errors: list[str]) -> dict:
    import bpy

    report: dict = {"enabled": enabled, "available": False, "scenes": [], "groups": [], "pass_counts": {}, "lights": 0}
    if not enabled:
        return report
    module = getattr(goo_npr, "light_groups", None)
    if module is None:
        try:
            from goo_npr import light_groups as module  # type: ignore[no-redef]
        except Exception as exc:  # noqa: BLE001
            report["error"] = f"goo_npr.light_groups unavailable: {type(exc).__name__}: {exc}"
            warnings.append(report["error"])
            return report
    if not hasattr(module, "sync_scene"):
        report["error"] = "goo_npr.light_groups.sync_scene() not found"
        warnings.append(report["error"])
        return report
    report["available"] = True
    total_groups = 0
    total_lights = 0
    for scene in bpy.data.scenes:
        entry: dict = {"name": scene.name, "groups": 0, "lights": 0, "passes": 0}
        try:
            sync = module.sync_scene(scene)
            if isinstance(sync, dict):
                entry["sync"] = {str(key): _plain(value) for key, value in sync.items()}
        except Exception as exc:  # noqa: BLE001
            message = f"sync_scene({scene.name}) failed: {type(exc).__name__}: {exc}"
            errors.append(message)
            C.log(traceback.format_exc())
        collection = getattr(scene, "goo_light_groups", None)
        try:
            entry["groups"] = len(collection) if collection is not None else 0
        except TypeError:
            entry["groups"] = 0
        scenes_lights = [obj for obj in scene.objects if obj.type == "LIGHT"]
        entry["lights"] = len(scenes_lights)
        entry["assigned_lights"] = len([obj for obj in scenes_lights if getattr(obj, "goo_light_group", "")])
        if hasattr(module, "collect_passes"):
            try:
                entry["passes"] = len(list(module.collect_passes(scene) or []))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"collect_passes({scene.name}) failed: {type(exc).__name__}: {exc}")
        total_groups += entry["groups"]
        total_lights += entry["lights"]
        report["scenes"].append(entry)
        report["pass_counts"][scene.name] = entry["passes"]
    report["groups"] = total_groups
    report["lights"] = total_lights
    C.log(f"light groups: {total_groups} group(s), {total_lights} light(s)")
    return report


def _plain(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _save(out_path: str, errors: list[str]) -> bool:
    import bpy

    try:
        bpy.ops.wm.save_as_mainfile(filepath=out_path, check_existing=False, compress=False)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"save_as_mainfile failed: {type(exc).__name__}: {exc}")
        C.log(traceback.format_exc())
        return False
    if not os.path.isfile(out_path):
        errors.append(f"Blender reported success but {out_path} does not exist")
        return False
    return True


if __name__ == "__main__":
    C.run_main(main, C.load_job())
