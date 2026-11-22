# SPDX-License-Identifier: GPL-3.0-or-later
"""Compositor side of the GOO port.

GOO Engine 4.4 added a "Screen Space & Compositing" workflow: screen space
effects (line weights, fog) are applied after rendering instead of being baked
into every material.  Blender 5.2 evaluates compositing through a *compositor
node group* (``scene.compositing_node_group``); the groups below are portable,
plain compositor node graphs that can be dropped into any project.
"""

from __future__ import annotations

import bpy

from . import i18n
from .nodes.registry import group
from .utils import gin, gout, iface, link, node, safe_unregister_all

i18n.register(
    {
        "goo.comp.setup": {"en_US": "Setup GOO Compositor", "zh_CN": "创建 GOO 合成节点树"},
        "goo.comp.done": {"en_US": "GOO: compositor ready", "zh_CN": "GOO: 合成已就绪"},
        "goo.comp.hint": {
            "en_US": "Creates a compositor tree: Render Layers → GOO Line Composite → Group Output.",
            "zh_CN": "创建合成节点树：渲染层 → GOO 线条合成 → 组输出。",
        },
    }
)


def _mix_color(tree, loc, blend="MULTIPLY", label=""):
    mix = node(tree, "ShaderNodeMix", loc=loc, label=label or blend.title())
    mix.data_type = "RGBA"
    mix.blend_type = blend
    # Shared Mix node socket layout on 5.x: 0 Factor, 6 A (color), 7 B (color);
    # output 2 is the color result.
    return mix


# ---------------------------------------------------------------------------
# Compositor node groups
# ---------------------------------------------------------------------------


@group(
    "GOO Line Composite",
    tree_type="CompositorNodeTree",
    category="compositor",
    label="Line Composite",
    description="Multiply a screen space line mask over the render, with tint and strength",
    version=1,
)
def build_line_composite(tree):
    iface(tree, "Image", "INPUT", "NodeSocketColor", description="Rendered image")
    iface(
        tree,
        "Line Mask",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        min_value=0.0,
        max_value=1.0,
        description="Line mask (1 = line)",
    )
    iface(tree, "Line Color", "INPUT", "NodeSocketColor", default=(0.02, 0.02, 0.02, 1.0))
    iface(tree, "Strength", "INPUT", "NodeSocketFloat", default=1.0, min_value=0.0, max_value=10.0)
    iface(tree, "Composite", "OUTPUT", "NodeSocketColor")

    gin_node = gin(tree, loc=(-620, 0))
    gout_node = gout(tree, loc=(560, 0))

    mask_strength = node(tree, "ShaderNodeMath", loc=(-380, -160), label="Mask × Strength")
    mask_strength.operation = "MULTIPLY"
    link(tree, gin_node.outputs["Line Mask"], mask_strength.inputs[0])
    link(tree, gin_node.outputs["Strength"], mask_strength.inputs[1])

    tint = _mix_color(tree, (-160, -160), "MIX", "Line Tint")
    link(tree, mask_strength.outputs[0], tint.inputs[0])
    tint.inputs[6].default_value = (1.0, 1.0, 1.0, 1.0)
    link(tree, gin_node.outputs["Line Color"], tint.inputs[7])

    multiply = _mix_color(tree, (120, 0), "MULTIPLY", "Lines Over Image")
    link(tree, mask_strength.outputs[0], multiply.inputs[0])
    link(tree, gin_node.outputs["Image"], multiply.inputs[6])
    link(tree, tint.outputs[2], multiply.inputs[7])
    link(tree, multiply.outputs[2], gout_node.inputs["Composite"])


@group(
    "GOO Kuwahara NPR",
    tree_type="CompositorNodeTree",
    category="compositor",
    label="Kuwahara NPR",
    description="Painterly Kuwahara filter; flattens gradients while keeping edges",
    version=1,
)
def build_kuwahara(tree):
    iface(tree, "Image", "INPUT", "NodeSocketColor")
    iface(tree, "Size", "INPUT", "NodeSocketFloat", default=4.0, min_value=1.0, max_value=9.0)
    iface(tree, "Uniformity", "INPUT", "NodeSocketInt", default=4, min_value=1, max_value=32)
    iface(tree, "Sharpness", "INPUT", "NodeSocketFloat", default=0.5, min_value=0.0, max_value=1.0)
    iface(tree, "Eccentricity", "INPUT", "NodeSocketFloat", default=0.5, min_value=0.0, max_value=1.0)
    iface(tree, "Filtered", "OUTPUT", "NodeSocketColor")

    gin_node = gin(tree, loc=(-520, 0))
    gout_node = gout(tree, loc=(420, 0))
    kuwahara = node(tree, "CompositorNodeKuwahara", loc=(-40, 0))
    link(tree, gin_node.outputs["Image"], kuwahara.inputs["Image"])
    for src, dst in (("Size", "Size"), ("Uniformity", "Uniformity"), ("Sharpness", "Sharpness"), ("Eccentricity", "Eccentricity")):
        if src in gin_node.outputs and dst in kuwahara.inputs:
            link(tree, gin_node.outputs[src], kuwahara.inputs[dst])
    link(tree, kuwahara.outputs["Image"], gout_node.inputs["Filtered"])


@group(
    "GOO Depth Fog",
    tree_type="CompositorNodeTree",
    category="compositor",
    label="Depth Fog",
    description="Screen space fog driven by the depth pass",
    version=1,
)
def build_depth_fog(tree):
    iface(tree, "Image", "INPUT", "NodeSocketColor")
    iface(tree, "Depth", "INPUT", "NodeSocketFloat", default=0.0, description="Normalised or metric depth")
    iface(tree, "Fog Color", "INPUT", "NodeSocketColor", default=(0.6, 0.7, 0.85, 1.0))
    iface(tree, "Start", "INPUT", "NodeSocketFloat", default=8.0)
    iface(tree, "End", "INPUT", "NodeSocketFloat", default=30.0)
    iface(tree, "Strength", "INPUT", "NodeSocketFloat", default=1.0, min_value=0.0, max_value=1.0)
    iface(tree, "Composite", "OUTPUT", "NodeSocketColor")

    gin_node = gin(tree, loc=(-560, 0))
    gout_node = gout(tree, loc=(560, 0))
    mr = node(tree, "ShaderNodeMapRange", loc=(-320, -120), label="Depth → Factor")
    mr.clamp = True
    link(tree, gin_node.outputs["Start"], mr.inputs["From Min"])
    link(tree, gin_node.outputs["End"], mr.inputs["From Max"])
    link(tree, gin_node.outputs["Depth"], mr.inputs["Value"])
    mul = node(tree, "ShaderNodeMath", loc=(-100, -120), label="× Strength")
    mul.operation = "MULTIPLY"
    link(tree, mr.outputs["Result"], mul.inputs[0])
    link(tree, gin_node.outputs["Strength"], mul.inputs[1])
    mix = _mix_color(tree, (140, 0), "MIX", "Fog")
    link(tree, mul.outputs[0], mix.inputs[0])
    link(tree, gin_node.outputs["Image"], mix.inputs[6])
    link(tree, gin_node.outputs["Fog Color"], mix.inputs[7])
    link(tree, mix.outputs[2], gout_node.inputs["Composite"])


# ---------------------------------------------------------------------------
# Operator: build a usable compositor tree in the current scene
# ---------------------------------------------------------------------------


class GOO_OT_setup_compositor(bpy.types.Operator):
    """Create a GOO compositor node tree with a line composite pass"""

    bl_idname = "goo.setup_compositor"
    bl_label = "Setup GOO Compositor"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .nodes.registry import ensure_all

        scene = context.scene
        if scene is None:
            return {"CANCELLED"}
        groups = ensure_all(only=["GOO Line Composite"])
        line_group = groups["GOO Line Composite"]
        tree = bpy.data.node_groups.get("GOO Compositor")
        if tree is None:
            tree = bpy.data.node_groups.new("GOO Compositor", "CompositorNodeTree")
        for n in list(tree.nodes):
            tree.nodes.remove(n)
        for item in list(tree.interface.items_tree):
            if getattr(item, "in_out", "") == "OUTPUT":
                tree.interface.remove(item)
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        try:
            rl = tree.nodes.new("CompositorNodeRLayers")
            group_node = tree.nodes.new("CompositorNodeGroup")
            group_node.node_tree = line_group
            output = tree.nodes.new("NodeGroupOutput")
        except RuntimeError as exc:
            self.report({"ERROR"}, f"GOO: unable to build compositor nodes: {exc}")
            return {"CANCELLED"}
        rl.location = (-420, 0)
        group_node.location = (-120, 0)
        output.location = (220, 0)
        tree.links.new(rl.outputs["Image"], group_node.inputs["Image"])
        tree.links.new(group_node.outputs["Composite"], output.inputs["Image"])
        scene.compositing_node_group = tree
        self.report({"INFO"}, i18n.t("goo.comp.done"))
        return {"FINISHED"}


CLASSES = (GOO_OT_setup_compositor,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    safe_unregister_all(CLASSES)
