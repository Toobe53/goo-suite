"""Measure the pixel content of a `goo render` output directory.

Runs *inside* Blender (it needs numpy and Blender's image loader), so it is not
part of the `goo` CLI package. It is a helper for verifying that per-light-group
passes really differ and that compositing reconstructs the all-lights render.

Usage (from the repository root):

    blender --factory-startup --background --python tools/analyze_passes.py -- \
        <render-dir> [<all-lights-beauty.exr>]

The optional second argument is a single all-lights reference render (make one
with `goo render scene.blend -o ref --no-light-groups`); when given, the tool
reports how closely the per-group passes reconstruct it.
"""

import glob
import json
import os
import sys

import numpy as np


def load(path):
    """Return (h, w, c) float32 pixels, top-down, and whether they are sRGB."""
    import bpy

    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    ch = img.channels
    buf = np.empty(w * h * ch, dtype=np.float32)
    img.pixels.foreach_get(buf)
    pixels = buf.reshape(h, w, ch)[::-1].copy()  # bottom-up -> top-down
    is_srgb = (not img.is_float) and img.colorspace_settings.name != "Non-Color"
    bpy.data.images.remove(img)
    return pixels, is_srgb


def to_linear(pixels, is_srgb):
    rgb = pixels[:, :, :3].astype(np.float64)
    if is_srgb:
        rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return rgb


def luminance(rgb):
    return 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]


def describe(path):
    pixels, is_srgb = load(path)
    rgb = to_linear(pixels, is_srgb)
    lum = luminance(rgb)
    return {
        "file": os.path.basename(path),
        "shape": (pixels.shape[0], pixels.shape[1]),
        "srgb": is_srgb,
        "rgb": rgb,
        "mean_rgb": rgb.reshape(-1, 3).mean(axis=0),
        "mean_luminance": float(lum.mean()),
        "max_luminance": float(lum.max()),
        "min_luminance": float(lum.min()),
    }


def pass_files(render_dir):
    manifest = os.path.join(render_dir, "manifest.json")
    files = []
    if os.path.isfile(manifest):
        with open(manifest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("passes", []):
            kind = entry.get("kind")
            if kind in ("light_group", "ambient", "beauty") and entry.get("path"):
                if os.path.isfile(entry["path"]):
                    files.append((kind, entry.get("group"), entry["path"]))
    if not files:
        for path in sorted(glob.glob(os.path.join(render_dir, "*_beauty.*"))):
            kind = "ambient" if "ambient" in os.path.basename(path) else "light_group"
            files.append((kind, os.path.basename(path).split("_beauty")[0], path))
        plain = glob.glob(os.path.join(render_dir, "beauty.*"))
        if plain:
            files.append(("beauty", None, plain[0]))
    return files


def main(argv):
    if len(argv) < 1:
        print("usage: blender -b --python tools/analyze_passes.py -- <render-dir> [all-lights.exr]")
        return 2
    render_dir = os.path.abspath(argv[0])
    reference = os.path.abspath(argv[1]) if len(argv) > 1 else None
    if not os.path.isdir(render_dir):
        print("not a directory: %s" % render_dir)
        return 2

    results = []
    print("render dir: %s" % render_dir)
    print("%-16s %-8s %-10s %-10s %-10s %s" % ("file", "kind", "mean lum", "max lum", "R-B", "mean RGB"))
    for kind, group, path in pass_files(render_dir):
        info = describe(path)
        info["kind"] = kind
        info["group"] = group
        results.append(info)
        print(
            "%-16s %-8s %-10.6f %-10.6f %-+10.4f %s"
            % (
                info["file"],
                kind,
                info["mean_luminance"],
                info["max_luminance"],
                info["mean_rgb"][0] - info["mean_rgb"][2],
                "[" + ", ".join("%.6f" % v for v in info["mean_rgb"]) + "]",
            )
        )

    groups = [r for r in results if r["kind"] == "light_group"]
    ambient = next((r for r in results if r["kind"] == "ambient"), None)
    if len(groups) >= 2:
        a, b = groups[0], groups[1]
        print(
            "\ngroup difference: %s lum %.6f vs %s lum %.6f  ratio %.3f"
            % (a["file"], a["mean_luminance"], b["file"], b["mean_luminance"],
               a["mean_luminance"] / max(b["mean_luminance"], 1e-9))
        )

    if reference and os.path.isfile(reference):
        ref = describe(reference)
        if groups:
            total = np.zeros_like(groups[0]["rgb"])
            for r in groups:
                total = total + r["rgb"]
            if ambient is not None and len(groups) > 1:
                total = total - (len(groups) - 1) * ambient["rgb"]
            total = np.clip(total, 0.0, None)
            diff = np.abs(total - ref["rgb"])
            print("\nreference: %s  mean lum %.6f" % (ref["file"], ref["mean_luminance"]))
            print(
                "reconstruction vs reference: mean abs %.8f max abs %.8f  relative %.4f %%"
                % (diff.mean(), diff.max(), 100.0 * diff.mean() / max(ref["mean_rgb"].mean(), 1e-9))
            )
            naive = sum(r["rgb"] for r in groups)
            ndiff = np.abs(naive - ref["rgb"])
            print(
                "naive sum (no ambient removal): mean abs %.8f  (%.1fx worse)"
                % (ndiff.mean(), ndiff.mean() / max(diff.mean(), 1e-12))
            )
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    raise SystemExit(main(argv))
