# SPDX-License-Identifier: GPL-3.0-or-later
"""Serializable render job and result models."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RenderJob:
    scene: str
    output: str
    camera: str = ""
    view_layer: str = ""
    samples: int = 32
    resolution: int = 100
    frame: str = "1"
    format: str = "PNG"
    engine: str = ""
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RenderJob":
        known = {
            "scene",
            "output",
            "camera",
            "view_layer",
            "samples",
            "resolution",
            "frame",
            "format",
            "engine",
            "job_id",
            "created",
            "metadata",
        }
        return cls(**{key: value for key, value in data.items() if key in known})

    def write(self, path: Path) -> Path:
        atomic_write_text(
            path,
            json.dumps(self.to_dict(), indent=2, ensure_ascii=True) + "\n",
        )
        return path

    @classmethod
    def read(cls, path: Path) -> "RenderJob":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class RenderResult:
    ok: bool
    job_id: str = ""
    files: list[str] = field(default_factory=list)
    log: str = ""
    error: str = ""
    duration: float = 0.0
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def failure(cls, error: str, job_id: str = "", log: str = "") -> "RenderResult":
        return cls(ok=False, job_id=job_id, error=error, log=log)
