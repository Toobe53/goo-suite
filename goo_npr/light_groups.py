# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO style Light Groups for Blender 5.2.

GOO Engine lets an artist group lights by name (``key``, ``fill``, ``rim`` …)
and then link *materials* to those groups, with per-group shadow toggles.  A
material can be lit by several groups and every group can be tuned in isolation.

Blender 5.2 cannot evaluate a light group mask inside an EEVEE shader, but it
ships native *light linking*: every light object has
``light.light_linking.receiver_collection`` and only objects inside that
collection are lit by the light.  This module builds the bridge:

* a scene level table of light groups (:class:`GOO_LightGroup`),
* the groups a material / object is linked to,
* for every light group a hidden (not linked into the scene) collection that
  contains exactly the receiving objects, assigned to the group's lights,
* a pass collector used by the external ``goo`` CLI to split a render into one
  beauty pass per light group and composite them additively.

The collections are intentionally detached from the scene: they only drive
light linking and never show up in the viewport or the outliner's scene tree.
"""

from __future__ import annotations

import re

import bpy

from . import i18n
from .preferences import get_prefs
from .utils import (
    GOO_ID,
    ensure_collection,
    iter_objects,
    log,
    object_materials,
    now_stamp,
    safe_unregister_all,
)

# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------

i18n.register(
    {
        "goo.lg.panel": {"en_US": "GOO Light Groups", "zh_CN": "GOO 灯光组"},
        "goo.lg.add": {"en_US": "Add Group", "zh_CN": "添加组"},
        "goo.lg.remove": {"en_US": "Remove Group", "zh_CN": "删除组"},
        "goo.lg.sync": {"en_US": "Sync Light Linking", "zh_CN": "同步灯光链接"},
        "goo.lg.name": {"en_US": "Group", "zh_CN": "组"},
        "goo.lg.shadows": {"en_US": "Cast Shadows", "zh_CN": "投射阴影"},
        "goo.lg.receivers": {"en_US": "Receivers", "zh_CN": "接收者"},
        "goo.lg.empty": {
            "en_US": "No light groups yet. Add one and assign lights to it.",
            "zh_CN": "尚无灯光组。请添加组并给灯光指定组。",
        },
        "goo.lg.help": {
            "en_US": "Lights whose group is empty light every object. Objects with no linked material receive every group (unless strict linking is enabled in the add-on preferences).",
            "zh_CN": "未指定组的灯光照亮所有物体。没有链接任何组的材质会接收所有组（除非在插件偏好中启用严格链接）。",
        },
        "goo.lg.strict": {"en_US": "Strict linking is ON", "zh_CN": "严格链接已开启"},
        "goo.lg.lenient": {"en_US": "Lenient linking (unlinked materials receive all groups)", "zh_CN": "宽松链接（未链接材质接收所有组）"},
        "goo.lg.auto": {"en_US": "Sync before render", "zh_CN": "渲染前自动同步"},
        "goo.lg.no_scene": {"en_US": "No active scene", "zh_CN": "没有活动场景"},
        "goo.lg.material_panel": {"en_US": "GOO Light Groups", "zh_CN": "GOO 灯光组"},
        "goo.lg.object_panel": {"en_US": "GOO Light Groups", "zh_CN": "GOO 灯光组"},
        "goo.lg.light_panel": {"en_US": "GOO Light Group", "zh_CN": "GOO 灯光组"},
        "goo.lg.assign": {"en_US": "Assign", "zh_CN": "指定"},
        "goo.lg.unassign": {"en_US": "Clear Links", "zh_CN": "清除链接"},
        "goo.lg.select": {"en_US": "Select Receivers", "zh_CN": "选择接收者"},
        "goo.lg.none": {"en_US": "None (light affects everything)", "zh_CN": "无（灯光影响所有物体）"},
        "goo.lg.stats": {"en_US": "{groups} groups · {lights} lights linked · {objects} objects indexed", "zh_CN": "{groups} 个组 · {lights} 盏灯已链接 · {objects} 个物体已索引"},
        "goo.lg.synced": {"en_US": "GOO: synced {groups} light groups", "zh_CN": "GOO: 已同步 {groups} 个灯光组"},
        "goo.lg.no_group": {"en_US": "GOO: create a light group first", "zh_CN": "GOO: 请先创建灯光组"},
        "goo.lg.deleted": {"en_US": "GOO: removed group '{name}'", "zh_CN": "GOO: 已删除灯光组 '{name}'"},
        "goo.lg.assigned": {"en_US": "GOO: {count} light(s) assigned to '{name}'", "zh_CN": "GOO: 已将 {count} 盏灯指定到 '{name}'"},
        "goo.lg.linked": {"en_US": "GOO: toggled link '{name}'", "zh_CN": "GOO: 已切换链接 '{name}'"},
        "goo.lg.light_hint": {"en_US": "Assign this light to a GOO light group.", "zh_CN": "将此灯光指定到 GOO 灯光组。"},
        "goo.lg.obj_hint": {
            "en_US": "Links below apply to the whole object (EEVEE light linking is object based).",
            "zh_CN": "下面的链接作用于整个物体（EEVEE 的灯光链接基于物体）。",
        },
        "goo.lg.mat_hint": {
            "en_US": "Link this material to light groups. Objects using it become receivers of those groups.",
            "zh_CN": "将此材质链接到灯光组，使用它的物体会成为这些组的接收者。",
        },
        "goo.lg.reset_shadow": {"en_US": "Shadows restored from backup", "zh_CN": "阴影设置已从备份恢复"},
    }
)


# ---------------------------------------------------------------------------
# Property groups
# ---------------------------------------------------------------------------


class GOO_LightGroup(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Group Name")
    use_shadow: bpy.props.BoolProperty(name="Cast Shadows", default=True)
    receiver_collection: bpy.props.PointerProperty(
        name="Receiver Collection",
        type=bpy.types.Collection,
        description="Hidden collection driving Blender's native light linking for this group",
    )


class GOO_LightGroupLink(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Light Group")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _prefix() -> str:
    prefs = get_prefs()
    return (prefs.collection_prefix if prefs else "GOO_LG_") or "GOO_LG_"


def _strict() -> bool:
    prefs = get_prefs()
    return bool(prefs.strict_light_groups) if prefs else False


def group_names(scene: bpy.types.Scene) -> list[str]:
    return [g.name for g in scene.goo_light_groups if g.name]


def find_group(scene: bpy.types.Scene, name: str) -> GOO_LightGroup | None:
    for item in scene.goo_light_groups:
        if item.name == name:
            return item
    return None


def ensure_group_item(scene: bpy.types.Scene, name: str) -> GOO_LightGroup:
    item = find_group(scene, name)
    if item is None:
        item = scene.goo_light_groups.add()
        item.name = name
    return item


def _collection_name(scene: bpy.types.Scene, group_name: str) -> str:
    safe = _SAFE.sub("_", group_name).strip("_") or "group"
    return f"{_prefix()}{safe}"


def _light_group_of(obj: bpy.types.Object) -> str:
    return getattr(obj, "goo_light_group", "") or ""


def _object_links(obj: bpy.types.Object) -> set[str]:
    names = {link.name for link in obj.goo_light_groups if link.name}
    for mat in object_materials(obj):
        names.update(link.name for link in mat.goo_light_groups if link.name)
    return names


def _linked_to(mat: bpy.types.Material, name: str) -> bool:
    return any(link.name == name for link in mat.goo_light_groups)


def _remove_links(owner, name: str) -> int:
    removed = 0
    for index in range(len(owner.goo_light_groups) - 1, -1, -1):
        if owner.goo_light_groups[index].name == name:
            owner.goo_light_groups.remove(index)
            removed += 1
    return removed


def _set_shadow(data: bpy.types.Light, value: bool) -> None:
    """Apply a group shadow toggle, keeping a backup for later restoration."""
    if data.use_shadow == value:
        return
    if "goo_orig_use_shadow" not in data:
        data["goo_orig_use_shadow"] = data.use_shadow
    data.use_shadow = value


def _restore_shadow(data: bpy.types.Light) -> None:
    if "goo_orig_use_shadow" in data:
        data.use_shadow = bool(data["goo_orig_use_shadow"])
        del data["goo_orig_use_shadow"]


def _set_light_active(obj: bpy.types.Object, active: bool) -> None:
    """Hide a light from renders when its group has no receiver.

    EEVEE ignores an *empty* receiver collection (a light with an empty
    ``receiver_collection`` still lights every object), so a group without any
    receiving object has to be expressed by hiding its lights from the render
    instead.  The original visibility is backed up and restored when the group
    becomes reachable again.
    """
    if active:
        if "goo_orig_hide_render" in obj:
            obj.hide_render = bool(obj["goo_orig_hide_render"])
            del obj["goo_orig_hide_render"]
    else:
        if "goo_orig_hide_render" not in obj:
            obj["goo_orig_hide_render"] = bool(obj.hide_render)
        obj.hide_render = True


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def sync_scene(scene: bpy.types.Scene, *, verbose: bool = False) -> dict:
    """Rebuild every receiver collection and assign it to the group lights.

    Returns statistics (also handy for the CLI and the self tests).
    """
    strict = _strict()
    names = group_names(scene)

    # 1. Auto discover groups that lights reference but the table misses.
    for obj in scene.objects:
        if obj.type == "LIGHT":
            gname = _light_group_of(obj)
            if gname and find_group(scene, gname) is None:
                ensure_group_item(scene, gname)
    names = group_names(scene)

    # 2. Gather lights per group.
    lights_by_group: dict[str, list[bpy.types.Object]] = {name: [] for name in names}
    ungrouped_lights: list[bpy.types.Object] = []
    for obj in scene.objects:
        if obj.type != "LIGHT":
            continue
        gname = _light_group_of(obj)
        if gname and gname in lights_by_group:
            lights_by_group[gname].append(obj)
        else:
            ungrouped_lights.append(obj)

    # 3. Work out receivers per group.
    members: dict[str, list[bpy.types.Object]] = {name: [] for name in names}
    indexed = 0
    for obj in scene.objects:
        if obj.type in {"LIGHT", "CAMERA", "SPEAKER", "LIGHT_PROBE"}:
            continue
        indexed += 1
        links = _object_links(obj)
        for name in names:
            if name in links or (not strict and not links):
                members[name].append(obj)

    # 4. Create / refresh the detached receiver collections.
    for name in names:
        item = find_group(scene, name)
        assert item is not None
        col_name = _collection_name(scene, name)
        col = item.receiver_collection
        if col is None or col.name != col_name or col.get(GOO_ID) is None:
            existing = bpy.data.collections.get(col_name)
            if existing is not None and existing.get(GOO_ID) is None:
                # A user collection with the same name: pick a free name instead
                # of hijacking it.
                suffix = 1
                while bpy.data.collections.get(f"{col_name}.{suffix:03d}") is not None:
                    suffix += 1
                col_name = f"{col_name}.{suffix:03d}"
                existing = None
            col = existing or bpy.data.collections.new(col_name)
            col[GOO_ID] = "light_group"
            col[GOO_ID + "_group"] = name
            # Detached collections still work for light linking (verified on 5.2)
            # and never pollute the scene tree.
            item.receiver_collection = col

        col = item.receiver_collection
        if col is None:
            continue
        wanted = set(members[name])
        for obj in list(col.objects):
            if obj not in wanted:
                col.objects.unlink(obj)
        present = set(col.objects)
        for obj in members[name]:
            if obj not in present:
                col.objects.link(obj)

    # 5. Assign receiver collections to lights and apply per-group shadow.
    linked_lights = 0
    all_objects = [o for o in scene.objects if o.type not in {"LIGHT", "CAMERA", "SPEAKER", "LIGHT_PROBE"}]
    for name, lights in lights_by_group.items():
        item = find_group(scene, name)
        if item is None:
            continue
        receivers = members[name]
        # An empty receiver collection is ignored by EEVEE; restrict only when
        # the group genuinely covers a subset of the scene.
        restrict = bool(receivers) and len(receivers) < len(all_objects)
        starved = bool(all_objects) and not receivers
        for light in lights:
            try:
                linking = light.light_linking
            except AttributeError:
                linking = None
            if linking is not None:
                linking.receiver_collection = item.receiver_collection if restrict else None
            _set_light_active(light, not starved)
            linked_lights += 1
        for light in lights:
            if light.data is not None:
                _set_shadow(light.data, bool(item.use_shadow))

    # 6. Ungrouped lights must not keep a stale receiver restriction.
    for light in ungrouped_lights:
        try:
            if light.light_linking.receiver_collection is not None:
                light.light_linking.receiver_collection = None
        except AttributeError:
            pass
        if light.data is not None and "goo_orig_use_shadow" in light.data:
            _restore_shadow(light.data)
        _set_light_active(light, True)

    # 7. Purge orphan GOO collections of removed groups.
    live = {item.receiver_collection for item in scene.goo_light_groups if item.receiver_collection}
    for col in list(bpy.data.collections):
        if col.get(GOO_ID) != "light_group" or col in live:
            continue
        if col.users == 0 and not col.objects:
            bpy.data.collections.remove(col)

    stats = {
        "groups": len(names),
        "lights_linked": linked_lights,
        "objects_indexed": indexed,
        "strict": strict,
        "receivers": {name: len(members[name]) for name in names},
        "ungrouped_lights": len(ungrouped_lights),
        "timestamp": now_stamp(),
    }
    if verbose:
        log(f"sync_scene {stats}")
    return stats


def collect_passes(scene: bpy.types.Scene) -> list[dict]:
    """Return the light-group render passes for the CLI.

    Each entry: ``{"group": str, "use_shadow": bool, "lights": [Object, ...]}``.
    Lights that belong to no group (or to a group that no longer exists) are
    collected in the default pass with ``group == ""``.
    """
    names = group_names(scene)
    passes: list[dict] = []
    for name in names:
        item = find_group(scene, name)
        lights = [o for o in scene.objects if o.type == "LIGHT" and _light_group_of(o) == name]
        if not lights:
            continue
        passes.append(
            {
                "group": name,
                "use_shadow": bool(item.use_shadow) if item else True,
                "lights": lights,
            }
        )
    default_lights = [
        o for o in scene.objects if o.type == "LIGHT" and (_light_group_of(o) not in names)
    ]
    if default_lights:
        passes.append({"group": "", "use_shadow": True, "lights": default_lights})
    return passes


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


def _active_scene(context: bpy.types.Context) -> bpy.types.Scene | None:
    return context.scene


class GOO_OT_light_group_add(bpy.types.Operator):
    """Add a GOO light group"""

    bl_idname = "goo.light_group_add"
    bl_label = "Add GOO Light Group"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name")

    def execute(self, context):
        scene = _active_scene(context)
        if scene is None:
            self.report({"ERROR"}, i18n.t("goo.lg.no_scene"))
            return {"CANCELLED"}
        base = self.name or "LightGroup"
        name = base
        index = 1
        while find_group(scene, name) is not None:
            index += 1
            name = f"{base}.{index:03d}"
        item = ensure_group_item(scene, name)
        item.receiver_collection = ensure_collection(_collection_name(scene, name), link=False)
        item.receiver_collection[GOO_ID] = "light_group"
        item.receiver_collection[GOO_ID + "_group"] = name
        scene.goo_light_groups_index = len(scene.goo_light_groups) - 1
        sync_scene(scene)
        self.report({"INFO"}, f"GOO: added light group '{name}'")
        return {"FINISHED"}


class GOO_OT_light_group_remove(bpy.types.Operator):
    """Remove the selected GOO light group"""

    bl_idname = "goo.light_group_remove"
    bl_label = "Remove GOO Light Group"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        scene = _active_scene(context)
        if scene is None or not scene.goo_light_groups:
            return {"CANCELLED"}
        index = self.index if self.index >= 0 else scene.goo_light_groups_index
        index = max(0, min(index, len(scene.goo_light_groups) - 1))
        item = scene.goo_light_groups[index]
        name = item.name
        col = item.receiver_collection
        for obj in scene.objects:
            if obj.type == "LIGHT" and _light_group_of(obj) == name:
                obj.goo_light_group = ""
                if obj.data is not None:
                    _restore_shadow(obj.data)
            _remove_links(obj, name)
        for mat in bpy.data.materials:
            _remove_links(mat, name)
        scene.goo_light_groups.remove(index)
        scene.goo_light_groups_index = max(0, index - 1)
        if col is not None and col.get(GOO_ID) == "light_group":
            if not col.objects:
                bpy.data.collections.remove(col)
        sync_scene(scene)
        self.report({"INFO"}, i18n.t("goo.lg.deleted", name=name))
        return {"FINISHED"}


class GOO_OT_light_group_sync(bpy.types.Operator):
    """Rebuild receiver collections and re-apply native light linking"""

    bl_idname = "goo.light_group_sync"
    bl_label = "Sync GOO Light Groups"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = _active_scene(context)
        if scene is None:
            self.report({"ERROR"}, i18n.t("goo.lg.no_scene"))
            return {"CANCELLED"}
        stats = sync_scene(scene, verbose=True)
        self.report({"INFO"}, i18n.t("goo.lg.synced", groups=stats["groups"]))
        return {"FINISHED"}


class GOO_OT_light_group_assign_lights(bpy.types.Operator):
    """Assign the selected lights to a GOO light group"""

    bl_idname = "goo.light_group_assign_lights"
    bl_label = "Assign Lights To GOO Group"
    bl_options = {"REGISTER", "UNDO"}

    group_name: bpy.props.StringProperty(name="Group")

    def execute(self, context):
        scene = _active_scene(context)
        if scene is None:
            return {"CANCELLED"}
        if not self.group_name:
            self.report({"ERROR"}, i18n.t("goo.lg.no_group"))
            return {"CANCELLED"}
        ensure_group_item(scene, self.group_name)
        count = 0
        for obj in context.selected_objects:
            if obj.type == "LIGHT":
                obj.goo_light_group = self.group_name
                count += 1
        if count == 0 and context.object is not None and context.object.type == "LIGHT":
            context.object.goo_light_group = self.group_name
            count = 1
        sync_scene(scene)
        self.report({"INFO"}, i18n.t("goo.lg.assigned", count=count, name=self.group_name))
        return {"FINISHED"}


class GOO_OT_light_group_assign(bpy.types.Operator):
    """Toggle a light group link on the active material or object"""

    bl_idname = "goo.light_group_assign"
    bl_label = "Toggle GOO Group Link"
    bl_options = {"REGISTER", "UNDO"}

    group_name: bpy.props.StringProperty(name="Group")
    target: bpy.props.EnumProperty(
        name="Target",
        items=(("MATERIAL", "Material", ""), ("OBJECT", "Object", "")),
        default="MATERIAL",
    )

    def execute(self, context):
        scene = _active_scene(context)
        if scene is None or not self.group_name:
            return {"CANCELLED"}
        owner = context.material if self.target == "MATERIAL" else context.object
        if owner is None:
            self.report({"ERROR"}, "GOO: no active material/object")
            return {"CANCELLED"}
        existing = next((link for link in owner.goo_light_groups if link.name == self.group_name), None)
        if existing is not None:
            owner.goo_light_groups.remove(owner.goo_light_groups.find(self.group_name))
        else:
            ensure_group_item(scene, self.group_name)
            link = owner.goo_light_groups.add()
            link.name = self.group_name
        sync_scene(scene)
        self.report({"INFO"}, i18n.t("goo.lg.linked", name=self.group_name))
        return {"FINISHED"}


class GOO_OT_light_group_clear_links(bpy.types.Operator):
    """Remove every GOO light group link from the active material/object"""

    bl_idname = "goo.light_group_unassign"
    bl_label = "Clear GOO Group Links"
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.props.EnumProperty(
        name="Target",
        items=(("MATERIAL", "Material", ""), ("OBJECT", "Object", "")),
        default="MATERIAL",
    )

    def execute(self, context):
        scene = _active_scene(context)
        owner = context.material if self.target == "MATERIAL" else context.object
        if owner is None:
            return {"CANCELLED"}
        owner.goo_light_groups.clear()
        if scene is not None:
            sync_scene(scene)
        return {"FINISHED"}


class GOO_OT_light_group_select(bpy.types.Operator):
    """Select the receiving objects of the selected light group"""

    bl_idname = "goo.light_group_select"
    bl_label = "Select GOO Group Receivers"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        scene = _active_scene(context)
        if scene is None or not scene.goo_light_groups:
            return {"CANCELLED"}
        index = self.index if self.index >= 0 else scene.goo_light_groups_index
        index = max(0, min(index, len(scene.goo_light_groups) - 1))
        item = scene.goo_light_groups[index]
        col = item.receiver_collection
        if col is None:
            return {"CANCELLED"}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        for obj in col.objects:
            try:
                obj.select_set(True)
            except RuntimeError:
                pass
        return {"FINISHED"}


CLASSES = (
    GOO_LightGroup,
    GOO_LightGroupLink,
    GOO_OT_light_group_add,
    GOO_OT_light_group_remove,
    GOO_OT_light_group_sync,
    GOO_OT_light_group_assign_lights,
    GOO_OT_light_group_assign,
    GOO_OT_light_group_clear_links,
    GOO_OT_light_group_select,
)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


class GOO_UL_light_groups(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="OUTLINER_COLLECTION")
        row.prop(item, "use_shadow", text="", icon="MOD_SHADOW" if item.use_shadow else "X", emboss=False)
        receivers = len(item.receiver_collection.objects) if item.receiver_collection else 0
        row.label(text=str(receivers))


class GOO_PT_light_groups(bpy.types.Panel):
    bl_label = "GOO Light Groups"
    bl_idname = "GOO_PT_light_groups"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        if scene is None:
            layout.label(text=i18n.t("goo.lg.no_scene"))
            return
        prefs = get_prefs()
        row = layout.row()
        row.template_list(
            "GOO_UL_light_groups",
            "",
            scene,
            "goo_light_groups",
            scene,
            "goo_light_groups_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("goo.light_group_add", text="", icon="ADD")
        col.operator("goo.light_group_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("goo.light_group_sync", text="", icon="FILE_REFRESH")
        col.operator("goo.light_group_select", text="", icon="RESTRICT_SELECT_OFF")

        if not scene.goo_light_groups:
            layout.label(text=i18n.t("goo.lg.empty"), icon="INFO")
        else:
            index = min(scene.goo_light_groups_index, len(scene.goo_light_groups) - 1)
            item = scene.goo_light_groups[index] if index >= 0 else None
            if item is not None:
                box = layout.box()
                box.prop(item, "name", text=i18n.t("goo.lg.name"))
                box.prop(item, "use_shadow", text=i18n.t("goo.lg.shadows"))
                box.operator("goo.light_group_assign_lights", text=i18n.t("goo.lg.assign"), icon="LIGHT").group_name = item.name
                if item.receiver_collection is not None:
                    box.label(text=f"{i18n.t('goo.lg.receivers')}: {len(item.receiver_collection.objects)}")
        layout.separator()
        if prefs is not None:
            layout.prop(prefs, "strict_light_groups")
            if prefs.strict_light_groups:
                layout.label(text=i18n.t("goo.lg.strict"), icon="LOCKED")
        layout.label(text=i18n.t("goo.lg.help"), icon="QUESTION")


class GOO_PT_material_light_groups(bpy.types.Panel):
    bl_label = "GOO Light Groups"
    bl_idname = "GOO_PT_material_light_groups"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    def draw(self, context):
        layout = self.layout
        mat = context.material
        scene = context.scene
        if mat is None or scene is None:
            layout.label(text=i18n.t("goo.lg.no_scene"))
            return
        layout.label(text=i18n.t("goo.lg.mat_hint"), icon="INFO")
        if not scene.goo_light_groups:
            layout.label(text=i18n.t("goo.lg.empty"), icon="INFO")
            return
        col = layout.column(align=True)
        for item in scene.goo_light_groups:
            if not item.name:
                continue
            linked = _linked_to(mat, item.name)
            op = col.operator(
                "goo.light_group_assign",
                text=item.name,
                icon="CHECKBOX_HLT" if linked else "CHECKBOX_DEHLT",
                depress=linked,
            )
            op.group_name = item.name
            op.target = "MATERIAL"
        layout.operator("goo.light_group_unassign", text=i18n.t("goo.lg.unassign"), icon="X").target = "MATERIAL"


class GOO_PT_object_light_groups(bpy.types.Panel):
    bl_label = "GOO Light Groups"
    bl_idname = "GOO_PT_object_light_groups"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        scene = context.scene
        if obj is None or scene is None:
            return
        if obj.type == "LIGHT":
            layout.label(text=i18n.t("goo.lg.light_hint"), icon="INFO")
            if scene.goo_light_groups:
                layout.prop_search(
                    obj,
                    "goo_light_group",
                    scene,
                    "goo_light_groups",
                    text=i18n.t("goo.lg.name"),
                )
                if not obj.goo_light_group:
                    layout.label(text=i18n.t("goo.lg.none"), icon="INFO")
                for item in scene.goo_light_groups:
                    if item.name and obj.goo_light_group != item.name:
                        op = layout.operator(
                            "goo.light_group_assign_lights",
                            text=item.name,
                            icon="LIGHT",
                        )
                        op.group_name = item.name
            else:
                layout.label(text=i18n.t("goo.lg.empty"), icon="INFO")
            return
        layout.label(text=i18n.t("goo.lg.obj_hint"), icon="INFO")
        if not scene.goo_light_groups:
            layout.label(text=i18n.t("goo.lg.empty"), icon="INFO")
            return
        col = layout.column(align=True)
        for item in scene.goo_light_groups:
            if not item.name:
                continue
            linked = any(link.name == item.name for link in obj.goo_light_groups)
            op = col.operator(
                "goo.light_group_assign",
                text=item.name,
                icon="CHECKBOX_HLT" if linked else "CHECKBOX_DEHLT",
                depress=linked,
            )
            op.group_name = item.name
            op.target = "OBJECT"
        layout.operator("goo.light_group_unassign", text=i18n.t("goo.lg.unassign"), icon="X").target = "OBJECT"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _render_pre_sync(scene, *args):
    prefs = get_prefs()
    if prefs is None:
        return
    if getattr(scene, "goo_auto_sync", True):
        try:
            sync_scene(scene)
        except Exception as exc:  # pragma: no cover - never break a render
            log(f"render_pre light group sync failed: {exc!r}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_PROP_CLASSES = (GOO_LightGroup, GOO_LightGroupLink)
_PANELS = (
    GOO_UL_light_groups,
    GOO_PT_light_groups,
    GOO_PT_material_light_groups,
    GOO_PT_object_light_groups,
)
_OPERATORS = (
    GOO_OT_light_group_add,
    GOO_OT_light_group_remove,
    GOO_OT_light_group_sync,
    GOO_OT_light_group_assign_lights,
    GOO_OT_light_group_assign,
    GOO_OT_light_group_clear_links,
    GOO_OT_light_group_select,
)

CLASSES = _PROP_CLASSES + _PANELS + _OPERATORS


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.goo_light_groups = bpy.props.CollectionProperty(type=GOO_LightGroup)
    bpy.types.Scene.goo_light_groups_index = bpy.props.IntProperty(name="Light Group", default=0)
    bpy.types.Scene.goo_auto_sync = bpy.props.BoolProperty(
        name="Sync light linking before renders", default=True
    )
    bpy.types.Material.goo_light_groups = bpy.props.CollectionProperty(type=GOO_LightGroupLink)
    bpy.types.Object.goo_light_groups = bpy.props.CollectionProperty(type=GOO_LightGroupLink)
    bpy.types.Object.goo_light_group = bpy.props.StringProperty(
        name="GOO Light Group",
        description="Light group this light belongs to (empty = affects every object)",
    )
    if _render_pre_sync not in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.append(_render_pre_sync)


def unregister() -> None:
    if _render_pre_sync in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.remove(_render_pre_sync)
    for prop, owner in (
        ("goo_light_groups", bpy.types.Scene),
        ("goo_light_groups_index", bpy.types.Scene),
        ("goo_auto_sync", bpy.types.Scene),
        ("goo_light_groups", bpy.types.Material),
        ("goo_light_groups", bpy.types.Object),
        ("goo_light_group", bpy.types.Object),
    ):
        if hasattr(owner, prop):
            delattr(owner, prop)
    safe_unregister_all(CLASSES)
