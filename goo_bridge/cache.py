# SPDX-License-Identifier: GPL-3.0-or-later
"""Small SQLite cache for scene analysis and external asset hashes.

Blender startup dominates analysis on repeated runs.  The cache key includes
the scene path, size, mtime and Blender version, so editing either side
invalidates the entry.  Asset hashes use size + mtime (and are re-hashed when
either changes); the cache is an optimisation, never a source of truth.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .config import home_directory


def cache_path() -> Path:
    override = os.environ.get("GOO_SUITE_CACHE")
    if override:
        return Path(override).expanduser()
    return home_directory() / "cache.sqlite3"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS analysis ("
        "key TEXT PRIMARY KEY, report TEXT NOT NULL, updated REAL NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS asset ("
        "path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
        "sha256 TEXT NOT NULL, updated REAL NOT NULL)"
    )
    return connection


def _stat_key(path: Path, blender_version: str = "") -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{blender_version}"


def get_analysis(path: Path, blender_version: str = "", store: Path | None = None) -> dict[str, Any] | None:
    key = _stat_key(path, blender_version)
    try:
        with closing(_connect(store)) as db:
            row = db.execute("SELECT report FROM analysis WHERE key = ?", (key,)).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if not row:
        return None
    try:
        report = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def put_analysis(path: Path, report: dict[str, Any], blender_version: str = "", store: Path | None = None) -> None:
    key = _stat_key(path, blender_version)
    try:
        with closing(_connect(store)) as db:
            db.execute(
                "INSERT OR REPLACE INTO analysis (key, report, updated) VALUES (?, ?, ?)",
                (key, json.dumps(report, ensure_ascii=True), time.time()),
            )
    except (OSError, sqlite3.Error):
        pass


def get_asset_hash(path: Path, store: Path | None = None) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        with closing(_connect(store)) as db:
            row = db.execute(
                "SELECT sha256 FROM asset WHERE path = ? AND size = ? AND mtime_ns = ?",
                (str(path.resolve()), stat.st_size, stat.st_mtime_ns),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    return str(row[0]) if row else None


def put_asset_hash(path: Path, sha256: str, store: Path | None = None) -> None:
    try:
        stat = path.stat()
    except OSError:
        return
    try:
        with closing(_connect(store)) as db:
            db.execute(
                "INSERT OR REPLACE INTO asset (path, size, mtime_ns, sha256, updated) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(path.resolve()), stat.st_size, stat.st_mtime_ns, sha256, time.time()),
            )
    except (OSError, sqlite3.Error):
        pass


def clear(store: Path | None = None) -> None:
    try:
        with closing(_connect(store)) as db:
            db.execute("DELETE FROM analysis")
            db.execute("DELETE FROM asset")
    except (OSError, sqlite3.Error):
        pass
