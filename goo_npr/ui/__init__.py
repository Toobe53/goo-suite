# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Port UI package: node editor and 3D Viewport sidebar panels."""

from __future__ import annotations

from . import panels


def register():
    panels.register()


def unregister():
    panels.unregister()


__all__ = ["panels", "register", "unregister"]
