# SPDX-License-Identifier: GPL-3.0-or-later
"""Small GOO utility groups (ShaderNodeTree, category ``utility``).

* ``GOO Depth Fade`` - camera depth driven fade factor plus a two color mix,
  handy for fog / distance fade on NPR materials.
* ``GOO Threshold Mask`` - smooth threshold with a softness band.
"""

from __future__ import annotations

import bpy

from ..utils import gin, gout, iface, in_sock, link, log, mix_rgb, node, out_sock
from .registry import group


def _math(tree, operation: str, loc, *, clamp: bool = False, label: str | None = None):
    n = node(tree, "ShaderNodeMath", loc=loc, label=label)
    n.operation = operation
    n.use_clamp = clamp
    return n


def _map_range(tree, loc, *, interpolation: str = "LINEAR", label: str | None = None):
    n = node(tree, "ShaderNodeMapRange", loc=loc, label=label)
    n.data_type = "FLOAT"
    n.interpolation_type = interpolation
    n.clamp = True
    return n


def _mix_float(tree, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "FLOAT"
    return n


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


# ---------------------------------------------------------------------------
# GOO Depth Fade
# ---------------------------------------------------------------------------
@group(
    "GOO Depth Fade",
    tree_type="ShaderNodeTree",
    category="utility",
    label="GOO Depth Fade",
    description=(
        "Fade a color by camera depth: Factor goes 0 -> 1 between Start and End, Invert flips it, "
        "Color mixes Color A / Color B with that factor. / 按相机深度淡出（雾气/远景淡出）。"
    ),
    version=1,
    featured_inputs=("Start", "End", "Invert"),
)
def build_depth_fade(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Start",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        description="Distance where the fade starts / 淡出起点",
    )
    iface(
        tree,
        "End",
        "INPUT",
        "NodeSocketFloat",
        default=10.0,
        description="Distance where the fade ends / 淡出终点",
    )
    iface(
        tree,
        "Invert",
        "INPUT",
        "NodeSocketBool",
        default=False,
        description="Flip the fade direction / 反转",
    )
    iface(
        tree,
        "Color A",
        "INPUT",
        "NodeSocketColor",
        default=(1.0, 1.0, 1.0, 1.0),
        description="Color at Factor 0 / 起始颜色",
    )
    iface(
        tree,
        "Color B",
        "INPUT",
        "NodeSocketColor",
        default=(0.0, 0.0, 0.0, 1.0),
        description="Color at Factor 1 / 结束颜色",
    )

    _out_socket(tree, "Factor", "NodeSocketFloat", description="Fade factor 0..1 / 淡出系数")
    _out_socket(tree, "Color", "NodeSocketColor", description="Mixed color / 混合颜色")

    gin(tree, (-1000.0, 0.0))
    gout(tree, (520.0, 0.0))

    camera = node(tree, "ShaderNodeCameraData", loc=(-900.0, 200.0), label="Camera Data")

    fade = _map_range(tree, (-700.0, 200.0), interpolation="SMOOTHSTEP", label="Depth Fade")
    link(tree, camera.outputs["View Z Depth"], fade.inputs["Value"])
    link(tree, in_sock(tree, "Start"), fade.inputs["From Min"])
    link(tree, in_sock(tree, "End"), fade.inputs["From Max"])
    fade.inputs["To Min"].default_value = 0.0
    fade.inputs["To Max"].default_value = 1.0

    inverted = _math(tree, "SUBTRACT", (-520.0, 340.0), label="1 - Factor")
    inverted.inputs[0].default_value = 1.0
    link(tree, fade.outputs["Result"], inverted.inputs[1])

    factor_mix = _mix_float(tree, (-340.0, 220.0), label="Invert")
    link(tree, in_sock(tree, "Invert"), factor_mix.inputs[0])
    link(tree, fade.outputs["Result"], factor_mix.inputs[2])
    link(tree, inverted.outputs[0], factor_mix.inputs[3])
    link(tree, factor_mix.outputs[0], out_sock(tree, "Factor"))

    color_mix = mix_rgb(tree, (-120.0, 80.0), blend="MIX")
    link(tree, factor_mix.outputs[0], color_mix.inputs[0])
    link(tree, in_sock(tree, "Color A"), color_mix.inputs[6])
    link(tree, in_sock(tree, "Color B"), color_mix.inputs[7])
    link(tree, color_mix.outputs[2], out_sock(tree, "Color"))

    _deselect(tree)

    log("GOO Depth Fade: smoothstep camera view Z fade, factor also drives the color mix")


# ---------------------------------------------------------------------------
# GOO Threshold Mask
# ---------------------------------------------------------------------------
@group(
    "GOO Threshold Mask",
    tree_type="ShaderNodeTree",
    category="utility",
    label="GOO Threshold Mask",
    description=(
        "Smooth threshold: Mask is 0 below Threshold - Softness/2, 1 above Threshold + Softness/2, "
        "smoothstep in between. / 平滑阈值遮罩。"
    ),
    version=1,
    featured_inputs=("Threshold", "Softness"),
)
def build_threshold_mask(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Value",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="Value to test / 输入值",
    )
    iface(
        tree,
        "Threshold",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="Threshold center / 阈值",
    )
    iface(
        tree,
        "Softness",
        "INPUT",
        "NodeSocketFloat",
        default=0.1,
        min_value=0.0,
        description="Width of the smooth band / 软边宽度",
    )

    _out_socket(tree, "Mask", "NodeSocketFloat", description="0 below, 1 above / 阈值遮罩")

    gin(tree, (-900.0, 0.0))
    gout(tree, (420.0, 0.0))

    safe_softness = _math(tree, "MAXIMUM", (-700.0, 180.0), label="max(Softness, 1e-4)")
    link(tree, in_sock(tree, "Softness"), safe_softness.inputs[0])
    safe_softness.inputs[1].default_value = 0.0001

    half = _math(tree, "MULTIPLY", (-520.0, 180.0), label="Softness / 2")
    link(tree, safe_softness.outputs[0], half.inputs[0])
    half.inputs[1].default_value = 0.5

    low = _math(tree, "SUBTRACT", (-340.0, 260.0), label="Threshold - Softness/2")
    link(tree, in_sock(tree, "Threshold"), low.inputs[0])
    link(tree, half.outputs[0], low.inputs[1])

    high = _math(tree, "ADD", (-340.0, 100.0), label="Threshold + Softness/2")
    link(tree, in_sock(tree, "Threshold"), high.inputs[0])
    link(tree, half.outputs[0], high.inputs[1])

    mask = _map_range(tree, (-120.0, 0.0), interpolation="SMOOTHSTEP", label="Mask")
    link(tree, in_sock(tree, "Value"), mask.inputs["Value"])
    link(tree, low.outputs[0], mask.inputs["From Min"])
    link(tree, high.outputs[0], mask.inputs["From Max"])
    mask.inputs["To Min"].default_value = 0.0
    mask.inputs["To Max"].default_value = 1.0
    link(tree, mask.outputs["Result"], out_sock(tree, "Mask"))

    _deselect(tree)

    log("GOO Threshold Mask: smoothstep band Threshold +/- max(Softness, 1e-4) / 2")

# ---------------------------------------------------------------------------
# hygiene
# ---------------------------------------------------------------------------
def _deselect(tree) -> None:
    """Clear node selection flags so the generated group is inert in the UI."""
    for n in tree.nodes:
        n.select = False
