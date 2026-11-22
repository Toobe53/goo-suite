# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless integration test for the goo_npr add-on (run inside Blender 5.2).

Usage:
    blender --factory-startup --background --python tests/test_addon.py

Exit code 0 means every assertion passed; Blender exits with 1 on failure.
The test is deliberately self-contained: it creates its own scene, materials,
lights and cameras, so it can run in factory startup without a .blend file.
"""

from __future__ import annotations

import math
import os
import sys
import traceback
import unittest

try:
    import bpy
    import numpy as np
except ImportError as exc:  # Blender's Python is required for this integration test.
    raise unittest.SkipTest(f"run inside Blender: {exc}") from exc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import goo_npr  # noqa: E402
import goo_npr.light_groups as lg  # noqa: E402
from goo_npr.nodes import registry  # noqa: E402

FAILS: list[str] = []
PASSES = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSES
    if condition:
        PASSES += 1
        print(f"PASS  {name}")
    else:
        FAILS.append(name)
        print(f"FAIL  {name}  {detail}")


def expected_groups() -> list[str]:
    """Group names required by the port; extra groups are allowed."""
    return [
        "GOO Shader Info",
        "GOO Curvature",
        "GOO Light Info",
        "GOO Light Info New",
        "GOO Set Depth",
        "GOO Screen Space Info",
        "GOO Twirl",
        "GOO Water Ripples",
        "GOO Hex Grid",
        "GOO SDF Primitive",
        "GOO SDF Op",
        "GOO SDF Vector Op",
        "GOO SDF Noise",
        "GOO OKLab Mix",
        "GOO OKLab Color Ramp",
        "GOO Threshold Mask",
        "GOO Scene Time",
        "GOO Active Camera",
        "GOO Depth Fade",
        "GOO Line Composite",
        "GOO Kuwahara NPR",
        "GOO Depth Fog",
    ]


def setup_render_scene() -> bpy.types.Scene:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.eevee.taa_render_samples = 8
    scene.view_settings.view_transform = "Standard"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("TestWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.05, 0.05, 0.05, 1.0)
    return scene


def render_mean(scene: bpy.types.Scene, path: str) -> list[float]:
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    try:
        px = np.array(img.pixels[:], dtype=np.float32).reshape(-1, 4)
        return [round(float(v), 4) for v in px[:, :3].mean(axis=0)]
    finally:
        bpy.data.images.remove(img)


def test_registration() -> None:
    try:
        goo_npr.register()
    except Exception:
        traceback.print_exc()
        check("addon register()", False, "exception")
        return
    check("addon register()", True)
    check("bl_info version", tuple(goo_npr.bl_info["blender"]) == (5, 2, 0))
    for op in ("build_library", "setup_compositor", "light_group_add", "light_group_sync"):
        check(f"operator goo.{op}", hasattr(bpy.ops.goo, op))


def test_node_library() -> None:
    try:
        built = registry.ensure_all(force=True)
    except Exception:
        traceback.print_exc()
        check("ensure_all()", False, "exception")
        return
    names = set(built)
    print("GROUPS", sorted(names))
    for name in expected_groups():
        check(f"node group {name}", name in names, f"missing (have {sorted(names)})")
    counts = {"ShaderNodeTree": 0, "GeometryNodeTree": 0, "CompositorNodeTree": 0}
    for name in names:
        counts[bpy.data.node_groups[name].bl_idname] += 1
    check(
        "group type counts 17/2/3",
        counts == {"ShaderNodeTree": 17, "GeometryNodeTree": 2, "CompositorNodeTree": 3},
        str(counts),
    )
    check("no extra node groups", len(built) == 22, f"have {len(built)}")
    # Rebuilding must preserve the datablock identity used by materials.
    target = next((n for n in ("GOO Twirl", "GOO Shader Info") if n in names), None)
    if target is None:
        check("rebuild preserves datablock", False, "no shader group built")
    else:
        before = bpy.data.node_groups.get(target)
        registry.ensure_all(force=True, only=[target])
        after = bpy.data.node_groups.get(target)
        check("rebuild preserves datablock", before is not None and before == after)
    # Instantiate every shader group in a material and render it once.
    mat = bpy.data.materials.new("GOO_Test_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    last = None
    for name in expected_groups():
        tree = bpy.data.node_groups.get(name)
        if tree is None or tree.bl_idname != "ShaderNodeTree":
            continue
        g = nt.nodes.new("ShaderNodeGroup")
        g.node_tree = tree
        last = g
    if last is not None:
        nt.links.new(last.outputs[0], out.inputs["Surface"])
    check("instantiate shader groups", last is not None)


def test_scene_time() -> None:
    tree = bpy.data.node_groups.get("GOO Scene Time")
    check("GOO Scene Time exists", tree is not None)
    if tree is None:
        return
    names = {item.name for item in tree.interface.items_tree if item.item_type == "SOCKET"}
    check(
        "Scene Time outputs Seconds/Frame/Start/End",
        {"Seconds", "Frame", "Start Frame", "End Frame"} <= names,
        f"have {sorted(names)}",
    )
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 3, 97
    updated = goo_npr.nodes.geometry.sync_scene_time(scene)
    start = tree.nodes.get("GOO Start Frame")
    end = tree.nodes.get("GOO End Frame")
    check(
        "Scene Time handler values",
        updated >= 1
        and start is not None
        and end is not None
        and start.outputs[0].default_value == 3.0
        and end.outputs[0].default_value == 97.0,
        f"updated={updated}",
    )


def test_active_camera() -> None:
    tree = bpy.data.node_groups.get("GOO Active Camera")
    check("GOO Active Camera exists", tree is not None)
    if tree is None:
        return
    names = {item.name for item in tree.interface.items_tree if item.item_type == "SOCKET"}
    check(
        "Active Camera sockets",
        {"Ignore Scene Override", "Override Camera", "Active Camera"} <= names,
        f"have {sorted(names)}",
    )
    switch = next((n for n in tree.nodes if n.bl_idname == "GeometryNodeSwitch"), None)
    check("Active Camera object switch", switch is not None and switch.input_type == "OBJECT")
    check(
        "Active Camera group output linked",
        any(link.to_socket.name == "Active Camera" for link in tree.links),
    )


def test_i18n_table() -> None:
    """Every registered UI key has both English and Chinese text."""
    import goo_npr.i18n as i18n

    table = i18n._STRINGS
    missing_en = sorted(key for key, value in table.items() if not value.get("en_US"))
    missing_zh = sorted(key for key, value in table.items() if not value.get("zh_CN"))
    check("i18n table has en_US for every key", not missing_en, f"missing {missing_en[:8]}")
    check("i18n table has zh_CN for every key", not missing_zh, f"missing {missing_zh[:8]}")
    camera = sorted(key for key in table if key.startswith("goo.node.active_camera."))
    check(
        "Active Camera keys translated",
        len(camera) >= 5 and all(table[key].get("zh_CN") for key in camera),
        f"have {camera}",
    )


def test_gp_smooth() -> None:
    """GOO's sculpt smoothing port: Laplacian smoothing of GP strokes."""
    scene = bpy.context.scene
    data = bpy.data.grease_pencils.new("GOO_GP_Smooth")
    obj = bpy.data.objects.new("GOO_GP_Smooth", data)
    scene.collection.objects.link(obj)
    layer = data.layers.new("Layer")
    frame = layer.frames.new(1)
    drawing = frame.drawing
    drawing.add_strokes([5])
    stroke = drawing.strokes[0]
    stroke.select = True
    for index, point in enumerate(stroke.points):
        point.position = (float(index), 1.0 if index == 2 else 0.0, 0.0)

    bpy.context.view_layer.objects.active = obj
    result = bpy.ops.goo.gp_smooth_strokes(factor=1.0, iterations=1, only_selected=True)
    check("gp_smooth_strokes runs", result == {"FINISHED"}, f"result={result}")
    positions = [tuple(point.position) for point in stroke.points]
    check(
        "gp_smooth preserves endpoints",
        positions[0] == (0.0, 0.0, 0.0) and positions[-1] == (4.0, 0.0, 0.0),
        f"positions={positions}",
    )
    check(
        "gp_smooth flattens a spike",
        positions[2][1] < 0.2,
        f"positions={positions}",
    )
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.grease_pencils.remove(data)


def test_light_info_new() -> None:
    tree = bpy.data.node_groups.get("GOO Light Info New")
    check("GOO Light Info New exists", tree is not None)
    if tree is None:
        return
    names = {item.name for item in tree.interface.items_tree if item.item_type == "SOCKET"}
    check(
        "Light Info New sockets",
        {"Light Color In", "Light Power In", "Light Position In", "Light Rotation In", "Light Distance In",
         "Light Color", "Light Power", "Light Position", "Light Rotation", "Light Distance"} <= names,
        f"have {sorted(names)}",
    )

    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("GOO_LIN_Cam")
    cam = bpy.data.objects.new("GOO_LIN_Cam", cam_data)
    scene.collection.objects.link(cam)
    light_data = bpy.data.lights.new("GOO_LIN_Light", "SUN")
    light = bpy.data.objects.new("GOO_LIN_Light", light_data)
    scene.collection.objects.link(light)
    light.location = (3.0, 4.0, 0.0)
    light.rotation_euler = (0.1, 0.2, 0.3)
    light_data.energy = 42.0
    light_data.color = (0.2, 0.4, 0.6)
    scene.camera = cam

    material = bpy.data.materials.new("GOO_LIN_Mat")
    material.use_nodes = True
    material.node_tree.nodes.clear()
    group_node = material.node_tree.nodes.new("ShaderNodeGroup")
    group_node.node_tree = tree

    result = bpy.ops.goo.light_info_new_from_active(
        light_name=light.name, material_name=material.name
    )
    drivers = list(material.node_tree.animation_data.drivers) if material.node_tree.animation_data else []
    check(
        "light_info_new operator wrote 11 drivers",
        result == {"FINISHED"} and len(drivers) == 11,
        f"result={result} drivers={len(drivers)}",
    )
    paths = {driver.data_path for driver in drivers}
    check(
        "light_info_new position/rotation/distance paths",
        any("Light Position In" in path for path in paths)
        and any("Light Rotation In" in path for path in paths)
        and any("Light Distance In" in path for path in paths),
    )
    distance = next((d for d in drivers if "Light Distance In" in d.data_path), None)
    check(
        "light_info_new distance expression",
        distance is not None
        and "sqrt" in distance.driver.expression
        and len(distance.driver.variables) == 6,
    )


def _make_armature(name: str, bones: tuple[str, ...]):
    bpy.ops.object.armature_add(enter_editmode=False, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = obj.data.edit_bones
    for bone in list(edit_bones):
        edit_bones.remove(bone)
    for index, bone_name in enumerate(bones):
        bone = edit_bones.new(bone_name)
        bone.head = (0.0, 0.0, float(index))
        bone.tail = (0.0, 0.0, float(index + 1))
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def test_pose_tools() -> None:
    """GOO's Isolate Bones / Invert All / Merge Constraints ports."""
    bpy.ops.object.select_all(action="DESELECT")
    src = _make_armature("GOO_POSE_Src", ("Bone", "Bone.002"))
    dst = _make_armature("GOO_POSE_Dst", ("Bone", "Bone.002"))

    con = src.pose.bones["Bone"].constraints.new("COPY_ROTATION")
    con.target = src
    con.subtarget = "Bone.002"
    con.influence = 0.5
    con.invert_x = True

    bpy.ops.object.select_all(action="DESELECT")
    dst.select_set(True)
    bpy.context.view_layer.objects.active = dst
    bpy.ops.object.mode_set(mode="POSE")
    for pose_bone in dst.pose.bones:
        pose_bone.select = pose_bone.name == "Bone"

    merged = bpy.ops.goo.pose_constraints_merge(src_object_name=src.name)
    dst_con = dst.pose.bones["Bone"].constraints[0] if dst.pose.bones["Bone"].constraints else None
    check(
        "pose constraints merge",
        merged == {"FINISHED"}
        and dst_con is not None
        and dst_con.type == "COPY_ROTATION"
        and dst_con.target == dst
        and dst_con.subtarget == "Bone.002"
        and abs(dst_con.influence - 0.5) < 1e-6
        and dst_con.invert_x is True,
        f"merged={merged} con={dst_con}",
    )

    inverted = bpy.ops.goo.copy_rotation_invert_all(value=True)
    check(
        "copy rotation invert all",
        inverted == {"FINISHED"}
        and dst_con is not None
        and (dst_con.invert_x, dst_con.invert_y, dst_con.invert_z) == (True, True, True),
        f"inverted={inverted}",
    )
    bpy.ops.goo.copy_rotation_invert_all(value=False)
    check(
        "copy rotation clear invert",
        dst_con is not None
        and (dst_con.invert_x, dst_con.invert_y, dst_con.invert_z) == (False, False, False),
    )

    hidden_before = {bone.name: bone.hide for bone in dst.data.bones}
    isolated = bpy.ops.goo.pose_isolate_bones()
    check(
        "pose isolate bones",
        isolated == {"FINISHED"}
        and dst.data.bones["Bone.002"].hide is True
        and dst.data.bones["Bone"].hide is False
        and list(dst.get("goo_isolate_backup", [])) == ["Bone.002"],
        f"hidden_before={hidden_before}",
    )
    restored = bpy.ops.goo.pose_isolate_bones()
    check(
        "pose isolate restore",
        restored == {"FINISHED"}
        and dst.data.bones["Bone.002"].hide is False
        and "goo_isolate_backup" not in dst,
    )

    flagged = bpy.ops.goo.pose_outliner_flag(value=True)
    pose_bone = dst.pose.bones["Bone"]
    check(
        "pose outliner flag set",
        flagged == {"FINISHED"}
        and pose_bone.hide_outliner is True
        and bool(pose_bone.get("goo_hide_outliner")) is True,
        f"flagged={flagged}",
    )
    cleared = bpy.ops.goo.pose_outliner_flag(value=False)
    check(
        "pose outliner flag clear",
        cleared == {"FINISHED"} and pose_bone.hide_outliner is False,
        f"cleared={cleared}",
    )

    bpy.ops.object.mode_set(mode="OBJECT")
    for obj in (src, dst):
        bpy.data.objects.remove(obj, do_unlink=True)


def test_light_groups(scene: bpy.types.Scene) -> None:
    # Clean the scene created by factory startup.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        if coll.name.startswith("GOO_LG_"):
            bpy.data.collections.remove(coll)

    scene.goo_light_groups.clear()
    key_group = scene.goo_light_groups.add()
    key_group.name = "key"
    key_group.use_shadow = True
    fill_group = scene.goo_light_groups.add()
    fill_group.name = "fill"
    fill_group.use_shadow = True

    cubes = {}
    for name, x in (("A", -1.8), ("B", 1.8)):
        bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
        obj = bpy.context.object
        obj.name = name
        mat = bpy.data.materials.new(f"MAT_{name}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
        bsdf.inputs["Roughness"].default_value = 1.0
        obj.data.materials.append(mat)
        cubes[name] = (obj, mat)

    # Materials: A linked to key, B linked to fill.
    for name, group in (("A", "key"), ("B", "fill")):
        obj, mat = cubes[name]
        item = mat.goo_light_groups.add()
        item.name = group

    lights = {}
    for name, color, x, group in (
        ("key", (1.0, 0.15, 0.15, 1.0), -2.5, "key"),
        ("fill", (0.15, 0.2, 1.0, 1.0), 2.5, "fill"),
    ):
        data = bpy.data.lights.new(name, type="SUN")
        data.energy = 5.0
        data.color = color[:3]
        obj = bpy.data.objects.new(name, data)
        bpy.context.collection.objects.link(obj)
        obj.rotation_euler = (math.radians(55), 0, math.radians(25 if name == "key" else -25))
        obj.goo_light_group = group
        lights[name] = obj

    cam_data = bpy.data.cameras.new("TestCam")
    cam = bpy.data.objects.new("TestCam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 6.0
    cam.location = (0, 0, 8)
    cam.rotation_euler = (0, 0, 0)
    scene.camera = cam

    info = lg.sync_scene(scene, verbose=True)
    print("SYNC", info)
    check("sync_scene ran", isinstance(info, dict) and bool(info))
    check("sync_scene receivers key/fill", info.get("receivers", {}).get("key") == 1 and info.get("receivers", {}).get("fill") == 1, f"receivers={info.get('receivers')}")
    passes = lg.collect_passes(scene)
    print("PASSES", [(p.get("group"), [o.name for o in p.get("lights", [])]) for p in passes])
    check("collect_passes covers key/fill", {"key", "fill"} <= {p.get("group") for p in passes})

    out = os.path.join(REPO, "_work", "lead", "addon_lg")
    os.makedirs(out, exist_ok=True)
    left = render_mean(scene, os.path.join(out, "lg.png"))
    print("LIGHT-LINKING MEAN", left)
    # With linking, A is red-lit and B blue-lit; the average is far from neutral.
    spread = max(left) - min(left)
    check("render shows per-group tint", spread > 0.01, f"mean={left}")


def test_compositor(scene: bpy.types.Scene) -> None:
    if not hasattr(bpy.ops.goo, "setup_compositor"):
        check("goo.setup_compositor exists", False)
        return
    try:
        bpy.ops.goo.setup_compositor()
    except Exception:
        traceback.print_exc()
        check("setup_compositor()", False, "exception")
        return
    check("compositing_node_group set", scene.compositing_node_group is not None)
    if scene.compositing_node_group is not None:
        check("compositor has render layers", any(n.bl_idname == "CompositorNodeRLayers" for n in scene.compositing_node_group.nodes))
        check("compositor has group node", any(n.bl_idname == "CompositorNodeGroup" for n in scene.compositing_node_group.nodes))


def test_overrides(scene: bpy.types.Scene) -> None:
    """Per-view-layer overrides, collection force-enable, topology mirror, camera."""
    for op in (
        "clear_view_layer_overrides",
        "collection_force_enable_children",
        "toggle_topology_mirror",
        "set_scene_camera",
    ):
        check(f"operator goo.{op}", hasattr(bpy.ops.goo, op))
    check("Material.goo_check_self_shadowing", hasattr(bpy.types.Material, "goo_check_self_shadowing"))
    check("Scene.goo_high_bit_depth", hasattr(bpy.types.Scene, "goo_high_bit_depth"))

    vl = scene.view_layers[0]
    mat = bpy.data.materials.new("GOO_Override_Mat")
    vl.material_override = mat
    vl.world_override = scene.world
    vl.samples = 7
    result = bpy.ops.goo.clear_view_layer_overrides()
    check("clear_view_layer_overrides", result == {"FINISHED"} and vl.material_override is None and vl.world_override is None and vl.samples == 0)

    # Force-enable a hidden child collection.
    parent = bpy.data.collections.new("GOO_Parent")
    child = bpy.data.collections.new("GOO_Child")
    parent.children.link(child)
    child.hide_viewport = True
    child.hide_render = True
    scene.collection.children.link(parent)
    bpy.context.view_layer.layer_collection.children[parent.name].exclude = False
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection.children[parent.name]
    result = bpy.ops.goo.collection_force_enable_children()
    check("collection_force_enable_children", result == {"FINISHED"} and not child.hide_viewport and not child.hide_render)
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection

    # Topology mirror toggle on the selected cube.
    cube = bpy.context.object if bpy.context.object and bpy.context.object.type == "MESH" else None
    if cube is None:
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube
    before = cube.data.use_mirror_topology
    result = bpy.ops.goo.toggle_topology_mirror()
    check("toggle_topology_mirror", result == {"FINISHED"} and cube.data.use_mirror_topology != before)

    # Scene camera by name.
    cam_data = bpy.data.cameras.new("GOO_Cam_Target")
    cam = bpy.data.objects.new("GOO_Cam_Target", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = None
    result = bpy.ops.goo.set_scene_camera(camera_name=cam.name)
    check("set_scene_camera", result == {"FINISHED"} and scene.camera == cam)


def test_lifecycle() -> None:
    """Disabling and re-enabling the add-on must remove and restore classes."""
    check("lifecycle: pose property registered", hasattr(bpy.types.PoseBone, "hide_outliner"))
    check("lifecycle: scene property registered", hasattr(bpy.types.Scene, "goo_light_groups"))
    try:
        goo_npr.unregister()
    except Exception:
        traceback.print_exc()
        check("lifecycle: unregister()", False, "exception")
        return
    check("lifecycle: unregister()", True)
    check("lifecycle: pose property removed", not hasattr(bpy.types.PoseBone, "hide_outliner"))
    check("lifecycle: scene property removed", not hasattr(bpy.types.Scene, "goo_light_groups"))
    try:
        goo_npr.register()
    except Exception:
        traceback.print_exc()
        check("lifecycle: re-register()", False, "exception")
        return
    check("lifecycle: re-register()", True)
    check("lifecycle: pose property restored", hasattr(bpy.types.PoseBone, "hide_outliner"))
    check("lifecycle: scene property restored", hasattr(bpy.types.Scene, "goo_light_groups"))


def main() -> None:
    test_registration()
    test_node_library()
    test_scene_time()
    test_active_camera()
    test_i18n_table()
    test_gp_smooth()
    test_light_info_new()
    test_pose_tools()
    scene = setup_render_scene()
    test_light_groups(scene)
    test_compositor(scene)
    test_overrides(scene)
    test_lifecycle()
    print(f"\n{PASSES} passed, {len(FAILS)} failed")
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        sys.exit(1)


if __name__ == "__main__":
    main()
