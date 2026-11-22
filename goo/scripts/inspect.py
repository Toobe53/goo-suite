# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect a blend file / the GOO node library for ``goo doctor`` and ``goo nodes``.

Read-only: this script never saves the blend file and never changes scene data
(``sync_scene`` is deliberately *not* called).
"""

from __future__ import annotations

import os
import sys

# Blender's --python does not add the script directory to sys.path; do it so
# the sibling _common module is importable in every invocation style.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402


def main(job: dict) -> None:
    import bpy

    timer = C.Timer()
    warnings: list[str] = []
    addon_root = C.addon_root(job)
    if addon_root and os.path.isdir(addon_root) and addon_root not in sys.path:
        sys.path.insert(0, addon_root)

    # --factory-startup disables add-ons: register goo_npr up-front so the
    # saved goo_light_groups properties are readable for the whole report.
    session_registered = False
    if job.get("light_groups", True):
        addon = C.import_addon(job, required=False)
        if addon is not None:
            try:
                session_registered = C.ensure_addon_registered(addon, required=False)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"could not register goo_npr: {type(exc).__name__}: {exc}")

    payload = {
        "ok": True,
        "tool": "inspect",
        "blender": {
            "version": bpy.app.version_string,
            "version_tuple": list(bpy.app.version),
            "background": bool(bpy.app.background),
            "binary": bpy.app.binary_path,
            "numpy": _numpy_version(),
        },
        "addon": {**_addon_report(), "session_registered": session_registered},
        "blend": bpy.data.filepath or None,
        "scenes": [],
        "engine": None,
        "resolution": None,
        "film_transparent": None,
        "objects": {"total": len(bpy.data.objects), "lights": 0, "meshes": len(bpy.data.meshes)},
        "materials": {"total": len(bpy.data.materials), "with_goo_groups": []},
        "node_groups": {"total": len(bpy.data.node_groups), "goo": []},
        "light_groups": {"available": False, "groups": [], "passes": []},
        "warnings": warnings,
        "seconds": 0.0,
    }

    scene = bpy.context.scene or (bpy.data.scenes[0] if bpy.data.scenes else None)
    if scene is not None:
        payload["engine"] = scene.render.engine
        payload["resolution"] = {
            "x": int(scene.render.resolution_x),
            "y": int(scene.render.resolution_y),
            "percentage": int(scene.render.resolution_percentage),
        }
        payload["film_transparent"] = bool(scene.render.film_transparent)
        payload["scenes"] = [
            {
                "name": item.name,
                "objects": len(item.objects),
                "frame": int(item.frame_current),
                "engine": item.render.engine,
                "camera": getattr(item.camera, "name", None) if item.camera else None,
                "light_groups": _scene_light_groups(item),
            }
            for item in bpy.data.scenes
        ]

    payload["objects"]["lights"] = len([obj for obj in bpy.data.objects if obj.type == "LIGHT"])

    # GOO node groups (the add-on tags its trees with a custom property).
    keys = C.goo_keys()
    registered = C.registry_names()
    goo_groups = []
    for group in bpy.data.node_groups:
        if C.is_goo_group(group, keys, registered):
            goo_groups.append(
                {
                    "name": group.name,
                    "type": group.bl_idname,
                    "nodes": len(group.nodes),
                    "properties": {
                        key: _safe_value(group.get(key)) for key in keys if group.get(key) is not None
                    },
                }
            )
    payload["node_groups"]["goo"] = goo_groups

    # Materials using GOO groups.
    material_hits = []
    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None:
            continue
        used = [
            node.name
            for node in material.node_tree.nodes
            if getattr(node, "node_tree", None) is not None and C.is_goo_group(node.node_tree, keys, registered)
        ]
        if used:
            material_hits.append({"name": material.name, "nodes": used})
    payload["materials"]["with_goo_groups"] = material_hits

    if job.get("specs"):
        payload["specs"], payload["spec_count"] = _registry_specs(warnings)

    if job.get("light_groups", True):
        payload["light_groups"] = _light_group_report(scene, warnings)

    payload["seconds"] = round(timer.elapsed, 3)
    C.log(f"inspected {payload['blend'] or '<startup scene>'} in {timer.elapsed:.2f}s")
    C.emit(payload, job)


def _numpy_version() -> str | None:
    try:
        import numpy

        return numpy.__version__
    except Exception:  # noqa: BLE001
        return None


def _addon_report() -> dict:
    report = {
        "installed": False,
        "enabled": False,
        "modules": [],
        "user_addon_path": None,
        "package_dir": None,
        "installed_dir": None,
        "bl_info_error": None,
        "error": None,
    }
    import os

    try:
        import bpy

        report["user_addon_path"] = bpy.utils.user_resource("SCRIPTS", path="addons")
    except Exception:  # noqa: BLE001
        pass
    try:
        import addon_utils

        report["modules"] = sorted(module.__name__ for module in addon_utils.modules())
        report["installed"] = "goo_npr" in report["modules"]
        enabled, loaded = addon_utils.check("goo_npr")
        report["enabled"] = bool(enabled or loaded)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    # Blender's addon_utils parses bl_info with ast.literal_eval; when bl_info
    # contains a non-literal (e.g. "version": ADDON_VERSION) the module is
    # silently dropped from modules().  Fall back to looking at the directory
    # so `goo doctor` can tell "not installed" apart from "unparsable bl_info".
    addons_dir = report.get("user_addon_path")
    if addons_dir:
        candidate = os.path.join(addons_dir, "goo_npr")
        if os.path.isfile(os.path.join(candidate, "__init__.py")):
            report["installed_dir"] = candidate
            if "goo_npr" not in report["modules"]:
                report["installed"] = True
                report["bl_info_error"] = (
                    "goo_npr is present in the add-ons directory but Blender's bl_info parser "
                    "rejected it (bl_info must contain only literals) / 插件已存在但 bl_info 无法解析（必须全部为字面量）"
                )
    try:
        import goo_npr

        report["package_dir"] = os.path.dirname(os.path.abspath(goo_npr.__file__))
    except Exception:  # noqa: BLE001
        pass
    return report


def _registry_specs(warnings: list[str]) -> tuple[list[dict], int]:
    try:
        from goo_npr.nodes import registry

        specs = registry.summary()
        if not isinstance(specs, list):
            specs = []
        return specs, len(specs)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"could not read the node registry: {type(exc).__name__}: {exc}")
        return [], 0


def _safe_value(value) -> str | int | float | None:
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _scene_light_groups(scene) -> list[dict]:
    groups = []
    collection = getattr(scene, "goo_light_groups", None)
    if collection is None:
        return groups
    try:
        for item in collection:
            receiver = getattr(item, "receiver_collection", None)
            groups.append(
                {
                    "name": item.name,
                    "use_shadow": bool(getattr(item, "use_shadow", True)),
                    "receiver_collection": getattr(receiver, "name", None) if receiver else None,
                    "lights": [],
                }
            )
    except (TypeError, AttributeError):
        pass
    return groups


def _light_group_report(scene, warnings: list[str]) -> dict:
    report: dict = {"available": False, "groups": [], "passes": [], "lights": [], "error": None}
    if scene is None:
        return report
    try:
        from goo_npr import light_groups as lg
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"goo_npr.light_groups unavailable: {type(exc).__name__}: {exc}"
        return report
    report["available"] = True
    try:
        scene_groups = _scene_light_groups(scene)
        lights = []
        counts: dict[str, int] = {}
        for obj in scene.objects:
            if obj.type != "LIGHT":
                continue
            group_name = str(getattr(obj, "goo_light_group", "") or "")
            counts[group_name] = counts.get(group_name, 0) + 1
            lights.append(
                {
                    "name": obj.name,
                    "type": str(getattr(getattr(obj, "data", None), "type", "LIGHT")),
                    "group": group_name or None,
                    "hide_render": bool(obj.hide_render),
                    "hide_viewport": bool(obj.hide_viewport),
                }
            )
        for group in scene_groups:
            group["lights"] = counts.get(group["name"], 0)
        report["groups"] = scene_groups
        report["lights"] = sorted(lights, key=lambda item: item["name"])
        report["unassigned_lights"] = counts.get("", 0)
        try:
            raw = lg.collect_passes(scene)
            report["passes"] = [
                {
                    "group": str(entry.get("group") or ""),
                    "label": str(entry.get("group") or "default"),
                    "lights": sorted(getattr(light, "name", "?") for light in entry.get("lights") or []),
                }
                for entry in raw or []
                if isinstance(entry, dict)
            ]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"collect_passes failed: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


if __name__ == "__main__":
    C.run_main(main, C.load_job())
