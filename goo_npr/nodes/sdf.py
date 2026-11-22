# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO signed distance field (SDF) node groups (ShaderNodeTree).

GOO Engine 4.4 implements its SDF family as C++ shader nodes
(``node_shader_sdf_primitive.cc``, ``node_shader_sdf_op.cc``,
``node_shader_sdf_vector_op.cc`` and ``node_shader_sdf_noise.cc``) backed by
``gpu_shader_material_sdf_*.glsl``.  This module re-creates the maths with
native shader node *groups* so the nodes work in stock Blender 5.2 (EEVEE and
Cycles) without a custom build.

Approximations / deliberate deviations
--------------------------------------
* The C++ nodes expose their mode through a ``NodeSocketMenu``.  Menu sockets
  cannot define items in a node *group* interface, so every selector here is a
  ``NodeSocketInt`` clamped to ``0..7`` (``COMPARE`` against each integer picks
  the branch).
* GOO's primitive node has one ``Size`` float, four ``Value`` floats, four
  ``Point`` vectors, an ``Angle``, ``Linewidth`` and ``Invert``.  This port uses
  the reduced interface requested for the port: a ``Size`` vector, ``Radius``
  and ``Roundness``.
* The GOO nodes branch in GLSL; a node group has to evaluate every branch and
  select afterwards.  Unused branches can therefore produce division by zero
  warnings on degenerate inputs (``Size`` components of 0, ``Scale`` components
  of 0, ``Size.y == 0`` for the cone).  Valid defaults never hit those.
* ``GOO SDF Noise`` reproduces the *spirit* of the GLSL ``sdFbm`` (distance
  field displaced by band-limited noise) but not its exact hash lattice: the
  node graph uses two Blender Noise Textures instead of a hard-coded sphere
  lattice because custom GLSL is not available inside a stock node group.

Enumerations
------------
``GOO SDF Primitive`` ``Shape``::

    0 Sphere         length(p) - Radius
    1 Box            exact box with half extents Size
    2 Rounded Box    box - Roundness
    3 Cylinder       Y axis, half height Size.y, radius Radius (capped)
    4 Torus          major Radius in the XZ plane, minor Size.x
    5 Capsule        Y axis, half segment length Size.y - Radius
    6 Plane          p.y (plane through the origin, normal +Y)
    7 Cone           radius = lerp(Radius, 0, clamp((p.y + Size.y) / (2 * Size.y), 0, 1))
                     distance = max(length(p.xz) - radius, abs(p.y) - Size.y)

``GOO SDF Op`` ``Operation``::

    0 Union             min(a, b)
    1 Intersect         max(a, b)
    2 Subtract          max(a, -b)
    3 Smooth Union      polynomial smin
    4 Smooth Intersect  -smooth_union(-a, -b)
    5 Smooth Subtract   -smooth_union(-a, b)
    6 Dilate            a - Radius
    7 Onion             abs(a) - Radius

``GOO SDF Vector Op`` ``Operation``::

    0 Repeat   (fract((p + 0.5 * Scale) / Scale) - 0.5) * Scale
    1 Mirror   abs(p)
    2 Twist    rotate around +Z by Amount * p.z
    3 Bend     rotate around +X by Amount * p.x
    4 Polar    (length(p.xz), atan2(p.z, p.x) / pi, p.y)
    5 Swizzle  (p.y, p.z, p.x)
    6 Rotate   rotate around +Z by Amount
    7 Extrude  (p.x, p.y, 0)

``GOO SDF Noise`` formula (documented on the builder)::

    n1 = noise(Position, scale=2, detail=Detail, roughness=Roughness) in [-1, 1]
    n2 = noise(Position, scale=6, detail=2,    roughness=Roughness) in [-1, 1]
    Distance_out = Distance + (n1 + 0.5 * Detail Inflation * n2) * 0.35 * Detail Blend
"""

from __future__ import annotations

import math

import bpy

from ..utils import gin, gout, iface, in_sock, link, log, node, out_sock
from .registry import group

PI = math.pi

#: ``ShaderNodeMix`` input/output indices in Blender 5.2.  The indices are
#: shared by all data types, so the unused data-type sockets must be skipped:
#: FLOAT -> Factor 0, A 2, B 3, Result 0;
#: VECTOR -> Factor 0, A 4, B 5, Result 1.
MIX_FACTOR = 0
MIX_FLOAT_A = 2
MIX_FLOAT_B = 3
MIX_FLOAT_RESULT = 0
MIX_VECTOR_A = 4
MIX_VECTOR_B = 5
MIX_VECTOR_RESULT = 1


# ---------------------------------------------------------------------------
# local helpers
# ---------------------------------------------------------------------------
def _math(
    tree: bpy.types.NodeTree,
    operation: str,
    loc: tuple[float, float],
    *,
    clamp: bool = False,
    label: str | None = None,
):
    n = node(tree, "ShaderNodeMath", loc=loc, label=label)
    n.operation = operation
    n.use_clamp = clamp
    return n


def _vm(
    tree: bpy.types.NodeTree,
    operation: str,
    loc: tuple[float, float],
    *,
    label: str | None = None,
    value=None,
):
    n = node(tree, "ShaderNodeVectorMath", loc=loc, label=label)
    n.operation = operation
    if value is not None:
        n.inputs[1].default_value = value
    return n


def _separate(tree, vector_socket, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeSeparateXYZ", loc=loc, label=label)
    link(tree, vector_socket, n.inputs[0])
    return n


def _combine(tree, x_socket_or_float, y_socket_or_float, z_socket_or_float, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeCombineXYZ", loc=loc, label=label)
    for index, value in enumerate((x_socket_or_float, y_socket_or_float, z_socket_or_float)):
        if isinstance(value, (int, float)):
            n.inputs[index].default_value = float(value)
        else:
            link(tree, value, n.inputs[index])
    return n


def _map_range(tree, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeMapRange", loc=loc, label=label)
    n.data_type = "FLOAT"
    n.interpolation_type = "LINEAR"
    n.clamp = True
    return n


def _mix_float(tree, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "FLOAT"
    return n


def _mix_vector(tree, loc, *, label: str | None = None):
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "VECTOR"
    return n


def _compare(tree, value_socket, target: int, loc, *, label: str | None = None):
    n = _math(tree, "COMPARE", loc, label=label)
    link(tree, value_socket, n.inputs[0])
    n.inputs[1].default_value = float(target)
    n.inputs[2].default_value = 0.5  # epsilon: Int selectors are exact
    return n


def _negate(tree, value_socket, loc, *, label: str | None = None):
    n = _math(tree, "MULTIPLY", loc, label=label)
    link(tree, value_socket, n.inputs[0])
    n.inputs[1].default_value = -1.0
    return n.outputs[0]


def _select_float(tree, base_socket, branches, selector_socket, loc, *, prefix: str):
    """Nested FLOAT Mix: result = base, then mix in branch ``i`` where selector == i."""
    result = base_socket
    x, y = loc
    for index, branch_socket in branches:
        row = y - index * 170.0
        condition = _compare(
            tree,
            selector_socket,
            index,
            (x - 220.0, row),
            label=f"{prefix} == {index}",
        )
        mix = _mix_float(tree, (x, row), label=f"{prefix} {index}")
        link(tree, condition.outputs[0], mix.inputs[MIX_FACTOR])
        link(tree, result, mix.inputs[MIX_FLOAT_A])
        link(tree, branch_socket, mix.inputs[MIX_FLOAT_B])
        result = mix.outputs[MIX_FLOAT_RESULT]
    return result


def _select_vector(tree, base_socket, branches, selector_socket, loc, *, prefix: str):
    """Nested VECTOR Mix: result = base, then mix in branch ``i`` where selector == i."""
    result = base_socket
    x, y = loc
    for index, branch_socket in branches:
        row = y - index * 170.0
        condition = _compare(
            tree,
            selector_socket,
            index,
            (x - 220.0, row),
            label=f"{prefix} == {index}",
        )
        mix = _mix_vector(tree, (x, row), label=f"{prefix} {index}")
        link(tree, condition.outputs[0], mix.inputs[MIX_FACTOR])
        link(tree, result, mix.inputs[MIX_VECTOR_A])
        link(tree, branch_socket, mix.inputs[MIX_VECTOR_B])
        result = mix.outputs[MIX_VECTOR_RESULT]
    return result


def _out_socket(tree, name: str, socket_type: str, *, description: str = ""):
    """Direction aware output declaration.

    ``utils.iface`` looks sockets up by name only, so declaring an output with
    the same name as an input would delete the input.  Outputs that reuse an
    input name ("Vector", "Distance") must be created through this helper.
    """
    for item in tree.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "OUTPUT" and item.name == name:
            if description:
                item.description = description
            return item
    item = tree.interface.new_socket(name=name, in_out="OUTPUT", socket_type=socket_type)
    if description:
        item.description = description
    return item


def _deselect(tree) -> None:
    """Clear node selection flags so the generated group is inert in the UI."""
    for n in tree.nodes:
        n.select = False


# ---------------------------------------------------------------------------
# 1. GOO SDF Primitive
# ---------------------------------------------------------------------------
def _box_distance(tree, p_socket, size_socket, loc, *, label: str):
    """Exact box SDF with half extents ``Size``."""
    absolute = _vm(tree, "ABSOLUTE", loc, label=f"{label} abs(p)")
    link(tree, p_socket, absolute.inputs[0])

    q = _vm(tree, "SUBTRACT", (loc[0] + 160.0, loc[1]), label=f"{label} q = abs(p) - Size")
    link(tree, absolute.outputs["Vector"], q.inputs[0])
    link(tree, size_socket, q.inputs[1])

    q_xyz = _separate(tree, q.outputs["Vector"], (loc[0] + 320.0, loc[1] + 260.0), label=f"{label} q.xyz")

    clamped = _vm(
        tree,
        "MAXIMUM",
        (loc[0] + 320.0, loc[1] - 60.0),
        label=f"{label} max(q, 0)",
        value=(0.0, 0.0, 0.0),
    )
    link(tree, q.outputs["Vector"], clamped.inputs[0])

    outside = _vm(tree, "LENGTH", (loc[0] + 480.0, loc[1] - 60.0), label=f"{label} outside")
    link(tree, clamped.outputs["Vector"], outside.inputs[0])

    max_xy = _math(
        tree, "MAXIMUM", (loc[0] + 480.0, loc[1] + 200.0), label=f"{label} max(q.x, q.y)"
    )
    link(tree, q_xyz.outputs["X"], max_xy.inputs[0])
    link(tree, q_xyz.outputs["Y"], max_xy.inputs[1])

    max_xyz = _math(
        tree, "MAXIMUM", (loc[0] + 480.0, loc[1] + 340.0), label=f"{label} max(..., q.z)"
    )
    link(tree, max_xy.outputs[0], max_xyz.inputs[0])
    link(tree, q_xyz.outputs["Z"], max_xyz.inputs[1])

    inside = _math(
        tree, "MINIMUM", (loc[0] + 480.0, loc[1] + 480.0), label=f"{label} inside"
    )
    link(tree, max_xyz.outputs[0], inside.inputs[0])
    inside.inputs[1].default_value = 0.0

    distance = _math(tree, "ADD", (loc[0] + 660.0, loc[1]), label=label)
    link(tree, outside.outputs["Value"], distance.inputs[0])
    link(tree, inside.outputs[0], distance.inputs[1])
    return distance.outputs[0]


def _cylinder_distance(tree, radial_length, py_socket, size_y_socket, radius_socket, loc, *, label: str):
    """Exact capped cylinder around the Y axis (radius ``Radius``, half height ``Size.y``)."""
    dx = _math(tree, "SUBTRACT", loc, label=f"{label} radial - Radius")
    link(tree, radial_length, dx.inputs[0])
    link(tree, radius_socket, dx.inputs[1])

    abs_y = _math(tree, "ABSOLUTE", (loc[0], loc[1] + 160.0), label=f"{label} abs(p.y)")
    link(tree, py_socket, abs_y.inputs[0])

    dy = _math(tree, "SUBTRACT", (loc[0] + 160.0, loc[1] + 160.0), label=f"{label} abs(p.y) - Size.y")
    link(tree, abs_y.outputs[0], dy.inputs[0])
    link(tree, size_y_socket, dy.inputs[1])

    inner = _math(tree, "MAXIMUM", (loc[0] + 320.0, loc[1] + 80.0), label=f"{label} max(dx, dy)")
    link(tree, dx.outputs[0], inner.inputs[0])
    link(tree, dy.outputs[0], inner.inputs[1])

    inner_clamped = _math(
        tree, "MINIMUM", (loc[0] + 480.0, loc[1] + 80.0), label=f"{label} min(max, 0)"
    )
    link(tree, inner.outputs[0], inner_clamped.inputs[0])
    inner_clamped.inputs[1].default_value = 0.0

    outside_x = _math(tree, "MAXIMUM", (loc[0] + 320.0, loc[1] + 280.0), label=f"{label} max(dx, 0)")
    link(tree, dx.outputs[0], outside_x.inputs[0])
    outside_x.inputs[1].default_value = 0.0

    outside_y = _math(tree, "MAXIMUM", (loc[0] + 320.0, loc[1] + 420.0), label=f"{label} max(dy, 0)")
    link(tree, dy.outputs[0], outside_y.inputs[0])
    outside_y.inputs[1].default_value = 0.0

    outside_vector = _combine(
        tree,
        outside_x.outputs[0],
        outside_y.outputs[0],
        0.0,
        (loc[0] + 480.0, loc[1] + 360.0),
        label=f"{label} (max(dx,0), max(dy,0), 0)",
    )
    outside = _vm(tree, "LENGTH", (loc[0] + 660.0, loc[1] + 360.0), label=f"{label} outside")
    link(tree, outside_vector.outputs["Vector"], outside.inputs[0])

    distance = _math(tree, "ADD", (loc[0] + 820.0, loc[1] + 160.0), label=label)
    link(tree, inner_clamped.outputs[0], distance.inputs[0])
    link(tree, outside.outputs["Value"], distance.inputs[1])
    return distance.outputs[0]


@group(
    "GOO SDF Primitive",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO SDF Primitive",
    description=(
        "Signed distance to an analytic primitive. Shape is an Int selector (0 Sphere, 1 Box, "
        "2 Rounded Box, 3 Cylinder, 4 Torus, 5 Capsule, 6 Plane, 7 Cone). Negative inside. "
        "/ 解析 SDF 图元（Int 选择器，负值在内部）。"
    ),
    version=1,
    featured_inputs=("Shape", "Size", "Radius", "Roundness"),
)
def build_sdf_primitive(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Vector",
        "INPUT",
        "NodeSocketVector",
        default=(0.0, 0.0, 0.0),
        description="Sample position / 采样位置",
    )
    iface(
        tree,
        "Shape",
        "INPUT",
        "NodeSocketInt",
        default=0,
        min_value=0,
        max_value=7,
        description=(
            "0 Sphere, 1 Box, 2 Rounded Box, 3 Cylinder (Y), 4 Torus (XZ), "
            "5 Capsule (Y), 6 Plane (Y), 7 Cone / 图元形状"
        ),
    )
    iface(
        tree,
        "Size",
        "INPUT",
        "NodeSocketVector",
        default=(0.5, 0.5, 0.5),
        description="Half extents: box, cylinder half height, torus minor radius, capsule half "
        "height, cone half height / 尺寸（半长）",
    )
    iface(
        tree,
        "Radius",
        "INPUT",
        "NodeSocketFloat",
        default=0.25,
        description="Sphere/cylinder/torus major/capsule/cone radius / 半径",
    )
    iface(
        tree,
        "Roundness",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        min_value=0.0,
        description="Rounded Box: the box distance is reduced by this amount / 圆角半径",
    )

    _out_socket(tree, "Distance", "NodeSocketFloat", description="Signed distance (negative inside) / 有符号距离")

    gin(tree, (-1800.0, 0.0))
    gout(tree, (900.0, 0.0))

    vector = in_sock(tree, "Vector")
    shape = in_sock(tree, "Shape")
    size = in_sock(tree, "Size")
    radius = in_sock(tree, "Radius")
    roundness = in_sock(tree, "Roundness")

    p_xyz = _separate(tree, vector, (-1600.0, 500.0), label="p.xyz")
    size_xyz = _separate(tree, size, (-1600.0, 260.0), label="Size.xyz")
    py = p_xyz.outputs["Y"]

    # 0 Sphere -----------------------------------------------------------------
    p_length = _vm(tree, "LENGTH", (-1400.0, 600.0), label="length(p)")
    link(tree, vector, p_length.inputs[0])
    sphere = _math(tree, "SUBTRACT", (-1220.0, 600.0), label="Sphere")
    link(tree, p_length.outputs["Value"], sphere.inputs[0])
    link(tree, radius, sphere.inputs[1])

    # 1 Box / 2 Rounded Box ----------------------------------------------------
    box = _box_distance(tree, vector, size, (-1400.0, 360.0), label="Box")
    rounded_box = _math(tree, "SUBTRACT", (-500.0, 380.0), label="Rounded Box")
    link(tree, box, rounded_box.inputs[0])
    link(tree, roundness, rounded_box.inputs[1])

    # 3 Cylinder ---------------------------------------------------------------
    flat_xz = _vm(
        tree, "MULTIPLY", (-1400.0, 60.0), label="Flatten XZ", value=(1.0, 0.0, 1.0)
    )
    link(tree, vector, flat_xz.inputs[0])
    radial = _vm(tree, "LENGTH", (-1220.0, 60.0), label="length(p.xz)")
    link(tree, flat_xz.outputs["Vector"], radial.inputs[0])
    cylinder = _cylinder_distance(
        tree,
        radial.outputs["Value"],
        py,
        size_xyz.outputs["Y"],
        radius,
        (-1040.0, 60.0),
        label="Cylinder",
    )

    # 4 Torus ------------------------------------------------------------------
    ring = _math(tree, "SUBTRACT", (-1040.0, -360.0), label="Torus ring")
    link(tree, radial.outputs["Value"], ring.inputs[0])
    link(tree, radius, ring.inputs[1])
    tube = _combine(
        tree,
        ring.outputs[0],
        py,
        0.0,
        (-860.0, -360.0),
        label="(length(p.xz) - Radius, p.y, 0)",
    )
    tube_length = _vm(tree, "LENGTH", (-680.0, -360.0), label="length(tube)")
    link(tree, tube.outputs["Vector"], tube_length.inputs[0])
    torus = _math(tree, "SUBTRACT", (-500.0, -360.0), label="Torus")
    link(tree, tube_length.outputs["Value"], torus.inputs[0])
    link(tree, size_xyz.outputs["X"], torus.inputs[1])

    # 5 Capsule ----------------------------------------------------------------
    half_segment = _math(tree, "SUBTRACT", (-1220.0, -600.0), label="Capsule half segment")
    link(tree, size_xyz.outputs["Y"], half_segment.inputs[0])
    link(tree, radius, half_segment.inputs[1])

    negative_half = _math(tree, "MULTIPLY", (-1040.0, -760.0))
    link(tree, half_segment.outputs[0], negative_half.inputs[0])
    negative_half.inputs[1].default_value = -1.0

    clamp_low = _math(tree, "MAXIMUM", (-860.0, -600.0), label="max(p.y, -half)")
    link(tree, py, clamp_low.inputs[0])
    link(tree, negative_half.outputs[0], clamp_low.inputs[1])

    clamp_high = _math(tree, "MINIMUM", (-680.0, -600.0), label="clamp(p.y, +-half)")
    link(tree, clamp_low.outputs[0], clamp_high.inputs[0])
    link(tree, half_segment.outputs[0], clamp_high.inputs[1])

    segment = _combine(
        tree, 0.0, clamp_high.outputs[0], 0.0, (-500.0, -600.0), label="Segment point"
    )
    delta = _vm(tree, "SUBTRACT", (-320.0, -600.0), label="p - segment")
    link(tree, vector, delta.inputs[0])
    link(tree, segment.outputs["Vector"], delta.inputs[1])

    delta_length = _vm(tree, "LENGTH", (-140.0, -600.0), label="length(p - segment)")
    link(tree, delta.outputs["Vector"], delta_length.inputs[0])
    capsule = _math(tree, "SUBTRACT", (40.0, -600.0), label="Capsule")
    link(tree, delta_length.outputs["Value"], capsule.inputs[0])
    link(tree, radius, capsule.inputs[1])

    # 6 Plane ------------------------------------------------------------------
    plane = py

    # 7 Cone -------------------------------------------------------------------
    cone_numerator = _math(tree, "ADD", (-1220.0, -900.0), label="Cone p.y + Size.y")
    link(tree, py, cone_numerator.inputs[0])
    link(tree, size_xyz.outputs["Y"], cone_numerator.inputs[1])

    cone_denominator = _math(tree, "MULTIPLY", (-1220.0, -1040.0), label="Cone 2 * Size.y")
    link(tree, size_xyz.outputs["Y"], cone_denominator.inputs[0])
    cone_denominator.inputs[1].default_value = 2.0

    cone_t = _math(tree, "DIVIDE", (-1040.0, -900.0), clamp=True, label="Cone t (clamped)")
    link(tree, cone_numerator.outputs[0], cone_t.inputs[0])
    link(tree, cone_denominator.outputs[0], cone_t.inputs[1])

    cone_one_minus_t = _math(tree, "SUBTRACT", (-860.0, -900.0), label="Cone 1 - t")
    cone_one_minus_t.inputs[0].default_value = 1.0
    link(tree, cone_t.outputs[0], cone_one_minus_t.inputs[1])

    cone_radius = _math(tree, "MULTIPLY", (-680.0, -900.0), label="Cone radius at y")
    link(tree, radius, cone_radius.inputs[0])
    link(tree, cone_one_minus_t.outputs[0], cone_radius.inputs[1])

    cone_radial = _math(tree, "SUBTRACT", (-500.0, -820.0), label="Cone length(p.xz) - r(y)")
    link(tree, radial.outputs["Value"], cone_radial.inputs[0])
    link(tree, cone_radius.outputs[0], cone_radial.inputs[1])

    abs_py = _math(tree, "ABSOLUTE", (-680.0, -1040.0), label="abs(p.y)")
    link(tree, py, abs_py.inputs[0])

    cone_vertical = _math(tree, "SUBTRACT", (-500.0, -1040.0), label="abs(p.y) - Size.y")
    link(tree, abs_py.outputs[0], cone_vertical.inputs[0])
    link(tree, size_xyz.outputs["Y"], cone_vertical.inputs[1])

    cone = _math(tree, "MAXIMUM", (-320.0, -900.0), label="Cone")
    link(tree, cone_radial.outputs[0], cone.inputs[0])
    link(tree, cone_vertical.outputs[0], cone.inputs[1])

    # Selector -----------------------------------------------------------------
    distance = _select_float(
        tree,
        sphere.outputs[0],
        [
            (1, box),
            (2, rounded_box.outputs[0]),
            (3, cylinder),
            (4, torus.outputs[0]),
            (5, capsule.outputs[0]),
            (6, plane),
            (7, cone.outputs[0]),
        ],
        shape,
        (520.0, 0.0),
        prefix="Shape",
    )
    link(tree, distance, out_sock(tree, "Distance"))

    _deselect(tree)

    log(
        "GOO SDF Primitive: 8 analytic distance branches + 7 COMPARE/nested Mix select; "
        "sphere/box/rounded box/capped Y cylinder/XZ torus/Y capsule/Y plane/approximate Y cone"
    )


# ---------------------------------------------------------------------------
# 2. GOO SDF Op
# ---------------------------------------------------------------------------
def _smooth_union(tree, a_socket, b_socket, k_socket, loc, *, label: str):
    """Polynomial smooth min: h = clamp(0.5 + 0.5 * (b - a) / k); mix(b, a, h) - k*h*(1-h)."""
    difference = _math(tree, "SUBTRACT", loc, label=f"{label} b - a")
    link(tree, b_socket, difference.inputs[0])
    link(tree, a_socket, difference.inputs[1])

    ratio = _math(tree, "DIVIDE", (loc[0] + 160.0, loc[1]), label=f"{label} (b - a) / k")
    link(tree, difference.outputs[0], ratio.inputs[0])
    link(tree, k_socket, ratio.inputs[1])

    half_ratio = _math(tree, "MULTIPLY", (loc[0] + 320.0, loc[1]), label=f"{label} 0.5 * ratio")
    link(tree, ratio.outputs[0], half_ratio.inputs[0])
    half_ratio.inputs[1].default_value = 0.5

    h = _math(
        tree,
        "ADD",
        (loc[0] + 480.0, loc[1]),
        clamp=True,
        label=f"{label} h = clamp(0.5 + 0.5*(b-a)/k)",
    )
    link(tree, half_ratio.outputs[0], h.inputs[0])
    h.inputs[1].default_value = 0.5

    mixed = _mix_float(tree, (loc[0] + 640.0, loc[1] - 80.0), label=f"{label} mix(b, a, h)")
    link(tree, h.outputs[0], mixed.inputs[MIX_FACTOR])
    link(tree, b_socket, mixed.inputs[MIX_FLOAT_A])
    link(tree, a_socket, mixed.inputs[MIX_FLOAT_B])

    one_minus_h = _math(tree, "SUBTRACT", (loc[0] + 640.0, loc[1] + 180.0), label=f"{label} 1 - h")
    one_minus_h.inputs[0].default_value = 1.0
    link(tree, h.outputs[0], one_minus_h.inputs[1])

    kh = _math(tree, "MULTIPLY", (loc[0] + 800.0, loc[1] + 180.0), label=f"{label} k * h")
    link(tree, k_socket, kh.inputs[0])
    link(tree, h.outputs[0], kh.inputs[1])

    correction = _math(
        tree, "MULTIPLY", (loc[0] + 960.0, loc[1] + 180.0), label=f"{label} k*h*(1-h)"
    )
    link(tree, kh.outputs[0], correction.inputs[0])
    link(tree, one_minus_h.outputs[0], correction.inputs[1])

    result = _math(tree, "SUBTRACT", (loc[0] + 1120.0, loc[1]), label=label)
    link(tree, mixed.outputs[MIX_FLOAT_RESULT], result.inputs[0])
    link(tree, correction.outputs[0], result.inputs[1])
    return result.outputs[0]


@group(
    "GOO SDF Op",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO SDF Op",
    description=(
        "Combine two signed distances. Operation is an Int selector (0 Union, 1 Intersect, "
        "2 Subtract, 3 Smooth Union, 4 Smooth Intersect, 5 Smooth Subtract, 6 Dilate, 7 Onion). "
        "Radius is the smooth blend width. / SDF 布尔与平滑操作。"
    ),
    version=1,
    featured_inputs=("Operation", "Radius"),
)
def build_sdf_op(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Distance A",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="First signed distance / 第一个有符号距离",
    )
    iface(
        tree,
        "Distance B",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="Second signed distance (unused by Dilate/Onion) / 第二个有符号距离",
    )
    iface(
        tree,
        "Operation",
        "INPUT",
        "NodeSocketInt",
        default=0,
        min_value=0,
        max_value=7,
        description=(
            "0 Union, 1 Intersect, 2 Subtract, 3 Smooth Union, 4 Smooth Intersect, "
            "5 Smooth Subtract, 6 Dilate, 7 Onion / 操作"
        ),
    )
    iface(
        tree,
        "Radius",
        "INPUT",
        "NodeSocketFloat",
        default=0.1,
        min_value=0.0001,
        description="Smooth blend width (smooth ops) or offset (Dilate/Onion) / 平滑半径",
    )

    _out_socket(tree, "Distance", "NodeSocketFloat", description="Result signed distance / 结果距离")

    gin(tree, (-1000.0, 0.0))
    gout(tree, (1700.0, 0.0))

    a = in_sock(tree, "Distance A")
    b = in_sock(tree, "Distance B")
    operation = in_sock(tree, "Operation")
    radius = in_sock(tree, "Radius")

    # 0 Union = min(a, b) -------------------------------------------------------
    union = _math(tree, "MINIMUM", (-780.0, 700.0), label="Union min(a, b)")
    link(tree, a, union.inputs[0])
    link(tree, b, union.inputs[1])

    # 1 Intersect = max(a, b) ---------------------------------------------------
    intersect = _math(tree, "MAXIMUM", (-780.0, 540.0), label="Intersect max(a, b)")
    link(tree, a, intersect.inputs[0])
    link(tree, b, intersect.inputs[1])

    # 2 Subtract = max(a, -b) ---------------------------------------------------
    negative_b = _negate(tree, b, (-960.0, 380.0), label="-b")
    subtract = _math(tree, "MAXIMUM", (-780.0, 380.0), label="Subtract max(a, -b)")
    link(tree, a, subtract.inputs[0])
    link(tree, negative_b, subtract.inputs[1])

    # 3 Smooth Union ------------------------------------------------------------
    smooth_union = _smooth_union(tree, a, b, radius, (-780.0, 180.0), label="Smooth Union")

    # 4 Smooth Intersect = -smooth_union(-a, -b) --------------------------------
    negative_a = _negate(tree, a, (-960.0, -60.0), label="-a (intersect)")
    negative_b_2 = _negate(tree, b, (-960.0, -180.0), label="-b (intersect)")
    smooth_intersect_inner = _smooth_union(
        tree, negative_a, negative_b_2, radius, (-780.0, -160.0), label="smooth_union(-a, -b)"
    )
    smooth_intersect = _negate(tree, smooth_intersect_inner, (560.0, -160.0), label="Smooth Intersect")

    # 5 Smooth Subtract = -smooth_union(-a, b) ----------------------------------
    negative_a_2 = _negate(tree, a, (-960.0, -420.0), label="-a (subtract)")
    smooth_subtract_inner = _smooth_union(
        tree, negative_a_2, b, radius, (-780.0, -420.0), label="smooth_union(-a, b)"
    )
    smooth_subtract = _negate(
        tree, smooth_subtract_inner, (560.0, -460.0), label="Smooth Subtract"
    )

    # 6 Dilate = a - Radius -----------------------------------------------------
    dilate = _math(tree, "SUBTRACT", (-780.0, -700.0), label="Dilate a - Radius")
    link(tree, a, dilate.inputs[0])
    link(tree, radius, dilate.inputs[1])

    # 7 Onion = abs(a) - Radius -------------------------------------------------
    abs_a = _math(tree, "ABSOLUTE", (-960.0, -840.0), label="abs(a)")
    link(tree, a, abs_a.inputs[0])
    onion = _math(tree, "SUBTRACT", (-780.0, -840.0), label="Onion abs(a) - Radius")
    link(tree, abs_a.outputs[0], onion.inputs[0])
    link(tree, radius, onion.inputs[1])

    distance = _select_float(
        tree,
        union.outputs[0],
        [
            (1, intersect.outputs[0]),
            (2, subtract.outputs[0]),
            (3, smooth_union),
            (4, smooth_intersect),
            (5, smooth_subtract),
            (6, dilate.outputs[0]),
            (7, onion.outputs[0]),
        ],
        operation,
        (1000.0, 0.0),
        prefix="Operation",
    )
    link(tree, distance, out_sock(tree, "Distance"))

    _deselect(tree)

    log(
        "GOO SDF Op: 8 combine ops (min/max/smooth polynomial smin and its negations) "
        "with 7 COMPARE + nested Float Mix selection"
    )


# ---------------------------------------------------------------------------
# 3. GOO SDF Vector Op
# ---------------------------------------------------------------------------
def _rotate_axis(tree, vector_socket, axis, angle_socket_or_float, loc, *, label: str):
    n = node(tree, "ShaderNodeVectorRotate", loc=loc, label=label)
    n.rotation_type = "AXIS_ANGLE"
    link(tree, vector_socket, n.inputs["Vector"])
    n.inputs["Center"].default_value = (0.0, 0.0, 0.0)
    n.inputs["Axis"].default_value = axis
    if isinstance(angle_socket_or_float, (int, float)):
        n.inputs["Angle"].default_value = float(angle_socket_or_float)
    else:
        link(tree, angle_socket_or_float, n.inputs["Angle"])
    return n


@group(
    "GOO SDF Vector Op",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO SDF Vector Op",
    description=(
        "Domain manipulation for SDF positions. Operation is an Int selector (0 Repeat, "
        "1 Mirror, 2 Twist, 3 Bend, 4 Polar, 5 Swizzle, 6 Rotate, 7 Extrude). "
        "/ SDF 坐标域操作（重复/镜像/扭曲/弯曲/极坐标/分量交换/旋转/挤出）。"
    ),
    version=1,
    featured_inputs=("Operation", "Amount", "Scale"),
)
def build_sdf_vector_op(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Vector",
        "INPUT",
        "NodeSocketVector",
        default=(0.0, 0.0, 0.0),
        description="Input position / 输入坐标",
    )
    iface(
        tree,
        "Operation",
        "INPUT",
        "NodeSocketInt",
        default=0,
        min_value=0,
        max_value=7,
        description=(
            "0 Repeat, 1 Mirror, 2 Twist, 3 Bend, 4 Polar, 5 Swizzle, 6 Rotate, "
            "7 Extrude / 操作"
        ),
    )
    iface(
        tree,
        "Amount",
        "INPUT",
        "NodeSocketFloat",
        default=1.0,
        description="Twist/Bend/Rotate angle in radians / 角度（弧度）",
    )
    iface(
        tree,
        "Scale",
        "INPUT",
        "NodeSocketVector",
        default=(1.0, 1.0, 1.0),
        description="Repeat cell size (all components must be non-zero) / 重复单元尺寸",
    )

    # Input and output share the name "Vector": use the direction aware helper.
    _out_socket(tree, "Vector", "NodeSocketVector", description="Transformed position / 变换后的坐标")

    gin(tree, (-1600.0, 0.0))
    gout(tree, (1300.0, 0.0))

    vector = in_sock(tree, "Vector")
    operation = in_sock(tree, "Operation")
    amount = in_sock(tree, "Amount")
    scale = in_sock(tree, "Scale")
    p_xyz = _separate(tree, vector, (-1400.0, 300.0), label="p.xyz")

    # 0 Repeat: (fract((p + 0.5 * Scale) / Scale) - 0.5) * Scale ----------------
    # MULTIPLY_ADD is A * B + C: Scale * 0.5 + p, i.e. p + 0.5 * Scale.
    shifted = node(tree, "ShaderNodeVectorMath", loc=(-1220.0, 760.0), label="p + 0.5 * Scale")
    shifted.operation = "MULTIPLY_ADD"
    link(tree, scale, shifted.inputs[0])
    shifted.inputs[1].default_value = (0.5, 0.5, 0.5)
    link(tree, vector, shifted.inputs[2])

    quotient = node(tree, "ShaderNodeVectorMath", loc=(-860.0, 760.0), label="(p + 0.5*Scale) / Scale")
    quotient.operation = "DIVIDE"
    link(tree, shifted.outputs["Vector"], quotient.inputs[0])
    link(tree, scale, quotient.inputs[1])

    fraction = _vm(tree, "FRACTION", (-680.0, 760.0), label="fract(...)")
    link(tree, quotient.outputs["Vector"], fraction.inputs[0])

    centered = _vm(tree, "SUBTRACT", (-500.0, 760.0), label="fract(...) - 0.5")
    link(tree, fraction.outputs["Vector"], centered.inputs[0])
    centered.inputs[1].default_value = (0.5, 0.5, 0.5)

    repeat = _vm(tree, "MULTIPLY", (-320.0, 760.0), label="Repeat")
    link(tree, centered.outputs["Vector"], repeat.inputs[0])
    link(tree, scale, repeat.inputs[1])

    # 1 Mirror: abs(p) ----------------------------------------------------------
    mirror = _vm(tree, "ABSOLUTE", (-320.0, 600.0), label="Mirror abs(p)")
    link(tree, vector, mirror.inputs[0])

    # 2 Twist: rotate around +Z by Amount * p.z ---------------------------------
    twist_angle = _math(tree, "MULTIPLY", (-1220.0, 300.0), label="Amount * p.z")
    link(tree, amount, twist_angle.inputs[0])
    link(tree, p_xyz.outputs["Z"], twist_angle.inputs[1])
    twist = _rotate_axis(tree, vector, (0.0, 0.0, 1.0), twist_angle.outputs[0], (-500.0, 440.0), label="Twist")

    # 3 Bend: rotate around +X by Amount * p.x ---------------------------------
    bend_angle = _math(tree, "MULTIPLY", (-1220.0, 120.0), label="Amount * p.x")
    link(tree, amount, bend_angle.inputs[0])
    link(tree, p_xyz.outputs["X"], bend_angle.inputs[1])
    bend = _rotate_axis(tree, vector, (1.0, 0.0, 0.0), bend_angle.outputs[0], (-320.0, 280.0), label="Bend")

    # 4 Polar: (length(p.xz), atan2(p.z, p.x) / pi, p.y) ------------------------
    polar_flat = _vm(
        tree, "MULTIPLY", (-1220.0, -80.0), label="Flatten XZ", value=(1.0, 0.0, 1.0)
    )
    link(tree, vector, polar_flat.inputs[0])
    polar_radius = _vm(tree, "LENGTH", (-1040.0, -80.0), label="length(p.xz)")
    link(tree, polar_flat.outputs["Vector"], polar_radius.inputs[0])

    polar_angle = _math(tree, "ARCTAN2", (-1040.0, -240.0), label="atan2(p.z, p.x)")
    link(tree, p_xyz.outputs["Z"], polar_angle.inputs[0])
    link(tree, p_xyz.outputs["X"], polar_angle.inputs[1])

    polar_scaled = _math(tree, "DIVIDE", (-860.0, -240.0), label="atan2 / pi")
    link(tree, polar_angle.outputs[0], polar_scaled.inputs[0])
    polar_scaled.inputs[1].default_value = PI

    polar = _combine(
        tree,
        polar_radius.outputs["Value"],
        polar_scaled.outputs[0],
        p_xyz.outputs["Y"],
        (-680.0, -160.0),
        label="Polar",
    )

    # 5 Swizzle: (p.y, p.z, p.x) ------------------------------------------------
    swizzle = _combine(
        tree,
        p_xyz.outputs["Y"],
        p_xyz.outputs["Z"],
        p_xyz.outputs["X"],
        (-320.0, -80.0),
        label="Swizzle (y, z, x)",
    )

    # 6 Rotate: around +Z by Amount --------------------------------------------
    rotate = _rotate_axis(tree, vector, (0.0, 0.0, 1.0), amount, (-320.0, -240.0), label="Rotate")

    # 7 Extrude: (p.x, p.y, 0) --------------------------------------------------
    extrude = _combine(
        tree, p_xyz.outputs["X"], p_xyz.outputs["Y"], 0.0, (-320.0, -400.0), label="Extrude (x, y, 0)"
    )

    vector_out = _select_vector(
        tree,
        repeat.outputs["Vector"],
        [
            (1, mirror.outputs["Vector"]),
            (2, twist.outputs["Vector"]),
            (3, bend.outputs["Vector"]),
            (4, polar.outputs["Vector"]),
            (5, swizzle.outputs["Vector"]),
            (6, rotate.outputs["Vector"]),
            (7, extrude.outputs["Vector"]),
        ],
        operation,
        (700.0, 0.0),
        prefix="Operation",
    )
    link(tree, vector_out, out_sock(tree, "Vector"))

    _deselect(tree)

    log(
        "GOO SDF Vector Op: 8 domain branches (Repeat via MULTIPLY_ADD/FRACTION, Vector Rotate "
        "twist/bend/rotate, polar/swap/extrude) + 7 COMPARE/nested Vector Mix select"
    )


# ---------------------------------------------------------------------------
# 4. GOO SDF Noise
# ---------------------------------------------------------------------------
@group(
    "GOO SDF Noise",
    tree_type="ShaderNodeTree",
    category="textures",
    label="GOO SDF Noise",
    description=(
        "Displace a signed distance with two octaves of deterministic Blender noise. "
        "d_out = Distance + (n1 + 0.5 * Detail Inflation * n2) * 0.35 * Detail Blend, "
        "where n1/n2 are 0..1 noise mapped to -1..1 (scale 2 / 6). "
        "/ 用两层噪声扰动 SDF 距离（近似 GOO 的 sdFbm）。"
    ),
    version=1,
    featured_inputs=("Distance", "Detail", "Roughness", "Detail Blend"),
)
def build_sdf_noise(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Position",
        "INPUT",
        "NodeSocketVector",
        default=(0.0, 0.0, 0.0),
        description="Sample position / 采样位置",
    )
    iface(
        tree,
        "Distance",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="Input signed distance / 输入距离",
    )
    iface(
        tree,
        "Detail",
        "INPUT",
        "NodeSocketFloat",
        default=2.0,
        description="Noise detail of the base octave / 基础噪声细节",
    )
    iface(
        tree,
        "Roughness",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="Noise roughness (both octaves) / 噪声粗糙度",
    )
    iface(
        tree,
        "Detail Inflation",
        "INPUT",
        "NodeSocketFloat",
        default=0.0,
        description="Extra weight on the high frequency octave / 高频细节膨胀",
    )
    iface(
        tree,
        "Detail Blend",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        description="Overall displacement strength (0..1 is sensible) / 整体混合强度",
    )

    # Input and output share the name "Distance": use the direction aware helper.
    _out_socket(tree, "Distance", "NodeSocketFloat", description="Displaced signed distance / 扰动后的距离")

    gin(tree, (-1000.0, 0.0))
    gout(tree, (900.0, 0.0))

    position = in_sock(tree, "Position")
    detail = in_sock(tree, "Detail")
    roughness = in_sock(tree, "Roughness")
    inflation = in_sock(tree, "Detail Inflation")
    blend = in_sock(tree, "Detail Blend")
    distance = in_sock(tree, "Distance")

    # Two octaves: scale 2 (detail linked) and scale 6 (detail fixed at 2).
    noise_low = node(tree, "ShaderNodeTexNoise", loc=(-780.0, 420.0), label="Noise scale 2")
    noise_low.noise_dimensions = "3D"
    link(tree, position, noise_low.inputs["Vector"])
    noise_low.inputs["Scale"].default_value = 2.0
    link(tree, detail, noise_low.inputs["Detail"])
    link(tree, roughness, noise_low.inputs["Roughness"])

    noise_high = node(tree, "ShaderNodeTexNoise", loc=(-780.0, 100.0), label="Noise scale 6")
    noise_high.noise_dimensions = "3D"
    link(tree, position, noise_high.inputs["Vector"])
    noise_high.inputs["Scale"].default_value = 6.0
    noise_high.inputs["Detail"].default_value = 2.0
    link(tree, roughness, noise_high.inputs["Roughness"])

    low_signed = _map_range(tree, (-560.0, 420.0), label="n1: 0..1 -> -1..1")
    link(tree, noise_low.outputs["Fac"], low_signed.inputs["Value"])
    low_signed.inputs["From Min"].default_value = 0.0
    low_signed.inputs["From Max"].default_value = 1.0
    low_signed.inputs["To Min"].default_value = -1.0
    low_signed.inputs["To Max"].default_value = 1.0

    high_signed = _map_range(tree, (-560.0, 100.0), label="n2: 0..1 -> -1..1")
    link(tree, noise_high.outputs["Fac"], high_signed.inputs["Value"])
    high_signed.inputs["From Min"].default_value = 0.0
    high_signed.inputs["From Max"].default_value = 1.0
    high_signed.inputs["To Min"].default_value = -1.0
    high_signed.inputs["To Max"].default_value = 1.0

    # term = 0.5 * Detail Inflation * n2
    inflation_term = _math(tree, "MULTIPLY", (-360.0, 220.0), label="Detail Inflation * n2")
    link(tree, high_signed.outputs["Result"], inflation_term.inputs[0])
    link(tree, inflation, inflation_term.inputs[1])

    half_inflation = _math(tree, "MULTIPLY", (-180.0, 220.0), label="0.5 * ...")
    link(tree, inflation_term.outputs[0], half_inflation.inputs[0])
    half_inflation.inputs[1].default_value = 0.5

    # detail = n1 + 0.5 * Detail Inflation * n2
    detail_sum = _math(tree, "ADD", (0.0, 320.0), label="n1 + inflated n2")
    link(tree, low_signed.outputs["Result"], detail_sum.inputs[0])
    link(tree, half_inflation.outputs[0], detail_sum.inputs[1])

    # amplitude = 0.35 * Detail Blend
    amplitude = _math(tree, "MULTIPLY", (0.0, 80.0), label="0.35 * Detail Blend")
    link(tree, blend, amplitude.inputs[0])
    amplitude.inputs[1].default_value = 0.35

    displacement = _math(tree, "MULTIPLY", (180.0, 260.0), label="detail * amplitude")
    link(tree, detail_sum.outputs[0], displacement.inputs[0])
    link(tree, amplitude.outputs[0], displacement.inputs[1])

    result = _math(tree, "ADD", (360.0, 160.0), label="Distance + displacement")
    link(tree, distance, result.inputs[0])
    link(tree, displacement.outputs[0], result.inputs[1])

    link(tree, result.outputs[0], out_sock(tree, "Distance"))

    _deselect(tree)

    log(
        "GOO SDF Noise: d_out = Distance + (n1 + 0.5*Detail Inflation*n2) * 0.35 * Detail Blend; "
        "n1/n2 from Noise Textures at scale 2/6 mapped 0..1 -> -1..1"
    )
