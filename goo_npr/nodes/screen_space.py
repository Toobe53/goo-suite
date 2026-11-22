# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO screen space information group (ShaderNodeTree).

A single, cheap group that exposes the screen space / camera space data NPR
shaders usually need.  Every output comes from EEVEE supported nodes
(TexCoord, Camera Data, Geometry, Layer Weight) - no Cycles fallback.
"""

from __future__ import annotations

import bpy

from ..utils import gin, gout, iface, link, log, node, out_sock
from .registry import group


def _out_socket(tree, name: str, socket_type: str, *, description: str = ""):
    """Declare a group output socket (same helper as shading.py)."""
    for item in tree.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "OUTPUT" and item.name == name:
            if description and item.description != description:
                item.description = description
            return item
    item = tree.interface.new_socket(name=name, in_out="OUTPUT", socket_type=socket_type)
    if description:
        item.description = description
    return item


@group(
    "GOO Screen Space Info",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO Screen Space Info",
    description=(
        "Screen position / UV, camera depth and distance, world position, facing ratio, "
        "fresnel and camera vector in one node. Screen Position.z carries the view depth. "
        "/ 屏幕空间信息节点（屏幕坐标、深度、菲涅尔等）。"
    ),
    version=1,
    featured_inputs=(),
)
def build_screen_space_info(tree: bpy.types.NodeTree) -> None:
    _out_socket(tree, "Screen Position", "NodeSocketVector", description="(Window.x, Window.y, View Z) / 屏幕位置")
    _out_socket(tree, "Screen UV", "NodeSocketVector", description="(Window.x, Window.y, 0) / 屏幕 UV")
    _out_socket(tree, "View Depth", "NodeSocketFloat", description="Camera view Z depth / 相机深度")
    _out_socket(tree, "View Distance", "NodeSocketFloat", description="Camera view distance / 相机距离")
    _out_socket(tree, "World Position", "NodeSocketVector", description="World space position / 世界坐标")
    _out_socket(tree, "Facing", "NodeSocketFloat", description="Layer Weight facing ratio / 朝向比")
    _out_socket(tree, "Fresnel", "NodeSocketFloat", description="Layer Weight fresnel / 菲涅尔")
    _out_socket(tree, "Camera Vector", "NodeSocketVector", description="Normalized incoming vector / 相机向量")

    gin(tree, (-1200.0, 0.0))
    gout(tree, (700.0, 0.0))

    tex_coord = node(tree, "ShaderNodeTexCoord", loc=(-1000.0, 260.0), label="Tex Coord")
    window = tex_coord.outputs["Window"]

    separate = node(tree, "ShaderNodeSeparateXYZ", loc=(-820.0, 260.0), label="Window XY")
    link(tree, window, separate.inputs["Vector"])

    camera = node(tree, "ShaderNodeCameraData", loc=(-1000.0, -100.0), label="Camera Data")
    geometry = node(tree, "ShaderNodeNewGeometry", loc=(-1000.0, -300.0), label="Geometry")

    screen_position = node(tree, "ShaderNodeCombineXYZ", loc=(-520.0, 300.0), label="Screen Position")
    link(tree, separate.outputs["X"], screen_position.inputs["X"])
    link(tree, separate.outputs["Y"], screen_position.inputs["Y"])
    link(tree, camera.outputs["View Z Depth"], screen_position.inputs["Z"])
    link(tree, screen_position.outputs["Vector"], out_sock(tree, "Screen Position"))

    screen_uv = node(tree, "ShaderNodeCombineXYZ", loc=(-520.0, 140.0), label="Screen UV")
    link(tree, separate.outputs["X"], screen_uv.inputs["X"])
    link(tree, separate.outputs["Y"], screen_uv.inputs["Y"])
    screen_uv.inputs["Z"].default_value = 0.0
    link(tree, screen_uv.outputs["Vector"], out_sock(tree, "Screen UV"))

    link(tree, camera.outputs["View Z Depth"], out_sock(tree, "View Depth"))
    link(tree, camera.outputs["View Distance"], out_sock(tree, "View Distance"))
    link(tree, geometry.outputs["Position"], out_sock(tree, "World Position"))

    facing = node(tree, "ShaderNodeLayerWeight", loc=(-820.0, -160.0), label="Layer Weight")
    facing.inputs["Blend"].default_value = 0.5
    link(tree, geometry.outputs["Normal"], facing.inputs["Normal"])
    link(tree, facing.outputs["Facing"], out_sock(tree, "Facing"))
    link(tree, facing.outputs["Fresnel"], out_sock(tree, "Fresnel"))

    incoming = node(tree, "ShaderNodeVectorMath", loc=(-820.0, -380.0), label="Camera Vector")
    incoming.operation = "NORMALIZE"
    link(tree, geometry.outputs["Incoming"], incoming.inputs[0])
    link(tree, incoming.outputs["Vector"], out_sock(tree, "Camera Vector"))

    _deselect(tree)

    log(
        "GOO Screen Space Info: TexCoord.Window + CameraData + Geometry + LayerWeight; "
        "Screen Position.z = view Z depth"
    )

# ---------------------------------------------------------------------------
# hygiene
# ---------------------------------------------------------------------------
def _deselect(tree) -> None:
    """Clear node selection flags so the generated group is inert in the UI."""
    for n in tree.nodes:
        n.select = False
