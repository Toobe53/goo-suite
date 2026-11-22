"""Smoke-test an *installed* copy of the add-on, outside the source tree.

Run by Blender, normally through ``tests/test_cli.py``:

    blender --background --factory-startup --addon goo_npr \
        --python tests/smoke_installed.py

Environment:
    GOO_INSTALL_ROOT   directory that must contain the imported goo_npr package
    GOO_SMOKE_OUT      PNG path for a 64x64 render (created by this script)

Exits 0 and prints ``SMOKE_OK ...`` on success; raises on any mismatch.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import addon_utils
import bpy

EXPECTED_GROUPS = 22
REQUIRED = {
    "GOO Shader Info",
    "GOO Active Camera",
    "GOO Light Info New",
    "GOO Scene Time",
    "GOO Line Composite",
}


def _fail(message: str) -> None:
    raise SystemExit(f"SMOKE_FAIL: {message}")


def main() -> None:
    enabled, _ = addon_utils.check("goo_npr")
    if not enabled:
        addon_utils.enable("goo_npr", default_set=True, persistent=True)

    import goo_npr

    here = Path(goo_npr.__file__).resolve()
    expected = Path(os.environ["GOO_INSTALL_ROOT"]).resolve()
    if expected not in here.parents and here.parent != expected:
        _fail(f"imported {here}, expected under {expected}")

    groups = [g for g in bpy.data.node_groups if g.name.startswith("GOO ")]
    if not groups:
        # In --background the register() timer that normally builds the
        # library after startup does not tick before --python runs.  Trigger
        # the same idempotent build explicitly (it is a no-op in the UI).
        goo_npr.reload_node_groups(force=False)
        groups = [g for g in bpy.data.node_groups if g.name.startswith("GOO ")]
    names = {g.name for g in groups}
    if len(groups) != EXPECTED_GROUPS:
        _fail(f"{len(groups)} GOO groups, expected {EXPECTED_GROUPS}: {sorted(names)}")
    missing = REQUIRED - names
    if missing:
        _fail(f"missing groups: {sorted(missing)}")

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.eevee.taa_render_samples = 4

    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    bpy.ops.object.camera_add(location=(4.0, -4.0, 3.0))
    camera = bpy.context.object
    camera.data.lens = 50
    camera.rotation_euler = (math.radians(63), 0.0, math.radians(45))
    scene.camera = camera
    bpy.ops.object.light_add(type="SUN", location=(3.0, -2.0, 5.0))

    out = Path(os.environ["GOO_SMOKE_OUT"]).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)

    if not out.is_file() or out.stat().st_size == 0:
        _fail(f"no render written to {out}")

    image = bpy.data.images.load(str(out))
    pixels = list(image.pixels)
    luminance = sum(pixels[i] for i in range(0, len(pixels), 4)) / max(1, len(pixels) // 4)
    if luminance <= 0.01:
        _fail(f"render is black (mean {luminance:.5f})")

    addon_utils.disable("goo_npr")
    if hasattr(bpy.types.PoseBone, "hide_outliner") or hasattr(bpy.types.Scene, "goo_light_groups"):
        _fail("disable left GOO classes registered")
    addon_utils.enable("goo_npr", default_set=True, persistent=True)
    if not hasattr(bpy.types.PoseBone, "hide_outliner") or not hasattr(bpy.types.Scene, "goo_light_groups"):
        _fail("re-enable did not restore GOO classes")
    print("LIFECYCLE_OK")

    print(
        "SMOKE_OK "
        + json.dumps(
            {
                "addon_path": str(here),
                "blender": bpy.app.version_string,
                "groups": len(groups),
                "mean": round(luminance, 5),
                "render": str(out),
            }
        )
    )


main()
