# SPDX-License-Identifier: GPL-3.0-or-later
"""High level operations behind the CLI subcommands.

Every function here returns a JSON-serialisable payload with ``"ok": True`` and
raises :class:`goo.errors.GooError` subclasses on failure; the CLI layer only
formats that.  The GUI also consumes these payloads when it shells out.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import REPO_ROOT, __version__, addon_root
from . import blender as blender_module
from . import config as config_module
from . import install as install_module
from .errors import JobFailed, UsageError
from .util import ensure_dir, platform_name, read_addon_version, version_text

#: Names accepted by ``--passes`` (aliases are normalised inside Blender).
RENDER_PASSES = ("beauty", "combined", "image", "depth", "normal", "shadow", "mist", "alpha")
#: Passes whose data is always written as EXR (linear) regardless of ``--format``.
DATA_PASSES = ("depth", "normal", "shadow", "mist", "alpha")


def _addon_dir(addon_path: Optional[str]) -> Path:
    if addon_path:
        candidate = Path(addon_path)
        return candidate if candidate.name == "goo_npr" else candidate / "goo_npr"
    return addon_root() / "goo_npr"


def _require_blend(blend: str) -> Path:
    path = Path(blend)
    if not path.exists():
        raise UsageError(
            f"blend file not found: {path}",
            hint="Check the path (quote paths containing spaces) / 请检查路径，含空格的路径需要加引号",
        )
    if not path.is_file():
        raise UsageError(f"not a file: {path}")
    if path.suffix.lower() not in (".blend", ".blend1"):
        raise UsageError(
            f"expected a .blend file, got {path.name}",
            hint="Pass the path to a Blender file / 请传入 .blend 文件路径",
        )
    return path


def _prepare_output_dir(path: Path) -> Path:
    """Create an output directory, failing with a usage error rather than a traceback."""
    if path.exists() and not path.is_dir():
        raise UsageError(
            f"output path exists and is not a directory: {path}",
            hint=(
                "Pass -o with a directory path (e.g. -o renders), or delete that file "
                "/ 请用 -o 指定一个目录路径（例如 -o renders），或先删除该同名文件"
            ),
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UsageError(
            f"cannot create output directory {path}: {exc}",
            hint="Check permissions or choose another -o path / 请检查权限或换一个输出路径",
        ) from exc
    return path


def _addon_job(addon_path: Optional[str]) -> str:
    directory = _addon_dir(addon_path)
    if not directory.is_dir():
        raise UsageError(
            f"add-on package not found: {directory}",
            hint="Pass --addon <repo-root> or run goo from a full checkout / 请用 --addon 指定包含 goo_npr 的目录",
        )
    return str(directory.parent)


def _script_result(rr: blender_module.RunResult, action: str) -> dict:
    payload = blender_module.ensure_ok(rr, action)
    payload.setdefault("ok", True)
    payload.setdefault("duration", round(rr.duration, 3))
    payload.setdefault("command", rr.command_line if hasattr(rr, "command_line") else "")
    return payload


# ---------------------------------------------------------------------------
# doctor / nodes
# ---------------------------------------------------------------------------
def doctor(
    blender: Optional[str] = None,
    blend: Optional[str] = None,
    *,
    addon_path: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    install = blender_module.discover(blender)
    addon_dir = _addon_dir(addon_path)
    job = {
        "addon_root": str(addon_dir.parent),
        "specs": False,
        "light_groups": True,
    }
    inspect_payload: Optional[dict] = None
    inspect_error: Optional[str] = None
    try:
        rr = blender_module.run_script(
            "inspect.py",
            blend=blend,
            args=job,
            blender=install,
            timeout=timeout or 300.0,
            hide_window=True,
        )
        inspect_payload = _script_result(rr, "scene inspection")
    except JobFailed as exc:
        # `doctor` still reports what it can; the failure is shown as an error.
        inspect_error = exc.message
        inspect_payload = exc.result or None
        if exc.stderr_tail:
            inspect_error += "\n" + exc.stderr_tail

    addon_report = {
        "path": str(addon_dir),
        "exists": addon_dir.is_dir(),
        "version": read_addon_version(addon_dir),
        "light_groups_module": (addon_dir / "light_groups.py").is_file(),
        "registry_module": (addon_dir / "nodes" / "registry.py").is_file(),
    }
    payload = {
        "ok": True,
        "command": "doctor",
        "cli": {
            "name": "goo",
            "version": __version__,
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform_name(),
            "repo_root": str(REPO_ROOT),
            "scripts_dir": str(blender_module.SCRIPTS_DIR),
            "config": config_module.describe(),
            "config_files": [str(path) for path in config_module.config_paths()],
        },
        "blender": install.as_dict(),
        "addon": addon_report,
        "blend": str(Path(blend).resolve()) if blend else None,
        "inspect": inspect_payload,
        "error": inspect_error,
    }
    if inspect_payload and inspect_payload.get("warnings"):
        payload["warnings"] = list(inspect_payload["warnings"])
    return payload


def nodes(
    blender: Optional[str] = None,
    blend: Optional[str] = None,
    *,
    addon_path: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    install = blender_module.discover(blender)
    directory = _addon_dir(addon_path)
    if not directory.is_dir():
        raise UsageError(
            f"add-on package not found: {directory}",
            hint="Pass --addon <repo-root> / 请用 --addon 指定包含 goo_npr 的目录",
        )
    if blend:
        _require_blend(blend)
    job = {
        "addon_root": str(directory.parent),
        "specs": True,
        "light_groups": True,
    }
    rr = blender_module.run_script(
        "inspect.py",
        blend=blend,
        args=job,
        blender=install,
        timeout=timeout or 300.0,
        hide_window=True,
    )
    payload = _script_result(rr, "node library inspection")
    payload["command"] = "nodes"
    payload["addon_version"] = read_addon_version(directory)
    return payload


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------
def apply(
    blend: str,
    output: Optional[str] = None,
    *,
    in_place: bool = False,
    build_library: bool = True,
    blender: Optional[str] = None,
    addon_path: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    source = _require_blend(blend)
    if not output and not in_place:
        raise UsageError(
            "no output path given",
            hint="Pass -o OUT.blend to write a copy, or --in-place to overwrite the input / 请用 -o 指定输出文件，或使用 --in-place",
        )
    target = Path(output) if output else source
    if target.exists() and target.resolve() != source.resolve() and target.is_dir():
        raise UsageError(f"output path is a directory: {target}")
    backup: Optional[str] = None
    if target.resolve() == source.resolve():
        backup = _backup(source)
    ensure_dir(str(target.parent if target.parent else Path(".")))
    addon_root_dir = _addon_job(addon_path)
    job = {
        "out_path": str(target),
        "build_library": bool(build_library),
        "sync_light_groups": True,
        "addon_root": addon_root_dir,
    }
    install = blender_module.discover(blender)
    rr = blender_module.run_script(
        "apply_library.py",
        blend=str(source),
        args=job,
        blender=install,
        timeout=timeout or 900.0,
        hide_window=True,
    )
    payload = _script_result(rr, "applying the node library")
    if not Path(payload.get("blend_out") or target).is_file():
        raise JobFailed(
            "Blender reported success but the output file is missing",
            hint=f"Check write permissions for {target.parent} / 请检查输出目录权限",
            stderr_tail=rr.output_tail(),
        )
    payload.update(
        {
            "command": "apply",
            "source": str(source),
            "target": str(target),
            "backup": backup,
            "settings": {"build_library": build_library, "sync_light_groups": True},
        }
    )
    return payload


def _backup(source: Path) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = source.with_name(f"{source.name}.{stamp}.bak")
    counter = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.{stamp}-{counter}.bak")
        counter += 1
    try:
        shutil.copy2(source, candidate)
    except OSError as exc:
        raise JobFailed(
            f"could not back up {source.name} before overwriting it: {exc}",
            hint="Use -o to write to a different file instead / 也可以改用 -o 输出到其他文件",
        )
    return str(candidate)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def render(
    blend: str,
    output_dir: Optional[str] = None,
    *,
    passes: Iterable[str] = ("beauty",),
    light_groups: bool = False,
    samples: Optional[int] = None,
    resolution_percentage: Optional[int] = None,
    fmt: Optional[str] = None,
    film_transparent: Optional[bool] = None,
    ambient: bool = True,
    burn_in: bool = False,
    camera: Optional[str] = None,
    view_layer: Optional[str] = None,
    blender: Optional[str] = None,
    addon_path: Optional[str] = None,
    timeout: Optional[float] = None,
    keep_temp: bool = False,
) -> dict:
    source = _require_blend(blend)
    cfg = config_module.load_config()
    out_dir = output_dir or cfg.get("output_dir")
    if not out_dir:
        raise UsageError(
            "no output directory given",
            hint="Pass -o OUT, or store a default with `goo config --output-dir DIR` / 请用 -o 指定输出目录",
        )
    pass_list = _normalise_passes(passes)
    if fmt is None:
        fmt = cfg.get("format") or "exr"
    fmt = str(fmt).lower()
    if fmt not in ("exr", "png"):
        raise UsageError(f"unsupported format {fmt!r}", hint="Use --format exr or --format png")
    if samples is None:
        samples = cfg.get("samples")
    if resolution_percentage is None:
        resolution_percentage = cfg.get("resolution_percentage")
    if resolution_percentage is not None and not (1 <= int(resolution_percentage) <= 1000):
        raise UsageError("--resolution-percentage must be between 1 and 1000")
    if burn_in:
        if fmt != "png":
            raise UsageError(
                "--burn-in needs --format png",
                hint=(
                    "The native render stamp is only burned into 8/16-bit PNG output; EXR keeps metadata only. "
                    "Use `goo render ... --format png --burn-in` / 烙入文字仅支持 PNG"
                ),
            )
        if light_groups:
            raise UsageError(
                "--burn-in is not supported with --light-groups",
                hint=(
                    "Light-group passes are additively composited by `goo composite`, which would remove the stamp. "
                    "Render a combined beauty, or burn in after compositing / 灯光组合成会丢失烙入文字"
                ),
            )

    addon_root_dir = _addon_job(addon_path)
    if light_groups:
        module = Path(addon_root_dir) / "goo_npr" / "light_groups.py"
        if not module.is_file():
            raise JobFailed(
                "light-group passes need goo_npr.light_groups, which is missing from this checkout",
                hint=(
                    "The add-on is still under construction or incomplete; reinstall it with `goo install` "
                    "插件缺少 light_groups 模块，请重新安装或更新源码"
                ),
                result={"missing": str(module)},
            )

    out_path = _prepare_output_dir(Path(out_dir))
    existing = sorted(p.name for p in out_path.glob("*_beauty.*"))
    job = {
        "out_dir": str(out_path.resolve()),
        "passes": list(pass_list),
        "light_groups": bool(light_groups),
        "samples": int(samples) if samples not in (None, "") else None,
        "resolution_percentage": int(resolution_percentage) if resolution_percentage not in (None, "") else None,
        "format": fmt,
        "film_transparent": film_transparent,
        "ambient": bool(ambient and light_groups),
        "burn_in": bool(burn_in),
        "camera": str(camera) if camera else None,
        "view_layer": str(view_layer).strip() if view_layer else None,
        "addon_root": addon_root_dir,
    }
    install = blender_module.discover(blender)
    rr = blender_module.run_script(
        "render_passes.py",
        blend=str(source),
        args=job,
        blender=install,
        timeout=timeout or 1800.0,
        keep_temp=keep_temp,
        hide_window=True,
    )
    payload = _script_result(rr, "render")
    payload["command"] = "render"
    payload["source"] = str(source)
    payload["output_dir"] = str(out_path.resolve())
    payload["pre_existing_beauty_files"] = existing
    manifest_path = Path(payload["output_dir"]) / "manifest.json"
    if not manifest_path.is_file():
        raise JobFailed(
            "render finished but manifest.json is missing",
            hint="The output directory was not written to; check permissions / 输出目录未写入，请检查权限",
            stderr_tail=rr.output_tail(),
        )
    payload["manifest"] = str(manifest_path)
    return payload


def _normalise_passes(passes: Iterable[str] | str) -> list[str]:
    if isinstance(passes, str):
        raw = [part.strip() for part in passes.replace(";", ",").split(",")]
    else:
        raw = []
        for item in passes:
            raw.extend(part.strip() for part in str(item).replace(";", ",").split(","))
    names: list[str] = []
    for name in raw:
        if not name:
            continue
        lowered = name.lower()
        if lowered not in RENDER_PASSES:
            raise UsageError(
                f"unknown pass {name!r}",
                hint=f"Choose from: {', '.join(RENDER_PASSES)} / 可选通道：{', '.join(RENDER_PASSES)}",
            )
        if lowered not in names:
            names.append(lowered)
    return names or ["beauty"]


# ---------------------------------------------------------------------------
# composite
# ---------------------------------------------------------------------------
def composite(
    out_dir: str,
    *,
    png: bool = False,
    exr: bool = True,
    exposure: float = 0.0,
    contrast: float = 1.0,
    shadow: bool = False,
    subtract_ambient: bool = True,
    basename: Optional[str] = None,
    blender: Optional[str] = None,
    timeout: Optional[float] = None,
    keep_temp: bool = False,
) -> dict:
    directory = Path(out_dir)
    if not directory.exists():
        raise UsageError(
            f"render directory not found: {directory}",
            hint="Run `goo render` first / 请先运行 goo render",
        )
    if not directory.is_dir():
        raise UsageError(f"not a directory: {directory}")
    if not exr and not png:
        raise UsageError("nothing to write", hint="Use --png and/or --no-exr / 请至少输出一种格式")
    if not any(directory.glob("*_beauty.*")) and not any(directory.glob("beauty.*")):
        raise UsageError(
            f"no beauty renders found in {directory}",
            hint="Run `goo render -o DIR` first / 该目录没有 goo render 输出的 beauty 通道",
        )
    if exposure < -20 or exposure > 20:
        raise UsageError("--exposure must be between -20 and +20 stops")
    if contrast <= 0 or contrast > 10:
        raise UsageError("--contrast must be in (0, 10]")
    job = {
        "out_dir": str(directory.resolve()),
        "png": bool(png),
        "exr": bool(exr),
        "exposure": float(exposure),
        "contrast": float(contrast),
        "apply_shadow": bool(shadow),
        "subtract_ambient": bool(subtract_ambient),
        "basename": basename or "composite",
    }
    install = blender_module.discover(blender)
    rr = blender_module.run_script(
        "composite.py",
        args=job,
        blender=install,
        timeout=timeout or 900.0,
        keep_temp=keep_temp,
        hide_window=True,
    )
    payload = _script_result(rr, "composite")
    payload["command"] = "composite"
    payload["output_dir"] = str(directory.resolve())
    for key, path in (payload.get("outputs") or {}).items():
        if not Path(path).is_file():
            raise JobFailed(
                f"composite did not write the {key.upper()} output",
                hint="Check disk space and permissions / 请检查磁盘空间和权限",
                stderr_tail=rr.output_tail(),
            )
    return payload


# ---------------------------------------------------------------------------
# add-on management
# ---------------------------------------------------------------------------
def install(
    blender: Optional[str] = None,
    *,
    dry_run: bool = False,
    target: Optional[str] = None,
    enable: bool = True,
    addon_path: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    payload = install_module.install(
        blender,
        dry_run=dry_run,
        target=target,
        enable=enable,
        addon_path=addon_path,
        timeout=timeout or 300.0,
    )
    payload["command"] = "install"
    return payload


def bundle(
    output: Optional[str] = None,
    *,
    addon_path: Optional[str] = None,
) -> dict:
    payload = install_module.bundle(output, addon_path=addon_path)
    payload["command"] = "bundle"
    return payload


def source_bundle(output: Optional[str] = None, *, root: Optional[str] = None) -> dict:
    payload = install_module.source_bundle(output, root=root)
    payload["command"] = "bundle"
    return payload


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def config_show() -> dict:
    return {
        "ok": True,
        "command": "config",
        "effective": config_module.load_config(),
        "files": [str(path) for path in config_module.config_paths()],
        "defaults": dict(config_module.DEFAULTS),
        "known_keys": list(config_module.KNOWN_KEYS),
    }


def config_set(
    updates: dict[str, Any],
    *,
    scope: str = "user",
) -> dict:
    cleaned = {key: value for key, value in updates.items() if value is not None}
    if not cleaned:
        raise UsageError("no configuration values given", hint="Example: goo config --blender <path> / 示例：goo config --blender <路径>")
    path = config_module.save_config(cleaned, scope=scope)
    return {
        "ok": True,
        "command": "config",
        "path": str(path),
        "scope": scope,
        "updates": cleaned,
        "effective": config_module.load_config(),
    }


def version_info() -> dict:
    addon_dir = addon_root() / "goo_npr"
    payload = {
        "ok": True,
        "command": "version",
        "cli": {
            "version": __version__,
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform_name(),
        },
        "addon": {
            "version": read_addon_version(addon_dir),
            "path": str(addon_dir),
            "exists": addon_dir.is_dir(),
        },
        "blender": None,
        "blender_error": None,
    }
    try:
        install = blender_module.discover()
    except Exception as exc:  # noqa: BLE001 - version must work without Blender
        payload["blender_error"] = getattr(exc, "message", str(exc))
    else:
        payload["blender"] = install.as_dict()
    return payload
