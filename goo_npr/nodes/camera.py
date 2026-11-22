# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera helpers matching GOO's GN camera override.

GOO 4.4 adds an ``Ignore Scene Override`` input to the native *Active Camera*
geometry node and reads ``scene->gn_camera_override``.  Blender 5.2 exposes
neither the scene field nor a way to inject the native node's input from
Python, so the port ships a small replacement group instead:

``GOO Active Camera`` (GeometryNodeTree)
    inputs  : ``Ignore Scene Override`` (bool, default True), ``Override Camera`` (Object)
    output  : ``Active Camera`` (Object)

With the default ``True`` the group passes through Blender's own *Active Camera*
node (i.e. ``scene.camera``).  Switch it off and wire a camera object into
``Override Camera`` to make geometry nodes use that camera without touching the
render camera.  The exact GOO behaviour (override applied globally by the native
node, including nodes created without this group) needs the C++ patch described
in ``docs/native-extension.md``.
"""

from __future__ import annotations

import bpy

from .. import i18n
from ..i18n_strings import tr
from .. import utils
from .registry import group

GROUP_NAME = "GOO Active Camera"

# Localized labels shown in the node group's interface.  These live next to the
# builder because the group is the only consumer; ``i18n_strings`` remains the
# shared table for the UI layer.
STRINGS = {
    "goo.node.active_camera.switch": {
        "en_US": "Override switch",
        "zh_CN": "覆盖开关",
    },
    "goo.node.active_camera.not": {
        "en_US": "Use override?",
        "zh_CN": "使用覆盖？",
    },
    "goo.node.active_camera.ignore.desc": {
        "en_US": "True (default) uses scene.camera; False uses Override Camera",
        "zh_CN": "开启（默认）使用场景相机；关闭则使用覆盖相机",
    },
    "goo.node.active_camera.override.desc": {
        "en_US": "Camera object used when Ignore Scene Override is off",
        "zh_CN": "关闭“忽略场景覆盖”时使用的相机对象",
    },
    "goo.node.active_camera.out.desc": {
        "en_US": "Camera object selected by the switch",
        "zh_CN": "开关选出的相机对象",
    },
}
i18n.register(STRINGS)


def _build_active_camera(tree):
    utils.gin(tree)

    active = utils.node(tree, "GeometryNodeInputActiveCamera", loc=(-700, 160))
    switch = utils.node(tree, "GeometryNodeSwitch", loc=(-240, 120))
    switch.input_type = "OBJECT"
    switch.label = tr("goo.node.active_camera.switch", "Override switch")

    ignore = utils.node(tree, "FunctionNodeBooleanMath", loc=(-700, -120))
    ignore.operation = "NOT"
    ignore.label = tr("goo.node.active_camera.not", "Use override?")

    utils.connect_input(tree, utils.in_sock(tree, "Ignore Scene Override"), ignore, "Boolean")
    utils.connect_input(tree, utils.in_sock(tree, "Override Camera"), switch, "True")
    utils.link(tree, active.outputs[0], switch.inputs["False"])
    utils.link(tree, ignore.outputs[0], switch.inputs["Switch"])
    utils.link(tree, switch.outputs[0], utils.out_sock(tree, "Active Camera"))


@group(
    GROUP_NAME,
    tree_type="GeometryNodeTree",
    category="geometry",
    label=GROUP_NAME,
    description=(
        "Active camera with an optional override object (GOO camera override "
        "approximation). / 带覆盖对象的活动相机（GOO camera override 近似实现）。"
    ),
    version=1,
    featured_inputs=("Ignore Scene Override", "Override Camera"),
)
def build(tree):
    utils.iface(
        tree,
        "Ignore Scene Override",
        "INPUT",
        "NodeSocketBool",
        description=tr(
            "goo.node.active_camera.ignore.desc",
            "True (default) uses scene.camera; False uses Override Camera",
        ),
        default=True,
    )
    utils.iface(
        tree,
        "Override Camera",
        "INPUT",
        "NodeSocketObject",
        description=tr(
            "goo.node.active_camera.override.desc",
            "Camera object used when Ignore Scene Override is off",
        ),
    )
    utils.iface(
        tree,
        "Active Camera",
        "OUTPUT",
        "NodeSocketObject",
        description=tr(
            "goo.node.active_camera.out.desc", "Camera object selected by the switch"
        ),
    )
    _build_active_camera(tree)


CLASSES = ()


def register():
    pass


def unregister():
    pass


__all__ = ["GROUP_NAME", "build", "register", "unregister"]
