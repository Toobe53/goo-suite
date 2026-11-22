# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-view-layer overrides and small GOO pipeline helpers (Blender 5.2).

GOO Engine adds material/world overrides per view layer, a force-enable path for
collection visibility, a camera drop-down and a transparency toggle in the World
tab.  Blender 5.2 already stores ``ViewLayer.material_override``,
``ViewLayer.world_override`` and ``ViewLayer.samples``; this module exposes them
in one GOO panel, adds the missing operators, and stores the two GOO flags that
have no native equivalent (``Material.goo_check_self_shadowing`` and
``Scene.goo_high_bit_depth``) so materials and the CLI can read them.

The operators are deliberately small and undoable; the panel only edits native
properties, so overrides survive save/reload exactly like any other view layer
setting.
"""

from __future__ import annotations

import bpy

from .utils import safe_unregister_all

# ---------------------------------------------------------------------------
# properties (no native equivalent)
# ---------------------------------------------------------------------------


def _register_properties() -> None:
    bpy.types.Material.goo_check_self_shadowing = bpy.props.BoolProperty(
        name="Check Self Shadowing",
        description=(
            "GOO Engine material flag: separate Cast Shadows from Self Shadows "
            "(costs extra memory in GOO; this port only stores the flag) "
            "/ GOO 材质标记：分离投射阴影与自阴影"
        ),
        default=False,
    )
    bpy.types.Scene.goo_high_bit_depth = bpy.props.BoolProperty(
        name="High ID Bit Depth",
        description=(
            "GOO Engine scene flag: 32-bit shadow IDs. Blender 5.2 has no shadow "
            "ID buffer; stored for the CLI / GOO 场景标记：高精度阴影 ID（仅记录）"
        ),
        default=False,
    )


def _unregister_properties() -> None:
    for owner, name in ((bpy.types.Material, "goo_check_self_shadowing"), (bpy.types.Scene, "goo_high_bit_depth")):
        if hasattr(owner, name):
            delattr(owner, name)


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------


class GOO_OT_clear_view_layer_overrides(bpy.types.Operator):
    """Clear material/world/sample overrides on the active view layer"""

    bl_idname = "goo.clear_view_layer_overrides"
    bl_label = "Clear GOO Overrides"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        vl = context.view_layer
        vl.material_override = None
        vl.world_override = None
        # ``samples`` is 0 when the Cycles per-view-layer override is off; keep
        # that default rather than guessing a value for EEVEE.
        vl.samples = 0
        self.report({"INFO"}, f"GOO: cleared overrides on view layer {vl.name!r}")
        return {"FINISHED"}


class GOO_OT_force_enable_collection_children(bpy.types.Operator):
    """Make the active collection and all its children visible for viewport and render"""

    bl_idname = "goo.collection_force_enable_children"
    bl_label = "Force Enable Collection Children"
    bl_options = {"REGISTER", "UNDO"}

    render: bpy.props.BoolProperty(name="Affect Render", default=True)
    viewport: bpy.props.BoolProperty(name="Affect Viewport", default=True)

    def execute(self, context):
        root = context.collection
        if root is None:
            self.report({"WARNING"}, "GOO: no active collection")
            return {"CANCELLED"}
        count = 0
        stack = [root]
        while stack:
            coll = stack.pop()
            if self.viewport:
                coll.hide_viewport = False
            if self.render:
                coll.hide_render = False
            count += 1
            stack.extend(child for child in coll.children if child not in stack)
        self.report({"INFO"}, f"GOO: forced {count} collection(s) visible")
        return {"FINISHED"}


class GOO_OT_toggle_topology_mirror(bpy.types.Operator):
    """Toggle Topology Mirror on selected mesh objects"""

    bl_idname = "goo.toggle_topology_mirror"
    bl_label = "Toggle Topology Mirror"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not meshes:
            self.report({"WARNING"}, "GOO: select at least one mesh object")
            return {"CANCELLED"}
        # Mirror to the new state of the first mesh so mixed selections converge.
        value = not bool(meshes[0].data.use_mirror_topology)
        for obj in meshes:
            obj.data.use_mirror_topology = value
        self.report({"INFO"}, f"GOO: topology mirror {'on' if value else 'off'} on {len(meshes)} mesh(es)")
        return {"FINISHED"}


class GOO_OT_set_scene_camera(bpy.types.Operator):
    """Set the active scene camera by name"""

    bl_idname = "goo.set_scene_camera"
    bl_label = "Set GOO Scene Camera"
    bl_options = {"REGISTER", "UNDO"}

    camera_name: bpy.props.StringProperty(name="Camera", default="")

    def execute(self, context):
        cam = bpy.data.objects.get(self.camera_name) if self.camera_name else None
        if cam is None or cam.type != "CAMERA":
            self.report({"WARNING"}, f"GOO: camera {self.camera_name!r} not found")
            return {"CANCELLED"}
        context.scene.camera = cam
        self.report({"INFO"}, f"GOO: active camera is {cam.name!r}")
        return {"FINISHED"}


CLASSES = (
    GOO_OT_clear_view_layer_overrides,
    GOO_OT_force_enable_collection_children,
    GOO_OT_toggle_topology_mirror,
    GOO_OT_set_scene_camera,
)


# ---------------------------------------------------------------------------
# panels
# ---------------------------------------------------------------------------


class GOO_PT_view_layer_overrides(bpy.types.Panel):
    bl_label = "GOO View Layer Overrides"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "view_layer"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        col = layout.column(align=True)
        for vl in scene.view_layers:
            box = col.box()
            box.label(text=vl.name, icon="RENDERLAYERS")
            row = box.row(align=True)
            row.prop(vl, "material_override", text="Material")
            row = box.row(align=True)
            row.prop(vl, "world_override", text="World")
            row = box.row(align=True)
            row.prop(vl, "samples", text="Samples")
            row = box.row(align=True)
            row.operator("goo.clear_view_layer_overrides", icon="X", text="Clear Overrides")
        box = layout.box()
        box.label(text="Scene", icon="SCENE_DATA")
        box.prop(scene, "camera", text="Camera")
        box.prop(scene, "goo_high_bit_depth", text="High ID Bit Depth")
        box.prop(scene.render, "film_transparent", text="Transparent Film")


class GOO_PT_world_goo(bpy.types.Panel):
    bl_label = "GOO World Settings"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "world"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene.render, "film_transparent", text="Transparent Film (GOO)")

        # GOO's "Transparency" toggle in the World tab: a material override that
        # lets a transparent world be composited over the scene.
        vl = context.view_layer
        layout.prop(vl, "world_override", text="World Override")
        layout.separator()
        box = layout.box()
        box.label(text="Collection visibility", icon="COLLECTION_COLOR_04")
        box.operator("goo.collection_force_enable_children", icon="HIDE_OFF")
        box.operator("goo.toggle_topology_mirror", icon="MOD_MIRROR")


class GOO_PT_material_goo(bpy.types.Panel):
    bl_label = "GOO Material Flags"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def draw(self, context):
        layout = self.layout
        mat = context.material
        layout.prop(mat, "goo_check_self_shadowing", text="Check Self Shadowing")


PANELS = (GOO_PT_view_layer_overrides, GOO_PT_world_goo, GOO_PT_material_goo)


def register() -> None:
    _register_properties()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for cls in PANELS:
        bpy.utils.register_class(cls)


def unregister() -> None:
    safe_unregister_all(PANELS)
    safe_unregister_all(CLASSES)
    _unregister_properties()
