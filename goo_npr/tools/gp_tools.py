# SPDX-License-Identifier: GPL-3.0-or-later
"""Grease Pencil (GP v3) helpers for the GOO port.

Blender 5.2 findings these operators rely on (empirically verified headless):

* GP data lives in ``bpy.data.grease_pencils``; the object type is
  ``GREASEPENCIL`` (the legacy ``GPENCIL`` type no longer exists).
* Layer visibility/selection are ``layer.hide`` / ``layer.select`` and the
  active layer is ``layers.active`` (there is no ``active_index``).
* ``layer.current_frame()`` returns a ``GreasePencilFrame``; the drawing is
  ``frame.drawing``.
* ``stroke.cyclic`` is a real, writable RNA property.  Writing it lazily
  creates the ``cyclic`` *CURVE BOOLEAN* attribute on the drawing.
* ``stroke.select`` is backed by the ``selection`` CURVE BOOLEAN attribute.
* ``drawing.attributes.new(name, type, domain)`` appends ``.001`` when the
  name already exists, so every accessor here calls ``attributes.get()``
  first.  Any attribute named ``goo_tg_random`` written here is a per-stroke
  FLOAT CURVE attribute readable from a material with an *Attribute* node.
"""

from __future__ import annotations

import random

import bpy

from .. import i18n
from ..i18n_strings import tr
from ..utils import log, safe_unregister_all

#: object type names treated as Grease Pencil (5.2 only uses GREASEPENCIL)
GP_OBJECT_TYPES = ("GREASEPENCIL", "GPENCIL")

#: per-stroke random value consumed by materials via an Attribute node
RANDOM_ATTRIBUTE = "goo_tg_random"

#: attribute created lazily by ``stroke.cyclic = True`` in Blender 5.2
CYCLIC_ATTRIBUTE = "cyclic"


# ---------------------------------------------------------------------------
# Data helpers (also used by the self test)
# ---------------------------------------------------------------------------
def is_gp_object(obj) -> bool:
    return obj is not None and getattr(obj, "type", "") in GP_OBJECT_TYPES


def active_gp_object(context):
    """Active Grease Pencil object, falling back to the selection."""
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    if is_gp_object(obj):
        return obj
    for candidate in getattr(context, "selected_objects", ()) or ():
        if is_gp_object(candidate):
            return candidate
    return None


def iter_layers(gp, *, visible_only: bool = True, selected_only: bool = False):
    """Yield Grease Pencil layers according to the visibility/selection filters."""
    for layer in getattr(gp, "layers", ()) or ():
        if visible_only and getattr(layer, "hide", False):
            continue
        if selected_only and not getattr(layer, "select", False):
            continue
        yield layer


def layer_drawing(layer, frame: int | None = None):
    """Return the drawing shown by ``layer`` at the current scene frame."""
    frame_struct = None
    current = getattr(layer, "current_frame", None)
    if callable(current):
        try:
            frame_struct = current()
        except Exception:  # pragma: no cover - defensive
            frame_struct = None
    if frame_struct is None and frame is not None:
        get_frame_at = getattr(layer, "get_frame_at", None)
        if callable(get_frame_at):
            try:
                frame_struct = get_frame_at(frame)
            except Exception:  # pragma: no cover - defensive
                frame_struct = None
    if frame_struct is None:
        return None
    return getattr(frame_struct, "drawing", None)


def ensure_curve_attribute(drawing, name: str, data_type: str):
    """Get (or create) a CURVE-domain attribute, avoiding ``.001`` clones."""
    attribute = drawing.attributes.get(name)
    if attribute is not None:
        if attribute.data_type == data_type and attribute.domain == "CURVE":
            return attribute
        # Same name but wrong type/domain: replace it so materials stay stable.
        try:
            drawing.attributes.remove(attribute)
        except Exception:  # pragma: no cover - defensive
            return None
    try:
        return drawing.attributes.new(name, data_type, "CURVE")
    except Exception as exc:  # pragma: no cover - defensive
        log(f"GOO: could not create GP attribute {name!r}: {exc}")
        return None


def _set_stroke_cyclic(drawing, stroke, index: int) -> bool:
    """Set ``stroke.cyclic``; fall back to the backing attribute if needed."""
    try:
        stroke.cyclic = True
        return True
    except Exception:  # pragma: no cover - legacy fallback
        attribute = ensure_curve_attribute(drawing, CYCLIC_ATTRIBUTE, "BOOLEAN")
        if attribute is None or index >= len(attribute.data):
            return False
        attribute.data[index].value = True
        return True


def close_strokes(drawing, threshold: float, *, only_selected: bool = True):
    """Close strokes whose endpoints are closer than ``threshold``.

    Returns ``(closed, examined)``.
    """
    closed = 0
    examined = 0
    for index, stroke in enumerate(drawing.strokes):
        if only_selected and not getattr(stroke, "select", False):
            continue
        points = getattr(stroke, "points", None)
        if points is None or len(points) < 2:
            continue
        examined += 1
        if getattr(stroke, "cyclic", False):
            continue
        start = points[0].position
        end = points[-1].position
        gap = (
            (start[0] - end[0]) ** 2
            + (start[1] - end[1]) ** 2
            + (start[2] - end[2]) ** 2
        ) ** 0.5
        if gap < threshold and _set_stroke_cyclic(drawing, stroke, index):
            closed += 1

    if closed:
        tag = getattr(drawing, "tag_positions_changed", None)
        if callable(tag):
            try:
                tag()
            except Exception:  # pragma: no cover - defensive
                pass
    return closed, examined


def randomize_strokes(drawing, *, only_selected: bool = False, seed: int = -1) -> int:
    """Write a random value in ``[0, 1)`` to ``goo_tg_random`` per stroke.

    Returns the number of strokes that received a value.
    """
    strokes = list(getattr(drawing, "strokes", ()) or ())
    indices = [
        index
        for index, stroke in enumerate(strokes)
        if not only_selected or getattr(stroke, "select", False)
    ]
    if not indices:
        return 0

    attribute = ensure_curve_attribute(drawing, RANDOM_ATTRIBUTE, "FLOAT")
    if attribute is None:
        raise RuntimeError(f"could not create the GP attribute {RANDOM_ATTRIBUTE!r}")

    count = min(len(attribute.data), len(strokes))
    rng = random.Random(seed) if seed is not None and seed >= 0 else random.Random()
    written = 0
    for index in indices:
        if index >= count:
            continue
        attribute.data[index].value = rng.random()
        written += 1
    return written


def smooth_strokes(
    drawing,
    *,
    factor: float = 0.5,
    iterations: int = 1,
    only_selected: bool = True,
) -> int:
    """Laplacian-smooth stroke point positions in place.

    Open strokes keep their two endpoints; cyclic strokes are smoothed across
    the seam.  Returns the number of strokes touched.
    """
    factor = max(0.0, min(1.0, float(factor)))
    iterations = max(1, int(iterations))
    if factor <= 0.0:
        return 0

    touched = 0
    for stroke in getattr(drawing, "strokes", ()) or ():
        if only_selected and not getattr(stroke, "select", False):
            continue
        points = getattr(stroke, "points", None)
        if points is None or len(points) < 3:
            continue
        cyclic = bool(getattr(stroke, "cyclic", False))
        coords = [tuple(point.position) for point in points]
        count = len(coords)
        for _ in range(iterations):
            previous = coords
            coords = list(previous)
            indices = range(count) if cyclic else range(1, count - 1)
            for index in indices:
                before = previous[(index - 1) % count]
                after = previous[(index + 1) % count]
                centre = (
                    (before[0] + after[0]) * 0.5,
                    (before[1] + after[1]) * 0.5,
                    (before[2] + after[2]) * 0.5,
                )
                coords[index] = tuple(
                    previous[index][axis] * (1.0 - factor) + centre[axis] * factor
                    for axis in range(3)
                )
        for point, coordinate in zip(points, coords):
            point.position = coordinate
        touched += 1

    if touched:
        tag = getattr(drawing, "tag_positions_changed", None)
        if callable(tag):
            try:
                tag()
            except Exception:  # pragma: no cover - defensive
                pass
    return touched


def update_gp_object(obj) -> None:
    tag = getattr(obj, "update_tag", None)
    if callable(tag):
        try:
            tag()
        except Exception:  # pragma: no cover - defensive
            pass


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class GOO_OT_gp_auto_close(bpy.types.Operator):
    """Close Grease Pencil strokes whose endpoints are closer than the threshold.

    Operates on the drawing at the current frame, on selected strokes by
    default, over the visible layers. / 自动闭合首尾端点间距小于阈值的蜡笔笔划，
    默认只处理选中笔划与可见图层当前帧的画稿。
    """

    bl_idname = "goo.gp_auto_close"
    bl_label = tr("goo.op.gp_auto_close.label", "Auto Close GP Strokes")
    bl_options = {"REGISTER", "UNDO"}

    threshold: bpy.props.FloatProperty(
        name=tr("goo.prop.threshold", "Gap Threshold"),
        description=tr(
            "goo.prop.threshold.desc",
            "Close a stroke when the distance between its first and last point is below this value (object units)",
        ),
        default=0.05,
        min=0.0,
        soft_max=1.0,
        precision=4,
    )
    only_selected: bpy.props.BoolProperty(
        name=tr("goo.prop.only_selected", "Selected Strokes Only"),
        description=tr(
            "goo.prop.only_selected.desc",
            "Only process strokes that are selected in the Grease Pencil object",
        ),
        default=True,
    )
    use_visible_layers: bpy.props.BoolProperty(
        name=tr("goo.prop.visible_layers", "Visible Layers Only"),
        description=tr(
            "goo.prop.visible_layers.desc", "Skip hidden Grease Pencil layers"
        ),
        default=True,
    )
    selected_layers_only: bpy.props.BoolProperty(
        name=tr("goo.prop.selected_layers_only", "Selected Layers Only"),
        description=tr(
            "goo.prop.selected_layers_only.desc",
            "Only process layers selected in the Grease Pencil data",
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return active_gp_object(context) is not None

    def execute(self, context):
        obj = active_gp_object(context)
        if obj is None:
            self.report({"ERROR"}, i18n.t("goo.op.gp_auto_close.no_gp"))
            return {"CANCELLED"}

        scene = getattr(context, "scene", None)
        frame = getattr(scene, "frame_current", 0) if scene is not None else 0
        layers = 0
        examined = 0
        closed = 0
        for layer in iter_layers(
            obj.data,
            visible_only=self.use_visible_layers,
            selected_only=self.selected_layers_only,
        ):
            drawing = layer_drawing(layer, frame)
            if drawing is None:
                continue
            layers += 1
            layer_closed, layer_examined = close_strokes(
                drawing, self.threshold, only_selected=self.only_selected
            )
            closed += layer_closed
            examined += layer_examined

        if layers == 0:
            self.report({"WARNING"}, i18n.t("goo.op.gp_auto_close.empty"))
            return {"CANCELLED"}

        update_gp_object(obj)
        if closed:
            self.report(
                {"INFO"},
                i18n.t(
                    "goo.op.gp_auto_close.done", count=closed, examined=examined
                ),
            )
        else:
            self.report(
                {"INFO"}, i18n.t("goo.op.gp_auto_close.none", examined=examined)
            )
        log(
            f"GOO: gp_auto_close on {obj.name!r}: closed {closed} / examined {examined} "
            f"on {layers} layer(s), threshold {self.threshold}"
        )
        return {"FINISHED"}


class GOO_OT_gp_random_texture(bpy.types.Operator):
    """Write a random value per stroke to the ``goo_tg_random`` attribute.

    Use an *Attribute* node named ``goo_tg_random`` in the stroke material to
    randomise the texture per stroke (GOO's per-stroke texture randomisation).
    / 为每条笔划写入 ``goo_tg_random`` 随机属性；在笔划材质中用名为
    ``goo_tg_random`` 的属性节点即可按笔划随机化纹理。
    """

    bl_idname = "goo.gp_random_texture"
    bl_label = tr("goo.op.gp_random_texture.label", "Randomize GP Stroke Texture")
    bl_options = {"REGISTER", "UNDO"}

    only_selected: bpy.props.BoolProperty(
        name=tr("goo.prop.only_selected", "Selected Strokes Only"),
        description=tr(
            "goo.prop.only_selected.desc",
            "Only process strokes that are selected in the Grease Pencil object",
        ),
        default=False,
    )
    seed: bpy.props.IntProperty(
        name=tr("goo.prop.seed", "Random Seed"),
        description=tr(
            "goo.prop.seed.desc", "Random seed; -1 uses a fresh random sequence"
        ),
        default=-1,
        min=-1,
    )
    use_visible_layers: bpy.props.BoolProperty(
        name=tr("goo.prop.visible_layers", "Visible Layers Only"),
        description=tr(
            "goo.prop.visible_layers.desc", "Skip hidden Grease Pencil layers"
        ),
        default=True,
    )
    selected_layers_only: bpy.props.BoolProperty(
        name=tr("goo.prop.selected_layers_only", "Selected Layers Only"),
        description=tr(
            "goo.prop.selected_layers_only.desc",
            "Only process layers selected in the Grease Pencil data",
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return active_gp_object(context) is not None

    def execute(self, context):
        obj = active_gp_object(context)
        if obj is None:
            self.report({"ERROR"}, i18n.t("goo.op.gp_random_texture.no_gp"))
            return {"CANCELLED"}

        scene = getattr(context, "scene", None)
        frame = getattr(scene, "frame_current", 0) if scene is not None else 0
        written = 0
        layers = 0
        for layer in iter_layers(
            obj.data,
            visible_only=self.use_visible_layers,
            selected_only=self.selected_layers_only,
        ):
            drawing = layer_drawing(layer, frame)
            if drawing is None:
                continue
            layers += 1
            try:
                written += randomize_strokes(
                    drawing, only_selected=self.only_selected, seed=self.seed
                )
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                log(f"GOO: gp_random_texture failed on layer {layer.name!r}: {exc}")
                return {"CANCELLED"}

        if written == 0:
            self.report({"WARNING"}, i18n.t("goo.op.gp_random_texture.none"))
            return {"CANCELLED"}

        update_gp_object(obj)
        self.report(
            {"INFO"},
            i18n.t(
                "goo.op.gp_random_texture.done",
                attribute=RANDOM_ATTRIBUTE,
                count=written,
            ),
        )
        log(
            f"GOO: gp_random_texture on {obj.name!r}: wrote {RANDOM_ATTRIBUTE!r} "
            f"to {written} stroke(s) on {layers} layer(s)"
        )
        return {"FINISHED"}


class GOO_OT_gp_smooth_strokes(bpy.types.Operator):
    """Smooth the points of Grease Pencil strokes (Laplacian).

    This is the portable counterpart of GOO's context-based sculpt smoothing:
    it runs on the current drawing instead of being baked into the sculpt
    brush.  / 对蜡笔笔划的顶点做拉普拉斯平滑；这是 GOO 雕刻模式平滑的可移植
    版本，以操作符在当前画稿上运行，而不是集成进雕刻笔刷。
    """

    bl_idname = "goo.gp_smooth_strokes"
    bl_label = tr("goo.op.gp_smooth.label", "Smooth GP Strokes")
    bl_options = {"REGISTER", "UNDO"}

    factor: bpy.props.FloatProperty(
        name=tr("goo.prop.factor", "Smoothing Factor"),
        description=tr(
            "goo.prop.factor.desc",
            "Weight of the neighbour average applied to every interior point",
        ),
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    iterations: bpy.props.IntProperty(
        name=tr("goo.prop.iterations", "Iterations"),
        description=tr(
            "goo.prop.iterations.desc", "Number of smoothing passes over the strokes"
        ),
        default=1,
        min=1,
        max=20,
    )
    only_selected: bpy.props.BoolProperty(
        name=tr("goo.prop.only_selected", "Selected Strokes Only"),
        description=tr(
            "goo.prop.only_selected.desc",
            "Only process strokes that are selected in the Grease Pencil object",
        ),
        default=True,
    )
    use_visible_layers: bpy.props.BoolProperty(
        name=tr("goo.prop.visible_layers", "Visible Layers Only"),
        description=tr(
            "goo.prop.visible_layers.desc", "Skip hidden Grease Pencil layers"
        ),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return active_gp_object(context) is not None

    def execute(self, context):
        obj = active_gp_object(context)
        if obj is None:
            self.report({"ERROR"}, i18n.t("goo.op.gp_smooth.no_gp"))
            return {"CANCELLED"}

        scene = getattr(context, "scene", None)
        frame = getattr(scene, "frame_current", 0) if scene is not None else 0
        touched = 0
        layers = 0
        for layer in iter_layers(obj.data, visible_only=self.use_visible_layers):
            drawing = layer_drawing(layer, frame)
            if drawing is None:
                continue
            layers += 1
            touched += smooth_strokes(
                drawing,
                factor=self.factor,
                iterations=self.iterations,
                only_selected=self.only_selected,
            )

        if touched == 0:
            self.report({"WARNING"}, i18n.t("goo.op.gp_smooth.none"))
            return {"CANCELLED"}

        update_gp_object(obj)
        self.report(
            {"INFO"},
            i18n.t("goo.op.gp_smooth.done", count=touched, iterations=self.iterations),
        )
        log(
            f"GOO: gp_smooth_strokes on {obj.name!r}: {touched} stroke(s) on "
            f"{layers} layer(s), factor {self.factor}, {self.iterations} pass(es)"
        )
        return {"FINISHED"}


CLASSES = (
    GOO_OT_gp_auto_close,
    GOO_OT_gp_random_texture,
    GOO_OT_gp_smooth_strokes,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    safe_unregister_all(CLASSES)


__all__ = [
    "GP_OBJECT_TYPES",
    "RANDOM_ATTRIBUTE",
    "CYCLIC_ATTRIBUTE",
    "GOO_OT_gp_auto_close",
    "GOO_OT_gp_random_texture",
    "GOO_OT_gp_smooth_strokes",
    "active_gp_object",
    "close_strokes",
    "ensure_curve_attribute",
    "iter_layers",
    "layer_drawing",
    "randomize_strokes",
    "smooth_strokes",
    "register",
    "unregister",
]
