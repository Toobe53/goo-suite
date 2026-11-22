# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO procedural texture node groups (ShaderNodeTree, category ``textures``).

All three groups are pure shader node graphs (Vector Math / Math / Mix), so
they work in EEVEE without Geometry Nodes and are deterministic for a given
input vector.  Node socket names stay English for file portability.
"""

from __future__ import annotations

import bpy

from ..utils import gin, gout, iface, in_sock, link, log, node, out_sock
from .registry import group

TWO_PI = 6.283185307179586
# Hex lattice constants for the "two offset rectangular grids" construction.
HEX_ROW = 0.8660254037844386  # sqrt(3) / 2, half the vertical centre spacing
HEX_ROW_2 = 1.7320508075688772  # sqrt(3), full vertical centre spacing
HEX_INRADIUS = 0.5  # distance from hex centre to its edges
# Unit normals of a flat top hexagon (30 / 90 / 150 degrees).
HEX_N1 = (HEX_ROW, 0.5, 0.0)
HEX_N2 = (0.0, 1.0, 0.0)
HEX_N3 = (-HEX_ROW, 0.5, 0.0)


# ---------------------------------------------------------------------------
# local helpers
# ---------------------------------------------------------------------------
def _math(tree, operation: str, loc, *, clamp: bool = False, label: str | None = None):
    n = node(tree, "ShaderNodeMath", loc=loc, label=label)
    n.operation = operation
    n.use_clamp = clamp
    return n


def _vm(tree, operation: str, loc, *, label: str | None = None, value=None):
    n = node(tree, "ShaderNodeVectorMath", loc=loc, label=label)
    n.operation = operation
    if value is not None:
        n.inputs[1].default_value = value
    return n


def _scale(tree, vector_socket, scale_socket, loc, *, label: str | None = None):
    """Vector Math SCALE - the factor is input index 3, not 1 (5.2 layout)."""
    n = node(tree, "ShaderNodeVectorMath", loc=loc, label=label)
    n.operation = "SCALE"
    link(tree, vector_socket, n.inputs[0])
    link(tree, scale_socket, n.inputs[3])
    return n


def _map_range(tree, loc, *, interpolation: str = "LINEAR", label: str | None = None):
    n = node(tree, "ShaderNodeMapRange", loc=loc, label=label)
    n.data_type = "FLOAT"
    n.interpolation_type = interpolation
    n.clamp = True
    return n


def _gray(tree, value_socket, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeCombineColor", loc=loc, label=label)
    for channel in ("Red", "Green", "Blue"):
        link(tree, value_socket, n.inputs[channel])
    return n


def _mix_float(tree, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "FLOAT"
    return n


# ---------------------------------------------------------------------------
# GOO Twirl
# ---------------------------------------------------------------------------
@group(
    "GOO Twirl",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO Twirl",
    description=(
        "Rotate a 2D vector around a center with a smooth radial falloff. "
        "GOO node_shader_twirl.cc exposes Vector / Center / Amount; this port adds the "
        "optional Radius and Angle controls (the effective rotation is Angle * Amount * falloff). "
        "/ 旋涡扭曲（围绕中心旋转二维向量，GOO 接口为 Vector/Center/Amount）。"
    ),
    version=1,
    featured_inputs=("Center", "Amount"),
)
def build_twirl(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Vector",
        "INPUT",
        "NodeSocketVector",
        default=(0.5, 0.5, 0.0),
        description="Input coordinate (usually UV or Generated) / 输入坐标",
    )
    iface(
        tree,
        "Center",
        "INPUT",
        "NodeSocketVector",
        default=(0.5, 0.5, 0.0),
        description="Twirl center in vector space / 旋涡中心",
    )
    iface(
        tree,
        "Amount",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        description="Twirl amount (radians at the center) / 旋涡量（弧度）",
    )
    iface(
        tree,
        "Radius",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        min_value=0.0001,
        description="Radius of influence / 影响半径",
    )
    iface(
        tree,
        "Angle",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        subtype="ANGLE",
        description="Rotation angle in radians / 旋转角度（弧度）",
    )

    # NOTE: the input is named ``Vector`` too; use the direction aware helper
    # so utils.iface does not delete the input while declaring the output.
    _out_socket(tree, "Vector", "NodeSocketVector", description="Twirled vector / 扭曲后的向量")
    _out_socket(tree, "Mask", "NodeSocketFloat", description="Radial falloff (1 center, 0 at Radius) / 径向遮罩")

    gin(tree, (-1000.0, 0.0))
    gout(tree, (420.0, 0.0))

    delta = _vm(tree, "SUBTRACT", (-820.0, 140.0), label="Vector - Center")
    link(tree, in_sock(tree, "Vector"), delta.inputs[0])
    link(tree, in_sock(tree, "Center"), delta.inputs[1])

    # Twirl is a 2D effect: measure the falloff in XY only.  Blender reports
    # Generated.z = 0.5 for zero-thickness meshes, which would otherwise add a
    # constant 0.5 to the distance and make the mask almost black on planes.
    planar = _vm(tree, "MULTIPLY", (-730.0, 60.0), label="Flatten XY")
    link(tree, delta.outputs["Vector"], planar.inputs[0])
    planar.inputs[1].default_value = (1.0, 1.0, 0.0)

    distance = _vm(tree, "LENGTH", (-640.0, 20.0), label="Distance")
    link(tree, planar.outputs["Vector"], distance.inputs[0])

    falloff = _map_range(tree, (-460.0, 0.0), interpolation="SMOOTHSTEP", label="Falloff")
    link(tree, distance.outputs["Value"], falloff.inputs["Value"])
    falloff.inputs["From Min"].default_value = 0.0
    link(tree, in_sock(tree, "Radius"), falloff.inputs["From Max"])
    falloff.inputs["To Min"].default_value = 1.0
    falloff.inputs["To Max"].default_value = 0.0

    angle = _math(tree, "MULTIPLY", (-640.0, 260.0), label="Angle * Strength")
    link(tree, in_sock(tree, "Angle"), angle.inputs[0])
    link(tree, in_sock(tree, "Amount"), angle.inputs[1])

    weighted_angle = _math(tree, "MULTIPLY", (-460.0, 260.0), label="* Falloff")
    link(tree, angle.outputs[0], weighted_angle.inputs[0])
    link(tree, falloff.outputs["Result"], weighted_angle.inputs[1])

    rotate = node(tree, "ShaderNodeVectorRotate", loc=(-200.0, 140.0), label="Twirl Rotate")
    rotate.rotation_type = "AXIS_ANGLE"
    link(tree, in_sock(tree, "Vector"), rotate.inputs["Vector"])
    link(tree, in_sock(tree, "Center"), rotate.inputs["Center"])
    rotate.inputs["Axis"].default_value = (0.0, 0.0, 1.0)
    link(tree, weighted_angle.outputs[0], rotate.inputs["Angle"])

    link(tree, rotate.outputs["Vector"], out_sock(tree, "Vector"))
    link(tree, falloff.outputs["Result"], out_sock(tree, "Mask"))

    _deselect(tree)

    log("GOO Twirl: 2D rotation via Vector Rotate (AXIS_ANGLE around +Z) with smooth radial falloff")


# ---------------------------------------------------------------------------
# GOO Water Ripples
# ---------------------------------------------------------------------------
@group(
    "GOO Water Ripples",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO Water Ripples",
    description=(
        "Concentric sine rings with exponential distance decay. Height is signed, Color is the "
        "0..1 grayscale encoding, Normal is the analytic surface normal of the height field. "
        "/ 水波纹（同心正弦环 + 指数衰减，法线为解析法线）。"
    ),
    version=1,
    featured_inputs=("Scale", "Time", "Wavelength", "Decay"),
)
def build_water_ripples(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Vector",
        "INPUT",
        "NodeSocketVector",
        default=(0.0, 0.0, 0.0),
        description="Input coordinate / 输入坐标",
    )
    iface(
        tree,
        "Scale",
        "INPUT",
        "NodeSocketFloat",
        default=5.0,
        min_value=0.0001,
        description="Radial scale of the rings / 波纹缩放",
    )
    iface(
        tree,
        "Time",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        description="Animation time (already in seconds / turns, scaled by 2 pi) / 时间",
    )
    iface(
        tree,
        "Wavelength",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        min_value=0.0001,
        description="Ring wavelength / 波长",
    )
    iface(
        tree,
        "Decay",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        min_value=0.0,
        description="Exponential decay with distance / 衰减",
    )
    iface(
        tree,
        "Center",
        "INPUT",
        "NodeSocketVector",
        default=(0.0, 0.0, 0.0),
        description="Ripple center / 波纹中心",
    )

    _out_socket(tree, "Color", "NodeSocketColor", description="0..1 grayscale ripple / 灰度颜色")
    _out_socket(tree, "Height", "NodeSocketFloat", description="Signed height / 有符号高度")
    _out_socket(tree, "Normal", "NodeSocketVector", description="Analytic normal / 解析法线")

    gin(tree, (-1500.0, 100.0))
    gout(tree, (1200.0, 100.0))

    centered = _vm(tree, "SUBTRACT", (-1320.0, 140.0), label="Vector - Center")
    link(tree, in_sock(tree, "Vector"), centered.inputs[0])
    link(tree, in_sock(tree, "Center"), centered.inputs[1])

    scaled = _scale(tree, centered.outputs["Vector"], in_sock(tree, "Scale"), (-1140.0, 140.0), label="* Scale")

    planar = _vm(tree, "MULTIPLY", (-960.0, 140.0), label="Drop Z", value=(1.0, 1.0, 0.0))
    link(tree, scaled.outputs["Vector"], planar.inputs[0])

    distance = _vm(tree, "LENGTH", (-780.0, 140.0), label="Distance")
    link(tree, planar.outputs["Vector"], distance.inputs[0])

    # k = 2 pi / wavelength
    wave_number = _math(tree, "DIVIDE", (-1140.0, -80.0), label="k = 2*pi / Wavelength")
    wave_number.inputs[0].default_value = TWO_PI
    link(tree, in_sock(tree, "Wavelength"), wave_number.inputs[1])

    # phase = distance * k - time * 2 pi
    phase_distance = _math(tree, "MULTIPLY", (-600.0, 260.0), label="distance * k")
    link(tree, distance.outputs["Value"], phase_distance.inputs[0])
    link(tree, wave_number.outputs[0], phase_distance.inputs[1])

    time_term = _math(tree, "MULTIPLY", (-960.0, 380.0), label="time * 2*pi")
    link(tree, in_sock(tree, "Time"), time_term.inputs[0])
    time_term.inputs[1].default_value = TWO_PI

    phase = _math(tree, "SUBTRACT", (-420.0, 220.0), label="Phase")
    link(tree, phase_distance.outputs[0], phase.inputs[0])
    link(tree, time_term.outputs[0], phase.inputs[1])

    ring = _math(tree, "SINE", (-240.0, 220.0), label="sin(phase)")
    link(tree, phase.outputs[0], ring.inputs[0])

    # envelope = exp(-distance * decay)
    decay_term = _math(tree, "MULTIPLY", (-600.0, -80.0), label="distance * Decay")
    link(tree, distance.outputs["Value"], decay_term.inputs[0])
    link(tree, in_sock(tree, "Decay"), decay_term.inputs[1])

    negative_decay = _math(tree, "MULTIPLY", (-420.0, -80.0), label="-d*Decay")
    link(tree, decay_term.outputs[0], negative_decay.inputs[0])
    negative_decay.inputs[1].default_value = -1.0

    envelope = _math(tree, "EXPONENT", (-240.0, -80.0), label="Envelope")
    link(tree, negative_decay.outputs[0], envelope.inputs[0])

    height = _math(tree, "MULTIPLY", (-60.0, 160.0), label="Height")
    link(tree, ring.outputs[0], height.inputs[0])
    link(tree, envelope.outputs[0], height.inputs[1])
    link(tree, height.outputs[0], out_sock(tree, "Height"))

    color_value = _math(tree, "MULTIPLY_ADD", (120.0, 300.0), label="0.5 * h + 0.5")
    link(tree, height.outputs[0], color_value.inputs[0])
    color_value.inputs[1].default_value = 0.5
    color_value.inputs[2].default_value = 0.5
    color = _gray(tree, color_value.outputs[0], (300.0, 300.0))
    link(tree, color.outputs["Color"], out_sock(tree, "Color"))

    # Analytic normal: dh/dd = (cos(phase) * k - sin(phase) * decay) * envelope
    cos_phase = _math(tree, "COSINE", (-240.0, 380.0), label="cos(phase)")
    link(tree, phase.outputs[0], cos_phase.inputs[0])

    cos_k = _math(tree, "MULTIPLY", (-60.0, 380.0), label="cos(phase) * k")
    link(tree, cos_phase.outputs[0], cos_k.inputs[0])
    link(tree, wave_number.outputs[0], cos_k.inputs[1])

    ring_decay = _math(tree, "MULTIPLY", (-60.0, 520.0), label="sin(phase) * decay")
    link(tree, ring.outputs[0], ring_decay.inputs[0])
    link(tree, in_sock(tree, "Decay"), ring_decay.inputs[1])

    dh_pre = _math(tree, "SUBTRACT", (120.0, 420.0), label="dh/dd pre-envelope")
    link(tree, cos_k.outputs[0], dh_pre.inputs[0])
    link(tree, ring_decay.outputs[0], dh_pre.inputs[1])

    dh = _math(tree, "MULTIPLY", (300.0, 420.0), label="dh/dd")
    link(tree, dh_pre.outputs[0], dh.inputs[0])
    link(tree, envelope.outputs[0], dh.inputs[1])

    negative_dh = _math(tree, "MULTIPLY", (480.0, 420.0), label="-dh/dd")
    link(tree, dh.outputs[0], negative_dh.inputs[0])
    negative_dh.inputs[1].default_value = -1.0

    direction = _vm(tree, "NORMALIZE", (-420.0, 620.0), label="Radial Direction")
    link(tree, planar.outputs["Vector"], direction.inputs[0])

    gradient = _vm(tree, "SCALE", (660.0, 560.0), label="gradient")
    link(tree, direction.outputs["Vector"], gradient.inputs[0])
    link(tree, negative_dh.outputs[0], gradient.inputs[3])

    up = _vm(tree, "ADD", (840.0, 560.0), label="+ Z", value=(0.0, 0.0, 1.0))
    link(tree, gradient.outputs["Vector"], up.inputs[0])

    normal = _vm(tree, "NORMALIZE", (1020.0, 560.0), label="Normal")
    link(tree, up.outputs["Vector"], normal.inputs[0])
    link(tree, normal.outputs["Vector"], out_sock(tree, "Normal"))

    _deselect(tree)

    log(
        "GOO Water Ripples: deterministic shader math (sin rings * exp decay), "
        "normal from the analytic height gradient - no geometry nodes"
    )


# ---------------------------------------------------------------------------
# GOO Hex Grid
# ---------------------------------------------------------------------------
def _hex_center(tree, vector_socket, offset, loc, *, label):
    """Nearest cell centre of one rectangular sublattice."""
    shifted = _vm(tree, "SUBTRACT", (loc[0] - 160.0, loc[1]), label=None)
    link(tree, vector_socket, shifted.inputs[0])
    shifted.inputs[1].default_value = offset

    quotient = _vm(tree, "DIVIDE", (loc[0], loc[1]), label=None)
    link(tree, shifted.outputs["Vector"], quotient.inputs[0])
    quotient.inputs[1].default_value = (1.0, HEX_ROW_2, 1.0)

    rounded = _vm(tree, "ROUND", (loc[0] + 140.0, loc[1]), label=None)
    link(tree, quotient.outputs["Vector"], rounded.inputs[0])

    centre = _vm(tree, "MULTIPLY", (loc[0] + 280.0, loc[1]), label=label)
    link(tree, rounded.outputs["Vector"], centre.inputs[0])
    centre.inputs[1].default_value = (1.0, HEX_ROW_2, 1.0)

    if offset != (0.0, 0.0, 0.0):
        final = _vm(tree, "ADD", (loc[0] + 420.0, loc[1]))
        link(tree, centre.outputs["Vector"], final.inputs[0])
        final.inputs[1].default_value = offset
        return final
    return centre


@group(
    "GOO Hex Grid",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO Hex Grid",
    description=(
        "True hexagonal tiling built from two offset rectangular grids: the nearest of the two "
        "candidate cell centres is picked, then the distance to the hex edges gives the lines. "
        "Cell is the nearest centre vector (usable as a White Noise seed). "
        "/ 真实六边形网格（两张偏移矩形格子取最近中心）。"
    ),
    version=1,
    featured_inputs=("Scale", "Size", "Radius", "Roundness"),
)
def build_hex_grid(tree: bpy.types.NodeTree) -> None:
    """Hex lattice with the socket set of GOO's node_shader_tex_hexagon.cc.

    GOO inputs: Vector, Scale, Size, Radius, Roundness.  Outputs: Value, Color,
    Hex Coords, Position, Cell UV, Cell ID.  This port adds the lattice
    ``Rotation`` input plus the compatibility outputs ``Mask`` and ``Cell``.
    ``Roundness`` is used as the anti-alias width of the line edge (the exact
    corner rounding of the C++ node is not reproduced).
    """
    iface(
        tree,
        "Vector",
        "INPUT",
        "NodeSocketVector",
        default=(0.0, 0.0, 0.0),
        description="Input coordinate / 输入坐标",
    )
    iface(
        tree,
        "Scale",
        "INPUT",
        "NodeSocketFloat",
        default=4.0,
        min_value=0.0001,
        description="Hex density / 六边形密度",
    )
    iface(
        tree,
        "Size",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        min_value=0.0001,
        description="Cell size multiplier (larger = bigger hexagons) / 格子尺寸",
    )
    iface(
        tree,
        "Radius",
        "INPUT",
        "NodeSocketFloat",
        default=0.04,
        min_value=0.0,
        max_value=0.5,
        description="Line half width in cell space (0..0.5) / 线宽",
    )
    iface(
        tree,
        "Roundness",
        "INPUT",
        "NodeSocketFloat",
        default=0.02,
        min_value=0.0,
        description="Anti-alias width of the line edge / 边缘柔化",
    )
    iface(
        tree,
        "Rotation",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        subtype="ANGLE",
        description="Lattice rotation in radians / 旋转",
    )

    # GOO compatible outputs first.
    _out_socket(tree, "Value", "NodeSocketFloat", description="Line mask 0..1 / 线条值")
    _out_socket(tree, "Color", "NodeSocketColor", description="Line mask as grayscale / 灰度线")
    _out_socket(tree, "Hex Coords", "NodeSocketVector", description="Cell centre in input space / 六边形中心坐标")
    _out_socket(tree, "Position", "NodeSocketVector", description="Unmodified input vector / 原始坐标")
    _out_socket(tree, "Cell UV", "NodeSocketVector", description="Local cell UV (0..1) / 格内 UV")
    _out_socket(tree, "Cell ID", "NodeSocketFloat", description="Stable random value per cell / 格子随机 ID")
    # Compatibility outputs for files/notes created before the rename.
    _out_socket(tree, "Mask", "NodeSocketFloat", description="Alias of Value / 线条遮罩")
    _out_socket(tree, "Cell", "NodeSocketVector", description="Alias of Hex Coords / 格子中心")

    gin(tree, (-1500.0, 200.0))
    gout(tree, (1700.0, 200.0))

    rotated = node(tree, "ShaderNodeVectorRotate", loc=(-1400.0, 220.0), label="Rotate")
    rotated.rotation_type = "AXIS_ANGLE"
    link(tree, in_sock(tree, "Vector"), rotated.inputs["Vector"])
    rotated.inputs["Center"].default_value = (0.0, 0.0, 0.0)
    rotated.inputs["Axis"].default_value = (0.0, 0.0, 1.0)
    link(tree, in_sock(tree, "Rotation"), rotated.inputs["Angle"])

    size_safe = _math(tree, "MAXIMUM", (-1400.0, -60.0), label="max(Size, 1e-4)")
    link(tree, in_sock(tree, "Size"), size_safe.inputs[0])
    size_safe.inputs[1].default_value = 0.0001

    density = _math(tree, "DIVIDE", (-1240.0, -60.0), label="Scale / Size")
    link(tree, in_sock(tree, "Scale"), density.inputs[0])
    link(tree, size_safe.outputs[0], density.inputs[1])

    scaled = _scale(tree, rotated.outputs["Vector"], density.outputs[0], (-1220.0, 220.0), label="* Scale / Size")

    centre_a = _hex_center(tree, scaled.outputs["Vector"], (0.0, 0.0, 0.0), (-1040.0, 320.0), label="Centre A")
    centre_b = _hex_center(tree, scaled.outputs["Vector"], (0.5, HEX_ROW, 0.0), (-1040.0, 60.0), label="Centre B")

    distance_a = _vm(tree, "DISTANCE", (-480.0, 340.0), label="Distance A")
    link(tree, scaled.outputs["Vector"], distance_a.inputs[0])
    link(tree, centre_a.outputs["Vector"], distance_a.inputs[1])

    distance_b = _vm(tree, "DISTANCE", (-480.0, 60.0), label="Distance B")
    link(tree, scaled.outputs["Vector"], distance_b.inputs[0])
    link(tree, centre_b.outputs["Vector"], distance_b.inputs[1])

    is_b = _math(tree, "LESS_THAN", (-310.0, 200.0), label="B closer")
    link(tree, distance_b.outputs["Value"], is_b.inputs[0])
    link(tree, distance_a.outputs["Value"], is_b.inputs[1])

    centre = _mix_vector(tree, (-140.0, 200.0), label="Nearest Centre")
    link(tree, is_b.outputs[0], centre.inputs[0])
    link(tree, centre_a.outputs["Vector"], centre.inputs[4])
    link(tree, centre_b.outputs["Vector"], centre.inputs[5])

    local = _vm(tree, "SUBTRACT", (40.0, 200.0), label="Local")
    link(tree, scaled.outputs["Vector"], local.inputs[0])
    link(tree, centre.outputs[1], local.inputs[1])

    dot_1 = _vm(tree, "DOT_PRODUCT", (40.0, 400.0), label=None, value=HEX_N1)
    link(tree, local.outputs["Vector"], dot_1.inputs[0])
    dot_2 = _vm(tree, "DOT_PRODUCT", (40.0, 540.0), label=None, value=HEX_N2)
    link(tree, local.outputs["Vector"], dot_2.inputs[0])
    dot_3 = _vm(tree, "DOT_PRODUCT", (40.0, 680.0), label=None, value=HEX_N3)
    link(tree, local.outputs["Vector"], dot_3.inputs[0])

    abs_1 = _math(tree, "ABSOLUTE", (220.0, 400.0))
    link(tree, dot_1.outputs["Value"], abs_1.inputs[0])
    abs_2 = _math(tree, "ABSOLUTE", (220.0, 540.0))
    link(tree, dot_2.outputs["Value"], abs_2.inputs[0])
    abs_3 = _math(tree, "ABSOLUTE", (220.0, 680.0))
    link(tree, dot_3.outputs["Value"], abs_3.inputs[0])

    max_12 = _math(tree, "MAXIMUM", (400.0, 460.0))
    link(tree, abs_1.outputs[0], max_12.inputs[0])
    link(tree, abs_2.outputs[0], max_12.inputs[1])

    hex_dot = _math(tree, "MAXIMUM", (580.0, 460.0), label="Hex Distance Field")
    link(tree, max_12.outputs[0], hex_dot.inputs[0])
    link(tree, abs_3.outputs[0], hex_dot.inputs[1])

    edge_signed = _math(tree, "SUBTRACT", (760.0, 460.0))
    link(tree, hex_dot.outputs[0], edge_signed.inputs[0])
    edge_signed.inputs[1].default_value = HEX_INRADIUS

    edge = _math(tree, "ABSOLUTE", (940.0, 460.0), label="Distance To Edge")
    link(tree, edge_signed.outputs[0], edge.inputs[0])

    round_min = _math(tree, "MAXIMUM", (760.0, 660.0), label="max(Roundness, 1e-4)")
    link(tree, in_sock(tree, "Roundness"), round_min.inputs[0])
    round_min.inputs[1].default_value = 0.0001

    feather_end = _math(tree, "ADD", (940.0, 660.0), label="Radius + Roundness")
    link(tree, round_min.outputs[0], feather_end.inputs[0])
    link(tree, in_sock(tree, "Radius"), feather_end.inputs[1])

    mask_node = _map_range(tree, (1120.0, 460.0), interpolation="SMOOTHSTEP", label="Line Mask")
    link(tree, edge.outputs[0], mask_node.inputs["Value"])
    link(tree, in_sock(tree, "Radius"), mask_node.inputs["From Min"])
    link(tree, feather_end.outputs[0], mask_node.inputs["From Max"])
    mask_node.inputs["To Min"].default_value = 1.0
    mask_node.inputs["To Max"].default_value = 0.0

    mask = mask_node.outputs["Result"]

    # GOO outputs ---------------------------------------------------------
    link(tree, mask, out_sock(tree, "Value"))

    color = _gray(tree, mask, (1300.0, 460.0))
    link(tree, color.outputs["Color"], out_sock(tree, "Color"))

    inverse_density = _math(tree, "DIVIDE", (-1040.0, -60.0), label="1 / (Scale/Size)")
    inverse_density.inputs[0].default_value = 1.0
    link(tree, density.outputs[0], inverse_density.inputs[1])

    hex_coords = _scale(tree, centre.outputs[1], inverse_density.outputs[0], (1300.0, 160.0), label="Hex Coords")
    link(tree, hex_coords.outputs["Vector"], out_sock(tree, "Hex Coords"))

    link(tree, in_sock(tree, "Vector"), out_sock(tree, "Position"))

    cell_uv = _vm(tree, "ADD", (1300.0, 40.0), label="Cell UV", value=(0.5, 0.5, 0.0))
    link(tree, local.outputs["Vector"], cell_uv.inputs[0])
    link(tree, cell_uv.outputs["Vector"], out_sock(tree, "Cell UV"))

    white = node(tree, "ShaderNodeTexWhiteNoise", loc=(1300.0, -140.0), label="Cell ID")
    white.noise_dimensions = "3D"
    link(tree, centre.outputs[1], white.inputs["Vector"])
    link(tree, white.outputs["Value"], out_sock(tree, "Cell ID"))

    # Compatibility aliases ----------------------------------------------
    link(tree, mask, out_sock(tree, "Mask"))
    link(tree, hex_coords.outputs["Vector"], out_sock(tree, "Cell"))

    _deselect(tree)

    log(
        "GOO Hex Grid: two offset rectangular lattices (round(p) and round(p - (0.5, sqrt3/2))), "
        "nearest centre + |q.n| distance field; GOO sockets Value/Color/Hex Coords/Position/"
        "Cell UV/Cell ID, Roundness used as edge anti-alias"
    )


# ---------------------------------------------------------------------------
# local helpers that depend on the mix socket layout
# ---------------------------------------------------------------------------
def _mix_vector(tree, loc, *, label: str | None = None):
    """Mix node in VECTOR mode: Factor=0, A=4, B=5, result=1."""
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "VECTOR"
    return n


def _out_socket(tree, name: str, socket_type: str, *, description: str = ""):
    """Direction aware socket declaration (see GOO Light Info in shading.py)."""
    for item in tree.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "OUTPUT" and item.name == name:
            if description:
                item.description = description
            return item
    item = tree.interface.new_socket(name=name, in_out="OUTPUT", socket_type=socket_type)
    if description:
        item.description = description
    return item

# ---------------------------------------------------------------------------
# hygiene
# ---------------------------------------------------------------------------
def _deselect(tree) -> None:
    """Clear node selection flags so the generated group is inert in the UI."""
    for n in tree.nodes:
        n.select = False

