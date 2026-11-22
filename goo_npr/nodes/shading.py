# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO shading node groups (ShaderNodeTree).

All groups in this module are *look development* helpers for an EEVEE NPR
pipeline.  They are deliberately free of Cycles only nodes (Bevel, Pointiness,
...) and of object reference sockets, which ``NodeTreeInterface.new_socket``
does not support in Blender 5.2.

Deviation notes (repeated in the group descriptions):

* ``GOO Shader Info`` evaluates its own Diffuse BSDF through ``Shader to RGB``.
  EEVEE cannot re-light a scene from inside a shader, so the diffuse terms are
  a stable, view independent approximation of GOO's first lighting pass.
* ``Cast Shadows`` / ``Self Shadows`` are Ambient Occlusion driven proxies,
  not a real shadow map lookup (EEVEE exposes no shadow mask node).
* ``GOO Set Depth`` can only *encode* depth, it cannot write the real depth
  buffer (impossible in pure Python EEVEE).  The external CLI consumes the
  ``Depth`` socket through an AOV / EXR pass.
"""

from __future__ import annotations

import bpy

from ..utils import (
    gin,
    gout,
    iface,
    in_sock,
    link,
    log,
    mix_rgb,
    node,
    out_sock,
    safe_unregister_all,
    set_default,
)
from .registry import group

# ---------------------------------------------------------------------------
# small local helpers (kept private: they only exist for readability)
# ---------------------------------------------------------------------------


def _math(tree, operation: str, loc, *, clamp: bool = False, label: str | None = None):
    n = node(tree, "ShaderNodeMath", loc=loc, label=label)
    n.operation = operation
    n.use_clamp = clamp
    return n


def _mix_float(tree, loc, *, label: str | None = None):
    """Mix node in FLOAT mode.

    Socket indices for ``ShaderNodeMix`` are fixed (5.x): Factor=0, A=2, B=3,
    result=0.  ``inputs["A"]`` would resolve to the *float* A for every data
    type, so the explicit indices are required.
    """
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "FLOAT"
    return n


def _gray(tree, value_socket, loc, *, label: str | None = None):
    """Combine one float socket into an RGB color (gray)."""
    n = node(tree, "ShaderNodeCombineColor", loc=loc, label=label)
    for channel in ("Red", "Green", "Blue"):
        link(tree, value_socket, n.inputs[channel])
    return n


def _map_range(tree, loc, *, interpolation: str = "LINEAR", label: str | None = None):
    n = node(tree, "ShaderNodeMapRange", loc=loc, label=label)
    n.data_type = "FLOAT"
    n.interpolation_type = interpolation
    n.clamp = True
    return n


def _out_socket(tree, name: str, socket_type: str, *, description: str = ""):
    """Declare an output socket that may share its name with an input socket.

    ``utils.iface`` looks sockets up by name through ``items_tree.get(name)``
    which returns the *most recently added* item.  Declaring ``Power`` as an
    output would therefore delete the ``Power`` input.  This helper only
    matches sockets of the requested direction (GOO Shader Info / Light Info
    expose the same name on both sides, exactly like GOO Engine).
    """
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
# GOO Shader Info
# ---------------------------------------------------------------------------
@group(
    "GOO Shader Info",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO Shader Info",
    description=(
        "GOO Shader Info approximation. GOO Engine (C++) splits every light into diffuse / "
        "cast shadow / self shadow / ambient terms in its patched EEVEE; Blender 5.2 exposes "
        "none of that per-light data to Python, so this group evaluates a white Diffuse BSDF "
        "through Shader to RGB and derives the shadow outputs from Ambient Occlusion. "
        "Socket names match node_shader_shader_info.cc. "
        "/ GOO 着色信息节点的近似实现（5.2 无法逐灯拆分，阴影用 AO 代理）。"
    ),
    version=1,
    featured_inputs=("Ambient Color", "Ambient Strength"),
)
def build_shader_info(tree: bpy.types.NodeTree) -> None:
    """GOO's central node: shaded diffuse, shadow masks and ambient light.

    Half Lambert formula (documented deviation): with ``L`` the luminance of
    the shaded diffuse color (0..1+, HDR allowed), the wrapper output is
    ``HL = clamp(0.5 * L + 0.5, 0, 1)``.  Enabling ``Half Lambert`` multiplies
    the diffuse color by ``clamp(HL / max(L, 0.02), 1, 8)`` so that the dark
    side stays readable instead of fading to black.  A real GOO half lambert
    replaces the ``N.L`` clamp; EEVEE cannot re-run its lighting from inside a
    shader, hence the luminance based approximation.
    """
    # -- interface ---------------------------------------------------------
    iface(
        tree,
        "Half Lambert",
        "INPUT",
        "NodeSocketBool",
        default=False,
        description="Enable the wrapped lighting approximation / 启用半兰伯特",
    )
    iface(
        tree,
        "Check Self Shadowing",
        "INPUT",
        "NodeSocketBool",
        default=False,
        description=(
            "Sample the extra self shadow mask (memory heavy in GOO, off by default) "
            "/ 是否计算自阴影（默认关闭）"
        ),
    )
    iface(
        tree,
        "Ambient Color",
        "INPUT",
        "NodeSocketColor",
        default=(0.55, 0.62, 0.72, 1.0),
        description="Ambient tint / 环境光颜色",
    )
    iface(
        tree,
        "Ambient Strength",
        "INPUT",
        "NodeSocketFloat",
        default=0.15,
        min_value=0.0,
        max_value=1.0,
        subtype="FACTOR",
        description="Ambient light strength / 环境光强度",
    )
    iface(
        tree,
        "Shadow Strength",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        min_value=0.0,
        max_value=1.0,
        subtype="FACTOR",
        description="Scale the cast shadow mask / 投影遮罩强度",
    )
    iface(
        tree,
        "AO Distance",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        min_value=0.0,
        description="Ambient occlusion radius used for the shadow proxy / 阴影代理 AO 半径",
    )
    iface(
        tree,
        "Self Shadow Distance",
        "INPUT",
        "NodeSocketFloat",
        default=0.15,
        min_value=0.0,
        description="Shorter AO radius used for the self shadow proxy / 自阴影 AO 半径",
    )

    _out_socket(tree, "Diffuse Shading", "NodeSocketColor", description="Shaded diffuse color / 漫反射着色")
    _out_socket(tree, "Cast Shadows", "NodeSocketFloat", description="Cast shadow mask 0..1 / 投影遮罩")
    _out_socket(tree, "Self Shadows", "NodeSocketFloat", description="Self shadow mask 0..1 / 自阴影遮罩")
    _out_socket(tree, "Ambient Lighting", "NodeSocketColor", description="Ambient light color / 环境光")
    # GOO spells this socket "Half-lambert factor" (node_shader_shader_info.cc).
    _out_socket(tree, "Half-lambert factor", "NodeSocketFloat", description="Wrapped luminance 0..1 / 半兰伯特值")

    gin(tree, (-1500.0, 260.0))
    gout(tree, (520.0, 260.0))

    # -- shaded diffuse ----------------------------------------------------
    diffuse = node(tree, "ShaderNodeBsdfDiffuse", loc=(-1320.0, 580.0), label="White Diffuse")
    diffuse.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    diffuse.inputs["Roughness"].default_value = 0.5
    s2r = node(tree, "ShaderNodeShaderToRGB", loc=(-1140.0, 580.0), label="Shader to RGB")
    link(tree, diffuse.outputs[0], s2r.inputs[0])

    luminance = node(tree, "ShaderNodeRGBToBW", loc=(-960.0, 440.0), label="Diffuse Luminance")
    link(tree, s2r.outputs["Color"], luminance.inputs["Color"])

    # Half Lambert = clamp(0.5 * L + 0.5)
    half = _math(tree, "MULTIPLY_ADD", (-780.0, 440.0), clamp=True, label="Half Lambert")
    link(tree, luminance.outputs["Val"], half.inputs[0])
    half.inputs[1].default_value = 0.5
    half.inputs[2].default_value = 0.5

    # Boost factor for the wrapped lighting: HL / max(L, 0.02), clamped 1..8.
    safe_l = _math(tree, "MAXIMUM", (-960.0, 260.0), label="max(L, 0.02)")
    link(tree, luminance.outputs["Val"], safe_l.inputs[0])
    safe_l.inputs[1].default_value = 0.02

    ratio = _math(tree, "DIVIDE", (-780.0, 260.0), label="HL / L")
    link(tree, half.outputs[0], ratio.inputs[0])
    link(tree, safe_l.outputs[0], ratio.inputs[1])

    ratio_min = _math(tree, "MAXIMUM", (-600.0, 260.0))
    link(tree, ratio.outputs[0], ratio_min.inputs[0])
    ratio_min.inputs[1].default_value = 1.0

    ratio_max = _math(tree, "MINIMUM", (-420.0, 260.0))
    link(tree, ratio_min.outputs[0], ratio_max.inputs[0])
    ratio_max.inputs[1].default_value = 8.0

    ratio_gray = _gray(tree, ratio_max.outputs[0], (-240.0, 260.0), label="Boost Color")

    boosted = mix_rgb(tree, (-60.0, 560.0), blend="MULTIPLY")
    boosted.inputs[0].default_value = 1.0
    link(tree, s2r.outputs["Color"], boosted.inputs[6])
    link(tree, ratio_gray.outputs["Color"], boosted.inputs[7])

    diffuse_mix = mix_rgb(tree, (150.0, 640.0), blend="MIX")
    link(tree, in_sock(tree, "Half Lambert"), diffuse_mix.inputs[0])
    link(tree, s2r.outputs["Color"], diffuse_mix.inputs[6])
    link(tree, boosted.outputs[2], diffuse_mix.inputs[7])
    link(tree, diffuse_mix.outputs[2], out_sock(tree, "Diffuse Shading"))

    # -- ambient occlusion proxies ----------------------------------------
    geom = node(tree, "ShaderNodeNewGeometry", loc=(-1500.0, -80.0))

    ao_wide = node(tree, "ShaderNodeAmbientOcclusion", loc=(-1320.0, 40.0), label="AO Wide")
    ao_wide.inside = False
    ao_wide.only_local = False
    ao_wide.samples = 16
    link(tree, in_sock(tree, "AO Distance"), ao_wide.inputs["Distance"])
    link(tree, geom.outputs["Normal"], ao_wide.inputs["Normal"])

    ao_narrow = node(tree, "ShaderNodeAmbientOcclusion", loc=(-1320.0, -200.0), label="AO Narrow")
    ao_narrow.inside = False
    ao_narrow.only_local = False
    ao_narrow.samples = 16
    link(tree, in_sock(tree, "Self Shadow Distance"), ao_narrow.inputs["Distance"])
    link(tree, geom.outputs["Normal"], ao_narrow.inputs["Normal"])

    # Cast Shadows = (1 - AO) * Shadow Strength
    cast_inv = _math(tree, "SUBTRACT", (-1140.0, 40.0), label="1 - AO")
    cast_inv.inputs[0].default_value = 1.0
    link(tree, ao_wide.outputs["AO"], cast_inv.inputs[1])

    cast = _math(tree, "MULTIPLY", (-960.0, 40.0), clamp=True, label="Cast Shadows")
    link(tree, cast_inv.outputs[0], cast.inputs[0])
    link(tree, in_sock(tree, "Shadow Strength"), cast.inputs[1])
    link(tree, cast.outputs[0], out_sock(tree, "Cast Shadows"))

    # Self Shadows = (1 - AO narrow) gated by Check Self Shadowing
    self_inv = _math(tree, "SUBTRACT", (-1140.0, -200.0), label="1 - AO (self)")
    self_inv.inputs[0].default_value = 1.0
    link(tree, ao_narrow.outputs["AO"], self_inv.inputs[1])

    self_gate = _mix_float(tree, (-960.0, -200.0), label="Self Shadow Gate")
    link(tree, in_sock(tree, "Check Self Shadowing"), self_gate.inputs[0])
    self_gate.inputs[2].default_value = 0.0
    link(tree, self_inv.outputs[0], self_gate.inputs[3])
    link(tree, self_gate.outputs[0], out_sock(tree, "Self Shadows"))

    # Ambient Lighting = Ambient Color * Ambient Strength * AO
    amb_ao = _gray(tree, ao_wide.outputs["AO"], (-1140.0, -400.0))
    amb_color = mix_rgb(tree, (-960.0, -400.0), blend="MULTIPLY")
    amb_color.inputs[0].default_value = 1.0
    link(tree, in_sock(tree, "Ambient Color"), amb_color.inputs[6])
    link(tree, amb_ao.outputs["Color"], amb_color.inputs[7])

    amb_strength = _gray(tree, in_sock(tree, "Ambient Strength"), (-1140.0, -580.0))
    amb_final = mix_rgb(tree, (-780.0, -460.0), blend="MULTIPLY")
    amb_final.inputs[0].default_value = 1.0
    link(tree, amb_color.outputs[2], amb_final.inputs[6])
    link(tree, amb_strength.outputs["Color"], amb_final.inputs[7])
    link(tree, amb_final.outputs[2], out_sock(tree, "Ambient Lighting"))

    link(tree, half.outputs[0], out_sock(tree, "Half-lambert factor"))

    _deselect(tree)

    log(
        "GOO Shader Info: EEVEE approximation - diffuse from Shader to RGB, "
        "shadow masks from Ambient Occlusion, half lambert = clamp(0.5*L+0.5)"
    )


# ---------------------------------------------------------------------------
# GOO Curvature
# ---------------------------------------------------------------------------
@group(
    "GOO Curvature",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO Curvature",
    description=(
        "Signed curvature proxy for EEVEE. Two Ambient Occlusion nodes (wide / narrow radius) "
        "are combined with Layer Weight Fresnel: convex edges > 0.5, flat = 0.5, creases < 0.5. "
        "Cycles only Pointiness / Bevel nodes are not used. "
        "/ 曲率代理节点（AO + 菲涅尔，凸边大于 0.5）。"
    ),
    version=1,
    featured_inputs=("Radius", "Contrast"),
)
def build_curvature(tree: bpy.types.NodeTree) -> None:
    """Signed curvature encoded as a grayscale color.

    Convex term: ``AO(wide) * Fresnel`` - Fresnel grows on silhouette / convex
    edges while a convex surface stays unoccluded (AO ≈ 1).
    Concave term: ``AO(narrow) - AO(wide)`` - a wider AO radius picks up more
    occluders inside creases, so the difference is positive there and ~0 on
    flat / convex areas.
    Encoded color: ``clamp(0.5 + 0.5 * (convex - concave))`` so convex edges
    are > 0.5, flat regions exactly 0.5 and creases < 0.5.
    """
    iface(
        tree,
        "Radius",
        "INPUT",
        "NodeSocketFloat",
        default=0.05,
        min_value=0.0001,
        description="Wide AO radius in scene units / 宽 AO 半径",
    )
    iface(
        tree,
        "Samples",
        "INPUT",
        "NodeSocketInt",
        default=8,
        min_value=1,
        max_value=64,
        description=(
            "Label only: EEVEE AO sample count is a render setting, not a linkable socket. "
            "The value is applied to the AO nodes when the group is rebuilt. / 重建时生效"
        ),
    )
    iface(
        tree,
        "Contrast",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        min_value=0.0,
        max_value=8.0,
        description="Contrast around 0.5 / 对比度",
    )

    _out_socket(tree, "Curvature", "NodeSocketColor", description="Signed curvature encoded as gray / 曲率灰度")
    _out_socket(tree, "Convex", "NodeSocketFloat", description="Convex edge mask / 凸边")
    _out_socket(tree, "Concave", "NodeSocketFloat", description="Concave crease mask / 凹处")
    _out_socket(tree, "Edge Mask", "NodeSocketFloat", description="Convex + concave / 边缘遮罩")

    gin(tree, (-1200.0, 0.0))
    gout(tree, (520.0, 0.0))

    # Samples is applied at build time (see docstring / interface description).
    samples_item = tree.interface.items_tree.get("Samples")
    samples = int(getattr(samples_item, "default_value", 8) or 8)

    geom = node(tree, "ShaderNodeNewGeometry", loc=(-1200.0, 0.0))

    narrow_radius = _math(tree, "MULTIPLY", (-1020.0, -160.0), label="Narrow Radius")
    link(tree, in_sock(tree, "Radius"), narrow_radius.inputs[0])
    narrow_radius.inputs[1].default_value = 0.25

    ao_wide = node(tree, "ShaderNodeAmbientOcclusion", loc=(-840.0, 160.0), label="AO Wide")
    ao_wide.inside = False
    ao_wide.only_local = False
    ao_wide.samples = samples
    link(tree, in_sock(tree, "Radius"), ao_wide.inputs["Distance"])
    link(tree, geom.outputs["Normal"], ao_wide.inputs["Normal"])

    ao_narrow = node(tree, "ShaderNodeAmbientOcclusion", loc=(-840.0, -160.0), label="AO Narrow")
    ao_narrow.inside = False
    ao_narrow.only_local = False
    ao_narrow.samples = samples
    link(tree, narrow_radius.outputs[0], ao_narrow.inputs["Distance"])
    link(tree, geom.outputs["Normal"], ao_narrow.inputs["Normal"])

    # Concave = max(AO narrow - AO wide, 0)
    conc = _math(tree, "SUBTRACT", (-660.0, -160.0), label="AO narrow - wide")
    link(tree, ao_narrow.outputs["AO"], conc.inputs[0])
    link(tree, ao_wide.outputs["AO"], conc.inputs[1])
    concave = _math(tree, "MAXIMUM", (-480.0, -160.0), clamp=True, label="Concave")
    link(tree, conc.outputs[0], concave.inputs[0])
    concave.inputs[1].default_value = 0.0
    link(tree, concave.outputs[0], out_sock(tree, "Concave"))

    # Convex = AO wide * Fresnel
    fresnel_node = node(tree, "ShaderNodeLayerWeight", loc=(-840.0, 320.0), label="Fresnel")
    fresnel_node.inputs["Blend"].default_value = 0.5
    link(tree, geom.outputs["Normal"], fresnel_node.inputs["Normal"])

    convex = _math(tree, "MULTIPLY", (-660.0, 240.0), clamp=True, label="Convex")
    link(tree, ao_wide.outputs["AO"], convex.inputs[0])
    link(tree, fresnel_node.outputs["Fresnel"], convex.inputs[1])
    link(tree, convex.outputs[0], out_sock(tree, "Convex"))

    # Signed = convex - concave, encoded to 0.5 + 0.5 * signed.
    signed = _math(tree, "SUBTRACT", (-480.0, 240.0), label="Signed Curvature")
    link(tree, convex.outputs[0], signed.inputs[0])
    link(tree, concave.outputs[0], signed.inputs[1])

    encoded = _math(tree, "MULTIPLY_ADD", (-300.0, 240.0), label="0.5 + 0.5 * signed")
    link(tree, signed.outputs[0], encoded.inputs[0])
    encoded.inputs[1].default_value = 0.5
    encoded.inputs[2].default_value = 0.5

    # Contrast around 0.5: c * x + (0.5 - 0.5 * c)
    neg_half = _math(tree, "MULTIPLY", (-120.0, 400.0))
    link(tree, in_sock(tree, "Contrast"), neg_half.inputs[0])
    neg_half.inputs[1].default_value = -0.5

    offset = _math(tree, "ADD", (60.0, 400.0))
    link(tree, neg_half.outputs[0], offset.inputs[0])
    offset.inputs[1].default_value = 0.5

    contrasted = _math(tree, "MULTIPLY_ADD", (-120.0, 240.0), clamp=True, label="Contrast")
    link(tree, encoded.outputs[0], contrasted.inputs[0])
    link(tree, in_sock(tree, "Contrast"), contrasted.inputs[1])
    link(tree, offset.outputs[0], contrasted.inputs[2])

    curvature_color = _gray(tree, contrasted.outputs[0], (220.0, 240.0))
    link(tree, curvature_color.outputs["Color"], out_sock(tree, "Curvature"))

    edge = _math(tree, "ADD", (220.0, 60.0), clamp=True, label="Edge Mask")
    link(tree, convex.outputs[0], edge.inputs[0])
    link(tree, concave.outputs[0], edge.inputs[1])
    link(tree, edge.outputs[0], out_sock(tree, "Edge Mask"))

    _deselect(tree)

    log(
        "GOO Curvature: AO based approximation (EEVEE has no Pointiness/Bevel for the "
        "shader path); Samples is a build time AO samples hint, not a live socket"
    )


# ---------------------------------------------------------------------------
# GOO Light Info
# ---------------------------------------------------------------------------
@group(
    "GOO Light Info",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO Light Info",
    description=(
        "Represents one light source as values (group sockets cannot carry object references "
        "in Blender 5.2). Use the operator 'GOO: Light Info From Active Light' to drive Color / "
        "Power / Specular from a real light. / 单个灯光信息的数值表示。"
    ),
    version=1,
    featured_inputs=("Color", "Power"),
)
def build_light_info(tree: bpy.types.NodeTree) -> None:
    """One light source expressed through value sockets."""
    iface(
        tree,
        "Color",
        "INPUT",
        "NodeSocketColor",
        default=(1.0, 1.0, 1.0, 1.0),
        description="Light color / 灯光颜色",
    )
    iface(
        tree,
        "Power",
        "INPUT",
        "NodeSocketFloat",
        default=1000.0,
        min_value=0.0,
        description="Light power in watts (1000 W = strength 1.0) / 灯光功率",
    )
    iface(
        tree,
        "Specular",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        min_value=0.0,
        description="Specular factor of the light / 高光系数",
    )

    # GOO node_shader_light_info.cc outputs Light Color / Light Power /
    # Perceptual Power; the last two are the same raw energy in its 4.4 source
    # (`perceptual_power = light->energy`).  The extra Scaled Color / Strength
    # sockets are a convenience of this port (they apply the power / specular
    # normalization that GOO expects the artist to do manually).
    _out_socket(tree, "Light Color", "NodeSocketColor", description="Raw light color (GOO compatible) / 原始灯光颜色")
    _out_socket(tree, "Light Power", "NodeSocketFloat", description="Light energy in watts / 灯光功率")
    _out_socket(tree, "Perceptual Power", "NodeSocketFloat", description="Perceptual power (same as Light Power in GOO 4.4) / 感知功率")
    _out_socket(tree, "Scaled Color", "NodeSocketColor", description="Color * power/1000 * specular (port helper) / 归一化颜色")
    _out_socket(tree, "Strength", "NodeSocketFloat", description="Light Power / 1000 (port helper) / 归一化强度")

    gin(tree, (-1100.0, 0.0))
    gout(tree, (520.0, 0.0))

    # Strength = max(Power / 1000, 0)
    divided = _math(tree, "DIVIDE", (-900.0, -180.0), label="Power / 1000")
    link(tree, in_sock(tree, "Power"), divided.inputs[0])
    divided.inputs[1].default_value = 1000.0

    strength = _math(tree, "MAXIMUM", (-720.0, -180.0), label="Strength")
    link(tree, divided.outputs[0], strength.inputs[0])
    strength.inputs[1].default_value = 0.0
    link(tree, strength.outputs[0], out_sock(tree, "Strength"))

    strength_gray = _gray(tree, strength.outputs[0], (-540.0, -180.0))

    colored = mix_rgb(tree, (-360.0, 120.0), blend="MULTIPLY")
    colored.inputs[0].default_value = 1.0
    link(tree, in_sock(tree, "Color"), colored.inputs[6])
    link(tree, strength_gray.outputs["Color"], colored.inputs[7])

    spec_gray = _gray(tree, in_sock(tree, "Specular"), (-540.0, -360.0))

    lit = mix_rgb(tree, (-180.0, 120.0), blend="MULTIPLY")
    lit.inputs[0].default_value = 1.0
    link(tree, colored.outputs[2], lit.inputs[6])
    link(tree, spec_gray.outputs["Color"], lit.inputs[7])
    link(tree, lit.outputs[2], out_sock(tree, "Scaled Color"))

    # Raw GOO semantics: the selected light's color and energy, unmodified.
    link(tree, in_sock(tree, "Color"), out_sock(tree, "Light Color"))
    link(tree, in_sock(tree, "Power"), out_sock(tree, "Light Power"))
    link(tree, in_sock(tree, "Power"), out_sock(tree, "Perceptual Power"))

    _deselect(tree)

    log(
        "GOO Light Info: value socket based (no Object sockets in 5.2 group interfaces); "
        "use goo.light_info_from_active to add drivers"
    )


# ---------------------------------------------------------------------------
# GOO Set Depth
# ---------------------------------------------------------------------------
@group(
    "GOO Set Depth",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO Set Depth",
    description=(
        "Depth modes for NPR work. Mode 0 = linear Near/Far, 1 = reciprocal (1/z), 2 = z / Far. "
        "IMPOSSIBLE IN EEVEE: this node cannot write the real depth buffer. The external CLI "
        "consumes the Depth socket through an AOV / EXR pass. / 深度模式（无法写入真实深度缓冲）。"
    ),
    version=1,
    featured_inputs=("Mode", "Near", "Far"),
)
def build_set_depth(tree: bpy.types.NodeTree) -> None:
    """Encode camera depth into a 0..1 value; never writes the depth buffer."""
    iface(
        tree,
        "Mode",
        "INPUT",
        "NodeSocketInt",
        default=0,
        min_value=0,
        max_value=2,
        description=(
            "0 = linear Near/Far, 1 = reciprocal, 2 = z/Far. "
            "(Menu sockets exist in 5.2 but cannot define items yet.) / 深度模式"
        ),
    )
    iface(
        tree,
        "Distance Scale",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        min_value=0.0,
        description="Multiply the view Z before encoding / 缩放相机距离",
    )
    iface(
        tree,
        "Offset",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        description="Add to the scaled view Z / 偏移",
    )
    iface(
        tree,
        "Near",
        "INPUT",
        "NodeSocketFloat",
        default=0.1,
        min_value=0.0,
        description="Near distance / 近裁剪",
    )
    iface(
        tree,
        "Far",
        "INPUT",
        "NodeSocketFloat",
        default=100.0,
        min_value=0.0001,
        description="Far distance / 远裁剪",
    )

    _out_socket(tree, "Depth", "NodeSocketFloat", description="Encoded depth 0..1 / 编码深度")
    _out_socket(tree, "View Z", "NodeSocketFloat", description="Raw camera view Z / 原始相机 Z")
    _out_socket(tree, "Alpha", "NodeSocketFloat", description="Depth clamped 0..1 / 深度 alpha")

    gin(tree, (-1500.0, 200.0))
    gout(tree, (700.0, 200.0))

    camera = node(tree, "ShaderNodeCameraData", loc=(-1400.0, 300.0))
    view_z = camera.outputs["View Z Depth"]

    scaled = _math(tree, "MULTIPLY", (-1200.0, 300.0), label="View Z * Scale")
    link(tree, view_z, scaled.inputs[0])
    link(tree, in_sock(tree, "Distance Scale"), scaled.inputs[1])

    adjusted = _math(tree, "ADD", (-1020.0, 300.0), label="+ Offset")
    link(tree, scaled.outputs[0], adjusted.inputs[0])
    link(tree, in_sock(tree, "Offset"), adjusted.inputs[1])

    # Mode 0: linear Near..Far
    linear = _map_range(tree, (-820.0, 460.0), label="Linear")
    link(tree, adjusted.outputs[0], linear.inputs["Value"])
    link(tree, in_sock(tree, "Near"), linear.inputs["From Min"])
    link(tree, in_sock(tree, "Far"), linear.inputs["From Max"])
    linear.inputs["To Min"].default_value = 0.0
    linear.inputs["To Max"].default_value = 1.0

    # Mode 1: reciprocal 1/z mapped from 1/Far..1/Near
    recip = _math(tree, "DIVIDE", (-820.0, 180.0), label="1 / z")
    recip.inputs[0].default_value = 1.0
    link(tree, adjusted.outputs[0], recip.inputs[1])

    inv_far = _math(tree, "DIVIDE", (-1000.0, 20.0), label="1 / Far")
    inv_far.inputs[0].default_value = 1.0
    link(tree, in_sock(tree, "Far"), inv_far.inputs[1])

    inv_near = _math(tree, "DIVIDE", (-1000.0, -140.0), label="1 / Near")
    inv_near.inputs[0].default_value = 1.0
    link(tree, in_sock(tree, "Near"), inv_near.inputs[1])

    reciprocal = _map_range(tree, (-640.0, 180.0), label="Reciprocal")
    link(tree, recip.outputs[0], reciprocal.inputs["Value"])
    link(tree, inv_far.outputs[0], reciprocal.inputs["From Min"])
    link(tree, inv_near.outputs[0], reciprocal.inputs["From Max"])
    reciprocal.inputs["To Min"].default_value = 0.0
    reciprocal.inputs["To Max"].default_value = 1.0

    # Mode 2: z / Far
    mode_far = _math(tree, "DIVIDE", (-820.0, -100.0), clamp=True, label="z / Far")
    link(tree, adjusted.outputs[0], mode_far.inputs[0])
    link(tree, in_sock(tree, "Far"), mode_far.inputs[1])

    # Select by integer mode: compare with 1 and 2 (Int -> Float link).
    is_one = _math(tree, "COMPARE", (-820.0, -300.0), label="Mode == 1")
    link(tree, in_sock(tree, "Mode"), is_one.inputs[0])
    is_one.inputs[1].default_value = 1.0
    is_one.inputs[2].default_value = 0.5

    is_two = _math(tree, "COMPARE", (-820.0, -460.0), label="Mode == 2")
    link(tree, in_sock(tree, "Mode"), is_two.inputs[0])
    is_two.inputs[1].default_value = 2.0
    is_two.inputs[2].default_value = 0.5

    pick_one = _mix_float(tree, (-440.0, 340.0), label="Linear / Reciprocal")
    link(tree, is_one.outputs[0], pick_one.inputs[0])
    link(tree, linear.outputs["Result"], pick_one.inputs[2])
    link(tree, reciprocal.outputs["Result"], pick_one.inputs[3])

    pick_two = _mix_float(tree, (-240.0, 340.0), label="Mode Select")
    link(tree, is_two.outputs[0], pick_two.inputs[0])
    link(tree, pick_one.outputs[0], pick_two.inputs[2])
    link(tree, mode_far.outputs[0], pick_two.inputs[3])

    depth = pick_two.outputs[0]
    link(tree, depth, out_sock(tree, "Depth"))

    alpha = _math(tree, "ADD", (-40.0, 160.0), clamp=True, label="Alpha")
    link(tree, depth, alpha.inputs[0])
    alpha.inputs[1].default_value = 0.0
    link(tree, alpha.outputs[0], out_sock(tree, "Alpha"))

    link(tree, view_z, out_sock(tree, "View Z"))

    _deselect(tree)

    log(
        "GOO Set Depth: encodes depth only - the real depth buffer cannot be written from "
        "Python/EEVEE; use an AOV/EXR pass in the external CLI"
    )


# ---------------------------------------------------------------------------
# Operator: drive GOO Light Info from a real light
# ---------------------------------------------------------------------------
def _light_driver_path(node_name: str, socket_name: str) -> str:
    escaped = node_name.replace('"', '\\"')
    return f'nodes["{escaped}"].inputs["{socket_name}"].default_value'


class GOO_OT_light_info_from_active(bpy.types.Operator):
    """Write drivers on a GOO Light Info node so it follows the active light"""

    bl_idname = "goo.light_info_from_active"
    bl_label = "GOO: Light Info From Active Light"
    bl_description = (
        "Drive the selected GOO Light Info node from a light object "
        "(color, power and specular factor) / 将灯光信息写入节点驱动"
    )
    bl_options = {"REGISTER", "UNDO"}

    # NOTE: Operator PointerProperty to a data-block does not register in
    # Blender 5.2 ("this type doesn't support data-block properties"), so the
    # operator takes datablock *names* and falls back to the active light /
    # material.  Verified on 5.2.3 (build_smoke).
    light_name: bpy.props.StringProperty(
        name="Light",
        description="Name of the light object to read (defaults to the active / selected light)",
    )
    material_name: bpy.props.StringProperty(
        name="Material",
        description="Name of the material with the GOO Light Info node (defaults to the active material)",
    )

    # -- resolution helpers ------------------------------------------------
    def _resolve_light(self, context):
        if self.light_name:
            light = bpy.data.objects.get(self.light_name)
            if light is not None and light.type == "LIGHT":
                return light
        active = getattr(context, "active_object", None)
        if active is not None and active.type == "LIGHT":
            return active
        try:
            selected = list(context.selected_objects)
        except (AttributeError, RuntimeError):
            selected = []
        for obj in selected:
            if obj.type == "LIGHT":
                return obj
        scene = getattr(context, "scene", None)
        for obj in getattr(scene, "objects", ()) if scene is not None else ():
            if obj.type == "LIGHT" and obj.select_get():
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
            data = getattr(active, "data", None)
            for material in getattr(data, "materials", ()) if data is not None else ():
                if material is not None:
                    return material
        material = getattr(context, "material", None)
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
        candidates = []
        for n in tree.nodes:
            if n.bl_idname != "ShaderNodeGroup":
                continue
            group_tree = getattr(n, "node_tree", None)
            if group_tree is None:
                continue
            if group_tree.name != "GOO Light Info":
                continue
            candidates.append(n)
        if not candidates:
            return None
        for n in candidates:
            if n.select:
                return n
        return candidates[0]

    @staticmethod
    def _socket_index(target, name: str) -> int:
        for index, socket in enumerate(target.inputs):
            if socket.name == name:
                return index
        return -1

    # -- driver helpers ----------------------------------------------------
    @staticmethod
    def _clear_drivers(tree, target) -> int:
        removed = 0
        for name in ("Color", "Power", "Specular"):
            index = GOO_OT_light_info_from_active._socket_index(target, name)
            if index < 0:
                continue
            path = _light_driver_path(target.name, name)
            channels = (0, 1, 2) if name == "Color" else (None,)
            for channel in channels:
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

    def _write_drivers(self, tree, target, light) -> int:
        written = 0
        color_index = self._socket_index(target, "Color")
        power_index = self._socket_index(target, "Power")
        specular_index = self._socket_index(target, "Specular")
        if color_index < 0 or power_index < 0 or specular_index < 0:
            raise RuntimeError("GOO Light Info node is missing Color / Power / Specular sockets")

        color_path = _light_driver_path(target.name, "Color")
        for channel in range(3):
            fcurve = tree.driver_add(color_path, channel)
            self._add_var(fcurve, "c", light, f"data.color[{channel}]")
            written += 1

        fcurve = tree.driver_add(_light_driver_path(target.name, "Power"))
        self._add_var(fcurve, "e", light, "data.energy")
        written += 1

        if hasattr(light.data, "specular_factor"):
            fcurve = tree.driver_add(_light_driver_path(target.name, "Specular"))
            self._add_var(fcurve, "s", light, "data.specular_factor")
            written += 1
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
                "GOO: material has no 'GOO Light Info' group node / 材质中没有 GOO Light Info 节点",
            )
            return {"CANCELLED"}

        tree = material.node_tree
        try:
            removed = self._clear_drivers(tree, target)
            written = self._write_drivers(tree, target, light)
        except Exception as exc:  # pragma: no cover - reported to the user
            self.report({"ERROR"}, f"GOO: could not write light drivers: {exc}")
            return {"CANCELLED"}

        target.label = light.name
        log(
            f"GOO Light Info: wrote {written} drivers on {material.name!r} from light "
            f"{light.name!r} (removed {removed} old)"
        )
        self.report({"INFO"}, f"GOO: {written} drivers from light {light.name!r}")
        return {"FINISHED"}


CLASSES = (GOO_OT_light_info_from_active,)


def register() -> None:
    for cls in CLASSES:
        if getattr(bpy.types, cls.__name__, None) is None:
            bpy.utils.register_class(cls)


def unregister() -> None:
    safe_unregister_all(CLASSES)

# ---------------------------------------------------------------------------
# hygiene
# ---------------------------------------------------------------------------
def _deselect(tree) -> None:
    """Clear node selection flags so the generated group is inert in the UI."""
    for n in tree.nodes:
        n.select = False
