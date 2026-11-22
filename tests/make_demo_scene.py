# SPDX-License-Identifier: GPL-3.0-or-later
"""Create tests/assets/demo_lightgroups.blend used by the headless tests.

Run:
    blender --factory-startup --background --python tests/make_demo_scene.py
"""

from __future__ import annotations

import math
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

ASSETS = os.path.join(REPO, "tests", "assets")
os.makedirs(ASSETS, exist_ok=True)

# Add-on must be importable so the scene uses the real property groups.
import goo_npr  # noqa: E402

if not hasattr(bpy.context.scene, "goo_light_groups"):
    goo_npr.register()

scene = bpy.context.scene
scene.name = "GOO Demo"
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 256
scene.render.resolution_y = 256
scene.render.resolution_percentage = 100
scene.eevee.taa_render_samples = 16
scene.view_settings.view_transform = "Standard"

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

if scene.world is None:
    scene.world = bpy.data.worlds.new("DemoWorld")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes.get("Background")
if bg is not None:
    bg.inputs[0].default_value = (0.05, 0.05, 0.05, 1.0)

scene.goo_light_groups.clear()
for name in ("key", "fill"):
    item = scene.goo_light_groups.add()
    item.name = name
    item.use_shadow = True

for name, x, group in (("A", -1.8, "key"), ("B", 1.8, "fill")):
    bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
    obj = bpy.context.object
    obj.name = name
    mat = bpy.data.materials.get(f"DEMO_{name}") or bpy.data.materials.new(f"DEMO_{name}")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    obj.data.materials.append(mat)
    link = mat.goo_light_groups.add()
    link.name = group
    # Also test object-level links on B; sync merges material and object links.
    if name == "B":
        olink = obj.goo_light_groups.add()
        olink.name = group

for name, color, rotation_z, group in (
    ("key_light", (1.0, 0.15, 0.15), 25.0, "key"),
    ("fill_light", (0.15, 0.2, 1.0), -25.0, "fill"),
):
    data = bpy.data.lights.new(name, type="SUN")
    data.energy = 5.0
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.rotation_euler = (math.radians(55), 0, math.radians(rotation_z))
    obj.goo_light_group = group

cam_data = bpy.data.cameras.new("DemoCam")
cam = bpy.data.objects.new("DemoCam", cam_data)
bpy.context.collection.objects.link(cam)
cam_data.type = "ORTHO"
cam_data.ortho_scale = 6.0
cam.location = (0, 0, 8)
scene.camera = cam

# A short in-file note so the asset is self-documenting.
text = bpy.data.texts.get("DEMO_README") or bpy.data.texts.new("DEMO_README")
text.clear()
text.write(
    "GOO Port demo scene / 演示场景\n"
    "- A (left) is linked to light group 'key' (red Sun).\n"
    "- B (right) is linked to light group 'fill' (blue Sun) via material + object links.\n"
    "- Run: python -m goo render tests/assets/demo_lightgroups.blend --light-groups -o out/passes\n"
    "- Then: python -m goo composite out/passes --png\n"
)

path = os.path.join(ASSETS, "demo_lightgroups.blend")
bpy.ops.wm.save_as_mainfile(filepath=path)
print("SAVED", path)
