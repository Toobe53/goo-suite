# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side still/frame renderer used by GOO Bridge.

Invoked as::

    blender --background scene.blend --python render_scene.py -- --job job.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback


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
    fmt = fmt.upper()
    return {"PNG": ".png", "JPEG": ".jpg", "JPG": ".jpg", "OPEN_EXR": ".exr", "EXR": ".exr", "TIFF": ".tif"}.get(
        fmt, ".png"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])

    import bpy

    started = time.time()
    with open(args.job, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    scene = bpy.context.scene

    camera_name = str(job.get("camera") or "").strip()
    if camera_name:
        camera = bpy.data.objects.get(camera_name)
        if camera is None or camera.type != "CAMERA":
            raise RuntimeError(f"camera object not found: {camera_name!r}")
        scene.camera = camera

    layer_name = str(job.get("view_layer") or "").strip()
    layer = None
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
    scene.render.image_settings.file_format = str(job.get("format") or "PNG").upper()
    if scene.render.image_settings.file_format == "OPEN_EXR":
        scene.render.image_settings.color_depth = "32"
        scene.render.image_settings.color_mode = "RGBA"

    output = os.path.abspath(str(job["output"]))
    os.makedirs(output, exist_ok=True)
    frames = _parse_frames(str(job.get("frame") or "1"))
    files: list[str] = []
    base = os.path.join(output, "render")
    for frame in frames:
        scene.frame_set(frame)
        filename = f"{base}_{frame:04d}{_extension(scene.render.image_settings.file_format)}"
        scene.render.filepath = filename
        bpy.ops.render.render(write_still=True)
        files.append(filename)

    manifest = {
        "ok": True,
        "job_id": job.get("job_id", ""),
        "files": files,
        "duration": round(time.time() - started, 3),
        "settings": {
            "camera": scene.camera.name if scene.camera else "",
            "view_layer": layer.name if layer else "",
            "samples": samples,
            "resolution": resolution,
            "format": scene.render.image_settings.file_format,
            "frames": frames,
            "engine": scene.render.engine,
        },
    }
    with open(os.path.join(output, "render_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=True)
    print("RENDER_OK", output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
