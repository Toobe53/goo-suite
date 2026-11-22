# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side worker for remote GOO Bridge render jobs.

The host uploads a bundle and starts::

    blender --background scene.blend --python remote_worker.py -- --job job.json

This script renders the requested frames into ``job.json``'s ``output``
directory, writes ``status.json`` while running, and finishes with
``result.json`` plus one ``GOO_REMOTE_RESULT:`` line on stdout.  It only needs
the Python bundled with Blender; no third-party modules are imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

RESULT_MARKER = "GOO_REMOTE_RESULT:"
STATUS_NAME = "status.json"
RESULT_NAME = "result.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _emit(payload: dict[str, Any], result_path: Path) -> None:
    """Persist the result and print the marker line the host parses."""
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    try:
        _write_json(result_path, payload)
    except OSError as exc:
        print(f"GOO_REMOTE_WARNING: cannot write {result_path}: {exc}", file=sys.stderr)
    print(f"{RESULT_MARKER} {text}", flush=True)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="remote_worker.py")
    parser.add_argument("--job", required=True, help="path to job.json")
    parser.add_argument("--result", default="", help="override result.json path")
    parser.add_argument("--status", default="", help="override status.json path")
    return parser.parse_args(list(argv))


def _parse_frames(value: str) -> list[int]:
    value = str(value or "1").strip()
    if not value:
        return [1]
    frames: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            first, last = int(start), int(end)
            step = 1 if last >= first else -1
            frames.extend(range(first, last + step, step))
        else:
            frames.append(int(part))
    return sorted(set(frames)) or [1]


def _extension(fmt: str) -> str:
    return {
        "PNG": ".png",
        "JPEG": ".jpg",
        "JPG": ".jpg",
        "OPEN_EXR": ".exr",
        "EXR": ".exr",
        "TIFF": ".tif",
        "BMP": ".bmp",
    }.get(fmt.upper(), ".png")


def _file_format(fmt: str) -> str:
    fmt = str(fmt or "PNG").upper()
    return {"EXR": "OPEN_EXR", "JPG": "JPEG"}.get(fmt, fmt)


def _resolve(job_dir: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else job_dir / path


def _render(bpy: Any, job: dict[str, Any], job_path: Path, started: float) -> dict[str, Any]:
    job_dir = job_path.parent
    scene = bpy.context.scene

    camera_name = str(job.get("camera") or "").strip()
    if camera_name:
        camera = bpy.data.objects.get(camera_name)
        if camera is None or camera.type != "CAMERA":
            raise RuntimeError(f"camera object not found: {camera_name!r}")
        scene.camera = camera
    if scene.camera is None:
        raise RuntimeError("scene has no camera; set one in the .blend or pass --camera")

    layer = None
    layer_name = str(job.get("view_layer") or "").strip()
    if layer_name:
        layer = scene.view_layers.get(layer_name)
        if layer is None:
            available = [item.name for item in scene.view_layers]
            raise RuntimeError(f"view layer not found: {layer_name!r}; available: {available}")

    samples = int(job.get("samples") or 0)
    if samples > 0:
        if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = samples
        if hasattr(scene, "cycles"):
            scene.cycles.samples = samples

    resolution = int(job.get("resolution") or 100)
    scene.render.resolution_percentage = max(1, min(1000, resolution))

    image_settings = scene.render.image_settings
    image_settings.file_format = _file_format(str(job.get("format") or "PNG"))
    if image_settings.file_format == "OPEN_EXR":
        # file_format first: the depth enum depends on the selected format.
        image_settings.color_depth = "32"
        image_settings.color_mode = "RGBA"
    scene.render.use_file_extension = False

    engine = str(job.get("engine") or "").strip()
    if engine:
        scene.render.engine = engine

    output_dir = _resolve(job_dir, str(job.get("output") or "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = _parse_frames(str(job.get("frame") or "1"))
    extension = _extension(image_settings.file_format)

    files: list[dict[str, Any]] = []
    for frame in frames:
        scene.frame_set(frame)
        filename = output_dir / f"render_{frame:04d}{extension}"
        scene.render.filepath = str(filename)
        bpy.ops.render.render(write_still=True)
        if not filename.is_file():
            raise RuntimeError(f"Blender did not write {filename}")
        if filename.is_relative_to(job_dir):
            relative = filename.relative_to(job_dir).as_posix()
        else:
            raise RuntimeError(
                f"output file {filename} is outside the job directory {job_dir}; "
                "set job.output to a relative path"
            )
        files.append(
            {
                "name": filename.name,
                "path": relative,
                "frame": frame,
                "size": filename.stat().st_size,
                "sha256": _sha256(filename),
            }
        )

    return {
        "ok": True,
        "job_id": str(job.get("job_id") or ""),
        "files": files,
        "duration": round(time.time() - started, 3),
        "settings": {
            "camera": scene.camera.name if scene.camera else "",
            "view_layer": layer.name if layer else "",
            "samples": samples,
            "resolution": resolution,
            "format": image_settings.file_format,
            "frames": frames,
            "engine": scene.render.engine,
            "file_extension": False,
        },
        "blender": {
            "version": ".".join(str(part) for part in bpy.app.version),
            "version_string": bpy.app.version_string,
            "background": bool(bpy.app.background),
        },
        "host": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
        },
        "finished": _utc_now(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    job_path = Path(args.job).expanduser().resolve()
    result_path = (
        Path(args.result).expanduser().resolve() if args.result else job_path.parent / RESULT_NAME
    )
    status_path = (
        Path(args.status).expanduser().resolve() if args.status else job_path.parent / STATUS_NAME
    )
    started = time.time()

    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _emit(
            {
                "ok": False,
                "job_id": "",
                "error": f"cannot read job.json: {exc}",
                "duration": round(time.time() - started, 3),
            },
            result_path,
        )
        return 1
    if not isinstance(job, dict):
        _emit(
            {"ok": False, "job_id": "", "error": "job.json is not an object", "duration": 0.0},
            result_path,
        )
        return 1
    job_id = str(job.get("job_id") or "")

    try:
        import bpy  # noqa: F401  (only available inside Blender)
    except Exception as exc:  # pragma: no cover - only when run outside Blender
        _emit(
            {
                "ok": False,
                "job_id": job_id,
                "error": f"remote_worker.py must run inside Blender: {exc}",
                "duration": round(time.time() - started, 3),
            },
            result_path,
        )
        return 1

    try:
        _write_json(
            status_path,
            {
                "state": "running",
                "job_id": job_id,
                "pid": os.getpid(),
                "started": _utc_now(),
                "blender": bpy.app.version_string,
                "scene": job.get("scene", ""),
            },
        )
        payload = _render(bpy, job, job_path, started)
        _write_json(
            status_path,
            {
                "state": "completed",
                "job_id": job_id,
                "finished": payload.get("finished", _utc_now()),
                "duration": payload.get("duration", 0.0),
                "files": len(payload.get("files") or []),
                "blender": payload.get("blender", {}),
            },
        )
    except Exception as exc:
        trace = traceback.format_exc()
        payload = {
            "ok": False,
            "job_id": job_id,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": trace[-4000:],
            "duration": round(time.time() - started, 3),
            "finished": _utc_now(),
        }
        try:
            _write_json(
                status_path,
                {
                    "state": "failed",
                    "job_id": job_id,
                    "error": payload["error"],
                    "finished": payload["finished"],
                },
            )
        except OSError:
            pass
        _emit(payload, result_path)
        return 1

    _emit(payload, result_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover - last resort so the host sees a failure
        traceback.print_exc()
        raise SystemExit(1)
