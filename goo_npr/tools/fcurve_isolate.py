# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO "isolate selected F-Curves" tool.

Blender 5.2 uses layered Actions: ``action.fcurves`` is gone.  The curves of
an Action live in::

    action.layers[*].strips[*].channelbags[*].fcurves

Each channelbag targets one slot (``channelbag.slot.identifier``, e.g.
``"OBCube"``), so curves are keyed by ``(slot_identifier, data_path,
array_index)`` for the restore pass.

The isolation state is stored as a JSON string in the Scene custom property
``goo_fcurve_isolate_state`` (JSON keeps the state small and ID-property safe).
"""

from __future__ import annotations

import json

import bpy

from .. import i18n
from ..i18n_strings import tr
from ..utils import log, safe_unregister_all

#: Scene custom property holding the isolation snapshot (JSON string).
STATE_KEY = "goo_fcurve_isolate_state"

#: View types from which the operators may be invoked (3D panel + Graph Editor).
ALLOWED_AREAS = {"GRAPH_EDITOR", "VIEW_3D"}

_HEADER_APPENDED = False


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------
def _slot_identifier(channelbag) -> str:
    slot = getattr(channelbag, "slot", None)
    if slot is not None:
        identifier = getattr(slot, "identifier", "")
        if identifier:
            return identifier
    return ""


def iter_action_fcurves_with_slots(action):
    """Yield ``(slot_identifier, fcurve)`` for every F-Curve in ``action``.

    Supports the Blender 5.x layered Action layout and falls back to the
    legacy ``action.fcurves`` for older data if it is ever encountered.
    """
    if action is None:
        return
    layers = getattr(action, "layers", None)
    if layers is not None:
        for layer in layers:
            for strip in getattr(layer, "strips", ()) or ():
                for channelbag in getattr(strip, "channelbags", ()) or ():
                    slot = _slot_identifier(channelbag)
                    for fcurve in getattr(channelbag, "fcurves", ()) or ():
                        yield slot, fcurve
        return
    for fcurve in getattr(action, "fcurves", ()) or ():  # pragma: no cover - legacy
        yield "", fcurve


def iter_action_fcurves(action):
    """Yield every F-Curve of ``action`` regardless of layer/strip/channelbag."""
    for _slot, fcurve in iter_action_fcurves_with_slots(action):
        yield fcurve


def _active_action(context):
    obj = getattr(context, "object", None) or getattr(context, "active_object", None)
    animation_data = getattr(obj, "animation_data", None) if obj is not None else None
    return getattr(animation_data, "action", None), obj


def _area_allowed(context) -> bool:
    area = getattr(context, "area", None)
    if area is None:  # headless
        return True
    return area.type in ALLOWED_AREAS


def read_state(scene):
    """Parsed isolation state, or ``None``."""
    raw = scene.get(STATE_KEY) if scene is not None else None
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return state if isinstance(state, dict) else None


# ---------------------------------------------------------------------------
# Core operations (also used by the self test)
# ---------------------------------------------------------------------------
def isolate_action_fcurves(action, scene, *, object_name: str = "") -> dict:
    """Mute every curve except the selected ones and snapshot the old state.

    Returns a result dict with ``ok``/``reason`` plus ``count``, ``muted`` and
    ``stored`` on success.
    """
    if action is None:
        return {"ok": False, "reason": "no_action"}

    entries = list(iter_action_fcurves_with_slots(action))
    selected = [fcurve for _slot, fcurve in entries if getattr(fcurve, "select", False)]
    if not selected:
        return {"ok": False, "reason": "no_selection"}

    snapshot = []
    for slot, fcurve in entries:
        snapshot.append(
            [
                slot,
                fcurve.data_path,
                int(getattr(fcurve, "array_index", 0)),
                bool(getattr(fcurve, "mute", False)),
                bool(getattr(fcurve, "select", False)),
            ]
        )

    state = {
        "version": 1,
        "action": action.name,
        "object": object_name,
        "curves": snapshot,
    }
    try:
        scene[STATE_KEY] = json.dumps(state)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "reason": "store_failed", "error": str(exc)}

    selected_ids = {id(fcurve) for fcurve in selected}
    muted = 0
    for _slot, fcurve in entries:
        is_selected = id(fcurve) in selected_ids
        fcurve.mute = not is_selected
        fcurve.select = is_selected
        if not is_selected:
            muted += 1

    log(
        f"GOO: isolated {len(selected)} F-Curve(s) of action {action.name!r} "
        f"({muted} muted, {len(entries)} stored)"
    )
    return {
        "ok": True,
        "action": action.name,
        "count": len(selected),
        "muted": muted,
        "stored": len(entries),
    }


def restore_action_fcurves(scene, action=None) -> dict:
    """Restore mute/select flags from the snapshot and clear it."""
    state = read_state(scene)
    if state is None:
        return {"ok": False, "reason": "no_state"}

    action_name = state.get("action", "")
    if action is None:
        action = bpy.data.actions.get(action_name)
    if action is None:
        return {"ok": False, "reason": "unknown_action", "name": action_name}

    primary = {}
    fallback = {}
    for slot, fcurve in iter_action_fcurves_with_slots(action):
        key = (slot, fcurve.data_path, int(getattr(fcurve, "array_index", 0)))
        primary.setdefault(key, fcurve)
        fallback.setdefault((key[1], key[2]), fcurve)

    restored = 0
    missing = 0
    for entry in state.get("curves", ()):
        try:
            slot, data_path, array_index, mute, select = entry
        except (TypeError, ValueError):
            missing += 1
            continue
        key = (slot, data_path, int(array_index))
        fcurve = primary.get(key) or fallback.get((data_path, int(array_index)))
        if fcurve is None:
            missing += 1
            continue
        fcurve.mute = bool(mute)
        fcurve.select = bool(select)
        restored += 1

    try:
        del scene[STATE_KEY]
    except Exception:  # pragma: no cover - defensive
        pass

    log(
        f"GOO: restored {restored}/{len(state.get('curves', ()))} F-Curve(s) "
        f"of action {action.name!r}"
    )
    return {
        "ok": True,
        "action": action.name,
        "restored": restored,
        "stored": len(state.get("curves", ())),
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class GOO_OT_fcurve_isolate(bpy.types.Operator):
    """Isolate the selected F-Curves of the active Action.

    Every other F-Curve of the action is muted; the previous mute/select state
    is snapshotted in the Scene so the operation can be undone.  Running the
    operator again restores the snapshot. / 隔离活动动作中选中的 F 曲线：静音其余
    曲线并将原始状态保存到场景中；再次运行可恢复。
    """

    bl_idname = "goo.fcurve_isolate"
    bl_label = tr("goo.op.fcurve_isolate.label", "Isolate F-Curves")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _area_allowed(context):
            return False
        action, _obj = _active_action(context)
        if action is not None:
            return True
        scene = getattr(context, "scene", None)
        return scene is not None and read_state(scene) is not None

    def execute(self, context):
        scene = getattr(context, "scene", None)
        action, obj = _active_action(context)

        if scene is not None and read_state(scene) is not None:
            result = restore_action_fcurves(scene, action)
            if not result["ok"]:
                self.report(
                    {"ERROR"},
                    i18n.t("goo.op.fcurve_restore.unknown_action", name=result.get("name", "")),
                )
                return {"CANCELLED"}
            self.report(
                {"INFO"},
                i18n.t("goo.op.fcurve_isolate.restored", count=result["restored"]),
            )
            return {"FINISHED"}

        if action is None:
            self.report({"ERROR"}, i18n.t("goo.op.fcurve_isolate.no_action"))
            return {"CANCELLED"}

        result = isolate_action_fcurves(
            action, scene, object_name=getattr(obj, "name", "") if obj is not None else ""
        )
        if not result["ok"]:
            if result.get("reason") == "no_selection":
                self.report({"WARNING"}, i18n.t("goo.op.fcurve_isolate.no_selection"))
            else:
                self.report(
                    {"ERROR"},
                    result.get("error", i18n.t("goo.op.fcurve_isolate.no_action")),
                )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            i18n.t(
                "goo.op.fcurve_isolate.done",
                count=result["count"],
                muted=result["muted"],
            ),
        )
        return {"FINISHED"}


class GOO_OT_fcurve_restore(bpy.types.Operator):
    """Restore the F-Curves muted by *Isolate F-Curves*.

    / 恢复由“隔离 F 曲线”静音的 F 曲线。
    """

    bl_idname = "goo.fcurve_restore"
    bl_label = tr("goo.op.fcurve_restore.label", "Restore F-Curves")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _area_allowed(context):
            return False
        scene = getattr(context, "scene", None)
        return scene is not None and read_state(scene) is not None

    def execute(self, context):
        scene = getattr(context, "scene", None)
        if scene is None or read_state(scene) is None:
            self.report({"WARNING"}, i18n.t("goo.op.fcurve_restore.no_state"))
            return {"CANCELLED"}

        action, _obj = _active_action(context)
        result = restore_action_fcurves(scene, action)
        if not result["ok"]:
            if result.get("reason") == "unknown_action":
                self.report(
                    {"ERROR"},
                    i18n.t(
                        "goo.op.fcurve_restore.unknown_action",
                        name=result.get("name", ""),
                    ),
                )
            else:
                self.report({"WARNING"}, i18n.t("goo.op.fcurve_restore.no_state"))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            i18n.t(
                "goo.op.fcurve_restore.done",
                count=result["restored"],
                stored=result["stored"],
            ),
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Graph Editor header button
# ---------------------------------------------------------------------------
def _draw_graph_header(self, context):
    action, _obj = _active_action(context)
    if action is None:
        return
    layout = self.layout
    layout.separator()
    row = layout.row(align=True)
    row.operator(
        "goo.fcurve_isolate",
        text=i18n.t("goo.ui.graph_isolate"),
        icon="FILE_REFRESH",
    )
    row.operator(
        "goo.fcurve_restore",
        text=i18n.t("goo.ui.graph_restore"),
        icon="LOOP_BACK",
    )


CLASSES = (GOO_OT_fcurve_isolate, GOO_OT_fcurve_restore)


def register():
    global _HEADER_APPENDED
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    header = getattr(bpy.types, "GRAPH_HT_header", None)
    if header is not None and not _HEADER_APPENDED:
        header.append(_draw_graph_header)
        _HEADER_APPENDED = True


def unregister():
    global _HEADER_APPENDED
    header = getattr(bpy.types, "GRAPH_HT_header", None)
    if header is not None and _HEADER_APPENDED:
        header.remove(_draw_graph_header)
        _HEADER_APPENDED = False
    safe_unregister_all(CLASSES)


__all__ = [
    "STATE_KEY",
    "GOO_OT_fcurve_isolate",
    "GOO_OT_fcurve_restore",
    "isolate_action_fcurves",
    "iter_action_fcurves",
    "iter_action_fcurves_with_slots",
    "read_state",
    "register",
    "restore_action_fcurves",
    "unregister",
]
