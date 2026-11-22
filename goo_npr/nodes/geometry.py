# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry-node helpers that approximate GOO's native geometry nodes.

GOO 4.4 changes ``Scene Time`` to output ``Start Frame`` and ``End Frame`` in
addition to Blender's ``Seconds``/``Frame``.  A Python node group cannot add
outputs to the native node, so this group re-exposes the native outputs and
carries two scene-synced value nodes for the start/end frames.  The values are
refreshed by ``sync_scene_time`` from ``frame_change_pre``/``render_pre``; node
group values are shared, which is correct because frame ranges are per scene.

The native ``Active Camera`` override input is C++ (``scene->gn_camera_override``)
and has no Python entry point.  ``GOO Active Camera`` (``nodes/camera.py``)
reproduces the same decision with an OBJECT switch group; see that module for the
two inputs (``Ignore Scene Override``, ``Override Camera``).
"""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from ..utils import gout, iface, link, log, node, out_sock
from .registry import group

GROUP_NAME = "GOO Scene Time"
START_VALUE = "GOO Start Frame"
END_VALUE = "GOO End Frame"


@group(
    GROUP_NAME,
    tree_type="GeometryNodeTree",
    category="geometry",
    label=GROUP_NAME,
    description=(
        "Approximates GOO's Scene Time node: native Seconds/Frame plus scene Start Frame "
        "and End Frame values, refreshed by a frame handler. / 近似 GOO 的 Scene Time："
        "保留 Seconds/Frame，并附加由帧处理函数同步的起始/结束帧。"
    ),
    version=1,
    featured_inputs=(),
)
def build_scene_time(tree: bpy.types.NodeTree) -> None:
    iface(tree, "Seconds", "OUTPUT", "NodeSocketFloat", description="Scene time in seconds / 秒")
    iface(tree, "Frame", "OUTPUT", "NodeSocketFloat", description="Current frame / 当前帧")
    iface(tree, "Start Frame", "OUTPUT", "NodeSocketFloat", description="Scene start frame / 起始帧")
    iface(tree, "End Frame", "OUTPUT", "NodeSocketFloat", description="Scene end frame / 结束帧")

    scene_time = node(tree, "GeometryNodeInputSceneTime", loc=(-520.0, 160.0), label="Scene Time")
    start = node(tree, "ShaderNodeValue", loc=(-520.0, -60.0), label="Start Frame", name=START_VALUE)
    end = node(tree, "ShaderNodeValue", loc=(-520.0, -220.0), label="End Frame", name=END_VALUE)
    gout(tree, (520.0, 0.0))

    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        start.outputs[0].default_value = float(scene.frame_start)
        end.outputs[0].default_value = float(scene.frame_end)

    link(tree, scene_time.outputs["Seconds"], out_sock(tree, "Seconds"))
    link(tree, scene_time.outputs["Frame"], out_sock(tree, "Frame"))
    link(tree, start.outputs[0], out_sock(tree, "Start Frame"))
    link(tree, end.outputs[0], out_sock(tree, "End Frame"))

    log("GOO Scene Time: Seconds/Frame from Scene Time, Start/End refreshed from scene by handler")


def sync_scene_time(scene: bpy.types.Scene | None = None) -> int:
    """Write ``scene.frame_start``/``frame_end`` into every GOO Scene Time tree.

    Returns the number of node trees that were updated.  Safe to call from a
    frame handler; it only touches two ``ShaderNodeValue`` outputs.
    """
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return 0

    updated = 0
    for tree in bpy.data.node_groups:
        if tree.name != GROUP_NAME:
            continue
        start = tree.nodes.get(START_VALUE)
        end = tree.nodes.get(END_VALUE)
        if start is None or end is None:
            continue
        start.outputs[0].default_value = float(scene.frame_start)
        end.outputs[0].default_value = float(scene.frame_end)
        updated += 1
    return updated


@persistent
def _frame_change(scene: bpy.types.Scene, depsgraph=None) -> None:
    sync_scene_time(scene)


@persistent
def _render_pre(scene: bpy.types.Scene, depsgraph=None) -> None:
    sync_scene_time(scene)


def register() -> None:
    handlers = bpy.app.handlers
    if _frame_change not in handlers.frame_change_pre:
        handlers.frame_change_pre.append(_frame_change)
    if _render_pre not in handlers.render_pre:
        handlers.render_pre.append(_render_pre)


def unregister() -> None:
    handlers = bpy.app.handlers
    if _frame_change in handlers.frame_change_pre:
        handlers.frame_change_pre.remove(_frame_change)
    if _render_pre in handlers.render_pre:
        handlers.render_pre.remove(_render_pre)


__all__ = ["GROUP_NAME", "build_scene_time", "register", "sync_scene_time", "unregister"]
