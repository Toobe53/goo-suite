# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the scripts that run *inside* Blender.

This module must be importable by Blender's bundled Python (3.13) and only uses
the standard library plus ``bpy``/``numpy`` which Blender itself provides.
The CLI never imports it.

Communication contract
----------------------
The CLI invokes ``blender ... --python <script> -- <job.json>``.  Every script:

1. loads the job JSON,
2. does its work,
3. writes ``{"ok": true, ...}`` to ``job["result_file"]`` **and** prints a line
   ``GOO_RESULT: {...}`` (the printed line is a fallback; the file wins because
   Blender may print extra lines after the script finishes),
4. on error writes ``{"ok": false, "error": ..., "hint": ..., "traceback": ...}``
   and exits with code 4 so ``--python-exit-code`` agrees with the payload.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

RESULT_PREFIX = "GOO_RESULT:"
EXIT_JOB_FAILED = 4


# ---------------------------------------------------------------------------
# job handling / result emission
# ---------------------------------------------------------------------------
def load_job() -> dict:
    """Load the JSON job passed after ``--`` on Blender's command line."""
    argv = sys.argv
    try:
        sep = argv.index("--")
    except ValueError:
        raise SystemExit("goo: this script must be run with a job JSON after '--'")
    rest = argv[sep + 1 :]
    if not rest:
        raise SystemExit("goo: missing job JSON path after '--'")
    path = rest[0]
    with open(path, "r", encoding="utf-8-sig") as handle:
        job = json.load(handle)
    if not isinstance(job, dict):
        raise SystemExit(f"goo: job file {path} does not contain a JSON object")
    job.setdefault("job_path", path)
    return job


def _dumps(payload: dict, *, compact: bool = False) -> str:
    if compact:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_default)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default)


def _json_default(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    return repr(value)


def emit(payload: dict, job: dict | None = None, exit_code: int = 0) -> None:
    """Write the result file (if any) and print the GOO_RESULT line."""
    payload = dict(payload)
    result_file = (job or {}).get("result_file")
    if result_file:
        try:
            directory = os.path.dirname(os.path.abspath(result_file))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(result_file, "w", encoding="utf-8") as handle:
                handle.write(_dumps(payload))
        except OSError as exc:  # pragma: no cover - defensive
            log(f"warning: could not write result file: {exc}")
    print(RESULT_PREFIX + " " + _dumps(payload, compact=True), flush=True)
    if exit_code:
        raise SystemExit(exit_code)


def write_manifest(out_dir: str, manifest: dict, job: dict | None = None, *, name: str = "manifest.json") -> str:
    """Write ``manifest.json`` (or *name*) next to the rendered images."""
    path = os.path.join(str(out_dir), name)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_dumps(manifest) + "\n")
    os.replace(temporary, path)
    return path


def fail(error: str, hint: str | None = None, job: dict | None = None, *, traceback_text: str | None = None) -> None:
    payload = {"ok": False, "error": str(error)}
    if hint:
        payload["hint"] = str(hint)
    if traceback_text:
        payload["traceback"] = traceback_text
    emit(payload, job, exit_code=EXIT_JOB_FAILED)


def run_main(main, job: dict | None = None) -> None:
    """Run *main(job)* and turn exceptions into structured failures."""
    job = job or {}
    try:
        main(job)
    except SystemExit as exc:  # allow deliberate exits
        if exc.code not in (0, None):
            raise
    except Exception as exc:  # noqa: BLE001 - report everything to the CLI
        fail(f"{type(exc).__name__}: {exc}", _hint_for(exc), job, traceback_text=traceback.format_exc())
        raise


def _hint_for(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "no space left" in lowered or "disk is full" in lowered:
        return "Free some disk space / 磁盘空间不足，请清理后重试"
    if "permission denied" in lowered:
        return "Close the file in other programs or pick another output folder / 请关闭占用文件的程序或更换输出目录"
    if "light_groups" in lowered:
        return "The goo_npr add-on may be incomplete; run `goo install` / 插件可能不完整，请运行 goo install"
    return ""


# ---------------------------------------------------------------------------
# logging / timing
# ---------------------------------------------------------------------------
def log(message: str) -> None:
    print(f"[goo] {message}", flush=True)


class Timer:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._start

    def lap(self) -> float:
        now = time.perf_counter()
        delta = now - self._start
        self._start = now
        return delta


# ---------------------------------------------------------------------------
# add-on import
# ---------------------------------------------------------------------------
def addon_root(job: dict) -> str | None:
    root = job.get("addon_root")
    if root:
        return str(root)
    env = os.environ.get("GOO_ADDON_PATH")
    return env or None


def import_addon(job: dict, *, required: bool = True):
    """Import ``goo_npr`` from the repo root (or the installed add-on)."""
    root = addon_root(job)
    if root and os.path.isdir(root) and root not in sys.path:
        sys.path.insert(0, root)
    try:
        import goo_npr  # noqa: F401  (imported for side effects / availability)
    except Exception as exc:  # noqa: BLE001
        if required:
            raise RuntimeError(
                f"cannot import goo_npr ({exc}); pass --addon or run `goo install` "
                "如果失败请运行 goo install 安装插件"
            ) from exc
        return None
    return goo_npr


def ensure_addon_registered(goo_npr, *, required: bool = True) -> bool:
    """Register the add-on's classes in this (headless) Blender session.

    ``--factory-startup`` disables every add-on, so a .blend saved with GOO
    light-group data would otherwise fail with ``AttributeError:
    'Scene' object has no attribute 'goo_light_groups'``.  Registering once is
    cheap and makes the CLI behave exactly like a GUI session with the add-on
    enabled.  Returns True when registration actually ran.
    """
    import bpy
    import addon_utils  # noqa: F401  (part of Blender's Python, not the CLI)

    if hasattr(bpy.types.Scene, "goo_light_groups"):
        return False
    if not hasattr(goo_npr, "register"):
        if required:
            raise RuntimeError(
                "goo_npr.register() is missing; reinstall the add-on "
                "插件不完整，请运行 goo install"
            )
        return False
    try:
        goo_npr.register()
    except Exception as exc:  # noqa: BLE001
        if required:
            raise RuntimeError(
                f"goo_npr.register() failed ({type(exc).__name__}: {exc}); "
                "run `goo install` / 请运行 goo install 安装插件"
            ) from exc
        return False
    # Some modules are optional while the add-on is being developed; make sure
    # the light-group properties we need are actually there.
    if required and not hasattr(bpy.types.Scene, "goo_light_groups"):
        raise RuntimeError(
            "goo_npr registered but light groups are unavailable; "
            "reinstall the add-on / 插件缺少灯组模块，请重新安装"
        )
    return True


# ---------------------------------------------------------------------------
# filesystem helpers (kept tiny; the CLI has the full versions)
# ---------------------------------------------------------------------------
_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def safe_name(name: str, fallback: str = "default") -> str:
    original = (name or "").strip()
    cleaned = "".join(ch if ch in _SAFE_CHARS else "_" for ch in original)
    cleaned = cleaned.strip("._-") or fallback
    return cleaned


def ensure_dir(path: str) -> str:
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def blend_path() -> str | None:
    import bpy  # local import: only valid inside Blender

    return bpy.data.filepath or None


def scene_or_fail(scene_name: str | None = None):
    import bpy

    if scene_name:
        scene = bpy.data.scenes.get(scene_name)
        if scene is None:
            raise RuntimeError(f"scene not found: {scene_name}")
        return scene
    scene = bpy.context.scene or (bpy.data.scenes[0] if bpy.data.scenes else None)
    if scene is None:
        raise RuntimeError("the blend file contains no scene")
    return scene


# ---------------------------------------------------------------------------
# GOO node group detection (shared by inspect / apply_library / composite)
# ---------------------------------------------------------------------------
def goo_keys() -> list[str]:
    """Custom-property keys the add-on stamps onto its node groups."""
    try:
        from goo_npr import utils

        keys = [str(getattr(utils, "GOO_ID", "goo_port"))]
        for attr in ("GOO_VERSION_KEY", "GOO_BUILDER_KEY", "GOO_SIGNATURE_KEY"):
            value = getattr(utils, attr, None)
            if isinstance(value, str) and value not in keys:
                keys.append(value)
        return keys
    except Exception:  # noqa: BLE001
        return ["goo_port"]


def registry_names() -> set[str]:
    """Names of every node group registered by the add-on (empty if unavailable)."""
    try:
        from goo_npr.nodes import registry

        return {spec.name for spec in registry.all_specs()}
    except Exception:  # noqa: BLE001
        return set()


def is_goo_group(group, keys: list[str] | None = None, names: set[str] | None = None) -> bool:
    if group is None:
        return False
    keys = keys if keys is not None else goo_keys()
    names = registry_names() if names is None else names
    try:
        if group.name in names:
            return True
        if str(group.name).startswith("GOO_"):
            return True
        return any(group.get(key) is not None for key in keys)
    except (AttributeError, TypeError, KeyError):
        return False


def goo_groups() -> list:
    """Every node group in the file that looks like it belongs to GOO."""
    import bpy

    keys = goo_keys()
    names = registry_names()
    return [group for group in bpy.data.node_groups if is_goo_group(group, keys, names)]


# ---------------------------------------------------------------------------
# render settings
# ---------------------------------------------------------------------------
def configure_render(scene, job: dict) -> dict:
    """Apply render overrides from the job and return the final settings."""
    render = scene.render
    settings: dict = {}

    percentage = job.get("resolution_percentage")
    if percentage not in (None, ""):
        render.resolution_percentage = max(1, min(1000, int(percentage)))

    samples = job.get("samples")
    if samples not in (None, ""):
        scene.eevee.taa_render_samples = max(1, min(8192, int(samples)))

    transparent = job.get("film_transparent")
    if transparent is not None:
        render.film_transparent = bool(transparent)

    camera_name = job.get("camera")
    if camera_name:
        import bpy

        camera = bpy.data.objects.get(str(camera_name))
        if camera is None or camera.type != "CAMERA":
            raise ValueError(
                f"--camera: no camera object named {camera_name!r} "
                f"(available: {[o.name for o in bpy.data.objects if o.type == 'CAMERA']})"
            )
        scene.camera = camera
        settings["camera"] = camera.name

    if job.get("burn_in"):
        # GOO Engine's "Render burn-in" is a native stamp in its fork; Blender's
        # own render stamp gives the same result for PNG output without touching
        # the renderer.  Keep it to the two values GOO burns in: blend file name
        # and frame.  The font scales with the output height so 128 px previews
        # stay readable instead of being covered by 18 px text.
        import bpy

        blend_name = os.path.basename(bpy.data.filepath) or "<unsaved>"
        render.use_stamp = True
        render.use_stamp_note = True
        render.use_stamp_scene = False
        render.use_stamp_filename = False
        render.use_stamp_frame = False
        for flag in (
            "use_stamp_date",
            "use_stamp_time",
            "use_stamp_render_time",
            "use_stamp_frame_range",
            "use_stamp_memory",
            "use_stamp_hostname",
            "use_stamp_camera",
            "use_stamp_lens",
            "use_stamp_marker",
            "use_stamp_sequencer_strip",
        ):
            if hasattr(render, flag):
                setattr(render, flag, False)
        render.stamp_note_text = f"{blend_name}  |  frame {scene.frame_current}"
        output_height = int(
            render.resolution_y * max(1, int(render.resolution_percentage)) / 100.0
        )
        render.stamp_font_size = max(8, min(28, int(output_height * 0.035)))
        render.stamp_foreground = (1.0, 1.0, 1.0, 1.0)
        render.stamp_background = (0.0, 0.0, 0.0, 0.6)
        settings["burn_in"] = True

    fmt = str(job.get("format") or "exr").lower()
    settings["format"] = fmt
    if fmt == "png":
        render.image_settings.file_format = "PNG"
        render.image_settings.color_depth = "16" if job.get("png_16bit") else "8"
        render.image_settings.color_mode = "RGBA"
        render.use_file_extension = True
        try:
            scene.view_settings.view_transform = "Standard"
        except (AttributeError, TypeError):
            pass
    else:
        render.image_settings.file_format = "OPEN_EXR"
        render.image_settings.color_depth = "32"
        render.image_settings.color_mode = "RGBA"
        render.image_settings.exr_codec = "ZIP"
        render.use_file_extension = True

    render.use_compositing = False
    render.use_sequencer = False

    settings.update(
        {
            "engine": render.engine,
            "resolution_x": int(render.resolution_x * render.resolution_percentage / 100.0),
            "resolution_y": int(render.resolution_y * render.resolution_percentage / 100.0),
            "resolution_percentage": int(render.resolution_percentage),
            "samples": int(scene.eevee.taa_render_samples),
            "film_transparent": bool(render.film_transparent),
            "image_settings": {
                "file_format": render.image_settings.file_format,
                "color_depth": render.image_settings.color_depth,
                "color_mode": render.image_settings.color_mode,
            },
            "view_transform": getattr(scene.view_settings, "view_transform", None),
        }
    )
    return settings


# ---------------------------------------------------------------------------
# numpy / image IO (imported lazily so import-only scripts stay cheap)
# ---------------------------------------------------------------------------
def numpy():
    import numpy

    return numpy


def linear_to_srgb(values):
    np = numpy()
    values = np.clip(values, 0.0, 1.0)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * np.power(values, 1.0 / 2.4) - 0.055)


def srgb_to_linear(values):
    np = numpy()
    values = np.clip(values, 0.0, 1.0)
    return np.where(values <= 0.04045, values / 12.92, np.power((values + 0.055) / 1.055, 2.4))


def load_image(path: str):
    """Return ``(pixels, is_srgb)`` where pixels is ``(h, w, channels)`` float32.

    Row 0 is the bottom row (Blender's convention).  Float images are stored
    linear; 8/16-bit images are stored with an sRGB transfer function and must
    be decoded by the caller.
    """
    import bpy

    np = numpy()
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        if width == 0 or height == 0:
            raise RuntimeError(f"image has no pixels: {path}")
        channels = 4 if image.channels == 4 else image.channels
        count = width * height * channels
        buffer = np.empty(count, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        pixels = buffer.reshape(height, width, channels).copy()
        is_srgb = not image.is_float and image.depth <= 32
        return pixels, bool(is_srgb)
    finally:
        bpy.data.images.remove(image)


def save_exr(path: str, pixels) -> None:
    """Write a float RGBA array (row 0 = bottom) as a 32-bit EXR."""
    import bpy

    np = numpy()
    height, width = pixels.shape[0], pixels.shape[1]
    data = np.ascontiguousarray(pixels, dtype=np.float32)
    if data.ndim == 3 and data.shape[2] == 4:
        flat = data.ravel()
    elif data.ndim == 3 and data.shape[2] == 3:
        alpha = np.ones((height, width, 1), dtype=np.float32)
        flat = np.concatenate([data, alpha], axis=2).ravel()
    else:
        raise RuntimeError("save_exr expects an (h, w, 3|4) array")
    image = bpy.data.images.new("goo_composite", width=width, height=height, alpha=True, float_buffer=True)
    try:
        image.pixels.foreach_set(flat)
        image.filepath_raw = str(path)
        image.file_format = "OPEN_EXR"
        image.save()
    finally:
        bpy.data.images.remove(image)


_PNG_CHANNELS = 4


def save_png(path: str, pixels, *, apply_srgb: bool = True) -> None:
    """Write an RGBA float array as an 8-bit sRGB PNG (pure Python + zlib).

    ``bpy.data.images.save()`` produced black frames for generated float images
    in 5.2, so this writes the PNG container manually (8-bit RGBA, filter 0).
    """
    import struct
    import zlib

    np = numpy()
    height, width = pixels.shape[0], pixels.shape[1]
    data = np.asarray(pixels, dtype=np.float32)
    if data.ndim != 3 or data.shape[2] not in (3, 4):
        raise RuntimeError("save_png expects an (h, w, 3|4) array")
    if data.shape[2] == 3:
        alpha = np.ones((height, width, 1), dtype=np.float32)
        data = np.concatenate([data, alpha], axis=2)
        height, width = data.shape[0], data.shape[1]
    values = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=0.0)
    if apply_srgb:
        values = linear_to_srgb(values)
    else:
        values = np.clip(values, 0.0, 1.0)
    values = np.clip(values, 0.0, 1.0)
    bytes_data = np.rint(values * 255.0).astype(np.uint8)
    # PNG rows run top-to-bottom, Blender's buffers run bottom-to-top.
    rows = bytes_data[::-1]
    raw = np.zeros((height, 1 + 4 * width), dtype=np.uint8)
    raw[:, 0] = 0  # filter type 0 (None)
    raw[:, 1:] = rows.reshape(height, 4 * width)
    compressed = zlib.compress(raw.tobytes(), 9)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    with open(str(path), "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", header))
        handle.write(chunk(b"IDAT", compressed))
        handle.write(chunk(b"IEND", b""))


def image_stats(path: str, *, ignore_above: float | None = None) -> dict:
    """Cheap pixel statistics used for manifests and tests.

    ``ignore_above`` masks sentinel values (e.g. the 1e10 "infinity" the EEVEE
    Z pass writes for background pixels) out of the reported numbers and adds
    ``valid_fraction`` / ``ignored_fraction`` so the manifest stays informative.
    """
    pixels, is_srgb = load_image(path)
    np = numpy()
    rgb = pixels[:, :, :3]
    if is_srgb:
        rgb = srgb_to_linear(rgb)
    luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    stats = {
        "width": int(pixels.shape[1]),
        "height": int(pixels.shape[0]),
    }
    if ignore_above is not None:
        valid = np.isfinite(luminance) & (luminance < float(ignore_above))
        valid_fraction = float(valid.mean())
        stats["valid_fraction"] = round(valid_fraction, 6)
        stats["ignored_fraction"] = round(1.0 - valid_fraction, 6)
        if valid.any():
            stats["mean"] = [round(float(v), 6) for v in rgb[valid].reshape(-1, 3).mean(axis=0)]
            stats["mean_luminance"] = round(float(luminance[valid].mean()), 6)
            stats["max_luminance"] = round(float(luminance[valid].max()), 6)
            stats["min_luminance"] = round(float(luminance[valid].min()), 6)
            stats["non_black_fraction"] = round(float((luminance[valid] > 1e-5).mean()), 6)
        else:
            stats.update(
                mean=[0.0, 0.0, 0.0],
                mean_luminance=0.0,
                max_luminance=0.0,
                min_luminance=0.0,
                non_black_fraction=0.0,
            )
        return stats
    stats.update(
        mean=[round(float(v), 6) for v in rgb.reshape(-1, 3).mean(axis=0)],
        mean_luminance=round(float(luminance.mean()), 6),
        max_luminance=round(float(luminance.max()), 6),
        min_luminance=round(float(luminance.min()), 6),
        non_black_fraction=round(float((luminance > 1e-5).mean()), 6),
    )
    return stats


def save_array(path: str, pixels, fmt: str, *, png: bool | None = None) -> None:
    fmt = (fmt or "exr").lower()
    if fmt in ("png",) or (png and fmt != "exr"):
        save_png(path, pixels, apply_srgb=True)
    else:
        save_exr(path, pixels)
