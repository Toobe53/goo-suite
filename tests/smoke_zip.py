# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the *shipped* goo_npr zip with Blender's native add-on installer.

Run by ``tests/test_cli.py`` with ``BLENDER_USER_SCRIPTS`` pointing at a fresh
directory and ``GOO_ZIP_PATH``/``GOO_SMOKE_OUT`` set.  This validates the exact
archive users download, not the source tree that ``goo install`` copies.
"""

from __future__ import annotations

import json
import os

import addon_utils
import bpy


def main() -> int:
    zip_path = os.environ["GOO_ZIP_PATH"]
    output = os.environ["GOO_SMOKE_OUT"]
    scripts_root = os.environ["BLENDER_USER_SCRIPTS"]

    bpy.ops.preferences.addon_install(filepath=zip_path, overwrite=True)
    module = addon_utils.enable("goo_npr", default_set=True)
    if module is None:
        raise RuntimeError("addon_utils.enable('goo_npr') returned None")

    addon_file = os.path.normcase(os.path.abspath(module.__file__))
    if not addon_file.startswith(os.path.normcase(os.path.abspath(scripts_root))):
        raise RuntimeError(f"enabled from outside the install root: {module.__file__}")

    result = bpy.ops.goo.build_library()
    if result != {"FINISHED"}:
        raise RuntimeError(f"build_library returned {result!r}")
    groups = sorted(tree.name for tree in bpy.data.node_groups if tree.name.startswith("GOO "))
    if len(groups) != 22:
        raise RuntimeError(f"expected 22 GOO groups, got {len(groups)}")

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.eevee.taa_render_samples = 4
    scene.render.filepath = output
    bpy.ops.render.render(write_still=True)
    if not os.path.isfile(output):
        raise RuntimeError(f"render did not write {output}")

    api_before = hasattr(bpy.types.PoseBone, "hide_outliner")
    addon_utils.disable("goo_npr", default_set=True)
    api_off = hasattr(bpy.types.PoseBone, "hide_outliner")
    addon_utils.enable("goo_npr", default_set=True)
    api_after = hasattr(bpy.types.PoseBone, "hide_outliner")
    if not (api_before and not api_off and api_after):
        raise RuntimeError(f"lifecycle mismatch: {api_before}/{api_off}/{api_after}")

    print(
        json.dumps(
            {
                "ok": True,
                "groups": len(groups),
                "has_active_camera": "GOO Active Camera" in groups,
                "render": output,
                "render_bytes": os.path.getsize(output),
                "lifecycle": [api_before, api_off, api_after],
            },
            sort_keys=True,
        )
    )
    print("ZIP_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
