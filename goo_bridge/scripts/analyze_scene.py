# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side scene inspector for GOO Bridge.

Run with::

    blender --background scene.blend --python analyze_scene.py -- --out result.json

Only the Python API bundled with Blender is used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _path_of(id_block, attribute: str = "filepath") -> str:
    raw = getattr(id_block, attribute, "") or ""
    if not raw:
        return ""
    try:
        import bpy

        return bpy.path.abspath(raw)
    except Exception:
        return raw


def _external_files() -> list[dict]:
    import bpy

    found: dict[str, dict] = {}
    collections = (
        ("image", bpy.data.images),
        ("library", bpy.data.libraries),
        ("font", bpy.data.fonts),
        ("sound", bpy.data.sounds),
        ("movieclip", bpy.data.movieclips),
        ("cache", bpy.data.cache_files),
    )
    for kind, blocks in collections:
        for block in blocks:
            path = _path_of(block)
            if not path:
                continue
            found[path] = {
                "path": path,
                "kind": kind,
                "name": getattr(block, "name", ""),
                "exists": os.path.exists(path),
            }
    return sorted(found.values(), key=lambda item: item["path"].lower())


def _node_group_names() -> list[str]:
    import bpy

    return sorted(tree.name for tree in bpy.data.node_groups)


def _material_goo_usage(goo_names: set[str]) -> list[dict]:
    import bpy

    usage: list[dict] = []
    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None:
            continue
        hits = sorted(
            {
                node.node_tree.name
                for node in material.node_tree.nodes
                if getattr(node, "node_tree", None) is not None
                and node.node_tree.name in goo_names
            }
        )
        if hits:
            usage.append({"material": material.name, "groups": hits})
    return usage


def _view_layers() -> list[str]:
    import bpy

    return [layer.name for layer in bpy.context.scene.view_layers]


def _feature_flags(goo: set[str], external: list[dict]) -> dict[str, bool]:
    import bpy

    scene = bpy.context.scene
    tree = getattr(scene, "node_tree", None) or getattr(scene, "compositing_node_group", None)
    compositor = tree if getattr(scene, "use_nodes", False) else None
    comp_names = set()
    if compositor is not None:
        comp_names = {node.name for node in compositor.nodes}

    return {
        "has_goo_groups": bool(goo),
        "light_groups": bool(comp_names & {"GOO Light Groups", "GOO Line Composite"})
        or any(name.startswith("GOO Light") for name in goo),
        "active_camera_override": hasattr(scene, "gn_camera_override")
        or "GOO Active Camera" in goo,
        "scene_time": "GOO Scene Time" in goo,
        "sdf": any(" SDF " in name or name.endswith(" SDF") for name in goo),
        "oklab": any("OKLab" in name for name in goo),
        "missing_external_files": any(not item["exists"] for item in external),
        "compositor_nodes": bool(comp_names),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])

    import bpy

    name = os.path.basename(bpy.data.filepath) if bpy.data.filepath else ""
    groups = _node_group_names()
    goo = {group for group in groups if group.startswith("GOO ")}
    external = _external_files()
    scene = bpy.context.scene
    report = {
        "ok": True,
        "file": bpy.data.filepath,
        "name": name,
        "blender_version": bpy.app.version_string,
        "blender_version_tuple": list(bpy.app.version),
        "engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "resolution_percentage": scene.render.resolution_percentage,
        "samples": getattr(getattr(scene, "eevee", None), "taa_render_samples", 0),
        "objects": len(bpy.data.objects),
        "materials": len(bpy.data.materials),
        "images": len(bpy.data.images),
        "libraries": len(bpy.data.libraries),
        "collections": len(bpy.data.collections),
        "node_groups": groups,
        "goo_groups": sorted(goo),
        "goo_group_count": len(goo),
        "goo_materials": _material_goo_usage(goo),
        "view_layers": _view_layers(),
        "active_camera": scene.camera.name if scene.camera else "",
        "features": _feature_flags(goo, external),
        "external_files": external,
        "missing_files": [item for item in external if not item["exists"]],
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=True)
    print("ANALYZE_OK", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
