# SPDX-License-Identifier: GPL-3.0-or-later
"""Pose/bone helpers matching GOO Engine's added rigging tools.

GOO 4.4 adds four small rigging conveniences that live in C++ there:

* ``V3D_OVERLAY_ISOLATE_BONES`` — isolate the selected bones while moving them;
* ``PCHAN_DRAW_SHOW_OUTLINER`` / ``PoseBone.hide_outliner`` — hide individual
  pose bones from the Outliner;
* ``ROTLIKE_INVERT_ALL`` — one "Invert All" flag on Copy Rotation;
* ``POSE_OT_constraints_merge`` — copy a source armature's pose constraints to
  the selected bones, re-linking targets to the destination rig.

Python cannot add view-layer flags or per-bone drawflags to the C++ structs,
so this module provides the closest behaviour that *is* reachable through public
5.2 APIs.  ``PoseBone.hide_outliner`` is re-created as an add-on RNA property with
the same name and semantics as GOO's flag; the GOO 4.4 public branch stores the
flag and shows the UI but has no Outliner consumer of it either, so the data model
matches exactly even though 5.2 cannot filter Outliner rows.  Each operator
documents its deviation in the docstring shown in the UI tooltip.

Add-on-defined RNA properties survive only while the add-on is enabled, so
persistent hints that must outlive a disabled add-on are stored in ID properties
instead.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, StringProperty

from ..i18n_strings import tr
from ..utils import log, safe_unregister_all

#: ID-property holding the list of bones hidden by the isolate toggle.
ISOLATE_BACKUP_PROP = "goo_isolate_backup"

#: ID-property (on the pose bone) backing the re-created ``hide_outliner`` flag.
OUTLINER_FLAG_PROP = "goo_hide_outliner"

#: ID-property (on the armature object) holding the merge source name.
MERGE_SOURCE_PROP = "goo_merge_source"

#: Constraint types treated as "rot-like" for the Invert All helper.
ROTATION_CONSTRAINTS = {"COPY_ROTATION"}

#: Pointer RNA properties that must not be copied verbatim between constraints.
_SKIP_POINTERS = {"target", "space_object"}


def _pose_armature(context):
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    if obj is None or getattr(obj, "type", "") != "ARMATURE":
        return None
    if getattr(obj, "mode", "") != "POSE":
        return None
    return obj


def selected_pose_bones(obj):
    """Selected pose bones of ``obj`` (``PoseBone.select`` in Pose Mode)."""
    return [pb for pb in obj.pose.bones if getattr(pb, "select", False)]


def _get_hide_outliner(pose_bone) -> bool:
    return bool(pose_bone.get(OUTLINER_FLAG_PROP, False))


def _set_hide_outliner(pose_bone, value: bool) -> None:
    pose_bone[OUTLINER_FLAG_PROP] = bool(value)


def set_invert_all(pose_bone, value: bool) -> int:
    """Set the per-axis inversions of every Copy Rotation constraint.

    GOO stores one ``ROTLIKE_INVERT_ALL`` bit; 5.2 exposes only the three
    per-axis booleans, so setting all three is the exact visual equivalent.
    Returns the number of constraints touched.
    """
    touched = 0
    for con in pose_bone.constraints:
        if con.type in ROTATION_CONSTRAINTS:
            con.invert_x = value
            con.invert_y = value
            con.invert_z = value
            touched += 1
    return touched


def _remap_pointer(value, src_obj, dst_obj):
    """Map IDs owned by the source rig onto the destination rig."""
    if value is None:
        return None
    if value == src_obj:
        return dst_obj
    if value == src_obj.data:
        return dst_obj.data
    return value


def copy_constraint(src_con, dst_pose_bone, src_obj, dst_obj):
    """Copy one constraint into ``dst_pose_bone`` with targets re-linked.

    Blender 5.2 has no ``Constraint.copy()``.  Instead the RNA property table is
    walked and every writable scalar/string/enum/pointer property is assigned.
    Pointers that reference the source rig (``target``, ``space_object`` or an
    armature ID) are remapped to the destination rig, mirroring GOO's
    ``con_relink_id_cb``.
    """
    dst_con = dst_pose_bone.constraints.new(src_con.type)
    for prop in src_con.bl_rna.properties:
        ident = prop.identifier
        if ident in {"rna_type", "type", "is_valid", "error_location", "error_rotation"}:
            continue
        if prop.type == "COLLECTION":
            continue
        try:
            value = getattr(src_con, ident)
        except Exception:
            continue
        if prop.type == "POINTER":
            try:
                setattr(dst_con, ident, _remap_pointer(value, src_obj, dst_obj))
            except Exception:
                pass
            continue
        try:
            setattr(dst_con, ident, value)
        except Exception:
            # Read-only or type-specific properties (e.g. legacy flags) are fine
            # to skip; the public ones needed for rigging all copy.
            pass
    return dst_con


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
class GOO_OT_pose_isolate_bones(bpy.types.Operator):
    """Isolate selected bones in the viewport (toggle)"""

    bl_idname = "goo.pose_isolate_bones"
    bl_label = tr("goo.pose.isolate", "Isolate Bones")
    bl_description = tr(
        "goo.pose.isolate.desc",
        "Hide unselected bones; run again to restore. GOO keeps the overlay flag "
        "in C++, this port toggles real bone visibility instead",
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _pose_armature(context) is not None

    def execute(self, context):
        obj = _pose_armature(context)
        if obj is None:
            self.report({"ERROR"}, "No armature in Pose Mode")
            return {"CANCELLED"}

        backup = obj.get(ISOLATE_BACKUP_PROP)
        if backup:
            for name in list(backup):
                bone = obj.data.bones.get(str(name))
                if bone is not None:
                    bone.hide = False
            del obj[ISOLATE_BACKUP_PROP]
            log(f"pose isolate: restored {len(list(backup))} bones")
            self.report({"INFO"}, f"Restored {len(list(backup))} bones")
            return {"FINISHED"}

        selected = {pb.name for pb in selected_pose_bones(obj)}
        hidden = []
        for bone in obj.data.bones:
            if bone.name in selected or bone.hide:
                continue
            bone.hide = True
            hidden.append(bone.name)
        obj[ISOLATE_BACKUP_PROP] = hidden
        log(f"pose isolate: hid {len(hidden)} bones")
        self.report({"INFO"}, f"Isolated {len(selected)} bone(s), hid {len(hidden)}")
        return {"FINISHED"}


class GOO_OT_copy_rotation_invert_all(bpy.types.Operator):
    """Set or clear Invert All on selected bones' Copy Rotation constraints"""

    bl_idname = "goo.copy_rotation_invert_all"
    bl_label = tr("goo.pose.invert_all", "Copy Rotation: Invert All")
    bl_description = tr(
        "goo.pose.invert_all.desc",
        "GOO adds a single Invert All flag to Copy Rotation; this sets the three "
        "per-axis invert booleans on the selected bones",
    )
    bl_options = {"REGISTER", "UNDO"}

    value: BoolProperty(name="Invert", default=True)

    @classmethod
    def poll(cls, context):
        return _pose_armature(context) is not None

    def execute(self, context):
        obj = _pose_armature(context)
        if obj is None:
            self.report({"ERROR"}, "No armature in Pose Mode")
            return {"CANCELLED"}
        touched = 0
        for pb in selected_pose_bones(obj):
            touched += set_invert_all(pb, bool(self.value))
        if not touched:
            self.report({"WARNING"}, "No Copy Rotation constraints on selected bones")
        else:
            self.report({"INFO"}, f"Updated {touched} constraint(s)")
        return {"FINISHED"}


class GOO_OT_pose_outliner_flag(bpy.types.Operator):
    """Set GOO's "Hide in Outliner" flag on selected pose bones"""

    bl_idname = "goo.pose_outliner_flag"
    bl_label = tr("goo.pose.outliner_flag", "Hide in Outliner Flag")
    bl_description = tr(
        "goo.pose.outliner_flag.desc",
        "Set GOO's PoseBone.hide_outliner flag on the selected bones. GOO 4.4 stores "
        "the flag and exposes the UI but its public branch has no Outliner filter "
        "consumer; Blender 5.2 has no row-filter hook either, so the flag is kept as "
        "metadata and shown in the GOO Pose panel",
    )
    bl_options = {"REGISTER", "UNDO"}

    value: BoolProperty(name="Hide in Outliner", default=True)

    @classmethod
    def poll(cls, context):
        return _pose_armature(context) is not None

    def execute(self, context):
        obj = _pose_armature(context)
        if obj is None:
            self.report({"ERROR"}, "No armature in Pose Mode")
            return {"CANCELLED"}
        selected = selected_pose_bones(obj)
        if not selected:
            self.report({"ERROR"}, "Select at least one pose bone")
            return {"CANCELLED"}
        for pb in selected:
            pb.hide_outliner = bool(self.value)
        context.view_layer.update()
        log(f"pose outliner flag: {len(selected)} bones -> {bool(self.value)}")
        self.report(
            {"INFO"},
            f"{'Hid' if self.value else 'Showed'} {len(selected)} bone(s) in the Outliner flag",
        )
        return {"FINISHED"}


class GOO_OT_pose_constraints_merge(bpy.types.Operator):
    """Copy a source armature's pose constraints to the selected bones"""

    bl_idname = "goo.pose_constraints_merge"
    bl_label = tr("goo.pose.merge", "Merge Bone Constraints")
    bl_description = tr(
        "goo.pose.merge.desc",
        "Copy pose constraints from another armature (matching bone names) onto "
        "the selected bones and re-link targets to this rig, like GOO's "
        "POSE_OT_constraints_merge",
    )
    bl_options = {"REGISTER", "UNDO"}

    src_object_name: StringProperty(
        name="Source",
        description="Armature object to copy constraints from",
        default="",
    )

    @classmethod
    def poll(cls, context):
        return _pose_armature(context) is not None

    def invoke(self, context, event):
        obj = _pose_armature(context)
        if obj is not None and not self.src_object_name:
            self.src_object_name = str(obj.get(MERGE_SOURCE_PROP, ""))
        if self.src_object_name:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        dst_obj = _pose_armature(context)
        if dst_obj is None:
            self.report({"ERROR"}, "No armature in Pose Mode")
            return {"CANCELLED"}

        src_name = self.src_object_name or str(dst_obj.get(MERGE_SOURCE_PROP, ""))
        src_obj = bpy.data.objects.get(src_name) if src_name else None
        if src_obj is None or getattr(src_obj, "type", "") != "ARMATURE":
            self.report({"ERROR"}, "Choose a source armature")
            return {"CANCELLED"}
        if src_obj == dst_obj:
            self.report({"ERROR"}, "Source and destination are the same armature")
            return {"CANCELLED"}

        selected = selected_pose_bones(dst_obj)
        if not selected:
            self.report({"ERROR"}, "Select at least one pose bone")
            return {"CANCELLED"}

        copied = 0
        matched_bones = 0
        for pb in selected:
            src_pb = src_obj.pose.bones.get(pb.name)
            if src_pb is None:
                continue
            matched_bones += 1
            for con in list(pb.constraints):
                pb.constraints.remove(con)
            for src_con in src_pb.constraints:
                copy_constraint(src_con, pb, src_obj, dst_obj)
                copied += 1

        if not matched_bones:
            self.report({"WARNING"}, "No matching bone names in the source rig")
            return {"CANCELLED"}

        dst_obj[MERGE_SOURCE_PROP] = src_obj.name
        dst_obj.update_tag()
        context.view_layer.update()
        log(
            f"pose constraints merge: {copied} from {src_obj.name} -> "
            f"{dst_obj.name} ({matched_bones} bones)"
        )
        self.report(
            {"INFO"},
            f"Copied {copied} constraint(s) from {src_obj.name} to {matched_bones} bone(s)",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------
class GOO_PT_pose_tools(bpy.types.Panel):
    """GOO rigging helpers in the 3D View sidebar."""

    bl_idname = "GOO_PT_pose_tools"
    bl_label = tr("goo.pose.panel", "GOO Pose Tools")
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GOO Pose"

    @classmethod
    def poll(cls, context):
        return _pose_armature(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = _pose_armature(context)
        selected = len(selected_pose_bones(obj)) if obj is not None else 0

        col = layout.column(align=True)
        col.label(text=f"Selected bones: {selected}")
        col.operator(
            "goo.pose_isolate_bones",
            text=tr("goo.pose.isolate.button", "Isolate / Restore Bones"),
            depress=bool(obj and obj.get(ISOLATE_BACKUP_PROP)),
        )

        box = layout.box()
        box.label(text=tr("goo.pose.outliner_box", "Outliner Flag"))
        active = getattr(context, "active_pose_bone", None)
        if active is not None:
            box.prop(
                active,
                "hide_outliner",
                text=tr("goo.pose.outliner_flag", "Hide in Outliner"),
            )
        row = box.row(align=True)
        row.operator("goo.pose_outliner_flag", text=tr("goo.pose.outliner_hide", "Hide")).value = True
        row.operator("goo.pose_outliner_flag", text=tr("goo.pose.outliner_show", "Show")).value = False

        box = layout.box()
        box.label(text=tr("goo.pose.copy_rotation", "Copy Rotation"))
        row = box.row(align=True)
        row.operator("goo.copy_rotation_invert_all", text="Invert All").value = True
        row.operator("goo.copy_rotation_invert_all", text="Clear Invert").value = False

        box = layout.box()
        box.label(text=tr("goo.pose.merge_box", "Merge Constraints"))
        if obj is not None:
            box.prop(obj, "goo_merge_source", text="Source")
        box.operator("goo.pose_constraints_merge", text=tr("goo.pose.merge.button", "Merge from Source"))


CLASSES = (
    GOO_OT_pose_isolate_bones,
    GOO_OT_copy_rotation_invert_all,
    GOO_OT_pose_outliner_flag,
    GOO_OT_pose_constraints_merge,
    GOO_PT_pose_tools,
)

_PROP_OWNERS: list[tuple[type, str]] = []


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    obj_type = bpy.types.Object
    if not hasattr(obj_type, "goo_merge_source"):
        obj_type.goo_merge_source = StringProperty(
            name=tr("goo.pose.merge_source", "Merge Source"),
            description="Armature object to copy pose constraints from",
            default="",
        )
        _PROP_OWNERS.append((obj_type, "goo_merge_source"))
    pb_type = bpy.types.PoseBone
    if not hasattr(pb_type, "hide_outliner"):
        pb_type.hide_outliner = BoolProperty(
            name=tr("goo.pose.outliner_flag", "Hide in Outliner"),
            description=(
                "GOO's PoseBone.hide_outliner flag (PCHAN_DRAW_SHOW_OUTLINER); "
                "stored as an ID property because Blender 5.2 has no Outliner row "
                "filter hook"
            ),
            get=_get_hide_outliner,
            set=_set_hide_outliner,
        )
        _PROP_OWNERS.append((pb_type, "hide_outliner"))


def unregister() -> None:
    for owner, prop_name in reversed(_PROP_OWNERS):
        try:
            delattr(owner, prop_name)
        except Exception:
            pass
    _PROP_OWNERS.clear()
    safe_unregister_all(CLASSES)
