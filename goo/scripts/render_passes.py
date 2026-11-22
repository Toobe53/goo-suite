# SPDX-License-Identifier: GPL-3.0-or-later
"""Render beauty / data passes inside Blender for ``goo render``.

Invoked as::

    blender --factory-startup --background scene.blend \
        --python-exit-code 4 --python render_passes.py -- job.json

Job keys (``goo/render.py`` / ``goo/cli.py`` fill these in)::

    out_dir              required output directory
    passes               list, default ["beauty"]
    light_groups         bool, render one beauty per light group
    samples              int (eevee taa_render_samples)
    resolution_percentage int
    film_transparent     bool
    format               "exr" | "png"
    scene                optional scene name
    view_layer           optional view layer name (default: first layer)
    ambient              bool, render ambient-only pass (default true)
    result_file          where to write the JSON payload

Outputs, all inside ``out_dir``:

* ``<group>_beauty.exr`` — one per light group (``default`` for unassigned),
  produced by hiding every other light while rendering;
* ``ambient_beauty.exr`` — all lights hidden, used by the compositor to remove
  the world light that would otherwise be counted once per group;
* ``beauty.exr`` — plain beauty when light groups are disabled;
* ``depth.exr`` / ``normal.exr`` / ``shadow.exr`` / ``mist.exr`` / ``image.exr``
  — generic passes rendered through a temporary compositor node group;
* ``manifest.json`` — deterministic description of everything written.

Verified constraints (Blender 5.2.3, EEVEE):

* ``image_settings.file_format`` must be set *before* ``color_depth``.
* Render Layer node outputs only appear for view-layer passes enabled *before*
  the node is created, so all requested passes are enabled up front.
* EXR output is raw linear; PNG output is sRGB-encoded display space.
"""

from __future__ import annotations

import os
import sys
import time

# Blender's --python does not add the script directory to sys.path; do it so
# the sibling _common module is importable in every invocation style.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C  # noqa: E402

PASS_ALIASES = {
    "combined": "beauty",
    "beauty": "beauty",
    "image": "image",
    "depth": "depth",
    "z": "depth",
    "normal": "normal",
    "shadow": "shadow",
    "mist": "mist",
    "alpha": "alpha",
}

GENERIC_PASSES = {
    # name: (render layer output, interface socket type, file extension, format)
    "depth": ("Depth", "NodeSocketFloat", ".exr", "exr"),
    "normal": ("Normal", "NodeSocketVector", ".exr", "exr"),
    "shadow": ("Shadow", "NodeSocketColor", ".exr", "exr"),
    "mist": ("Mist", "NodeSocketFloat", ".exr", "exr"),
    "alpha": ("Alpha", "NodeSocketFloat", ".exr", "exr"),
    "image": ("Image", "NodeSocketColor", None, None),
}

PASS_FLAGS = {
    "depth": "use_pass_z",
    "normal": "use_pass_normal",
    "shadow": "use_pass_shadow",
    "mist": "use_pass_mist",
}

#: EEVEE writes 1e10 into the Z pass for background pixels; mask that sentinel
#: out of the manifest statistics (and say how much was masked).
_SENTINEL_PASSES = {"depth": 1e6}


def main(job: dict) -> None:
    import bpy

    timer = C.Timer()
    scene = C.scene_or_fail(job.get("scene"))
    out_dir = job.get("out_dir")
    if not out_dir:
        raise RuntimeError("job is missing 'out_dir'")
    out_dir = os.path.abspath(str(out_dir))
    C.ensure_dir(out_dir)

    requested = _requested_passes(job)
    want_groups = bool(job.get("light_groups"))
    want_ambient = bool(job.get("ambient", True))
    warnings: list[str] = []
    passes_out: list[dict] = []
    written: list[str] = []

    C.log(f"blend: {bpy.data.filepath or '<unsaved>'}")
    C.log(f"scene: {scene.name}  engine: {scene.render.engine}")
    C.log(f"requested passes: {', '.join(requested)}  light groups: {want_groups}")

    view_layer_name = str(job.get("view_layer") or "").strip()
    if view_layer_name:
        view_layer = scene.view_layers.get(view_layer_name)
        if view_layer is None:
            available = ", ".join(layer.name for layer in scene.view_layers) or "<none>"
            raise RuntimeError(
                f"view layer {view_layer_name!r} not found in scene {scene.name!r} "
                f"(available: {available}) / 场景中没有该视图层"
            )
    else:
        view_layer = scene.view_layers[0]
    state = _capture_state(scene, view_layer)
    try:
        settings = C.configure_render(scene, job)
        settings.update(
            {
                "scene": scene.name,
                "view_layer": view_layer.name,
                "frame": int(scene.frame_current),
                "file_extension": False,
            }
        )
        # We always hand Blender a complete file name, so do not append another
        # extension; configure_render() enables it by default.
        scene.render.use_file_extension = False

        group_report = None
        passes = []
        if want_groups:
            group_report, passes, warnings = _collect_groups(scene, job, warnings)
            C.log(f"light group passes: {len(passes)}")
            _render_light_groups(scene, out_dir, passes, job, passes_out, written, warnings)
            if want_ambient and len(passes) > 1:
                ambient = _render_ambient(scene, out_dir, job, warnings)
                if ambient:
                    passes_out.append(ambient)
                    written.append(ambient["file"])
            elif want_ambient and len(passes) == 1:
                warnings.append("skipped ambient pass: only one light group, nothing to de-duplicate")

        generic = [name for name in requested if name != "beauty"]
        if not want_groups and "beauty" in requested:
            entry = _render_plain_beauty(scene, out_dir, job, warnings)
            passes_out.append(entry)
            written.append(entry["file"])
        if want_groups and not passes:
            warnings.append("no light groups/lights found; rendered a plain beauty pass instead")
            entry = _render_plain_beauty(scene, out_dir, job, warnings)
            passes_out.append(entry)
            written.append(entry["file"])

        if generic:
            _render_generic_passes(scene, view_layer, out_dir, generic, job, passes_out, written, warnings)

        manifest = {
            "ok": True,
            "tool": "render_passes",
            "blender": {
                "version": bpy.app.version_string,
                "version_tuple": list(bpy.app.version),
                "background": bool(bpy.app.background),
                "numpy": _numpy_version(),
            },
            "blend": bpy.data.filepath or None,
            "output_dir": out_dir,
            "settings": settings,
            "requested_passes": requested,
            "light_groups": group_report,
            "passes": passes_out,
            "files": written,
            "manifest": "manifest.json",
            "warnings": warnings,
            "seconds": round(timer.elapsed, 3),
        }
        C.write_manifest(out_dir, manifest, job)
        C.log(f"rendered {len(passes_out)} pass(es) in {timer.elapsed:.2f}s -> {out_dir}")
        C.emit(manifest, job)
    finally:
        _restore_state(scene, view_layer, state)


# ---------------------------------------------------------------------------
# pass list handling
# ---------------------------------------------------------------------------
def _requested_passes(job: dict) -> list[str]:
    raw = job.get("passes") or ["beauty"]
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(";", ",").split(",")]
    names: list[str] = []
    for item in raw:
        name = PASS_ALIASES.get(str(item).strip().lower())
        if name is None:
            raise RuntimeError(f"unknown pass: {item!r} (known: {', '.join(sorted(PASS_ALIASES))})")
        if name not in names:
            names.append(name)
    if not names:
        names = ["beauty"]
    return names


# ---------------------------------------------------------------------------
# render state capture / restore
# ---------------------------------------------------------------------------
def _capture_state(scene, view_layer) -> dict:
    state = {
        "compositing_node_group": getattr(scene, "compositing_node_group", None),
        "use_compositing": bool(scene.render.use_compositing),
        "use_file_extension": bool(scene.render.use_file_extension),
        "filepath": scene.render.filepath,
        "format": scene.render.image_settings.file_format,
        "depth": scene.render.image_settings.color_depth,
        "mode": scene.render.image_settings.color_mode,
        "view_transform": getattr(scene.view_settings, "view_transform", None),
        "pass_flags": {},
    }
    for attr in list(PASS_FLAGS.values()) + ["use_pass_object_index"]:
        if hasattr(view_layer, attr):
            state["pass_flags"][attr] = bool(getattr(view_layer, attr))
    return state


def _restore_state(scene, view_layer, state: dict) -> None:
    for attr, value in state["pass_flags"].items():
        try:
            setattr(view_layer, attr, value)
        except (AttributeError, TypeError):
            pass
    try:
        scene.render.filepath = state["filepath"]
        scene.render.use_file_extension = state["use_file_extension"]
    except (AttributeError, TypeError):
        pass
    _set_image_format(scene, state["format"], state["depth"], state["mode"])
    if state["view_transform"] is not None:
        try:
            scene.view_settings.view_transform = state["view_transform"]
        except (AttributeError, TypeError):
            pass
    try:
        scene.compositing_node_group = state["compositing_node_group"]
    except (AttributeError, TypeError):
        pass
    try:
        scene.render.use_compositing = state["use_compositing"]
    except (AttributeError, TypeError):
        pass


def _set_image_format(scene, file_format: str, depth: str | None = None, mode: str | None = None) -> None:
    settings = scene.render.image_settings
    settings.file_format = file_format  # must come first
    if depth:
        try:
            settings.color_depth = depth
        except TypeError:
            pass
    if mode:
        try:
            settings.color_mode = mode
        except TypeError:
            pass


def _format_for(job: dict, override: str | None = None) -> str:
    fmt = str(override or job.get("format") or "exr").lower()
    return "png" if fmt == "png" else "exr"


def _prepare_beauty_format(scene, job: dict, fmt: str) -> None:
    if fmt == "png":
        _set_image_format(scene, "PNG", "16" if job.get("png_16bit") else "8", "RGBA")
        try:
            scene.view_settings.view_transform = "Standard"
        except (AttributeError, TypeError):
            pass
    else:
        _set_image_format(scene, "OPEN_EXR", "32", "RGBA")
        scene.render.image_settings.exr_codec = "ZIP"


# ---------------------------------------------------------------------------
# light groups
# ---------------------------------------------------------------------------
def _collect_groups(scene, job: dict, warnings: list[str]):
    goo_npr = C.import_addon(job, required=True)
    # The CLI always starts from --factory-startup, so the add-on is not loaded
    # even when the .blend was saved with it enabled: register it to get the
    # goo_light_groups / goo_light_group properties back.
    C.ensure_addon_registered(goo_npr)
    try:
        light_groups = goo_npr.light_groups
    except AttributeError as exc:  # pragma: no cover - add-on incomplete
        raise RuntimeError(
            "goo_npr.light_groups is missing; reinstall the add-on / 插件不完整，请重新安装"
        ) from exc

    sync_report: dict = {}
    if hasattr(light_groups, "sync_scene"):
        result = light_groups.sync_scene(scene)
        if isinstance(result, dict):
            sync_report = result
        C.log(f"sync_scene: {sync_report}")
    else:  # pragma: no cover
        warnings.append("goo_npr.light_groups.sync_scene() not found")

    raw_passes = []
    if hasattr(light_groups, "collect_passes"):
        raw_passes = list(light_groups.collect_passes(scene) or [])
    else:  # pragma: no cover
        warnings.append("goo_npr.light_groups.collect_passes() not found")

    passes: list[dict] = []
    covered: set[str] = set()
    for entry in raw_passes:
        if not isinstance(entry, dict):
            continue
        group_name = str(entry.get("group") or "")
        lights = [obj for obj in entry.get("lights") or [] if obj is not None and getattr(obj, "type", "") == "LIGHT"]
        if not lights:
            warnings.append(f"light group {group_name or 'default'} has no lights; skipped")
            continue
        covered.update(obj.name for obj in lights)
        passes.append({"group": group_name, "lights": lights})

    # Add the unassigned "default" pass if the add-on did not include it but the
    # scene has lights that no group claimed.
    if job.get("include_default", True):
        leftovers = [
            obj
            for obj in scene.objects
            if obj.type == "LIGHT" and obj.name not in covered and not obj.hide_render
        ]
        if leftovers and not any(not p["group"] for p in passes):
            passes.append({"group": "", "lights": leftovers})
            warnings.append(f"added default pass with {len(leftovers)} unassigned light(s)")

    report = {
        "enabled": True,
        "sync": sync_report,
        "groups": [
            {
                "name": _group_label(p["group"]),
                "raw_name": p["group"],
                "lights": sorted(obj.name for obj in p["lights"]),
                "lighting_type": _lighting_type(p["lights"]),
            }
            for p in passes
        ],
        "pass_count": len(passes),
    }
    return report, passes, warnings


def _group_label(name: str) -> str:
    return name if name.strip() else "default"


def _lighting_type(lights: list) -> str:
    kinds = sorted({str(getattr(getattr(obj, "data", None), "type", "LIGHT")).lower() for obj in lights})
    if not kinds:
        return "none"
    if len(kinds) == 1:
        return kinds[0]
    return "+".join(kinds)


def _all_scene_lights(scene) -> list:
    return [obj for obj in scene.objects if obj.type == "LIGHT"]


def _set_visibility(scene, original: dict[str, bool], enabled: set[str]) -> None:
    for obj in _all_scene_lights(scene):
        obj.hide_render = bool(original.get(obj.name, False)) or (obj.name not in enabled)


def _render_light_groups(scene, out_dir: str, passes: list[dict], job: dict, passes_out: list[dict], written: list[str], warnings: list[str]) -> None:
    fmt = _format_for(job)
    _prepare_beauty_format(scene, job, fmt)
    ext = "." + fmt
    original = {obj.name: bool(obj.hide_render) for obj in _all_scene_lights(scene)}
    used: set[str] = set()
    try:
        for index, entry in enumerate(passes, start=1):
            label = _group_label(entry["group"])
            base = C.safe_name(label, "default")
            filename = f"{base}_beauty{ext}"
            counter = 2
            while filename in used:
                filename = f"{base}_{counter}_beauty{ext}"
                counter += 1
            used.add(filename)
            path = os.path.join(out_dir, filename)
            enabled = {obj.name for obj in entry["lights"] if not original.get(obj.name, False)}
            _set_visibility(scene, original, enabled)
            started = time.perf_counter()
            _render_still(scene, path, job.get("view_layer"))
            seconds = time.perf_counter() - started
            stats = _stats(path, warnings)
            C.log(f"[{index}/{len(passes)}] group '{label}': {filename} ({seconds:.2f}s)")
            passes_out.append(
                {
                    "name": base,
                    "group": label,
                    "group_raw": entry["group"],
                    "kind": "light_group",
                    "lighting_type": _lighting_type(entry["lights"]),
                    "lights": sorted(obj.name for obj in entry["lights"]),
                    "file": filename,
                    "path": path,
                    "format": fmt,
                    "seconds": round(seconds, 3),
                    "stats": stats,
                }
            )
            written.append(filename)
    finally:
        for obj in _all_scene_lights(scene):
            obj.hide_render = bool(original.get(obj.name, False))


def _render_ambient(scene, out_dir: str, job: dict, warnings: list[str]) -> dict | None:
    fmt = _format_for(job)
    _prepare_beauty_format(scene, job, fmt)
    filename = f"ambient_beauty.{fmt}"
    path = os.path.join(out_dir, filename)
    original = {obj.name: bool(obj.hide_render) for obj in _all_scene_lights(scene)}
    try:
        _set_visibility(scene, original, set())
        started = time.perf_counter()
        _render_still(scene, path, job.get("view_layer"))
        seconds = time.perf_counter() - started
    finally:
        for obj in _all_scene_lights(scene):
            obj.hide_render = bool(original.get(obj.name, False))
    stats = _stats(path, warnings)
    C.log(f"ambient pass: {filename} ({seconds:.2f}s)")
    return {
        "name": "ambient",
        "group": "ambient",
        "kind": "ambient",
        "lighting_type": "world",
        "lights": [],
        "file": filename,
        "path": path,
        "format": fmt,
        "seconds": round(seconds, 3),
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# plain beauty and generic passes
# ---------------------------------------------------------------------------
def _render_plain_beauty(scene, out_dir: str, job: dict, warnings: list[str]) -> dict:
    fmt = _format_for(job)
    _prepare_beauty_format(scene, job, fmt)
    filename = f"beauty.{fmt}"
    path = os.path.join(out_dir, filename)
    started = time.perf_counter()
    _render_still(scene, path, job.get("view_layer"))
    seconds = time.perf_counter() - started
    C.log(f"beauty pass: {filename} ({seconds:.2f}s)")
    return {
        "name": "beauty",
        "group": None,
        "kind": "beauty",
        "lighting_type": "all",
        "lights": sorted(obj.name for obj in _all_scene_lights(scene)),
        "file": filename,
        "path": path,
        "format": fmt,
        "seconds": round(seconds, 3),
        "stats": _stats(path, warnings),
    }


def _render_generic_passes(scene, view_layer, out_dir: str, generic: list[str], job: dict, passes_out: list[dict], written: list[str], warnings: list[str]) -> None:
    import bpy

    enabled: dict[str, bool] = {}
    for name in generic:
        flag = PASS_FLAGS.get(name)
        if not flag:
            continue
        if hasattr(view_layer, flag):
            try:
                setattr(view_layer, flag, True)
                enabled[name] = True
            except (AttributeError, TypeError) as exc:
                warnings.append(f"cannot enable {flag} for pass '{name}': {exc}")
        else:
            warnings.append(f"pass '{name}' is not available in this Blender build; skipped")

    original_group = getattr(scene, "compositing_node_group", None)
    original_use = bool(scene.render.use_compositing)
    try:
        for name in generic:
            spec = GENERIC_PASSES[name]
            rl_socket, socket_type, ext, fmt_override = spec
            if name in PASS_FLAGS and not enabled.get(name):
                continue
            fmt = _format_for(job, fmt_override)
            if fmt == "png":
                _set_image_format(scene, "PNG", "16" if job.get("png_16bit") else "8", "RGBA")
                try:
                    scene.view_settings.view_transform = "Standard"
                except (AttributeError, TypeError):
                    pass
            else:
                _set_image_format(scene, "OPEN_EXR", "32", "RGBA")
            filename = f"{name}{ext or '.' + fmt}"
            path = os.path.join(out_dir, filename)
            started = time.perf_counter()
            tree = _make_pass_tree(scene, view_layer, name, rl_socket, socket_type)
            try:
                scene.compositing_node_group = tree
                try:
                    scene.render.use_compositing = True
                except (AttributeError, TypeError):
                    pass
                _render_still(scene, path, view_layer.name)
            finally:
                scene.compositing_node_group = original_group
                try:
                    scene.render.use_compositing = original_use
                except (AttributeError, TypeError):
                    pass
                _remove_tree(tree)
            seconds = time.perf_counter() - started
            C.log(f"pass '{name}': {filename} ({seconds:.2f}s)")
            passes_out.append(
                {
                    "name": name,
                    "group": None,
                    "kind": "generic",
                    "lighting_type": "all",
                    "socket": rl_socket,
                    "file": filename,
                    "path": path,
                    "format": fmt,
                    "seconds": round(seconds, 3),
                    "stats": _stats(path, warnings, ignore_above=_SENTINEL_PASSES.get(name)),
                }
            )
            written.append(filename)
    finally:
        scene.compositing_node_group = original_group
        try:
            scene.render.use_compositing = original_use
        except (AttributeError, TypeError):
            pass


def _make_pass_tree(scene, view_layer, name: str, rl_socket: str, socket_type: str):
    """Create a compositor group whose single output is the requested socket.

    The output socket is deliberately always *Color*: on Blender 5.2.3 a
    VALUE-typed group output silently makes the render fall back to the
    combined image (verified: Depth and Image renders came out identical),
    while VALUE/VECTOR sockets convert correctly into a Color output.
    """
    import bpy

    tree = bpy.data.node_groups.new(f"goo pass {name}", "CompositorNodeTree")
    try:
        tree.interface.new_socket(name=rl_socket, in_out="OUTPUT", socket_type="NodeSocketColor")
    except Exception:  # noqa: BLE001 - older/other signature
        tree.interface.new_socket(name=rl_socket, in_out="OUTPUT", socket_type=socket_type)
    rl = tree.nodes.new("CompositorNodeRLayers")
    try:
        rl.scene = scene
    except (AttributeError, TypeError):
        pass
    try:
        if rl.layer != view_layer.name:
            rl.layer = view_layer.name
    except (AttributeError, TypeError):
        pass
    output = tree.nodes.new("NodeGroupOutput")
    try:
        source = rl.outputs[rl_socket]
    except KeyError as exc:
        outputs = [socket.name for socket in rl.outputs]
        _remove_tree(tree)
        raise RuntimeError(
            f"render layer has no '{rl_socket}' output (available: {', '.join(outputs)}); "
            "the pass could not be enabled / 无法启用该通道"
        ) from exc
    tree.links.new(source, output.inputs[rl_socket])
    return tree


def _remove_tree(tree) -> None:
    import bpy

    try:
        if tree.users == 0:
            bpy.data.node_groups.remove(tree)
    except (ReferenceError, RuntimeError):
        pass


# ---------------------------------------------------------------------------
# rendering / statistics
# ---------------------------------------------------------------------------
def _render_still(scene, path: str, layer: str | None = None) -> None:
    import bpy

    scene.render.filepath = str(path)
    kwargs = {"write_still": True}
    if layer:
        kwargs["layer"] = layer
    bpy.ops.render.render(**kwargs)
    if not os.path.isfile(path):
        raise RuntimeError(
            f"Blender did not write {path}; check the output directory permissions / 输出目录不可写"
        )


def _stats(path: str, warnings: list[str], *, ignore_above: float | None = None) -> dict:
    try:
        return C.image_stats(path, ignore_above=ignore_above)
    except Exception as exc:  # noqa: BLE001 - statistics must never fail a render
        warnings.append(f"could not read pixels of {os.path.basename(path)}: {exc}")
        return {}


def _numpy_version() -> str | None:
    try:
        import numpy

        return numpy.__version__
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    C.run_main(main, C.load_job())
