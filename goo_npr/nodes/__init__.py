# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Port node group layer.

Importing this package imports every feature module so the ``@group``
decorators register their :class:`goo_npr.nodes.registry.GroupSpec`.  No group
is *built* here: building happens through ``registry.ensure_all()`` which the
add-on calls after Blender is ready (see ``build_all``).

Feature modules
---------------
shading.py      GOO Shader Info, GOO Curvature, GOO Light Info, GOO Set Depth
light_info_new.py GOO Light Info New (drivers: color/power/position/rotation/distance)
procedural.py   GOO Twirl, GOO Water Ripples, GOO Hex Grid
sdf.py          GOO SDF Primitive / Op / Vector Op / Noise
oklab.py        GOO OKLab Mix, GOO OKLab Color Ramp
geometry.py     GOO Scene Time (GeometryNodeTree)
camera.py       GOO Active Camera (GeometryNodeTree, camera override switch)
screen_space.py GOO Screen Space Info
utility.py      GOO Depth Fade, GOO Threshold Mask
"""

from __future__ import annotations

from . import registry, shading, light_info_new, procedural, sdf, oklab, geometry, camera, screen_space, utility

#: Feature modules in registration order (order controls the panel order).
MODULES = (shading, light_info_new, procedural, sdf, oklab, geometry, camera, screen_space, utility)

#: Operator classes are registered by the feature modules themselves.
CLASSES: tuple[type, ...] = ()


def register() -> None:
    """Register operators declared by the feature modules."""
    for module in MODULES:
        register_fn = getattr(module, "register", None)
        if register_fn is not None:
            register_fn()


def unregister() -> None:
    """Unregister operators declared by the feature modules (reverse order)."""
    for module in reversed(MODULES):
        unregister_fn = getattr(module, "unregister", None)
        if unregister_fn is not None:
            unregister_fn()


def build_all(force: bool = False) -> dict:
    """Build every registered group (delegates to ``registry.ensure_all``)."""
    return registry.ensure_all(force=force)


def group_names() -> tuple[str, ...]:
    """Names of every registered group spec, in registration order."""
    return tuple(spec.name for spec in registry.all_specs())


__all__ = [
    "CLASSES",
    "MODULES",
    "build_all",
    "camera",
    "group_names",
    "geometry",
    "light_info_new",
    "procedural",
    "oklab",
    "register",
    "registry",
    "sdf",
    "screen_space",
    "shading",
    "unregister",
    "utility",
]
