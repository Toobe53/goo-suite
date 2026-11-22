# SPDX-License-Identifier: GPL-3.0-or-later
"""Low level helpers shared by all GOO node group builders and operators.

The add-on generates every GOO node group procedurally.  This module contains
the small API surface used by the individual builders:

* idempotent creation / in-place rebuilding of node groups,
* a compact way to declare group interface sockets,
* socket lookups that survive interface renames.

Builder contract
----------------

A builder is a plain function ``builder(tree)``.  It must:

1. declare the group interface with :func:`iface` (inputs first, outputs last),
2. add a ``NodeGroupInput`` / ``NodeGroupOutput`` via :func:`gin` / :func:`gout`,
3. place internal nodes and links between them.

Builders must be deterministic and must not touch the scene, so they can also
run inside ``blender --background``.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional

import bpy

GOO_ID = "goo_port"
GOO_VERSION_KEY = "goo_builder_version"
GOO_BUILDER_KEY = "goo_builder"
GOO_SIGNATURE_KEY = "goo_signature"

SUPPORTED_TREE_TYPES = ("ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree")

#: Socket identifiers accepted by ``NodeTreeInterface.new_socket`` on 4.x/5.x.
SOCKET_TYPES = (
    "NodeSocketFloat",
    "NodeSocketColor",
    "NodeSocketVector",
    "NodeSocketInt",
    "NodeSocketBool",
    "NodeSocketShader",
    "NodeSocketObject",
    "NodeSocketMaterial",
    "NodeSocketString",
    "NodeSocketMenu",
    "NodeSocketRotation",
)

_LOG: list[str] = []


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------
def safe_unregister(cls: type) -> bool:
    """Unregister a Blender class without failing on an already-clean state.

    Blender 5.2 does not expose every registered ``PropertyGroup`` /
    ``AddonPreferences`` subclass as ``bpy.types.<ClassName>``.  Guarding
    ``unregister_class`` with ``getattr(bpy.types, ...)`` therefore silently
    skips real unregistrations and breaks add-on re-enable.  Always attempt the
    operation and treat "not registered" as success.
    """
    try:
        bpy.utils.unregister_class(cls)
        return True
    except RuntimeError:
        return False


def safe_unregister_all(classes: Iterable[type]) -> None:
    """Unregister classes in reverse registration order, ignoring stale ones."""
    for cls in reversed(list(classes)):
        safe_unregister(cls)


def log(message: str) -> None:
    """Append a message to the in-memory log used by the GUI/CLI and print it."""
    _LOG.append(message)
    del _LOG[:-2000]
    print(f"[goo] {message}")


def log_history() -> list[str]:
    return list(_LOG)


def clear_log() -> None:
    _LOG.clear()


# ---------------------------------------------------------------------------
# node group lifecycle
# ---------------------------------------------------------------------------
def find_group(name: str) -> Optional[bpy.types.NodeTree]:
    tree = bpy.data.node_groups.get(name)
    return tree


def remove_group(name: str) -> bool:
    tree = bpy.data.node_groups.get(name)
    if tree is None:
        return False
    bpy.data.node_groups.remove(tree)
    log(f"removed node group {name!r}")
    return True


def _remove_all_interface_items(tree: bpy.types.NodeTree) -> None:
    iface = tree.interface
    for item in list(iface.items_tree):
        try:
            iface.remove(item)
        except RuntimeError:
            # output sockets can only be removed after their nodes are gone
            pass


def clear_group(tree: bpy.types.NodeTree) -> None:
    """Remove every node and interface item while keeping the datablock alive.

    Keeping the datablock means existing ``ShaderNodeGroup`` instances in
    materials stay valid after a rebuild.
    """
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    _remove_all_interface_items(tree)
    try:
        for item in list(tree.interface.items_tree):
            tree.interface.remove(item)
    except RuntimeError:
        pass


def tree_signature(tree: bpy.types.NodeTree, builder: Callable[..., Any], version: int) -> str:
    """Return a signature used to detect stale node groups.

    The signature combines the builder identity, the explicit builder version
    and a timestamp free source line count.  Bumping ``version`` in the builder
    registry is the supported way to force a rebuild.
    """
    owner = getattr(builder, "__qualname__", repr(builder))
    return f"{owner}:{version}"


def tree_is_stale(tree: bpy.types.NodeTree, builder: Callable[..., Any], version: int) -> bool:
    if tree.get(GOO_ID) is not True:
        return True
    if int(tree.get(GOO_VERSION_KEY, -1)) != int(version):
        return True
    if tree.get(GOO_BUILDER_KEY) != getattr(builder, "__qualname__", repr(builder)):
        return True
    return False


def ensure_group(
    name: str,
    tree_type: str,
    builder: Callable[[bpy.types.NodeTree], Any],
    version: int = 1,
    force: bool = False,
) -> bpy.types.NodeTree:
    """Create or rebuild a node group and return it.

    Parameters
    ----------
    name:
        Datablock name, e.g. ``"GOO Shader Info"``.
    tree_type:
        One of :data:`SUPPORTED_TREE_TYPES`.
    builder:
        Function receiving the (possibly pre-existing) node tree.
    version:
        Builder version.  Increase it whenever the generated node layout
        changes so existing files are upgraded on the next sync.
    force:
        Rebuild even when the stored signature matches.
    """
    if tree_type not in SUPPORTED_TREE_TYPES:
        raise ValueError(f"unsupported node tree type {tree_type!r}")
    tree = bpy.data.node_groups.get(name)
    if tree is not None and tree.bl_idname != tree_type:
        log(f"node group {name!r} has wrong type {tree.bl_idname}, recreating")
        bpy.data.node_groups.remove(tree)
        tree = None

    if tree is not None and not force and not tree_is_stale(tree, builder, version):
        tree.use_fake_user = True
        return tree

    if tree is None:
        tree = bpy.data.node_groups.new(name, tree_type)
        tree[GOO_ID] = True
        log(f"created node group {name!r} ({tree_type})")
    else:
        clear_group(tree)
        log(f"rebuilding node group {name!r} (version {version})")

    tree[GOO_ID] = True
    # The generated library is a project asset, not a temporary datablock:
    # without a fake user Blender purges zero-user node groups on save, which
    # would silently empty the library after "Build" + close.
    tree.use_fake_user = True
    tree[GOO_VERSION_KEY] = int(version)
    tree[GOO_BUILDER_KEY] = getattr(builder, "__qualname__", repr(builder))
    tree[GOO_SIGNATURE_KEY] = tree_signature(tree, builder, version)
    try:
        tree.description = f"GOO port: {name}"
    except AttributeError:
        pass
    builder(tree)
    return tree


# ---------------------------------------------------------------------------
# interface helpers
# ---------------------------------------------------------------------------
def iface(
    tree: bpy.types.NodeTree,
    name: str,
    in_out: str,
    socket_type: str,
    *,
    description: str = "",
    default: Any = None,
    min_value: Any = None,
    max_value: Any = None,
    subtype: Optional[str] = None,
    hide_value: bool = False,
) -> Any:
    """Declare one interface socket and return the created item."""
    if in_out not in ("INPUT", "OUTPUT"):
        raise ValueError("in_out must be INPUT or OUTPUT")
    if socket_type not in SOCKET_TYPES:
        raise ValueError(f"unsupported socket type {socket_type!r}")
    item = tree.interface.items_tree.get(name)
    if item is not None and item.item_type == "SOCKET" and item.in_out == in_out:
        return item
    if item is not None:
        tree.interface.remove(item)
    item = tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if description:
        item.description = description
    if subtype is not None and hasattr(item, "subtype"):
        item.subtype = subtype
    if default is not None and hasattr(item, "default_value"):
        try:
            item.default_value = default
        except (TypeError, ValueError):
            pass
    for attr, value in (("min_value", min_value), ("max_value", max_value)):
        if value is not None and hasattr(item, attr):
            try:
                setattr(item, attr, value)
            except (TypeError, ValueError):
                pass
    if hide_value and hasattr(item, "hide_value"):
        item.hide_value = True
    return item


def interface_item(tree: bpy.types.NodeTree, name: str) -> Any:
    return tree.interface.items_tree.get(name)


def interface_names(tree: bpy.types.NodeTree, in_out: Optional[str] = None) -> list[str]:
    names = []
    for item in tree.interface.items_tree:
        if item.item_type != "SOCKET":
            continue
        if in_out and item.in_out != in_out:
            continue
        names.append(item.name)
    return names


# ---------------------------------------------------------------------------
# node helpers
# ---------------------------------------------------------------------------
def node(
    tree: bpy.types.NodeTree,
    idname: str,
    *,
    loc: tuple[float, float] = (0.0, 0.0),
    label: Optional[str] = None,
    name: Optional[str] = None,
    width: Optional[float] = None,
    mute: bool = False,
) -> bpy.types.Node:
    """Create a node with a friendly location and optional label."""
    n = tree.nodes.new(idname)
    n.location = loc
    if label:
        n.label = label
    if name:
        n.name = name
    if width is not None:
        try:
            n.width = width
        except AttributeError:
            pass
    if mute:
        n.mute = True
    return n


def gin(tree: bpy.types.NodeTree, loc: tuple[float, float] = (-520.0, 0.0)) -> bpy.types.Node:
    """Return (and create if needed) the group input node."""
    for n in tree.nodes:
        if n.bl_idname == "NodeGroupInput":
            return n
    n = tree.nodes.new("NodeGroupInput")
    n.location = loc
    n.name = n.label = "GOO Input"
    return n


def gout(tree: bpy.types.NodeTree, loc: tuple[float, float] = (520.0, 0.0)) -> bpy.types.Node:
    """Return (and create if needed) the group output node."""
    for n in tree.nodes:
        if n.bl_idname == "NodeGroupOutput":
            return n
    n = tree.nodes.new("NodeGroupOutput")
    n.location = loc
    n.name = n.label = "GOO Output"
    return n


def in_sock(tree: bpy.types.NodeTree, name: str) -> Any:
    """Return the *output* socket of the group input node called ``name``."""
    n = gin(tree)
    sock = n.outputs.get(name)
    if sock is None:
        raise KeyError(f"no group input socket {name!r} in {tree.name!r}")
    return sock


def out_sock(tree: bpy.types.NodeTree, name: str) -> Any:
    """Return the *input* socket of the group output node called ``name``."""
    n = gout(tree)
    sock = n.inputs.get(name)
    if sock is None:
        raise KeyError(f"no group output socket {name!r} in {tree.name!r}")
    return sock


def link(tree: bpy.types.NodeTree, from_socket: Any, to_socket: Any) -> Any:
    """Create a link, silently replacing links into a single input."""
    return tree.links.new(from_socket, to_socket)


def connect_input(
    tree: bpy.types.NodeTree,
    sock: Any,
    node: bpy.types.Node,
    input_name: str,
) -> Any:
    return link(tree, sock, node.inputs[input_name])


def set_default(socket: Any, value: Any) -> None:
    try:
        socket.default_value = value
    except (TypeError, ValueError, AttributeError):
        pass


def mix_rgb(tree: bpy.types.NodeTree, loc: tuple[float, float] = (0, 0), blend: str = "MIX") -> Any:
    """Small convenience wrapper around ``ShaderNodeMix`` (5.x)."""
    n = node(tree, "ShaderNodeMix", loc=loc)
    n.data_type = "RGBA"
    n.blend_type = blend
    return n


def math_node(tree: bpy.types.NodeTree, operation: str, loc=(0, 0), **kwargs) -> Any:
    n = node(tree, "ShaderNodeMath", loc=loc)
    n.operation = operation
    for key, value in kwargs.items():
        if key in n.inputs:
            n.inputs[key].default_value = value
    return n


def vector_node(tree: bpy.types.NodeTree, operation: str, loc=(0, 0)) -> Any:
    n = node(tree, "ShaderNodeVectorMath", loc=loc)
    n.operation = operation
    return n


def is_shader_tree(tree: bpy.types.NodeTree) -> bool:
    return tree.bl_idname == "ShaderNodeTree"


def is_geometry_tree(tree: bpy.types.NodeTree) -> bool:
    return tree.bl_idname == "GeometryNodeTree"


def is_compositor_tree(tree: bpy.types.NodeTree) -> bool:
    return tree.bl_idname == "CompositorNodeTree"


# ---------------------------------------------------------------------------
# object / material helper data
# ---------------------------------------------------------------------------
def iter_objects(context=None) -> Iterable[bpy.types.Object]:
    context = context or bpy.context
    scene = context.scene
    if scene is None:
        return ()
    return list(scene.objects)


def object_materials(obj: bpy.types.Object) -> list[bpy.types.Material]:
    mats = []
    for slot in getattr(obj, "material_slots", ()):
        if slot.material is not None:
            mats.append(slot.material)
    data = getattr(obj, "data", None)
    mat = getattr(data, "material", None) if data is not None else None
    if mat is not None and mat not in mats:
        mats.append(mat)
    return mats


def set_custom_flag(item, key: str, value) -> None:
    try:
        item[key] = value
    except (TypeError, ValueError):
        pass


def ensure_collection(name: str, parent: Optional[bpy.types.Collection] = None, link: bool = False) -> bpy.types.Collection:
    """Return a collection, optionally linking it to ``parent``.

    GOO light group collections are intentionally kept *detached* (``link``
    left at ``False``): Blender light linking accepts collections that are not
    part of the view layer, and detached collections cannot affect visibility.
    """
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        coll[GOO_ID] = True
        log(f"created collection {name!r}")
    if link and parent is not None and coll.name not in parent.children:
        try:
            parent.children.link(coll)
        except RuntimeError:
            pass
    return coll


def frame_all_nodes(tree: bpy.types.NodeTree) -> None:
    """Best effort: trigger the node editor's auto-arrange alternatives."""
    # ``bpy.ops.node....
    # ``bpy.ops.node....
    # Node editors are not available in background mode; callers who need tidy
    # layouts should position nodes inside the builder.
    _ = tree


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
