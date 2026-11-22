# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Port command line tool.

The package is **stdlib only** and must never import ``bpy``: every operation
that needs Blender is delegated to ``blender.exe --background`` and a script
from :mod:`goo.scripts`.

Entry points
------------
``python -m goo ...``  (source checkout)
``goo ...``            (after ``pip install -e .``)
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "1.0.0"
CLI_NAME = "goo"

PACKAGE_DIR = Path(__file__).resolve().parent
#: Repository root (the directory that contains ``goo/`` and ``goo_npr/``).
REPO_ROOT = PACKAGE_DIR.parent
#: Directory holding the Blender side scripts.
SCRIPTS_DIR = PACKAGE_DIR / "scripts"


def addon_root() -> Path:
    """Directory that holds the ``goo_npr`` package."""
    override = os.environ.get("GOO_ADDON_PATH")
    if override:
        candidate = Path(override)
        if candidate.name == "goo_npr":
            return candidate.parent
        return candidate
    return REPO_ROOT


def addon_dir() -> Path:
    return addon_root() / "goo_npr"


__all__ = [
    "__version__",
    "CLI_NAME",
    "PACKAGE_DIR",
    "REPO_ROOT",
    "SCRIPTS_DIR",
    "addon_dir",
    "addon_root",
]
