# SPDX-License-Identifier: GPL-3.0-or-later
"""Error types and exit codes used by the GOO CLI.

Exit codes
----------
0   success
1   unexpected internal error (bug)
2   usage / configuration error
3   Blender 5.2+ was not found
4   a Blender job failed (script error, render error, bad blend, ...)
"""

from __future__ import annotations

from typing import Iterable, Optional

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_NO_BLENDER = 3
EXIT_JOB_FAILED = 4


class GooError(Exception):
    """Base class for all user facing CLI errors.

    ``hint`` carries one actionable sentence (English, optionally followed by a
    short Chinese hint) that the CLI prints under the error line.
    """

    code = EXIT_JOB_FAILED
    kind = "error"

    def __init__(self, message: str, hint: Optional[str] = None, code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code

    def as_dict(self) -> dict:
        data = {"ok": False, "error": self.message, "kind": self.kind, "exit_code": self.code}
        if self.hint:
            data["hint"] = self.hint
        return data


class UsageError(GooError):
    """Bad command line arguments or a configuration value we cannot use."""

    code = EXIT_USAGE
    kind = "usage"


class ConfigError(UsageError):
    kind = "config"


class BlenderNotFound(GooError):
    """No usable Blender 5.2+ installation was found."""

    code = EXIT_NO_BLENDER
    kind = "blender_missing"


class JobFailed(GooError):
    """A Blender subprocess exited non zero or reported ``ok: false``."""

    code = EXIT_JOB_FAILED
    kind = "job_failed"

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        code: Optional[int] = None,
        *,
        stderr_tail: str = "",
        stdout_tail: str = "",
        result: Optional[dict] = None,
        returncode: Optional[int] = None,
    ):
        super().__init__(message, hint, code)
        self.stderr_tail = stderr_tail or ""
        self.stdout_tail = stdout_tail or ""
        self.result = result or {}
        self.returncode = returncode

    def as_dict(self) -> dict:
        data = super().as_dict()
        if self.returncode is not None:
            data["returncode"] = self.returncode
        if self.stderr_tail:
            data["stderr_tail"] = self.stderr_tail
        if self.result:
            data["result"] = self.result
        return data


def tail_lines(text: str, count: int = 30) -> str:
    """Return the last *count* non-empty trailing lines of *text*."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line for line in lines if line.strip()]
    return "\n".join(lines[-count:])


def join_hints(*hints: Optional[str]) -> Optional[str]:
    out: list[str] = []
    for hint in hints:
        if hint and hint not in out:
            out.append(hint)
    return " | ".join(out) if out else None


def format_items(items: Iterable[str], limit: int = 8) -> str:
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f", ... (+{len(items) - limit})"
