# SPDX-License-Identifier: GPL-3.0-or-later
"""``python -m goo`` entry point.

``pyproject.toml`` maps the ``goo`` console script to ``goo.__main__:main``, so
this module has to expose a zero-argument ``main()`` returning a process exit
code.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
