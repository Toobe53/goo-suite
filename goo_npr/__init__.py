# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO NPR — a portable re-implementation of DillonGoo GOO Engine features.

The add-on is intentionally self contained (pure Python, no compiled extension)
so it can be dropped into any Blender 5.2 installation.  It ships:

* procedurally generated node groups that re-create GOO's node based look
  development (Shader Info, Curvature, Screen Space Info, Light Info,
  Set Depth and the Twirl / Water Ripples / Hex Grid textures),
* GOO style *Light Groups* built on top of Blender 5.2's native light linking,
* quality of life tools (light group passes, Grease Pencil helpers, F-Curve
  isolation, viewport line width, procedural compositor effects).

The matching external command line tool lives in the ``goo`` Python package of
the same repository and drives Blender head-less for multi pass rendering,
baking and batch processing.
"""

from __future__ import annotations

import traceback

import bpy

from .utils import safe_unregister_all

ADDON_VERSION = (1, 0, 0)

bl_info = {
    "name": "GOO NPR (GOO Engine port)",
    "author": "GOO Port contributors",
    # Blender parses bl_info with literal-only AST rules: a name reference
    # here makes addon_utils reject the whole add-on ("AST error parsing
    # bl_info"), so the literal tuple is required.  Keep ADDON_VERSION in sync.
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": (
        "Node Editor > Add > GOO Engine · Properties > Scene > GOO Light Groups · "
        "3D Viewport > Sidebar > GOO"
    ),
    "description": (
        "GOO Engine style NPR node groups, light groups and tooling for Blender 5.2 "
        "(portable, pure Python)"
    ),
    "warning": "",
    "category": "Render",
}

#: Minimum Blender version this port supports.  Blender 5.2 introduced the
#: compositor node group API and the light linking attributes used here.
MIN_BLENDER_VERSION = (5, 2, 0)

#: Modules registered in order.  Each module exposes ``register()`` and
#: ``unregister()``.
_MODULES: list[str] = [
    "preferences",
    "i18n_strings",
    "light_groups",
    "nodes",
    "add_menu",
    "ui.panels",
    "tools.gp_tools",
    "tools.fcurve_isolate",
    "tools.viewport_linewidth",
    "tools.pose_tools",
    "compositor",
    "overrides",
]

_REGISTERED: list[str] = []


def _module_name(short: str) -> str:
    return f"{__name__}.{short}"


def version_supported() -> bool:
    return tuple(bpy.app.version) >= MIN_BLENDER_VERSION


def _data_ready() -> bool:
    """True when ``bpy.data`` is past the restricted-startup stage.

    Blender calls add-on ``register()`` while loading startup files, when
    ``bpy.data`` is a ``_RestrictData`` proxy without ``node_groups``.  Node
    groups cannot be built there, so the startup build is deferred to a timer.
    """
    try:
        return hasattr(bpy.data, "node_groups")
    except Exception:
        return False


def _deferred_build():
    """Timer callback: build the library once Blender finished starting up."""
    try:
        if _data_ready():
            reload_node_groups(force=False)
    except Exception:
        traceback.print_exc()
    return None


def reload_node_groups(force: bool = True) -> dict:
    """Rebuild every GOO node group (used by operators and the CLI)."""
    from .nodes import registry

    return registry.ensure_all(force=force)


def _register_modules() -> None:
    for short in _MODULES:
        mod_name = _module_name(short)
        try:
            module = __import__(mod_name, fromlist=["register"])
        except Exception:
            print(f"[goo] could not import {mod_name}")
            traceback.print_exc()
            continue
        register = getattr(module, "register", None)
        if register is None:
            continue
        try:
            register()
            _REGISTERED.append(mod_name)
        except Exception:
            print(f"[goo] register() failed for {mod_name}")
            traceback.print_exc()


def _unregister_modules() -> None:
    for mod_name in reversed(_REGISTERED):
        module = __import__(mod_name, fromlist=["unregister"])
        unregister = getattr(module, "unregister", None)
        if unregister is None:
            continue
        try:
            unregister()
        except Exception:
            print(f"[goo] unregister() failed for {mod_name}")
            traceback.print_exc()
    _REGISTERED.clear()


class GOO_OT_build_library(bpy.types.Operator):
    """Create or refresh all GOO node groups in this file"""

    bl_idname = "goo.build_library"
    bl_label = "Rebuild GOO Node Library"
    bl_options = {"REGISTER", "UNDO"}

    force: bpy.props.BoolProperty(name="Force rebuild", default=True)

    def execute(self, context):
        from .nodes import registry

        try:
            built = registry.ensure_all(force=self.force)
        except Exception as exc:
            self.report({"ERROR"}, f"GOO: failed to build node groups: {exc}")
            traceback.print_exc()
            return {"CANCELLED"}
        self.report({"INFO"}, f"GOO: {len(built)} node groups ready")
        return {"FINISHED"}


CLASSES = (GOO_OT_build_library,)


def register() -> None:
    if not version_supported():
        version = ".".join(str(v) for v in bpy.app.version)
        required = ".".join(str(v) for v in MIN_BLENDER_VERSION)
        raise RuntimeError(
            f"GOO NPR requires Blender {required} or newer, this is {version}. "
            f"Install the add-on into Blender 5.2+ / GOO NPR 需要 Blender 5.2 或更高版本"
        )
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _register_modules()

    from .preferences import get_prefs

    prefs = get_prefs()
    if prefs is not None and prefs.build_on_startup:
        if _data_ready():
            try:
                reload_node_groups(force=False)
            except Exception:
                traceback.print_exc()
        else:
            # Restricted-startup context: defer until bpy.data is real.
            bpy.app.timers.register(_deferred_build, first_interval=0.25)


def unregister() -> None:
    _unregister_modules()
    safe_unregister_all(CLASSES)
