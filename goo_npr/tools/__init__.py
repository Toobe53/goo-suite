# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Port tools: Grease Pencil helpers, F-Curve isolation, line width."""

from __future__ import annotations

from . import fcurve_isolate, gp_tools, pose_tools, viewport_linewidth

#: registration order (unregistration runs in reverse)
MODULES = (gp_tools, fcurve_isolate, viewport_linewidth, pose_tools)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()


__all__ = [
    "MODULES",
    "fcurve_isolate",
    "gp_tools",
    "pose_tools",
    "viewport_linewidth",
    "register",
    "unregister",
]
