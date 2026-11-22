# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the release artifacts in ``dist/`` against ``SHA256SUMS``.

The check is deliberately dependency free and works on the *shipped* files,
not on a source checkout: it hashes every entry listed in ``SHA256SUMS`` and
then opens each archive to confirm the files a user actually needs are inside.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

#: Core members that must be present in each archive.  Matched by filename
#: prefix so a future version bump does not need a code change.
REQUIRED_MEMBERS = {
    "goo_npr-": (
        "goo_npr/__init__.py",
        "goo_npr/nodes/camera.py",
        "goo_npr/tools/pose_tools.py",
    ),
    "goo_suite-": (
        "goo/__main__.py",
        "goo/cli.py",
        "goo/verify.py",
        "goo_npr/__init__.py",
        "goo_npr/nodes/camera.py",
        "goo_npr/tools/pose_tools.py",
        "goo_bridge/cli.py",
        "goo_bridge/remote.py",
    ),
    "goo-suite-": (
        "README.md",
        "pyproject.toml",
        "goo/verify.py",
        "goo_npr/nodes/camera.py",
        "goo_npr/tools/pose_tools.py",
        "goo_bridge/cli.py",
        "tests/test_addon.py",
        "docs/features.md",
    ),
    # Old names remain verifiable for one release cycle.
    "goo_port-": (
        "goo/__main__.py",
        "goo/verify.py",
        "goo_npr/__init__.py",
        "goo_npr/nodes/camera.py",
    ),
    "goo-port-": (
        "README.md",
        "pyproject.toml",
        "goo/verify.py",
        "goo_npr/nodes/camera.py",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_for(name: str) -> tuple[str, ...]:
    for prefix, members in REQUIRED_MEMBERS.items():
        if name.startswith(prefix):
            return members
    return ()


def _read_sums(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"malformed SHA256SUMS line: {line!r}")
        entries.append((parts[0].lower(), parts[-1]))
    return entries


def verify_dist(dist: Path | str) -> dict:
    """Return a JSON-serialisable verification report for *dist*."""
    dist = Path(dist)
    checks: list[dict] = []
    errors: list[str] = []

    sums_path = dist / "SHA256SUMS"
    if not sums_path.is_file():
        return {
            "ok": False,
            "command": "verify",
            "dist": str(dist),
            "checks": [],
            "errors": [f"SHA256SUMS not found: {sums_path}"],
        }

    try:
        entries = _read_sums(sums_path)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "command": "verify",
            "dist": str(dist),
            "checks": [],
            "errors": [str(exc)],
        }

    if not entries:
        errors.append(f"{sums_path} lists no files")

    for expected, name in entries:
        path = dist / name
        detail = {"name": name, "sha256": expected, "present": path.is_file()}
        if not path.is_file():
            detail["ok"] = False
            errors.append(f"missing file: {name}")
            checks.append(detail)
            continue

        actual = _sha256(path)
        detail["actual"] = actual
        detail["bytes"] = path.stat().st_size
        detail["ok"] = actual == expected
        if not detail["ok"]:
            errors.append(f"hash mismatch: {name} (expected {expected}, got {actual})")

        if path.suffix in (".zip", ".whl"):
            try:
                with zipfile.ZipFile(path) as archive:
                    names = set(archive.namelist())
                required = _required_for(name)
                missing = [member for member in required if member not in names]
                detail["entries"] = len(names)
                detail["required"] = list(required)
                if missing:
                    detail["ok"] = False
                    errors.append(f"{name} is missing: {', '.join(missing)}")
            except (OSError, zipfile.BadZipFile) as exc:
                detail["ok"] = False
                errors.append(f"{name} is not a valid archive: {exc}")

        checks.append(detail)

    return {
        "ok": not errors,
        "command": "verify",
        "dist": str(dist),
        "files": len(entries),
        "checks": checks,
        "errors": errors,
    }
