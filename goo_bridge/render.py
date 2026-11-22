# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a render job through a local Blender installation."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from .blender import BlenderInfo, find_blender, run_blender
from .jobs import RenderJob, RenderResult


def render_script() -> Path:
    return Path(__file__).resolve().parent / "scripts" / "render_scene.py"


def render(
    scene: Path | str,
    output: Path | str,
    *,
    camera: str = "",
    view_layer: str = "",
    samples: int = 32,
    resolution: int = 100,
    frame: str = "1",
    format: str = "PNG",
    blender: BlenderInfo | None = None,
    timeout: int = 3600,
) -> RenderResult:
    scene_path = Path(scene).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if not scene_path.is_file():
        return RenderResult.failure(f"scene not found: {scene_path}")
    info = blender or find_blender()
    script = render_script()
    if not script.is_file():
        return RenderResult.failure(f"render script missing: {script}")
    output_path.mkdir(parents=True, exist_ok=True)
    job = RenderJob(
        scene=str(scene_path),
        output=str(output_path),
        camera=camera,
        view_layer=view_layer,
        samples=samples,
        resolution=resolution,
        frame=str(frame),
        format=format,
    )
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="goo-bridge-render-") as temp:
        job_path = job.write(Path(temp) / "job.json")
        result = run_blender(
            info,
            ["--background", str(scene_path), "--python", str(script), "--", "--job", str(job_path)],
            timeout=timeout,
        )
        if result.returncode != 0:
            log = (result.stderr or result.stdout or "").strip()
            return RenderResult(
                ok=False,
                job_id=job.job_id,
                error=f"Blender render failed (exit {result.returncode})",
                log=log[-4000:],
                duration=round(time.time() - started, 3),
                settings=job.to_dict(),
            )
        manifest_path = output_path / "render_manifest.json"
        if not manifest_path.is_file():
            return RenderResult(
                ok=False,
                job_id=job.job_id,
                error="Blender finished without render_manifest.json",
                log=(result.stdout or "")[-4000:],
                duration=round(time.time() - started, 3),
                settings=job.to_dict(),
            )
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        missing = [path for path in manifest.get("files", []) if not Path(path).is_file()]
        if missing:
            return RenderResult(
                ok=False,
                job_id=job.job_id,
                files=list(manifest.get("files", [])),
                error=f"render reported missing files: {missing[:3]}",
                log=(result.stdout or "")[-4000:],
                duration=round(time.time() - started, 3),
                settings=manifest.get("settings", job.to_dict()),
            )
        return RenderResult(
            ok=True,
            job_id=job.job_id,
            files=list(manifest.get("files", [])),
            log=(result.stdout or "")[-4000:],
            duration=float(manifest.get("duration") or round(time.time() - started, 3)),
            settings=manifest.get("settings", job.to_dict()),
        )
