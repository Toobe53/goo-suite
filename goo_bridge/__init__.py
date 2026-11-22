# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Bridge: a standalone GUI/CLI around Blender for GOO-style NPR rendering.

The package deliberately uses only the Python standard library.  Blender is
treated as an external render engine and is started as a subprocess; it is
never imported or modified.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
