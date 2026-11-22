# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry of every node group the GOO port ships.

Each feature module registers one :class:`GroupSpec` per node group.  The
registry drives:

* the *Add > GOO Engine* node menu,
* the *GOO Node Library* panel,
* the ``goo apply``/``goo sync`` command that materialises groups in a file,
* the self test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import bpy

from .. import i18n
from ..utils import ensure_group, log

#: Categories used by the Add menu and the node library panel.
CATEGORIES = ("shader", "textures", "geometry", "compositor", "utility")

CATEGORY_LABELS = {
    "shader": {"en_US": "Shading", "zh_CN": "着色"},
    "textures": {"en_US": "Textures", "zh_CN": "纹理"},
    "geometry": {"en_US": "Geometry", "zh_CN": "几何"},
    "compositor": {"en_US": "Compositing", "zh_CN": "合成"},
    "utility": {"en_US": "Utilities", "zh_CN": "工具"},
}


@dataclass(frozen=True)
class GroupSpec:
    """Description of one generated node group."""

    name: str
    builder: Callable[[bpy.types.NodeTree], None]
    tree_type: str = "ShaderNodeTree"
    category: str = "shader"
    label: str = ""
    description: str = ""
    version: int = 1
    #: Optional: socket names treated as "main" inputs for compact UI previews.
    featured_inputs: tuple[str, ...] = field(default_factory=tuple)

    def label_text(self) -> str:
        return self.label or self.name

    def category_label(self) -> str:
        labels = CATEGORY_LABELS.get(self.category, {})
        return labels.get(i18n.current_language()) or labels.get("en_US") or self.category


_REGISTRY: list[GroupSpec] = []


def register(spec: GroupSpec) -> GroupSpec:
    """Register a node group specification (idempotent by name)."""
    if spec.category not in CATEGORIES:
        raise ValueError(f"unknown category {spec.category!r} for {spec.name!r}")
    existing = [s for s in _REGISTRY if s.name == spec.name]
    if existing:
        _REGISTRY[_REGISTRY.index(existing[0])] = spec
    else:
        _REGISTRY.append(spec)
    return spec


def group(
    name: str,
    *,
    tree_type: str = "ShaderNodeTree",
    category: str = "shader",
    label: str = "",
    description: str = "",
    version: int = 1,
    featured_inputs: tuple[str, ...] = (),
):
    """Decorator variant of :func:`register` for builders."""

    def decorator(builder: Callable[[bpy.types.NodeTree], None]) -> Callable[[bpy.types.NodeTree], None]:
        register(
            GroupSpec(
                name=name,
                builder=builder,
                tree_type=tree_type,
                category=category,
                label=label,
                description=description,
                version=version,
                featured_inputs=featured_inputs,
            )
        )
        return builder

    return decorator


def all_specs() -> list[GroupSpec]:
    return list(_REGISTRY)


def specs_for_category(category: str) -> list[GroupSpec]:
    return [s for s in _REGISTRY if s.category == category]


def specs_for_tree(tree_type: str) -> list[GroupSpec]:
    return [s for s in _REGISTRY if s.tree_type == tree_type]


def find_spec(name: str) -> Optional[GroupSpec]:
    for spec in _REGISTRY:
        if spec.name == name:
            return spec
    return None


def ensure_all(force: bool = False, only: Iterable[str] | None = None) -> dict[str, bpy.types.NodeTree]:
    """Materialise every registered node group (or a subset by name)."""
    wanted = set(only) if only else None
    built: dict[str, bpy.types.NodeTree] = {}
    for spec in _REGISTRY:
        if wanted is not None and spec.name not in wanted:
            continue
        try:
            built[spec.name] = ensure_group(
                spec.name,
                spec.tree_type,
                spec.builder,
                version=spec.version,
                force=force,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log(f"failed to build node group {spec.name!r}: {exc!r}")
            raise
    return built


def summary() -> list[dict[str, str]]:
    """Return a JSON friendly summary for the CLI/doctor."""
    return [
        {
            "name": s.name,
            "category": s.category,
            "tree_type": s.tree_type,
            "version": str(s.version),
            "description": s.description,
        }
        for s in _REGISTRY
    ]
