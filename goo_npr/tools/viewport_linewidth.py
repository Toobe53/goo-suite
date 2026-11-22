# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO viewport line width overlay.

Creates two Scene properties and a ``SpaceView3D`` draw handler:

* ``scene.goo_viewport_linewidth_enabled`` (bool)
* ``scene.goo_viewport_line_width`` (float, 0.5 - 5.0, default 1.0)

The handler is registered on ``WINDOW`` / ``PRE_VIEW`` and applies
``gpu.state.line_width_set`` for the following viewport drawing.  Registering
the handler does not require a window, so this works in ``--background`` too;
the draw callback itself simply never runs without a viewport.
"""

from __future__ import annotations

import bpy

from .. import i18n
from ..i18n_strings import tr
from ..utils import log, safe_unregister_all

try:  # gpu is available in background builds as well
    import gpu
except Exception:  # pragma: no cover - defensive
    gpu = None

#: Scene property names (also referenced by the 3D View panel).
PROP_ENABLED = "goo_viewport_linewidth_enabled"
PROP_WIDTH = "goo_viewport_line_width"

DEFAULT_WIDTH = 1.0
MIN_WIDTH = 0.5
MAX_WIDTH = 5.0

_DRAW_HANDLE = None
_PROPS_ADDED = False


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
def _add_props():
    global _PROPS_ADDED
    scene_type = bpy.types.Scene
    if not hasattr(scene_type, PROP_ENABLED):
        setattr(
            scene_type,
            PROP_ENABLED,
            bpy.props.BoolProperty(
                name=tr("goo.prop.linewidth_enabled", "Custom Viewport Line Width"),
                description=tr(
                    "goo.prop.linewidth_enabled.desc",
                    "Draw viewport lines with the custom width below",
                ),
                default=False,
            ),
        )
        _PROPS_ADDED = True
    if not hasattr(scene_type, PROP_WIDTH):
        setattr(
            scene_type,
            PROP_WIDTH,
            bpy.props.FloatProperty(
                name=tr("goo.prop.linewidth", "Viewport Line Width"),
                description=tr(
                    "goo.prop.linewidth.desc",
                    "Line width used by the GOO viewport overlay (0.5 - 5.0)",
                ),
                default=DEFAULT_WIDTH,
                min=MIN_WIDTH,
                max=MAX_WIDTH,
                precision=2,
            ),
        )
        _PROPS_ADDED = True


def _remove_props():
    global _PROPS_ADDED
    if not _PROPS_ADDED:
        return
    scene_type = bpy.types.Scene
    for prop in (PROP_WIDTH, PROP_ENABLED):
        if hasattr(scene_type, prop):
            try:
                delattr(scene_type, prop)
            except Exception:  # pragma: no cover - defensive
                pass
    _PROPS_ADDED = False


# ---------------------------------------------------------------------------
# Draw handler
# ---------------------------------------------------------------------------
def _draw_viewport_line_width():
    """Apply the configured line width for the current viewport draw pass."""
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return
    if not getattr(scene, PROP_ENABLED, False):
        return
    width = float(getattr(scene, PROP_WIDTH, DEFAULT_WIDTH) or DEFAULT_WIDTH)
    if gpu is None:
        return
    try:
        gpu.state.line_width_set(width)
    except Exception:  # pragma: no cover - defensive (no GPU context)
        pass


def _ensure_draw_handler():
    global _DRAW_HANDLE
    if _DRAW_HANDLE is not None:
        return _DRAW_HANDLE
    try:
        _DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_viewport_line_width, (), "WINDOW", "PRE_VIEW"
        )
    except Exception as exc:  # pragma: no cover - defensive
        log(f"GOO: could not register viewport line width handler: {exc}")
        _DRAW_HANDLE = None
    return _DRAW_HANDLE


def _remove_draw_handler():
    global _DRAW_HANDLE
    if _DRAW_HANDLE is None:
        return
    try:
        bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
    except Exception:  # pragma: no cover - defensive
        pass
    _DRAW_HANDLE = None


def has_draw_handler() -> bool:
    return _DRAW_HANDLE is not None


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------
class GOO_OT_viewport_linewidth_toggle(bpy.types.Operator):
    """Enable or disable the custom GOO viewport line width.

    / 启用或关闭 GOO 自定义视图线宽。
    """

    bl_idname = "goo.viewport_linewidth_toggle"
    bl_label = tr(
        "goo.op.viewport_linewidth_toggle.label", "Toggle Custom Line Width"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return scene is not None and hasattr(scene, PROP_ENABLED)

    def execute(self, context):
        scene = getattr(context, "scene", None)
        if scene is None or not hasattr(scene, PROP_ENABLED):
            return {"CANCELLED"}
        enabled = not bool(getattr(scene, PROP_ENABLED, False))
        setattr(scene, PROP_ENABLED, enabled)
        width = float(getattr(scene, PROP_WIDTH, DEFAULT_WIDTH) or DEFAULT_WIDTH)
        if enabled:
            self.report(
                {"INFO"},
                i18n.t("goo.op.viewport_linewidth_toggle.on", width=width),
            )
        else:
            self.report(
                {"INFO"}, i18n.t("goo.op.viewport_linewidth_toggle.off")
            )
        log(f"GOO: viewport line width enabled={enabled} width={width:.2f}")
        return {"FINISHED"}


CLASSES = (GOO_OT_viewport_linewidth_toggle,)


def register():
    _add_props()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _ensure_draw_handler()


def unregister():
    _remove_draw_handler()
    safe_unregister_all(CLASSES)
    _remove_props()


__all__ = [
    "PROP_ENABLED",
    "PROP_WIDTH",
    "DEFAULT_WIDTH",
    "MIN_WIDTH",
    "MAX_WIDTH",
    "GOO_OT_viewport_linewidth_toggle",
    "has_draw_handler",
    "register",
    "unregister",
]
