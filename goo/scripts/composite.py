# SPDX-License-Identifier: GPL-3.0-or-later
"""Combine per-light-group beauty renders into a single image inside Blender.

Run with Blender because reading/writing image pixels needs ``bpy`` and the
add-on's numpy.  Everything is done in **linear** space:

1. read every light-group beauty listed in ``manifest.json`` (EXR is linear;
   PNG inputs are decoded from sRGB first),
2. add them together,
3. subtract ``(groups - 1) * ambient`` so the world light is not counted once
   per group (``ambient_beauty.exr`` from ``render_passes.py``),
4. optionally multiply by the ``shadow.exr`` / ``shadow_cast.exr`` matte,
5. apply exposure (stops) and contrast around 0.18,
6. write ``composite.exr`` (32-bit float) and optionally ``composite.png``
   (8-bit sRGB, written by hand because ``Image.save()`` was unreliable).

Job keys::

    out_dir          required, directory holding manifest.json and the passes
    png              write composite.png (default false)
    exr              write composite.exr (default true)
    exposure         stops, default 0.0
    contrast         default 1.0
    apply_shadow     multiply by the shadow pass when present (default false)
    subtract_ambient remove duplicated world light (default true)
    basename         default "composite"
"""

from __future__ import annotations

import json
import os
import sys

# Blender's --python does not add the script directory to sys.path; do it so
# the sibling _common module is importable in every invocation style.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402


def main(job: dict) -> None:
    np = C.numpy()
    timer = C.Timer()
    out_dir = job.get("out_dir")
    if not out_dir:
        raise RuntimeError("job is missing 'out_dir'")
    out_dir = os.path.abspath(str(out_dir))
    if not os.path.isdir(out_dir):
        raise RuntimeError(f"output directory does not exist: {out_dir}")

    warnings: list[str] = []
    manifest = _load_manifest(out_dir, warnings)
    entries = _collect_inputs(out_dir, manifest, warnings)
    if not entries["groups"] and not entries["beauty"]:
        raise RuntimeError(
            f"no beauty passes found in {out_dir}; run `goo render` first "
            "没有找到可合成的渲染通道，请先运行 goo render"
        )

    basename = C.safe_name(str(job.get("basename") or "composite"), "composite")
    exposure = float(job.get("exposure") or 0.0)
    contrast = float(job.get("contrast") if job.get("contrast") is not None else 1.0)
    write_exr = bool(job.get("exr", True))
    write_png = bool(job.get("png", False))
    if not write_exr and not write_png:
        raise RuntimeError("nothing to write: both EXR and PNG output are disabled")
    apply_shadow = bool(job.get("apply_shadow", False))
    subtract_ambient = bool(job.get("subtract_ambient", True))

    layers: list[dict] = []
    for entry in entries["groups"] or entries["beauty"]:
        layers.append(_load_layer(entry, warnings))
    if not layers:
        raise RuntimeError("no readable beauty images")

    shape = layers[0]["pixels"].shape
    for layer in layers[1:]:
        if layer["pixels"].shape != shape:
            raise RuntimeError(
                f"image size mismatch: {layer['file']} is {layer['pixels'].shape[1]}x{layer['pixels'].shape[0]} "
                f"but {layers[0]['file']} is {shape[1]}x{shape[0]} / 图像尺寸不一致"
            )
    height, width, channels = shape

    group_count = len([layer for layer in layers if layer["kind"] == "light_group"])
    additive = np.zeros((height, width, 4), dtype=np.float32)
    for layer in layers:
        additive[:, :, :3] += layer["pixels"][:, :, :3]
        additive[:, :, 3] = np.maximum(additive[:, :, 3], layer["pixels"][:, :, 3])
    result = additive
    info: dict = {
        "additive_layers": [layer["file"] for layer in layers],
        "group_count": group_count,
        "ambient_subtracted": False,
        "shadow_applied": False,
        "shadow_files": [],
        "single_beauty": not entries["groups"],
    }

    ambient = entries.get("ambient")
    if subtract_ambient and ambient and group_count > 1:
        amb = _load_layer(ambient, warnings)
        if amb["pixels"].shape != shape:
            warnings.append(f"ambient pass has a different size and was ignored: {amb['file']}")
        else:
            factor = float(group_count - 1)
            result = result.copy()
            result[:, :, :3] = np.maximum(result[:, :, :3] - amb["pixels"][:, :, :3] * factor, 0.0)
            info["ambient_subtracted"] = True
            info["ambient_factor"] = factor

    if apply_shadow:
        for shadow_file in entries["shadow"]:
            layer = _load_layer(shadow_file, warnings)
            if layer["pixels"].shape != shape:
                warnings.append(f"shadow pass has a different size and was ignored: {layer['file']}")
                continue
            mask = np.clip(layer["pixels"][:, :, :3], 0.0, 1.0)
            mean = float(mask.mean())
            if mean <= 1e-4:
                warnings.append(f"shadow pass is black and was ignored: {layer['file']}")
                continue
            if mean >= 0.9999:
                warnings.append(f"shadow pass is fully white (no shadows) and was ignored: {layer['file']}")
                continue
            result = result.copy()
            result[:, :, :3] = result[:, :, :3] * mask
            info["shadow_applied"] = True
            info["shadow_files"].append(layer["file"])
            info["shadow_mean"] = round(mean, 6)
    elif entries["shadow"]:
        warnings.append(
            "shadow pass(es) found but not applied; pass --shadow to composite with them "
            "发现 shadow 通道但未使用，可加 --shadow 启用"
        )

    if exposure:
        result = result.copy()
        result[:, :, :3] = result[:, :, :3] * float(2.0 ** exposure)
    if contrast and contrast != 1.0:
        result = result.copy()
        pivot = 0.18
        rgb = result[:, :, :3]
        rgb = np.maximum(rgb, 0.0)
        result[:, :, :3] = np.maximum(((rgb / pivot - 1.0) * contrast + 1.0) * pivot, 0.0)

    result[:, :, :3] = np.nan_to_num(result[:, :, :3], nan=0.0, posinf=0.0, neginf=0.0)
    result[:, :, 3] = np.clip(np.nan_to_num(result[:, :, 3], nan=0.0), 0.0, 1.0)

    outputs: dict = {}
    if write_exr:
        exr_path = os.path.join(out_dir, f"{basename}.exr")
        C.save_exr(exr_path, result)
        outputs["exr"] = exr_path
        C.log(f"wrote {exr_path}")
    if write_png:
        png_path = os.path.join(out_dir, f"{basename}.png")
        C.save_png(png_path, result, apply_srgb=True)
        outputs["png"] = png_path
        C.log(f"wrote {png_path}")

    out_stats = C.image_stats(outputs.get("exr") or outputs["png"])
    payload = {
        "ok": True,
        "tool": "composite",
        "blend": _blend_path(),
        "out_dir": out_dir,
        "outputs": outputs,
        "inputs": [
            {
                "file": layer["file"],
                "kind": layer["kind"],
                "group": layer.get("group"),
                "format": layer["format"],
                "mean_luminance": layer["mean_luminance"],
            }
            for layer in layers
        ],
        "settings": {
            "exposure": exposure,
            "contrast": contrast,
            "apply_shadow": apply_shadow,
            "subtract_ambient": subtract_ambient,
            "exr": write_exr,
            "png": write_png,
            "basename": basename,
        },
        "combination": info,
        "stats": out_stats,
        "manifest": manifest.get("manifest") if manifest else None,
        "warnings": warnings,
        "seconds": round(timer.elapsed, 3),
    }
    C.write_manifest(out_dir, payload, job, name=f"{basename}_manifest.json")
    C.emit(payload, job)


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------
def _load_manifest(out_dir: str, warnings: list[str]) -> dict | None:
    path = os.path.join(out_dir, "manifest.json")
    if not os.path.isfile(path):
        warnings.append("manifest.json not found; falling back to file name patterns")
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("passes"), list):
            return data
        warnings.append("manifest.json is not a render manifest; falling back to file name patterns")
    except (OSError, ValueError) as exc:
        warnings.append(f"could not read manifest.json ({exc}); falling back to file name patterns")
    return None


def _collect_inputs(out_dir: str, manifest: dict | None, warnings: list[str]) -> dict:
    groups: list[dict] = []
    beauty: list[dict] = []
    ambient: dict | None = None
    shadows: list[dict] = []
    if manifest:
        for entry in manifest.get("passes") or []:
            if not isinstance(entry, dict):
                continue
            filename = entry.get("file") or ""
            path = entry.get("path") or os.path.join(out_dir, filename)
            if not os.path.isfile(path):
                warnings.append(f"missing pass file listed in manifest: {filename}")
                continue
            item = {
                "file": os.path.basename(path),
                "path": path,
                "format": entry.get("format") or _format_of(path),
                "kind": entry.get("kind") or "light_group",
                "group": entry.get("group"),
            }
            kind = item["kind"]
            if kind == "light_group":
                groups.append(item)
            elif kind == "beauty":
                beauty.append(item)
            elif kind == "ambient":
                ambient = item
            elif kind == "generic" and item["group"] is None and entry.get("name") == "shadow":
                shadows.append(item)
    if not groups and not beauty:
        # Fallback: name patterns, deterministic order.
        for filename in sorted(os.listdir(out_dir)):
            path = os.path.join(out_dir, filename)
            if not os.path.isfile(path):
                continue
            lowered = filename.lower()
            if not lowered.endswith((".exr", ".png")):
                continue
            stem, _ = os.path.splitext(filename)
            item = {"file": filename, "path": path, "format": _format_of(path), "kind": "light_group", "group": None}
            if stem == "ambient_beauty":
                ambient = item
            elif stem == "beauty":
                item["kind"] = "beauty"
                beauty.append(item)
            elif stem.endswith("_beauty"):
                item["group"] = stem[: -len("_beauty")]
                groups.append(item)
            elif stem in ("shadow", "shadow_cast"):
                shadows.append(item)
    for extra in ("shadow.exr", "shadow_cast.exr"):
        path = os.path.join(out_dir, extra)
        if os.path.isfile(path) and not any(shadow["path"] == path for shadow in shadows):
            shadows.append(
                {"file": extra, "path": path, "format": _format_of(path), "kind": "generic", "group": None}
            )
    return {"groups": groups, "beauty": beauty, "ambient": ambient, "shadow": shadows}


def _format_of(path: str) -> str:
    return "png" if path.lower().endswith(".png") else "exr"


def _load_layer(entry: dict, warnings: list[str]) -> dict:
    pixels, is_srgb = C.load_image(entry["path"])
    if is_srgb:
        pixels = pixels.copy()
        pixels[:, :, :3] = C.srgb_to_linear(pixels[:, :, :3])
        warnings.append(f"{entry['file']} was an 8/16-bit image; decoded from sRGB to linear")
    if pixels.shape[2] == 3:
        import numpy as np

        alpha = np.ones((pixels.shape[0], pixels.shape[1], 1), dtype=np.float32)
        pixels = np.concatenate([pixels, alpha], axis=2)
    rgb = pixels[:, :, :3]
    luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    return {
        **entry,
        "pixels": pixels,
        "mean_luminance": round(float(luminance.mean()), 6),
    }


def _blend_path() -> str | None:
    try:
        import bpy

        return bpy.data.filepath or None
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    C.run_main(main, C.load_job())
