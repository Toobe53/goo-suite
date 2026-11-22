# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels for the GOO port.

* *Node Editor > GOO* — the node library grouped by registry category.
* *3D View > GOO Tools* — Grease Pencil helpers, F-Curve isolation and the
  viewport line width controls.

All user facing text goes through the add-on i18n helper.
"""

from __future__ import annotations

import bpy

from .. import i18n
from ..add_menu import SUPPORTED_NODE_TREE_TYPES, category_label
from ..i18n_strings import tr
from ..nodes import registry
from ..preferences import get_prefs
from ..tools.fcurve_isolate import STATE_KEY as FCURVE_STATE_KEY
from ..utils import log, safe_unregister_all

#: Grease Pencil object type names across Blender versions (5.2 = GREASEPENCIL).
GP_OBJECT_TYPES = ("GREASEPENCIL", "GPENCIL")

#: Scene property created by ``goo_npr.tools.viewport_linewidth``.
LINEWIDTH_ENABLED_PROP = "goo_viewport_linewidth_enabled"
LINEWIDTH_VALUE_PROP = "goo_viewport_line_width"


def _language_label() -> str:
    if i18n.current_language() == "zh_CN":
        return tr("goo.ui.language_name_zh", "简体中文")
    return tr("goo.ui.language_name_en", "English")


def _active_object(context):
    return getattr(context, "active_object", None) or getattr(context, "object", None)


class GOO_PT_node_library(bpy.types.Panel):
    """GOO node library in the node editor sidebar."""

    bl_idname = "GOO_PT_node_library"
    bl_label = tr("goo.ui.node_library", "GOO Node Library")
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "GOO"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        if space is None or getattr(space, "type", "") != "NODE_EDITOR":
            return False
        if getattr(space, "tree_type", "") not in SUPPORTED_NODE_TREE_TYPES:
            return False
        prefs = get_prefs(context)
        if prefs is not None and not prefs.show_node_library:
            return False
        return True

    def draw(self, context):
        layout = self.layout
        space = getattr(context, "space_data", None)
        tree_type = getattr(space, "tree_type", "") if space is not None else ""

        summary = registry.summary()
        all_count = len(summary)
        specs = registry.specs_for_tree(tree_type) if tree_type else []

        row = layout.row(align=True)
        row.operator(
            "goo.build_library",
            text=i18n.t("goo.ui.rebuild"),
            icon="FILE_REFRESH",
        )
        layout.label(
            text=i18n.t(
                "goo.ui.node_library_count",
                count=len(specs),
                total=all_count,
            ),
            icon="NODETREE",
        )

        if not specs:
            layout.label(text=i18n.t("goo.ui.node_library_empty"), icon="INFO")
        else:
            for category in registry.CATEGORIES:
                category_specs = [spec for spec in specs if spec.category == category]
                if not category_specs:
                    continue
                box = layout.box()
                box.label(text=category_label(category))
                column = box.column(align=True)
                for spec in category_specs:
                    built = bpy.data.node_groups.get(spec.name) is not None
                    op = column.operator(
                        "goo.add_node_group",
                        text=spec.label_text(),
                        icon="CHECKMARK" if built else "NODETREE",
                    )
                    op.group_name = spec.name

        layout.separator()
        layout.label(
            text=i18n.t("goo.ui.language_hint", language=_language_label()),
            icon="INFO",
        )


class GOO_PT_tools_panel(bpy.types.Panel):
    """GOO tools in the 3D Viewport sidebar."""

    bl_idname = "GOO_PT_tools_panel"
    bl_label = tr("goo.ui.tools", "GOO Tools")
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GOO"

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = _active_object(context)

        if obj is None:
            layout.label(text=i18n.t("goo.ui.active_object_none"), icon="INFO")
        else:
            layout.label(
                text=i18n.t(
                    "goo.ui.active_object",
                    name=obj.name,
                    type=getattr(obj, "type", "?"),
                )
            )

        # -- Grease Pencil ------------------------------------------------
        gp_box = layout.box()
        gp_box.label(text=i18n.t("goo.ui.section_gp"))
        gp_column = gp_box.column(align=True)
        gp_column.operator(
            "goo.gp_auto_close",
            text=i18n.t("goo.ui.gp_auto_close"),
        )
        gp_column.operator(
            "goo.gp_random_texture",
            text=i18n.t("goo.ui.gp_random_texture"),
        )
        gp_column.operator(
            "goo.gp_smooth_strokes",
            text=i18n.t("goo.ui.gp_smooth"),
        )
        if obj is None or getattr(obj, "type", "") not in GP_OBJECT_TYPES:
            gp_column.label(text=i18n.t("goo.ui.no_gp"), icon="INFO")

        # -- F-Curves -----------------------------------------------------
        curve_box = layout.box()
        curve_box.label(text=i18n.t("goo.ui.section_fcurve"))
        curve_column = curve_box.column(align=True)
        curve_column.operator(
            "goo.fcurve_isolate",
            text=i18n.t("goo.ui.fcurve_isolate"),
        )
        curve_column.operator(
            "goo.fcurve_restore",
            text=i18n.t("goo.ui.fcurve_restore"),
        )
        animation_data = getattr(obj, "animation_data", None) if obj is not None else None
        action = getattr(animation_data, "action", None)
        if action is None:
            curve_column.label(text=i18n.t("goo.ui.no_action"), icon="INFO")
        elif scene.get(FCURVE_STATE_KEY):
            curve_column.label(text=i18n.t("goo.ui.fcurve_is_active"), icon="FILE_REFRESH")
        else:
            curve_column.label(text=i18n.t("goo.ui.fcurve_hint"), icon="INFO")

        # -- Viewport line width -----------------------------------------
        view_box = layout.box()
        view_box.label(text=i18n.t("goo.ui.section_viewport"))
        if hasattr(scene, LINEWIDTH_ENABLED_PROP):
            view_box.prop(
                scene,
                LINEWIDTH_ENABLED_PROP,
                text=i18n.t("goo.ui.linewidth_enabled"),
            )
            if hasattr(scene, LINEWIDTH_VALUE_PROP):
                view_box.prop(
                    scene,
                    LINEWIDTH_VALUE_PROP,
                    text=i18n.t("goo.ui.linewidth_value"),
                )
            view_box.operator(
                "goo.viewport_linewidth_toggle",
                text=i18n.t("goo.ui.linewidth_toggle"),
            )
        else:
            view_box.label(text=i18n.t("goo.ui.linewidth_unavailable"), icon="INFO")


CLASSES = (GOO_PT_node_library, GOO_PT_tools_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    log("GOO: panels registered")


def unregister():
    safe_unregister_all(CLASSES)


__all__ = [
    "GOO_PT_node_library",
    "GOO_PT_tools_panel",
    "CLASSES",
    "register",
    "unregister",
]
