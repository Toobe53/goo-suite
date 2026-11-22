# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify release hashes and archive members."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(path: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def verify_dist(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    sums_path = directory / "SHA256SUMS"
    result: dict[str, Any] = {"ok": True, "files": [], "checks": [], "errors": []}
    if not sums_path.is_file():
        result["ok"] = False
        result["errors"].append(f"missing SHA256SUMS: {sums_path}")
        return result
    sums = read_sums(sums_path)
    if not sums:
        result["ok"] = False
        result["errors"].append("SHA256SUMS is empty")
        return result
    for name, expected in sums.items():
        path = directory / name
        if not path.is_file():
            result["ok"] = False
            result["errors"].append(f"missing file: {name}")
            continue
        actual = sha256(path)
        ok = actual == expected
        result["files"].append(name)
        result["checks"].append({"file": name, "ok": ok, "expected": expected, "actual": actual})
        if not ok:
            result["ok"] = False
            result["errors"].append(f"hash mismatch: {name} (expected {expected}, got {actual})")
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                result["checks"][-1]["entries"] = len(names)
            except zipfile.BadZipFile as exc:
                result["ok"] = False
                result["errors"].append(f"bad archive {name}: {exc}")
    return result
