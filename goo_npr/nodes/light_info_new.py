# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Light Info (new) — one light as plain values plus live drivers.

GOO 4.4 ships two Light Info nodes:

* ``node_shader_light_info.cc`` (legacy): Color / Power / Perceptual Power.
* ``node_shader_light_info_new.cc``: Color / Power / Position / Rotation /
  Distance, with a per-node ``light_object`` pointer.

Blender 5.2 group interfaces cannot carry object sockets, so this port exposes
the five values as inputs and an operator writes drivers from a real light
object.  GOO's own file declares ``Light Distance`` but its GPU function never
feeds the value (the source comment says "simplified version without distance
calculation").  Here the distance is an explicit extension: the distance from
the scene camera to the light, written as a driver expression.
"""

from __future__ import annotations

import bpy

from ..utils import gin, gout, iface, in_sock, link, log, out_sock, safe_unregister
from .registry import group

GROUP_NAME = "GOO Light Info New"

#: Input socket -> (output socket, default, description)
_SCALARS = (
    (
        "Light Power In",
        "Light Power",
        1000.0,
        "Light energy in watts / 灯光功率",
    ),
    (
        "Light Distance In",
        "Light Distance",
        0.0,
        "Distance from the scene camera to the light (port extension) / 相机到灯光的距离（移植扩展）",
    ),
)
_VECTORS = (
    (
        "Light Position In",
        "Light Position",
        "Light object location (world when unparented) / 灯光对象位置",
    ),
    (
        "Light Rotation In",
        "Light Rotation",
        "Light object Euler rotation in radians / 灯光欧拉旋转",
    ),
)


@group(
    GROUP_NAME,
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO Light Info New",
    description=(
        "GOO 4.4's new Light Info node: color, power, position, rotation and camera "
        "distance of one light. Group sockets cannot carry object references in Blender "
        "5.2; use 'GOO: Light Info New From Active Light' to write drivers. / GOO 新版灯光"
        "信息节点（颜色/功率/位置/旋转/距离），用操作符绑定真实灯光。"
    ),
    version=1,
    featured_inputs=("Light Color In", "Light Power In", "Light Distance In"),
)
def build_light_info_new(tree: bpy.types.NodeTree) -> None:
    """Pass the five light values through to GOO-named outputs."""
    iface(
        tree,
        "Light Color In",
        "INPUT",
        "NodeSocketColor",
        default=(1.0, 1.0, 1.0, 1.0),
        description="Raw light color / 原始灯光颜色",
    )
    for name, _out, default, description in _SCALARS:
        iface(tree, name, "INPUT", "NodeSocketFloat", default=default, description=description)
    for name, _out, description in _VECTORS:
        iface(tree, name, "INPUT", "NodeSocketVector", default=(0.0, 0.0, 0.0), description=description)

    out_sock_color = "Light Color"
    iface(
        tree,
        out_sock_color,
        "OUTPUT",
        "NodeSocketColor",
        description="Raw light color (GOO compatible) / 原始灯光颜色",
    )
    for _in, name, _default, description in _SCALARS:
        iface(tree, name, "OUTPUT", "NodeSocketFloat", description=description)
    for _in, name, description in _VECTORS:
        iface(tree, name, "OUTPUT", "NodeSocketVector", description=description)

    gin(tree, (-700.0, 0.0))
    gout(tree, (520.0, 0.0))

    link(tree, in_sock(tree, "Light Color In"), out_sock(tree, "Light Color"))
    for name, _out, _default, _description in _SCALARS:
        link(tree, in_sock(tree, name), out_sock(tree, name.replace(" In", "")))
    for name, _out, _description in _VECTORS:
        link(tree, in_sock(tree, name), out_sock(tree, name.replace(" In", "")))

    for node in tree.nodes:
        node.select = False

    log(
        "GOO Light Info New: five value sockets; use goo.light_info_new_from_active "
        "to drive them from a light object"
    )


# ---------------------------------------------------------------------------
# Operator: bind the group to a real light
# ---------------------------------------------------------------------------
def _driver_path(node_name: str, socket_name: str) -> str:
    escaped = node_name.replace('"', '\\"')
    return f'nodes["{escaped}"].inputs["{socket_name}"].default_value'


def _socket_index(target, name: str) -> int:
    for index, socket in enumerate(target.inputs):
        if socket.name == name:
            return index
    return -1


class GOO_OT_light_info_new_from_active(bpy.types.Operator):
    """Drive GOO Light Info New from one light object"""

    bl_idname = "goo.light_info_new_from_active"
    bl_label = "GOO: Light Info New From Active Light"
    bl_description = (
        "Write color, power, position, rotation and camera distance drivers from a light "
        "on the selected GOO Light Info New node / 将灯光的位置/旋转/颜色/功率/距离写入驱动"
    )
    bl_options = {"REGISTER", "UNDO"}

    light_name: bpy.props.StringProperty(
        name="Light",
        description="Name of the light object to read (defaults to the active / selected light)",
    )
    material_name: bpy.props.StringProperty(
        name="Material",
        description="Material containing the GOO Light Info New node (defaults to the active material)",
    )

    # -- resolution ---------------------------------------------------------
    def _resolve_light(self, context):
        if self.light_name:
            light = bpy.data.objects.get(self.light_name)
            if light is not None and light.type == "LIGHT":
                return light
        active = getattr(context, "active_object", None)
        if active is not None and active.type == "LIGHT":
            return active
        for obj in getattr(context, "selected_objects", ()) or ():
            if obj.type == "LIGHT":
                return obj
        scene = getattr(context, "scene", None)
        for obj in getattr(scene, "objects", ()) if scene is not None else ():
            if obj.type == "LIGHT":
                return obj
        return None

    def _resolve_material(self, context):
        if self.material_name:
            material = bpy.data.materials.get(self.material_name)
            if material is not None:
                return material
        active = getattr(context, "active_object", None)
        if active is not None:
            material = getattr(active, "active_material", None)
            if material is not None:
                return material
        for material in bpy.data.materials:
            if self._find_node(material) is not None:
                return material
        return None

    @staticmethod
    def _find_node(material):
        tree = getattr(material, "node_tree", None) if material is not None else None
        if tree is None:
            return None
        candidates = [
            node
            for node in tree.nodes
            if node.bl_idname == "ShaderNodeGroup"
            and getattr(node, "node_tree", None) is not None
            and node.node_tree.name == GROUP_NAME
        ]
        for node in candidates:
            if node.select:
                return node
        return candidates[0] if candidates else None

    # -- drivers ------------------------------------------------------------
    @staticmethod
    def _clear_drivers(tree, target) -> int:
        animation = getattr(tree, "animation_data", None)
        if animation is None or not animation.drivers:
            return 0
        removed = 0
        for name in (
            "Light Color In",
            "Light Power In",
            "Light Distance In",
            "Light Position In",
            "Light Rotation In",
        ):
            path = _driver_path(target.name, name)
            is_vector = name.endswith("Position In") or name.endswith("Rotation In")
            for channel in ((0, 1, 2) if is_vector else (None,)):
                try:
                    if channel is None:
                        tree.driver_remove(path)
                    else:
                        tree.driver_remove(path, channel)
                    removed += 1
                except (TypeError, RuntimeError):
                    pass
        return removed

    @staticmethod
    def _add_var(fcurve, variable_name: str, target_id, data_path: str) -> None:
        variable = fcurve.driver.variables.new()
        variable.name = variable_name
        variable.type = "SINGLE_PROP"
        target = variable.targets[0]
        target.id_type = "OBJECT"
        target.id = target_id
        target.data_path = data_path
        fcurve.driver.expression = variable_name

    def _write_drivers(self, tree, target, light, camera) -> int:
        written = 0
        for channel in range(3):
            fcurve = tree.driver_add(_driver_path(target.name, "Light Color In"), channel)
            self._add_var(fcurve, "c", light, f"data.color[{channel}]")
            written += 1

        fcurve = tree.driver_add(_driver_path(target.name, "Light Power In"))
        self._add_var(fcurve, "e", light, "data.energy")
        written += 1

        for channel in range(3):
            fcurve = tree.driver_add(_driver_path(target.name, "Light Position In"), channel)
            self._add_var(fcurve, "p", light, f"location[{channel}]")
            written += 1

        for channel in range(3):
            fcurve = tree.driver_add(_driver_path(target.name, "Light Rotation In"), channel)
            self._add_var(fcurve, "r", light, f"rotation_euler[{channel}]")
            written += 1

        if camera is not None:
            # sqrt((lx-cx)^2 + (ly-cy)^2 + (lz-cz)^2), plain driver expression.
            fcurve = tree.driver_add(_driver_path(target.name, "Light Distance In"))
            fcurve.driver.type = "SCRIPTED"
            for prefix, obj in (("p", light), ("c", camera)):
                for axis, index in (("x", 0), ("y", 1), ("z", 2)):
                    variable = fcurve.driver.variables.new()
                    variable.name = f"{prefix}{axis}"
                    variable.type = "SINGLE_PROP"
                    variable.targets[0].id_type = "OBJECT"
                    variable.targets[0].id = obj
                    variable.targets[0].data_path = f"location[{index}]"
            fcurve.driver.expression = (
                "sqrt((px-cx)**2 + (py-cy)**2 + (pz-cz)**2)"
            )
            written += 1
        else:
            target.inputs[_socket_index(target, "Light Distance In")].default_value = 0.0

        return written

    def execute(self, context):
        light = self._resolve_light(context)
        if light is None:
            self.report({"ERROR"}, "GOO: no light object found / 未找到灯光对象")
            return {"CANCELLED"}

        material = self._resolve_material(context)
        if material is None or getattr(material, "node_tree", None) is None:
            self.report({"ERROR"}, "GOO: no material with a node tree found / 未找到节点材质")
            return {"CANCELLED"}

        target = self._find_node(material)
        if target is None:
            self.report(
                {"ERROR"},
                "GOO: material has no 'GOO Light Info New' group node / 材质中没有 GOO Light Info New 节点",
            )
            return {"CANCELLED"}

        tree = material.node_tree
        camera = getattr(context.scene, "camera", None)
        try:
            removed = self._clear_drivers(tree, target)
            written = self._write_drivers(tree, target, light, camera)
        except Exception as exc:  # pragma: no cover - reported to the user
            self.report({"ERROR"}, f"GOO: could not write light drivers: {exc}")
            return {"CANCELLED"}

        target.label = light.name
        log(
            f"GOO Light Info New: wrote {written} drivers on {material.name!r} from "
            f"{light.name!r} (removed {removed} old, camera={getattr(camera, 'name', None)!r})"
        )
        self.report({"INFO"}, f"GOO: {written} drivers from light {light.name!r}")
        return {"FINISHED"}


def register() -> None:
    bpy.utils.register_class(GOO_OT_light_info_new_from_active)


def unregister() -> None:
    bpy.utils.unregister_class(GOO_OT_light_info_new_from_active)


__all__ = [
    "GROUP_NAME",
    "GOO_OT_light_info_new_from_active",
    "build_light_info_new",
    "register",
    "unregister",
]
