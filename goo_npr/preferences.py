# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences for the GOO port."""

from __future__ import annotations

import bpy

from . import i18n
from .utils import safe_unregister_all

PREF_LANGUAGE = "language"
PREF_BUILD_ON_STARTUP = "build_on_startup"
PREF_STRICT_GROUPS = "strict_light_groups"
PREF_COLLECTION_PREFIX = "collection_prefix"
PREF_SHOW_NODE_LIBRARY = "show_node_library"
PREF_DEBUG = "debug"


class GOO_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    language: bpy.props.EnumProperty(
        name="Language",
        description="Language used by GOO panels and node labels. AUTO follows Blender's UI language",
        items=(
            ("AUTO", "Auto / 自动", "Follow Blender's UI language"),
            ("en_US", "English", "English UI"),
            ("zh_CN", "简体中文", "Chinese UI"),
        ),
        default="AUTO",
        update=lambda self, context: i18n.invalidate(),
    )

    build_on_startup: bpy.props.BoolProperty(
        name="Build node groups on startup",
        description="Create the GOO node groups when the add-on is enabled so they are immediately available in Add > GOO Engine",
        default=True,
    )

    strict_light_groups: bpy.props.BoolProperty(
        name="Strict light group linking",
        description=(
            "When enabled, objects whose materials link no GOO light group receive no light from lights "
            "that belong to a group. When disabled such objects receive every group (GOO's lenient default)"
        ),
        default=False,
    )

    collection_prefix: bpy.props.StringProperty(
        name="Receiver collection prefix",
        description="Prefix of the hidden collections used to drive Blender's native light linking",
        default="GOO_LG_",
    )

    show_node_library: bpy.props.BoolProperty(
        name="Show node library panel",
        description="Show the GOO node library in the node editor sidebar",
        default=True,
    )

    debug: bpy.props.BoolProperty(
        name="Debug logging",
        description="Print verbose diagnostics to the console",
        default=False,
    )

    def draw(self, context: bpy.types.Context) -> None:
        from . import ADDON_VERSION

        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "language")
        col.prop(self, "build_on_startup")
        col.prop(self, "show_node_library")
        col.separator()
        box = col.box()
        box.label(text="Light Groups / 灯光组")
        box.prop(self, "strict_light_groups")
        box.prop(self, "collection_prefix")
        box.separator()
        col.prop(self, "debug")
        col.label(text=f"GOO Port {'.'.join(str(v) for v in ADDON_VERSION)}", icon="INFO")
        col.label(text=i18n.t("goo.pref.lang_hint"), icon="INFO")


i18n.register(
    {
        "goo.pref.lang_hint": {
            "en_US": "Language also applies to node labels after a rebuild.",
            "zh_CN": "语言切换会在重建后应用到节点标签。",
        },
    }
)

CLASSES = (GOO_AddonPreferences,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    safe_unregister_all(CLASSES)


def get_prefs(context: bpy.types.Context | None = None) -> GOO_AddonPreferences | None:
    context = context or bpy.context
    try:
        addon = context.preferences.addons[__package__]
    except (AttributeError, KeyError):
        return None
    return addon.preferences
