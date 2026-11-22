# SPDX-License-Identifier: GPL-3.0-or-later
"""External asset inventory shared by analysis, local and remote rendering."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Asset:
    path: str
    kind: str = ""
    name: str = ""
    exists: bool = False
    size: int = 0
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: Path, *, use_cache: bool = True, cache_store: Path | None = None) -> str:
    if use_cache:
        from .cache import get_asset_hash

        cached = get_asset_hash(path, cache_store)
        if cached:
            return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    if use_cache:
        from .cache import put_asset_hash

        put_asset_hash(path, value, cache_store)
    return value


def _from_item(item: dict[str, Any], cache_store: Path | None = None) -> Asset | None:
    path = str(item.get("path") or "")
    if not path:
        return None
    candidate = Path(path).expanduser()
    exists = candidate.is_file()
    return Asset(
        path=str(candidate),
        kind=str(item.get("kind") or ""),
        name=str(item.get("name") or ""),
        exists=exists,
        size=candidate.stat().st_size if exists else 0,
        sha256=sha256_file(candidate, cache_store=cache_store) if exists else "",
    )


def from_analysis(report: dict[str, Any], *, cache_store: Path | None = None) -> list[Asset]:
    items = [
        item
        for item in (report.get("external_files") or [])
        if isinstance(item, dict) and str(item.get("path") or "")
    ]
    if len(items) <= 2:
        return [asset for asset in (_from_item(item, cache_store) for item in items) if asset]

    # Large scenes can reference hundreds of textures.  hashlib releases the
    # GIL while hashing, so a bounded thread pool cuts wall-clock time without
    # changing ordering or error behaviour.
    workers = min(8, max(1, (os.cpu_count() or 1)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [asset for asset in pool.map(lambda item: _from_item(item, cache_store), items) if asset]


def missing(assets: Iterable[Asset]) -> list[Asset]:
    return [asset for asset in assets if not asset.exists]


def manifest(assets: Iterable[Asset]) -> dict[str, Any]:
    items = [asset.to_dict() for asset in assets]
    return {
        "version": 1,
        "assets": items,
        "missing": [item["path"] for item in items if not item["exists"]],
        "total_bytes": sum(int(item["size"]) for item in items),
    }
