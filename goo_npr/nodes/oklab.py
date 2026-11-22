# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO OKLab shader node groups (Blender 5.2, ``ShaderNodeTree``).

GOO Engine 4.4 ships a C++ node, ``ShaderNodeOKLabColorRamp`` (UI name
"OKLab Color Ramp"), whose storage is a native ColorBand: the user edits stops
with the regular ColorRamp widget and the node evaluates the band by
interpolating in OKLab.  Blender's Python API cannot create a custom node with
a ColorRamp widget, so this module ports the *behaviour* as two node groups:

``GOO OKLab Mix``
    Interpolate two scene-linear colors by a factor in OKLab.
``GOO OKLab Color Ramp``
    Four fixed stops (color + position), evaluated as a piecewise OKLab ramp.

The math is Björn Ottosson's OKLab (2020), the same matrices as GOO's
``gpu_shader_material_oklab_colorramp.glsl``.

Forward (linear sRGB -> LMS -> OKLab)::

    l = 0.4122214708 r + 0.5363325363 g + 0.0514459929 b
    m = 0.2119034982 r + 0.6806995451 g + 0.1073969566 b
    s = 0.0883024619 r + 0.2817188376 g + 0.6299787005 b
    l' = cbrt(l), m' = cbrt(m), s' = cbrt(s)
    L = 0.2104542553 l' + 0.7936177850 m' - 0.0040720468 s'
    a = 1.9779984951 l' - 2.4285922050 m' + 0.4505937099 s'
    b = 0.0259040371 l' + 0.7827717662 m' - 0.8086757660 s'

Inverse (OKLab -> LMS -> linear sRGB)::

    l' = L + 0.3963377774 a + 0.2158037573 b
    m' = L - 0.1055613458 a - 0.0638541728 b
    s' = L - 0.0894841775 a - 1.2914855480 b
    l = l'^3, m = m'^3, s = s'^3
    r =  4.0767416621 l - 3.3077115913 m + 0.2309699292 s
    g = -1.2684380046 l + 2.6097574011 m - 0.3413193965 s
    b = -0.0041960863 l - 0.7034186147 m + 1.7076147010 s

Deviations from GOO 4.4
-----------------------
* **No alpha.** ``ShaderNodeSeparateColor`` / ``ShaderNodeCombineColor``
  expose RGB only in shader node trees (the geometry-nodes ``FunctionNode*``
  variants that do have Alpha cannot be added to a ``ShaderNodeTree``), so the
  ``Color`` output carries alpha 1.0 and the input alphas are not mixed.  GOO
  lerps the two input alphas; do that explicitly outside the group when needed.
* **Cube root.** Blender's ``Math`` POWER clamps a negative base to zero.  The
  forward LMS inputs are non-negative for every shader color (all coefficients
  are positive), so this never triggers in practice; GOO's GLSL uses
  ``sign(x) * pow(abs(x) + 1e-10, 1/3)`` instead.  The inverse cube uses two
  MULTIPLY nodes so negative ``l'/m'/s'`` (possible for out-of-gamut OKLab
  values) stay signed, like Ottosson's ``x^3``.
* **No gamut mapping / clamp.** GOO divides linear RGB by its largest
  component when it exceeds 1, clamps negative RGB to zero and clamps the mix
  factor and result to [0, 1].  This port implements the formulas above
  verbatim, which keeps OKLab internally consistent; OKLab interpolation can
  leave the sRGB gamut, so add a Clamp node downstream for GOO's exact output
  range.
* **Fixed stops.** The native node has a ColorRamp widget with an arbitrary
  number of stops and an Alpha output; ``GOO OKLab Color Ramp`` exposes four
  stops, Fac and a Color output.  GOO's 2-stop GPU path also applies an ease
  (smoothstep) to the factor; that is not ported - both groups interpolate
  linearly.
* Ramp stop positions may be non-monotonic.  Evaluation stays numerically
  defined (the local segment factor is clamped to [0, 1] and the stop distance
  is floored at ``SEGMENT_EPSILON``), but like the native node the result then
  follows the stops in the order they are given.

All inputs and outputs are scene-linear RGB - shader color sockets are already
linear, so no sRGB decode happens inside the groups.
"""

from __future__ import annotations

import bpy

from ..utils import gin, gout, iface, in_sock, link, log, node, out_sock
from .registry import group

#: Ottosson linear sRGB -> LMS matrix (row major).
OKLAB_LMS_FROM_RGB = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)

#: Cube-rooted LMS (l', m', s') -> OKLab matrix (row major).
OKLAB_LAB_FROM_LMS = (
    (0.2104542553, 0.7936177850, -0.0040720468),
    (1.9779984951, -2.4285922050, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.8086757660),
)

#: OKLab -> cube-rooted LMS matrix; the first column is always 1 (the L term).
OKLAB_LMS_FROM_LAB = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)

#: LMS -> linear sRGB matrix (row major).
OKLAB_RGB_FROM_LMS = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)

ONE_THIRD = 1.0 / 3.0
#: Floor for ``stop[i + 1].position - stop[i].position`` (avoids / 0 when two
#: stops share a position or are out of order).
SEGMENT_EPSILON = 1e-6

#: Column / row pitch of the generated math grids (node editor units).
COL = 170.0
ROW = 150.0

#: Default ramp stops, in stop order.
STOP_COLORS = (
    (0.0, 0.0, 0.0, 1.0),
    (1.0, 0.0, 0.0, 1.0),
    (0.0, 1.0, 0.0, 1.0),
    (1.0, 1.0, 1.0, 1.0),
)
STOP_POSITIONS = (0.0, 0.33, 0.66, 1.0)


# ---------------------------------------------------------------------------
# local helpers
# ---------------------------------------------------------------------------
def _math(tree, operation: str, loc, *, clamp: bool = False, label: str | None = None):
    n = node(tree, "ShaderNodeMath", loc=loc, label=label)
    n.operation = operation
    n.use_clamp = clamp
    return n


def _mul(tree, socket, factor: float, loc, *, label: str | None = None):
    """``socket * factor`` - returns the result socket."""
    n = _math(tree, "MULTIPLY", loc, label=label)
    link(tree, socket, n.inputs[0])
    n.inputs[1].default_value = factor
    return n.outputs[0]


def _mul_add(tree, socket, factor: float, addend, loc, *, label: str | None = None):
    """``socket * factor + addend`` - returns the result socket."""
    n = _math(tree, "MULTIPLY_ADD", loc, label=label)
    link(tree, socket, n.inputs[0])
    n.inputs[1].default_value = factor
    link(tree, addend, n.inputs[2])
    return n.outputs[0]


def _power_third(tree, socket, loc, *, label: str | None = None):
    """Cube root through Math POWER (negative bases are clamped to 0)."""
    n = _math(tree, "POWER", loc, label=label)
    link(tree, socket, n.inputs[0])
    n.inputs[1].default_value = ONE_THIRD
    return n.outputs[0]


def _cube(tree, socket, loc, *, label: str | None = None):
    """Sign preserving ``socket ** 3`` through two MULTIPLY nodes."""
    square = _math(tree, "MULTIPLY", loc, label=label)
    link(tree, socket, square.inputs[0])
    link(tree, socket, square.inputs[1])
    cube = _math(tree, "MULTIPLY", loc=(loc[0] + COL, loc[1]), label=label)
    link(tree, square.outputs[0], cube.inputs[0])
    link(tree, socket, cube.inputs[1])
    return cube.outputs[0]


def _mix_float(tree, factor, a_socket, b_socket, loc, *, label: str | None = None):
    """Mix node in FLOAT mode: Factor=0, A=2, B=3, result=0."""
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "FLOAT"
    link(tree, factor, n.inputs[0])
    link(tree, a_socket, n.inputs[2])
    link(tree, b_socket, n.inputs[3])
    return n.outputs[0]


def _mix_color(tree, factor, a_socket, b_socket, loc, *, label: str | None = None):
    """Mix node in RGBA mode: Factor=0, A=6, B=7, result=2."""
    n = node(tree, "ShaderNodeMix", loc=loc, label=label)
    n.data_type = "RGBA"
    n.blend_type = "MIX"
    # OKLab carries negative a/b values: the result must not be clamped.
    for attr, value in (("clamp_factor", True), ("clamp_result", False)):
        if hasattr(n, attr):
            setattr(n, attr, value)
    link(tree, factor, n.inputs[0])
    link(tree, a_socket, n.inputs[6])
    link(tree, b_socket, n.inputs[7])
    return n.outputs[2]


def _forward_oklab(tree, color_socket, loc, *, label: str = "OKLab"):
    """Linear sRGB color socket -> ``(L, a, b)`` float sockets.

    ``loc`` is the separator anchor; the six 3x3 matrix rows are laid out below
    it.  Each row is ``MUL`` followed by two ``MULTIPLY_ADD`` nodes, i.e. the
    literal formula from the module docstring.
    """
    x0, y0 = loc
    sep = node(tree, "ShaderNodeSeparateColor", loc=(x0, y0), label=f"{label} RGB")
    link(tree, color_socket, sep.inputs["Color"])
    rgb = (sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"])

    cbrt = []
    for row, coeffs in enumerate(OKLAB_LMS_FROM_RGB):
        y = y0 - ROW * (row + 1)
        acc = _mul(tree, rgb[0], coeffs[0], (x0 + COL, y), label=f"{label} LMS {row}")
        acc = _mul_add(tree, rgb[1], coeffs[1], acc, (x0 + 2 * COL, y))
        acc = _mul_add(tree, rgb[2], coeffs[2], acc, (x0 + 3 * COL, y))
        cbrt.append(_power_third(tree, acc, (x0 + 4 * COL, y), label=f"{label} cbrt {row}"))

    lab = []
    for row, coeffs in enumerate(OKLAB_LAB_FROM_LMS):
        y = y0 - ROW * (4 + row)
        acc = _mul(tree, cbrt[0], coeffs[0], (x0 + 5 * COL, y), label=f"{label} Lab {row}")
        acc = _mul_add(tree, cbrt[1], coeffs[1], acc, (x0 + 6 * COL, y))
        acc = _mul_add(tree, cbrt[2], coeffs[2], acc, (x0 + 7 * COL, y))
        lab.append(acc)
    return tuple(lab)


def _inverse_oklab(tree, lab, loc, *, label: str = "OKLab"):
    """``(L, a, b)`` float sockets -> linear sRGB color socket."""
    L, a, b = lab
    x0, y0 = loc

    lms = []
    for row, coeffs in enumerate(OKLAB_LMS_FROM_LAB):
        y = y0 - ROW * row
        acc = _mul_add(tree, a, coeffs[1], L, (x0, y), label=f"{label} lms' {row}")
        acc = _mul_add(tree, b, coeffs[2], acc, (x0 + COL, y))
        lms.append(_cube(tree, acc, (x0 + 2 * COL, y), label=f"{label} cube {row}"))

    rgb = []
    for row, coeffs in enumerate(OKLAB_RGB_FROM_LMS):
        y = y0 - ROW * (3 + row)
        acc = _mul(tree, lms[0], coeffs[0], (x0 + 4 * COL, y), label=f"{label} RGB {row}")
        acc = _mul_add(tree, lms[1], coeffs[1], acc, (x0 + 5 * COL, y))
        acc = _mul_add(tree, lms[2], coeffs[2], acc, (x0 + 6 * COL, y))
        rgb.append(acc)

    comb = node(tree, "ShaderNodeCombineColor", loc=(x0 + 7 * COL, y0 - ROW * 4), label=f"{label} combine")
    for channel, socket in zip(("Red", "Green", "Blue"), rgb):
        link(tree, socket, comb.inputs[channel])
    return comb.outputs["Color"]


def _segment_factor(tree, fac, pos_low, pos_high, loc, *, label: str | None = None):
    """``clamp((fac - pos_low) / max(pos_high - pos_low, eps))``."""
    x, y = loc
    delta = _math(tree, "SUBTRACT", (x, y), label=f"{label} distance")
    link(tree, pos_high, delta.inputs[0])
    link(tree, pos_low, delta.inputs[1])

    denom = _math(tree, "MAXIMUM", (x + COL, y), label=f"{label} max(distance, eps)")
    link(tree, delta.outputs[0], denom.inputs[0])
    denom.inputs[1].default_value = SEGMENT_EPSILON

    numerator = _math(tree, "SUBTRACT", (x + COL, y - ROW), label=f"{label} fac - low")
    link(tree, fac, numerator.inputs[0])
    link(tree, pos_low, numerator.inputs[1])

    factor = _math(tree, "DIVIDE", (x + 2 * COL, y - ROW), clamp=True, label=label)
    link(tree, numerator.outputs[0], factor.inputs[0])
    link(tree, denom.outputs[0], factor.inputs[1])
    return factor.outputs[0]


def _less_than(tree, value_socket, threshold_socket, loc, *, label: str | None = None):
    n = _math(tree, "LESS_THAN", loc, label=label)
    link(tree, value_socket, n.inputs[0])
    link(tree, threshold_socket, n.inputs[1])
    return n.outputs[0]


def _one_minus(tree, socket, loc, *, label: str | None = None):
    n = _math(tree, "SUBTRACT", loc, label=label)
    n.inputs[0].default_value = 1.0
    link(tree, socket, n.inputs[1])
    return n.outputs[0]


def _multiply(tree, a_socket, b_socket, loc, *, label: str | None = None):
    n = _math(tree, "MULTIPLY", loc, label=label)
    link(tree, a_socket, n.inputs[0])
    link(tree, b_socket, n.inputs[1])
    return n.outputs[0]


def _deselect(tree) -> None:
    """Clear node selection flags so the generated group is inert in the UI."""
    for n in tree.nodes:
        n.select = False


# ---------------------------------------------------------------------------
# GOO OKLab Mix
# ---------------------------------------------------------------------------
@group(
    "GOO OKLab Mix",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO OKLab Mix",
    description=(
        "Interpolate two scene-linear colors in OKLab (perceptual L/a/b lerp, "
        "then inverse transform back to linear RGB). GOO 4.4 has no stand-alone "
        "OKLab mix node; this uses the same matrices as GOO OKLab Color Ramp. "
        "/ 在 OKLab 空间插值两种颜色（按 L/a/b 线性插值后反变换回线性 RGB）。"
    ),
    version=1,
    featured_inputs=("Color A", "Color B", "Factor"),
)
def build_oklab_mix(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Color A",
        "INPUT",
        "NodeSocketColor",
        default=(1.0, 0.0, 0.0, 1.0),
        description="First (factor 0) color, scene linear / 插值起点颜色",
    )
    iface(
        tree,
        "Color B",
        "INPUT",
        "NodeSocketColor",
        default=(0.0, 0.0, 1.0, 1.0),
        description="Second (factor 1) color, scene linear / 插值终点颜色",
    )
    iface(
        tree,
        "Factor",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        min_value=0.0,
        max_value=1.0,
        subtype="FACTOR",
        description="Interpolation factor (0 = Color A, 1 = Color B) / 插值系数",
    )
    iface(
        tree,
        "Color",
        "OUTPUT",
        "NodeSocketColor",
        description="Interpolated linear RGB color / 插值后的线性颜色",
    )

    gin(tree, loc=(-3100.0, 400.0))
    gout(tree, loc=(500.0, -1650.0))

    factor = in_sock(tree, "Factor")
    lab_a = _forward_oklab(tree, in_sock(tree, "Color A"), (-2850.0, 250.0), label="Color A")
    lab_b = _forward_oklab(tree, in_sock(tree, "Color B"), (-2850.0, -1150.0), label="Color B")

    lab = []
    for axis, a_socket, b_socket, y in zip(
        ("L", "a", "b"),
        lab_a,
        lab_b,
        (-1050.0, -1200.0, -1350.0),
    ):
        lab.append(_mix_float(tree, factor, a_socket, b_socket, (-1350.0, y), label=f"Mix {axis}"))

    color = _inverse_oklab(tree, lab, (-1150.0, -1050.0), label="Mix")
    link(tree, color, out_sock(tree, "Color"))

    _deselect(tree)

    log(
        f"GOO OKLab Mix: {len(tree.nodes)} nodes, linear sRGB -> LMS -> OKLab, "
        "float Mix on L/a/b, inverse transform; alpha not mixed (shader trees "
        "expose no alpha socket), POWER cbrt clamps negative LMS, no gamut clamp"
    )


# ---------------------------------------------------------------------------
# GOO OKLab Color Ramp
# ---------------------------------------------------------------------------
@group(
    "GOO OKLab Color Ramp",
    tree_type="ShaderNodeTree",
    category="shader",
    label="GOO OKLab Color Ramp",
    description=(
        "Approximation of GOO Engine's native OKLab Color Ramp: Fac is evaluated "
        "against four fixed stops and adjacent stop colors are interpolated in "
        "OKLab. The original is a C++ node with a real ColorRamp widget, so this "
        "port exposes four stops instead of an editable band. "
        "/ GOO OKLab Color Ramp 的近似实现：Fac 与四个固定色标比较，相邻色标在 OKLab "
        "空间插值；原版为带 ColorRamp 控件的 C++ 节点，此处以四个固定色标代替。"
    ),
    version=1,
    featured_inputs=("Fac",),
)
def build_oklab_color_ramp(tree: bpy.types.NodeTree) -> None:
    iface(
        tree,
        "Fac",
        "INPUT",
        "NodeSocketFloat",
        default=0.5,
        min_value=0.0,
        max_value=1.0,
        subtype="FACTOR",
        description="Ramp position (0 = Stop 1, 1 = Stop 4) / 渐变位置",
    )
    for index in range(4):
        iface(
            tree,
            f"Stop {index + 1} Color",
            "INPUT",
            "NodeSocketColor",
            default=STOP_COLORS[index],
            description=f"Color of stop {index + 1} (scene linear) / 第 {index + 1} 个色标颜色",
        )
        iface(
            tree,
            f"Stop {index + 1} Position",
            "INPUT",
            "NodeSocketFloat",
            default=STOP_POSITIONS[index],
            min_value=0.0,
            max_value=1.0,
            description=f"Position of stop {index + 1} / 第 {index + 1} 个色标位置",
        )
    iface(
        tree,
        "Color",
        "OUTPUT",
        "NodeSocketColor",
        description="Interpolated ramp color / 渐变颜色",
    )

    gin(tree, loc=(-3000.0, 1600.0))
    gout(tree, loc=(4300.0, -500.0))

    fac = in_sock(tree, "Fac")
    positions = [in_sock(tree, f"Stop {index + 1} Position") for index in range(4)]

    # Convert every stop color to OKLab once.  L/a/b are packed into a color
    # socket (CombineColor) which is only used internally as an OKLab carrier:
    # a and b are commonly negative and MUST stay unclamped through the Mix
    # nodes below (see _mix_color).
    block_locs = (
        (-2400.0, 700.0),
        (-800.0, 700.0),
        (-2400.0, -700.0),
        (-800.0, -700.0),
    )
    pack_locs = (
        (600.0, 150.0),
        (600.0, -75.0),
        (600.0, -1275.0),
        (600.0, -1500.0),
    )
    stops = []
    for index in range(4):
        lab = _forward_oklab(
            tree,
            in_sock(tree, f"Stop {index + 1} Color"),
            block_locs[index],
            label=f"Stop {index + 1}",
        )
        pack = node(
            tree,
            "ShaderNodeCombineColor",
            loc=pack_locs[index],
            label=f"Stop {index + 1} OKLab",
        )
        for channel, socket in zip(("Red", "Green", "Blue"), lab):
            link(tree, socket, pack.inputs[channel])
        stops.append((positions[index], pack.outputs["Color"]))

    # Four stops => three segments; every segment gets its own clamped local
    # factor t = clamp((Fac - p_i) / max(p_{i+1} - p_i, eps), 0, 1).
    segment_factors = [
        _segment_factor(
            tree,
            fac,
            positions[index],
            positions[index + 1],
            (600.0, -2200.0 - 400.0 * index),
            label=f"Segment {index + 1}",
        )
        for index in range(3)
    ]
    segments = [
        _mix_color(
            tree,
            segment_factors[index],
            stops[index][1],
            stops[index + 1][1],
            (1200.0, (100.0, -600.0, -1300.0)[index]),
            label=f"Segment {index + 1} OKLab",
        )
        for index in range(3)
    ]

    # Exclusive masks.  m1 = Fac < p2 => segment 1, m2 = Fac < p3.  With
    # e1 = m1, e2 = m2 * (1 - m1), e3 = (1 - m2) * (1 - m1) the weights sum to
    # exactly 1 (all factors are 0/1) and only one segment survives.
    mask1 = _less_than(tree, fac, positions[1], (1400.0, -2400.0), label="Fac < Stop 2")
    mask2 = _less_than(tree, fac, positions[2], (1400.0, -2575.0), label="Fac < Stop 3")
    inv_mask1 = _one_minus(tree, mask1, (1600.0, -2400.0), label="1 - (Fac < Stop 2)")
    inv_mask2 = _one_minus(tree, mask2, (1600.0, -2575.0), label="1 - (Fac < Stop 3)")
    weight2 = _multiply(tree, mask2, inv_mask1, (1800.0, -2450.0), label="Segment 2 weight")
    weight3 = _multiply(tree, inv_mask2, inv_mask1, (1800.0, -2600.0), label="Segment 3 weight")

    selected = _mix_color(tree, weight2, segments[0], segments[1], (2100.0, 0.0), label="Select segment 2")
    selected = _mix_color(tree, weight3, selected, segments[2], (2400.0, 0.0), label="Select segment 3")

    unpack = node(tree, "ShaderNodeSeparateColor", loc=(2650.0, 0.0), label="Ramp OKLab")
    link(tree, selected, unpack.inputs["Color"])
    color = _inverse_oklab(
        tree,
        (unpack.outputs["Red"], unpack.outputs["Green"], unpack.outputs["Blue"]),
        (2850.0, 0.0),
        label="Ramp",
    )
    link(tree, color, out_sock(tree, "Color"))

    _deselect(tree)

    log(
        f"GOO OKLab Color Ramp: {len(tree.nodes)} nodes, 4 fixed stops (native "
        "GOO node uses a ColorRamp widget), OKLab conversions once per stop, "
        "clamped segment factor, mask-selected segment, single inverse transform; "
        "no alpha output, no gamut clamp"
    )


__all__ = ["build_oklab_color_ramp", "build_oklab_mix"]
