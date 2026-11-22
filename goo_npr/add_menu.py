# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Engine entries in the Node Editor *Add* menu.

The menu itself is intentionally data-driven: everything comes from
``goo_npr.nodes.registry`` so the node-builder agent can add groups without
touching this file.
"""

from __future__ import annotations

import bpy

from . import i18n
from .i18n_strings import tr
from .nodes import registry
from .utils import log, safe_unregister_all

#: node tree bl_idname -> group node bl_idname (verified headless in Blender 5.2)
NODE_GROUP_IDNAMES: dict[str, str] = {
    "ShaderNodeTree": "ShaderNodeGroup",
    "GeometryNodeTree": "GeometryNodeGroup",
    "CompositorNodeTree": "CompositorNodeGroup",
}

#: tree types this add-on supports inside the Node Editor
SUPPORTED_NODE_TREE_TYPES = tuple(NODE_GROUP_IDNAMES)

_APPENDED = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def category_label(category: str) -> str:
    """Translated label for a registry category (falls back to the id)."""
    labels = registry.CATEGORY_LABELS.get(category, {})
    language = i18n.current_language()
    if language in labels:
        return labels[language]
    return labels.get("en_US", category)


def supported_tree_type(context) -> str:
    """Return the tree type of the node editor in ``context``, or ``""``."""
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "type", "") != "NODE_EDITOR":
        return ""
    tree_type = getattr(space, "tree_type", "")
    return tree_type if tree_type in NODE_GROUP_IDNAMES else ""


def _space_tree(context):
    """The tree currently edited in the node editor, if any."""
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "type", "") != "NODE_EDITOR":
        return None
    return getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)


def _material_tree(context):
    """Node tree of the material being edited / active in the context.

    Blender 5.2 removed ``Context.material`` from the RNA, but a
    ``temp_override(material=...)`` still exposes it, and the active object's
    material stays reachable.  Both paths are supported so headless callers
    and the 3D Viewport can drive the operator too.
    """
    mat = getattr(context, "material", None)
    candidates = []
    if mat is not None:
        candidates.append(mat)
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    if obj is not None:
        candidates.append(getattr(obj, "active_material", None))
        for slot in getattr(obj, "material_slots", ()) or ():
            candidates.append(getattr(slot, "material", None))
    for candidate in candidates:
        tree = getattr(candidate, "node_tree", None)
        if tree is not None and tree.bl_idname == "ShaderNodeTree":
            return tree
    return None


def _geometry_tree(context):
    """Geometry node tree edited in the context (object modifier or group editor)."""
    space = getattr(context, "space_data", None)
    if space is not None and getattr(space, "type", "") == "NODE_EDITOR":
        if getattr(space, "tree_type", "") == "GeometryNodeTree":
            tree = _space_tree(context)
            if tree is not None and tree.bl_idname == "GeometryNodeTree":
                return tree
    obj = getattr(context, "active_object", None) or getattr(context, "object", None)
    for modifier in getattr(obj, "modifiers", ()) or ():
        tree = getattr(modifier, "node_group", None)
        if tree is not None and tree.bl_idname == "GeometryNodeTree":
            return tree
    return None


def _compositor_tree(context):
    """5.2 compositor is a node-group datablock on the Scene."""
    scene = getattr(context, "scene", None)
    tree = getattr(scene, "compositing_node_group", None) if scene is not None else None
    if tree is not None and tree.bl_idname == "CompositorNodeTree":
        return tree
    return None


def resolve_target_tree(context, tree_type: str):
    """Find the node tree of ``tree_type`` the operator should edit.

    Resolution order: node editor ``edit_tree``/``node_tree`` first, then
    context/material/object fallbacks (headless tests, 3D Viewport), then the
    scene compositor group.
    """
    if tree_type == "CompositorNodeTree":
        return _compositor_tree(context)
    if tree_type == "ShaderNodeTree":
        return _material_tree(context)
    if tree_type == "GeometryNodeTree":
        return _geometry_tree(context)
    return None


def resolve_any_tree(context):
    """First supported target tree reachable from ``context``."""
    for tree_type in SUPPORTED_NODE_TREE_TYPES:
        tree = resolve_target_tree(context, tree_type)
        if tree is not None:
            return tree
    return None


def add_group_node(tree, group_name: str, location=(0.0, 0.0)):
    """Create a group node for ``group_name`` in ``tree`` and return it.

    Raises ``KeyError`` for unknown groups, ``ValueError`` for a tree type
    mismatch and ``RuntimeError`` when the registry failed to build the group.
    """
    spec = registry.find_spec(group_name)
    if spec is None:
        raise KeyError(f"unknown GOO node group: {group_name}")
    if tree.bl_idname != spec.tree_type:
        raise ValueError(
            f"node group {group_name!r} is a {spec.tree_type}, not a {tree.bl_idname}"
        )

    registry.ensure_all(only=[group_name])
    group = bpy.data.node_groups.get(group_name)
    if group is None:
        raise RuntimeError(f"GOO node group {group_name!r} could not be created")

    node = tree.nodes.new(NODE_GROUP_IDNAMES[spec.tree_type])
    node.node_tree = group
    try:
        node.location = (float(location[0]), float(location[1]))
    except (TypeError, ValueError, IndexError):
        node.location = (0.0, 0.0)
    node.select = True
    try:
        tree.nodes.active = node
    except (AttributeError, TypeError):
        pass
    log(f"GOO: added node group {group_name!r} to {tree.name!r} ({tree.bl_idname})")
    return node


def _cursor_location(context):
    space = getattr(context, "space_data", None)
    location = getattr(space, "cursor_location", None) if space is not None else None
    if location is None:
        return (0.0, 0.0)
    return (float(location[0]), float(location[1]))


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------
class GOO_OT_add_node_group(bpy.types.Operator):
    """Add a generated GOO node group to the edited node tree.

    / 将生成的 GOO 节点组添加到当前编辑的节点树。
    """

    bl_idname = "goo.add_node_group"
    bl_label = tr("goo.menu.add_node_group", "Add GOO Node Group")
    bl_description = tr(
        "goo.menu.add_node_group.desc",
        "Add a generated GOO node group to the edited node tree",
    )
    bl_options = {"REGISTER", "UNDO"}

    group_name: bpy.props.StringProperty(
        name="Group Name",
        description="Registry name of the GOO node group to add",
        default="",
    )

    @classmethod
    def poll(cls, context):
        # Enabled inside a supported Node Editor even when the editor has no
        # bound tree yet (execute reports the reason), or anywhere a fallback
        # tree can be resolved (headless tests / 3D View).
        if supported_tree_type(context):
            return True
        return resolve_any_tree(context) is not None

    def execute(self, context):
        spec = registry.find_spec(self.group_name)
        if spec is None:
            self.report(
                {"ERROR"}, i18n.t("goo.menu.unknown_group", name=self.group_name)
            )
            return {"CANCELLED"}

        tree = resolve_target_tree(context, spec.tree_type)
        if tree is None:
            self.report({"ERROR"}, i18n.t("goo.menu.no_tree", tree_type=spec.tree_type))
            return {"CANCELLED"}

        try:
            add_group_node(tree, spec.name, _cursor_location(context))
        except Exception as exc:  # registry builder failures must not kill Blender
            self.report({"ERROR"}, f"{spec.name}: {exc}")
            log(f"GOO: failed to add node group {spec.name!r}: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, i18n.t("goo.menu.added", name=spec.name))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
class NODE_MT_goo_engine_add(bpy.types.Menu):
    """GOO Engine node group picker in the Node Editor Add menu."""

    bl_idname = "NODE_MT_goo_engine_add"
    bl_label = "GOO Engine"

    def draw(self, context):
        layout = self.layout
        tree_type = supported_tree_type(context)
        specs = registry.specs_for_tree(tree_type) if tree_type else []

        if not specs:
            layout.label(text=i18n.t("goo.menu.empty"), icon="INFO")
            layout.separator()
            layout.operator(
                "goo.build_library",
                text=i18n.t("goo.menu.rebuild"),
                icon="FILE_REFRESH",
            )
            return

        for category in registry.CATEGORIES:
            category_specs = [spec for spec in specs if spec.category == category]
            if not category_specs:
                continue
            column = layout.column()
            column.label(text=category_label(category))
            for spec in category_specs:
                op = column.operator(
                    "goo.add_node_group",
                    text=spec.label_text(),
                    icon="NODETREE",
                )
                op.group_name = spec.name

        layout.separator()
        layout.operator(
            "goo.build_library",
            text=i18n.t("goo.menu.rebuild"),
            icon="FILE_REFRESH",
        )


def _draw_add_menu(self, context):
    """Append a GOO submenu to the Node Editor Add menu (only when relevant)."""
    if not supported_tree_type(context):
        return
    self.layout.separator()
    self.layout.menu(
        NODE_MT_goo_engine_add.bl_idname,
        text=i18n.t("goo.menu.add_submenu"),
        icon="NODETREE",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
CLASSES = (GOO_OT_add_node_group, NODE_MT_goo_engine_add)


def register():
    global _APPENDED
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if not _APPENDED:
        bpy.types.NODE_MT_add.append(_draw_add_menu)
        _APPENDED = True


def unregister():
    global _APPENDED
    if _APPENDED:
        bpy.types.NODE_MT_add.remove(_draw_add_menu)
        _APPENDED = False
    safe_unregister_all(CLASSES)


__all__ = [
    "NODE_GROUP_IDNAMES",
    "SUPPORTED_NODE_TREE_TYPES",
    "GOO_OT_add_node_group",
    "NODE_MT_goo_engine_add",
    "add_group_node",
    "category_label",
    "register",
    "resolve_any_tree",
    "resolve_target_tree",
    "supported_tree_type",
    "unregister",
]
