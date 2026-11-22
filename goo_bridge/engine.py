# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter for the optional GOO node-group engine.

GOO Bridge is useful without the ``goo-port`` project: stock Blender can render
any scene that does not depend on GOO's custom shader nodes.  Scenes that *do*
contain the L2 node groups are different: those groups have to be materialised
into the .blend first, which is what the external ``goo`` CLI does.

Detection is deliberately best-effort and never imports ``bpy``.  In order:

1. an explicit path (``detect_engine(explicit=...)``),
2. ``GOO_BRIDGE_GOO`` (an executable path or a command name),
3. a ``goo``/``goo.exe`` executable on ``PATH``,
4. a local ``goo-port`` package (``GOO_BRIDGE_GOO_PACKAGE``, an importable
   ``goo`` package, or the sibling checkout used by the development tree).

The engine's public surface is small:

``EngineInfo``
    ``found``, ``path``, ``version`` and ``groups`` (registered node groups).
``apply_scene(blend, out)``
    Run ``goo apply`` and return a JSON-serialisable result.
``render_scene(blend, out, ...)``
    Prefer ``goo render``; when no engine is available fall back to Blender's
    background renderer (``scripts/engine_render.py``).

Both functions return an :class:`EngineResult`, a ``dict`` subclass.  The
result intentionally mirrors the shape used by :mod:`goo_bridge.render` so the
CLI can print it as JSON.  Engine failures are reported as ``ok: false`` with
an ``error`` string instead of raising; a broken optional engine should not
take down the bridge.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .blender import BlenderError, BlenderInfo, find_blender, run_blender
from .jobs import RenderJob

ENGINE_ENV = "GOO_BRIDGE_GOO"
PACKAGE_ENV = "GOO_BRIDGE_GOO_PACKAGE"

_GROUP_RE = re.compile(r"^\s*@group\s*\(", re.MULTILINE)
_VERSION_RE = re.compile(r"([0-9][0-9A-Za-z.\-]*(?:\.[0-9][0-9A-Za-z.\-]*)+)")
_GOO_VERSION_RE = re.compile(r"(?:goo(?:-bridge)?\s+)([0-9][0-9A-Za-z.\-]*)", re.IGNORECASE)


class EngineError(RuntimeError):
    """Raised internally when the engine command cannot be started."""


class EngineResult(dict):
    """A JSON payload with attribute access (``result.ok`` == ``result["ok"]``)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - normal AttributeError path
            raise AttributeError(name) from exc

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


@dataclass
class EngineInfo:
    """What :func:`detect_engine` found.

    ``path`` is the executable path or, for the Python-package fallback, the
    directory of the ``goo`` package.  ``groups`` is the number of ``@group``
    registrations the adapter can see; it is a static count when the engine
    cannot be asked without starting Blender.
    """

    found: bool = False
    path: str = ""
    version: str = ""
    groups: int = 0
    source: str = "none"  # "executable" | "package" | "none"
    addon_path: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Minimal stable shape consumed by ``goo-bridge doctor --json``."""
        return {
            "found": bool(self.found),
            "path": self.path,
            "version": self.version,
            "groups": int(self.groups),
        }

    @property
    def is_executable(self) -> bool:
        return self.found and self.source == "executable"

    @property
    def is_package(self) -> bool:
        return self.found and self.source == "package"

    def command(self) -> list[str]:
        """The argv prefix used to invoke the engine, or ``[]``."""
        if self.is_executable and self.path:
            return _command_for_path(Path(self.path))
        if self.is_package:
            return [sys.executable, "-m", "goo"]
        return []

    def environment(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """Environment for package invocations (keeps ``python -m goo`` working)."""
        env = dict(os.environ if base is None else base)
        if self.is_package and self.path:
            parent = str(Path(self.path).expanduser().resolve().parent)
            previous = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = parent + (os.pathsep + previous if previous else "")
        return env


# ---------------------------------------------------------------------------
# detection helpers
# ---------------------------------------------------------------------------
def _command_for_path(path: Path) -> list[str]:
    """Return an argv prefix for *path*.

    Python entry points (used by tests and portable installs) are executed with
    the current interpreter so a missing execute bit or Windows file
    association cannot hide them.
    """
    if path.suffix.lower() in {".py", ".pyw"}:
        return [sys.executable, str(path)]
    return [str(path)]


def _which(command: str) -> Path | None:
    found = shutil.which(command)
    return Path(found) if found else None


def _extract_json(text: str) -> dict[str, Any] | None:
    """Return the most useful JSON object embedded in *text*.

    ``goo`` prints clean JSON in ``--json`` mode, but tests and wrappers often
    prepend human-readable lines.  Parsing every ``{`` with ``raw_decode``
    keeps both cases working without a brittle "find the last line" rule.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict):
        return value
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if not candidates:
        return None
    preferred = {"ok", "command", "version", "cli", "addon", "groups", "spec_count", "passes"}
    for candidate in reversed(candidates):
        if preferred.intersection(candidate):
            return candidate
    return candidates[-1]


def _text_version(text: str) -> str:
    match = _GOO_VERSION_RE.search(text or "")
    if match:
        return match.group(1)
    # A bare "0.1.0" is only used when the probe output actually mentions goo,
    # so a random error message cannot become a version.
    if re.search(r"\bgoo\b", text or "", re.IGNORECASE):
        match = _VERSION_RE.search(text)
        if match:
            return match.group(1)
    return ""


def _payload_version(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    candidates: list[Any] = [
        payload.get("version"),
        _dig(payload, "cli", "version"),
        _dig(payload, "goo", "version"),
        _dig(payload, "engine", "version"),
        _dig(payload, "addon", "version"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _payload_groups(payload: Mapping[str, Any] | None) -> int:
    if not isinstance(payload, Mapping):
        return 0
    for key in ("groups", "group_count", "goo_group_count", "spec_count", "node_group_count"):
        value = payload.get(key)
        number = _as_count(value)
        if number is not None:
            return number
    node_groups = payload.get("node_groups")
    if isinstance(node_groups, Mapping):
        for key in ("total", "count", "goo"):
            number = _as_count(node_groups.get(key))
            if number is not None:
                return number
    addon = payload.get("addon")
    if isinstance(addon, Mapping):
        for key in ("groups", "group_count", "node_groups"):
            number = _as_count(addon.get(key))
            if number is not None:
                return number
    specs = payload.get("specs")
    if isinstance(specs, list):
        return len(specs)
    return 0


def _as_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return None


def _dig(payload: Mapping[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _payload_addon_path(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for key in ("addon_path", "addon_dir"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    addon = payload.get("addon")
    if isinstance(addon, str) and addon.strip():
        return addon.strip()
    if isinstance(addon, Mapping):
        for key in ("path", "dir", "root"):
            value = addon.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = _dig(payload, "engine", "addon_path")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _normalise_addon(path: str | os.PathLike[str] | None) -> Path | None:
    """Accept a goo_npr dir, a repo root, or a nodes dir; return goo_npr."""
    if not path:
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return None
    if candidate.is_file():
        candidate = candidate.parent
    if candidate.name == "goo_npr":
        return candidate
    if (candidate / "goo_npr").is_dir():
        return candidate / "goo_npr"
    if candidate.name == "nodes" and candidate.parent.name == "goo_npr":
        return candidate.parent
    if candidate.name == "goo_npr" or (candidate / "nodes").is_dir():
        return candidate
    return candidate


def _count_groups(addon: Path | None) -> int:
    """Count ``@group(...)`` decorators without importing Blender's ``bpy``."""
    if addon is None or not addon.is_dir():
        return 0
    nodes = addon / "nodes" if (addon / "nodes").is_dir() else addon
    if not nodes.is_dir():
        return 0
    total = 0
    for path in nodes.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(_GROUP_RE.findall(text))
    return total


def _probe_executable(path: Path) -> tuple[str, int, str, str]:
    """Return ``(version, groups, addon_path, log)`` for an executable."""
    base = _command_for_path(path)
    attempts = [base + ["version", "--json"], base + ["--version"], base + ["-V"]]
    log_parts: list[str] = []
    for command in attempts:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        log_parts.append(text.strip())
        payload = _extract_json(text)
        version = _payload_version(payload) or _text_version(text)
        groups = _payload_groups(payload)
        addon = _payload_addon_path(payload)
        if completed.returncode == 0 and (payload is not None or version):
            return version, groups, addon, "\n".join(part for part in log_parts if part)[-4000:]
        if payload is not None and (version or groups):
            return version, groups, addon, "\n".join(part for part in log_parts if part)[-4000:]
    return "", 0, "", "\n".join(part for part in log_parts if part)[-4000:]


def _package_dir_from_root(root: Path) -> Path | None:
    root = root.expanduser()
    if root.is_file():
        root = root.parent
    if (root / "goo" / "__init__.py").is_file():
        return root / "goo"
    if root.name == "goo" and (root / "__init__.py").is_file():
        return root
    if root.name == "goo_npr" and (root.parent / "goo" / "__init__.py").is_file():
        return root.parent / "goo"
    return None


def _package_roots() -> Iterable[Path]:
    for env_name in (PACKAGE_ENV, "GOO_PORT_HOME"):
        value = os.environ.get(env_name)
        if value:
            yield Path(value).expanduser()
    try:
        spec = importlib.util.find_spec("goo")
    except (ImportError, AttributeError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        yield Path(spec.origin).resolve().parent
    here = Path(__file__).resolve()
    workspace = here.parents[2] if len(here.parents) > 2 else here.parent
    yield workspace / "goo-port"
    yield workspace / "goo"
    cwd = Path.cwd()
    yield cwd / "goo-port"
    yield cwd / "goo"


def _detect_package() -> EngineInfo | None:
    seen: set[str] = set()
    for root in _package_roots():
        package = _package_dir_from_root(root)
        if package is None:
            continue
        key = str(package.resolve())
        if key in seen:
            continue
        seen.add(key)
        addon = _normalise_addon(package.parent / "goo_npr")
        if addon is None or not addon.is_dir():
            continue
        version = ""
        try:
            text = (package / "__init__.py").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
        if match:
            version = match.group(1).strip()
        return EngineInfo(
            found=True,
            path=str(package.resolve()),
            version=version,
            groups=_count_groups(addon),
            source="package",
            addon_path=str(addon.resolve()),
        )
    return None


def _executable_candidate(explicit: str | os.PathLike[str] | None) -> tuple[Path | None, str]:
    if explicit is not None:
        raw = str(explicit)
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return candidate.resolve(), ""
        found = shutil.which(raw) if raw and not any(sep in raw for sep in ("/", "\\")) else None
        if found:
            return Path(found).resolve(), ""
        return None, f"GOO engine not found: {raw}"
    env_value = os.environ.get(ENGINE_ENV, "").strip()
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.is_file():
            return candidate.resolve(), ""
        found = shutil.which(env_value)
        if found:
            return Path(found).resolve(), ""
        return None, f"GOO engine from {ENGINE_ENV} not found: {env_value}"
    found = _which("goo")
    if found:
        return found.resolve(), ""
    return None, ""


def detect_engine(
    explicit: str | os.PathLike[str] | None = None,
    *,
    use_package: bool = True,
    probe: bool = True,
) -> EngineInfo:
    """Locate the GOO engine.

    Parameters are intentionally small: ``explicit`` is the same path a user
    would configure, ``use_package`` allows disabling the Python-package
    fallback, and ``probe`` can be turned off in tests that only care about
    file discovery.
    """
    path, error = _executable_candidate(explicit)
    if path is not None:
        version, groups, addon_path, _log = _probe_executable(path) if probe else ("", 0, "", "")
        addon = _normalise_addon(addon_path)
        if addon is not None and groups == 0:
            groups = _count_groups(addon)
        return EngineInfo(
            found=True,
            path=str(path),
            version=version,
            groups=groups,
            source="executable",
            addon_path=str(addon) if addon is not None else "",
        )
    if explicit is not None:
        return EngineInfo(error=error or f"GOO engine not found: {explicit}")
    if error:
        return EngineInfo(error=error)
    if use_package:
        package = _detect_package()
        if package is not None:
            return package
    return EngineInfo(error="GOO engine not found")


# ---------------------------------------------------------------------------
# engine invocation
# ---------------------------------------------------------------------------
def _run_engine(
    info: EngineInfo,
    arguments: Sequence[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    command = info.command()
    if not command:
        raise EngineError("GOO engine command is empty")
    try:
        return subprocess.run(
            [*command, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, int(timeout)),
            env=info.environment(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineError(f"GOO engine timed out after {timeout}s: {' '.join(command)}") from exc
    except OSError as exc:
        raise EngineError(f"could not start GOO engine: {exc}") from exc


def _engine_log(completed: subprocess.CompletedProcess[str]) -> str:
    return ((completed.stderr or "") + "\n" + (completed.stdout or "")).strip()[-4000:]


def _blender_arguments(blender: BlenderInfo | str | Path | None) -> list[str]:
    if blender is None:
        return []
    if isinstance(blender, BlenderInfo):
        return ["--blender", str(blender.path)]
    return ["--blender", str(blender)]


def _result_failure(
    message: str,
    *,
    mode: str,
    info: EngineInfo | None = None,
    log: str = "",
    started: float | None = None,
) -> EngineResult:
    payload: EngineResult = EngineResult(
        ok=False,
        mode=mode,
        error=message,
        files=[],
        log=log,
        duration=round(time.time() - started, 3) if started is not None else 0.0,
    )
    if info is not None:
        payload["engine"] = info.to_dict()
    return payload


def apply_scene(
    blend: str | os.PathLike[str],
    out: str | os.PathLike[str],
    *,
    engine: EngineInfo | None = None,
    blender: BlenderInfo | str | Path | None = None,
    addon: str | os.PathLike[str] | None = None,
    timeout: int = 900,
) -> EngineResult:
    """Materialise the GOO node library into *blend* and write *out*.

    The engine CLI is used because building node groups means executing
    ``goo_npr`` inside Blender.  When no engine is available the call returns a
    failure instead of silently writing an unprepared copy.
    """
    started = time.time()
    scene = Path(blend).expanduser().resolve()
    target = Path(out).expanduser().resolve()
    info = engine if engine is not None else detect_engine()
    if not scene.is_file():
        return _result_failure(f"scene not found: {scene}", mode="engine", info=info, started=started)
    if not info.found:
        return _result_failure(
            info.error or "GOO engine not found; install goo-port or set GOO_BRIDGE_GOO",
            mode="engine",
            info=info,
            started=started,
        )
    if target == scene:
        arguments = ["apply", str(scene), "--in-place", "--json"]
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        arguments = ["apply", str(scene), "-o", str(target), "--json"]
    arguments.extend(_blender_arguments(blender))
    if addon is not None:
        arguments.extend(["--addon", str(addon)])
    arguments.extend(["--timeout", str(max(30, int(timeout)))])
    try:
        completed = _run_engine(info, arguments, timeout=timeout + 120)
    except EngineError as exc:
        return _result_failure(str(exc), mode="engine", info=info, started=started)
    log = _engine_log(completed)
    payload = _extract_json(completed.stdout) or _extract_json(completed.stderr) or {}
    if completed.returncode != 0:
        return _result_failure(
            f"goo apply failed (exit {completed.returncode})",
            mode="engine",
            info=info,
            log=log,
            started=started,
        )
    if payload.get("ok") is False:
        return _result_failure(
            str(payload.get("error") or "goo apply reported failure"),
            mode="engine",
            info=info,
            log=log,
            started=started,
        )
    blend_out = Path(str(payload.get("blend_out") or payload.get("target") or target)).expanduser()
    if not blend_out.is_file():
        return _result_failure(
            f"goo apply finished but the output blend is missing: {blend_out}",
            mode="engine",
            info=info,
            log=log,
            started=started,
        )
    return EngineResult(
        ok=True,
        mode="engine",
        engine=info.to_dict(),
        source=str(scene),
        target=str(target),
        blend_out=str(blend_out),
        files=[str(blend_out)],
        log=log,
        error="",
        duration=round(time.time() - started, 3),
        settings={"build_library": not bool(payload.get("no_build")), "addon": str(addon or "")},
        payload=payload,
    )


def _goo_format(format_name: str) -> str:
    value = str(format_name or "PNG").strip().lower()
    if value in {"exr", "open_exr", "openexr"}:
        return "exr"
    return "png"


def _files_from_render_payload(payload: Mapping[str, Any], output: Path) -> tuple[list[str], str]:
    """Return ``(files, manifest)`` from a goo render JSON payload."""
    files: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = output / candidate
        text = str(candidate)
        if text not in files:
            files.append(text)

    for key in ("files", "outputs", "renders"):
        value = payload.get(key)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item.get("file") if isinstance(item, Mapping) else item)
        elif isinstance(value, Mapping):
            for item in value.values():
                add(item)
    passes = payload.get("passes")
    if isinstance(passes, list):
        for item in passes:
            if isinstance(item, Mapping):
                add(item.get("file") or item.get("path"))
    manifest = ""
    for key in ("manifest", "manifest_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            manifest = str(Path(value).expanduser())
            break
    if manifest and Path(manifest).is_file():
        try:
            data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for key in ("files", "outputs"):
            value = data.get(key) if isinstance(data, Mapping) else None
            if isinstance(value, list):
                for item in value:
                    add(item)
            elif isinstance(value, Mapping):
                for item in value.values():
                    add(item)
    if manifest == "":
        candidate = output / "manifest.json"
        if candidate.is_file():
            manifest = str(candidate)
    if not files:
        for pattern in ("*.png", "*.exr", "*.jpg", "*.jpeg", "*.tif", "*.tiff"):
            for path in sorted(output.glob(pattern)):
                add(str(path))
    return files, manifest


def _render_with_engine(
    info: EngineInfo,
    scene: Path,
    output: Path,
    *,
    camera: str,
    view_layer: str,
    samples: int,
    resolution: int,
    format_name: str,
    blender: BlenderInfo | str | Path | None,
    timeout: int,
) -> EngineResult:
    started = time.time()
    format_arg = _goo_format(format_name)
    arguments = [
        "render",
        str(scene),
        "-o",
        str(output),
        "--json",
        "--samples",
        str(max(1, int(samples))),
        "--resolution-percentage",
        str(max(1, min(1000, int(resolution)))),
        "--format",
        format_arg,
        "--timeout",
        str(max(30, int(timeout))),
    ]
    if camera:
        arguments.extend(["--camera", str(camera)])
    if view_layer:
        arguments.extend(["--view-layer", str(view_layer)])
    arguments.extend(_blender_arguments(blender))
    try:
        completed = _run_engine(info, arguments, timeout=timeout + 300)
    except EngineError as exc:
        return _result_failure(str(exc), mode="engine", info=info, started=started)
    log = _engine_log(completed)
    payload = _extract_json(completed.stdout) or _extract_json(completed.stderr) or {}
    if completed.returncode != 0:
        return _result_failure(
            f"goo render failed (exit {completed.returncode})",
            mode="engine",
            info=info,
            log=log,
            started=started,
        )
    if payload.get("ok") is False:
        return _result_failure(
            str(payload.get("error") or "goo render reported failure"),
            mode="engine",
            info=info,
            log=log,
            started=started,
        )
    files, manifest = _files_from_render_payload(payload, output)
    settings = payload.get("settings") if isinstance(payload.get("settings"), Mapping) else {}
    return EngineResult(
        ok=True,
        mode="engine",
        engine=info.to_dict(),
        source=str(scene),
        output_dir=str(output),
        files=files,
        manifest=manifest,
        log=log,
        error="",
        duration=round(float(payload.get("seconds") or payload.get("duration") or (time.time() - started)), 3),
        settings=dict(settings),
        payload=payload,
    )


def _render_with_blender(
    scene: Path,
    output: Path,
    *,
    camera: str,
    view_layer: str,
    samples: int,
    resolution: int,
    frame: str,
    format_name: str,
    blender: BlenderInfo | str | Path | None,
    timeout: int,
) -> EngineResult:
    started = time.time()
    try:
        info = blender if isinstance(blender, BlenderInfo) else find_blender(str(blender) if blender else None)
    except BlenderError as exc:
        return _result_failure(str(exc), mode="blender", started=started)
    script = Path(__file__).resolve().parent / "scripts" / "engine_render.py"
    if not script.is_file():
        return _result_failure(f"fallback render script missing: {script}", mode="blender", started=started)
    output.mkdir(parents=True, exist_ok=True)
    job = RenderJob(
        scene=str(scene),
        output=str(output),
        camera=camera,
        view_layer=view_layer,
        samples=max(1, int(samples)),
        resolution=max(1, min(1000, int(resolution))),
        frame=str(frame),
        format=str(format_name or "PNG").upper(),
        engine="blender",
    )
    with tempfile.TemporaryDirectory(prefix="goo-bridge-engine-") as temp:
        job_path = job.write(Path(temp) / "job.json")
        try:
            completed = run_blender(
                info,
                ["--background", str(scene), "--factory-startup", "--python", str(script), "--", "--job", str(job_path)],
                timeout=timeout,
            )
        except BlenderError as exc:
            return _result_failure(str(exc), mode="blender", started=started)
        log = ((completed.stderr or "") + "\n" + (completed.stdout or "")).strip()[-4000:]
        if completed.returncode != 0:
            return _result_failure(
                f"Blender fallback render failed (exit {completed.returncode})",
                mode="blender",
                log=log,
                started=started,
            )
        manifest_path = output / "render_manifest.json"
        if not manifest_path.is_file():
            return _result_failure(
                "Blender finished without render_manifest.json",
                mode="blender",
                log=log,
                started=started,
            )
        try:
            manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _result_failure(f"cannot read render_manifest.json: {exc}", mode="blender", log=log, started=started)
        files = [str(path) for path in manifest.get("files", [])]
        missing = [path for path in files if not Path(path).is_file()]
        if missing:
            return _result_failure(
                f"render reported missing files: {missing[:3]}",
                mode="blender",
                log=log,
                started=started,
            )
        return EngineResult(
            ok=True,
            mode="blender",
            engine=EngineInfo().to_dict(),
            source=str(scene),
            output_dir=str(output),
            files=files,
            manifest=str(manifest_path),
            log=log,
            error="",
            duration=float(manifest.get("duration") or round(time.time() - started, 3)),
            settings=manifest.get("settings", job.to_dict()),
            payload=manifest,
        )


def render_scene(
    blend: str | os.PathLike[str],
    out: str | os.PathLike[str],
    *,
    camera: str = "",
    view_layer: str = "",
    samples: int = 32,
    resolution: int = 100,
    frame: str = "1",
    format: str = "PNG",
    engine: EngineInfo | None = None,
    blender: BlenderInfo | str | Path | None = None,
    timeout: int = 3600,
    prefer_engine: bool = True,
) -> EngineResult:
    """Render *blend* to *out*, preferring the GOO engine.

    ``engine`` may be an already-detected :class:`EngineInfo`; passing
    ``EngineInfo()`` (or ``prefer_engine=False``) forces the Blender fallback,
    which is useful for scenes without L2 node groups and for tests.
    """
    started = time.time()
    scene = Path(blend).expanduser().resolve()
    output = Path(out).expanduser().resolve()
    if not scene.is_file():
        return _result_failure(f"scene not found: {scene}", mode="none", started=started)
    info = engine if engine is not None else (detect_engine() if prefer_engine else EngineInfo())
    if prefer_engine and info.found:
        return _render_with_engine(
            info,
            scene,
            output,
            camera=camera,
            view_layer=view_layer,
            samples=samples,
            resolution=resolution,
            format_name=format,
            blender=blender,
            timeout=timeout,
        )
    return _render_with_blender(
        scene,
        output,
        camera=camera,
        view_layer=view_layer,
        samples=samples,
        resolution=resolution,
        frame=frame,
        format_name=format,
        blender=blender,
        timeout=timeout,
    )


__all__ = [
    "ENGINE_ENV",
    "PACKAGE_ENV",
    "EngineError",
    "EngineInfo",
    "EngineResult",
    "apply_scene",
    "detect_engine",
    "render_scene",
]
