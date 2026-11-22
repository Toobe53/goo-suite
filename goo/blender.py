# SPDX-License-Identifier: GPL-3.0-or-later
"""Discovery of and communication with a Blender 5.2+ executable.

Everything in this module is stdlib only; ``bpy`` is never imported.  Blender is
always started as::

    blender --factory-startup --background [file.blend] \
            --python-exit-code 4 --python <script.py> -- <job.json>

Scripts report their result by printing a line ``GOO_RESULT: {json}`` and (more
reliably) by writing the same JSON to the ``result_file`` given in the job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from . import SCRIPTS_DIR, addon_root
from . import config as config_module
from .errors import BlenderNotFound, ConfigError, JobFailed, UsageError, join_hints, tail_lines
from .util import format_duration, parse_version, platform_name, plural, read_json, version_text

#: Lowest Blender release the GOO port supports.
MIN_BLENDER_VERSION: tuple[int, ...] = (5, 2, 0)
#: Preferred (highest known) minor for messaging.
PREFERRED_VERSION = "5.2"

DEFAULT_TIMEOUT = 900.0
VERSION_TIMEOUT = 240.0
RESULT_PREFIX = "GOO_RESULT:"

_VERSION_PROBE = "import bpy,sys;print('BLENDER_VERSION %d.%d.%d' % tuple(bpy.app.version))"
_VERSION_RE = re.compile(r"BLENDER_VERSION\s+(\d+)\.(\d+)\.(\d+)")
_RESULT_RE = re.compile(r"^GOO_RESULT:\s*(\{.*\})\s*$", re.MULTILINE)

_VERSION_CACHE: dict[str, tuple[tuple[int, ...], str]] = {}


# ---------------------------------------------------------------------------
# result object
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    """Outcome of one ``blender --background`` invocation.

    Unpacks like a ``(returncode, stdout, stderr)`` tuple for convenience while
    also exposing the parsed JSON payload in :attr:`result`.
    """

    returncode: int
    stdout: str
    stderr: str
    result: Optional[dict] = None
    command: list[str] = field(default_factory=list)
    duration: float = 0.0
    timed_out: bool = False
    job: Optional[dict] = None
    workdir: Optional[str] = None

    def __iter__(self) -> Iterator[Any]:
        yield self.returncode
        yield self.stdout
        yield self.stderr

    @property
    def ok(self) -> bool:
        if self.timed_out or self.returncode != 0:
            return False
        return not (self.result and self.result.get("ok") is False)

    @property
    def command_line(self) -> str:
        return subprocess.list2cmdline(self.command) if self.command else ""

    def output_tail(self, lines: int = 40) -> str:
        return tail_lines(self.stderr, lines) or tail_lines(self.stdout, lines)


def _parse_result(stdout: str, stderr: str, result_file: Optional[Path]) -> Optional[dict]:
    if result_file is not None:
        data = read_json(result_file)
        if data is not None:
            return data
    for text in (stdout, stderr):
        if not text:
            continue
        matches = _RESULT_RE.findall(text)
        if matches:
            try:
                data = json.loads(matches[-1])
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return None


def _with_workdir(rr: RunResult, text: str) -> str:
    if rr.workdir:
        text = (text + f"\n[job files kept in {rr.workdir}]").strip()
    return text


def ensure_ok(rr: RunResult, action: str = "Blender job") -> dict:
    """Raise :class:`JobFailed` unless *rr* succeeded, else return its payload."""
    if rr.timed_out:
        raise JobFailed(
            f"{action} timed out after {format_duration(rr.duration)}",
            hint=(
                "Increase --timeout, lower the resolution or samples / 增大 --timeout、降低分辨率或采样数"
            ),
            stderr_tail=_with_workdir(rr, rr.output_tail()),
            returncode=rr.returncode,
        )
    payload = rr.result
    failure = payload is not None and payload.get("ok") is False
    if rr.returncode != 0 or failure:
        if payload and payload.get("error"):
            message = str(payload.get("error"))
            hint = payload.get("hint")
        else:
            message = f"{action} failed (exit code {rr.returncode})"
            hint = "Inspect the Blender log below / 请查看下方的 Blender 日志"
        if payload and payload.get("traceback"):
            hint = hint or "See the traceback in the Blender log / 请查看 Blender 日志中的 traceback"
        raise JobFailed(
            message,
            hint=hint,
            stderr_tail=_with_workdir(rr, rr.output_tail()),
            result=payload or {},
            returncode=rr.returncode,
        )
    return payload or {}


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BlenderInstall:
    path: str
    version: tuple[int, ...]
    version_string: str
    source: str = "unknown"

    @property
    def display(self) -> str:
        return f"Blender {self.version_string} ({self.path})"

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "version": version_text(self.version),
            "version_string": self.version_string,
            "source": self.source,
        }


def _norm(path: os.PathLike[str] | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _glob(pattern: str) -> list[str]:
    try:
        return [str(p) for p in Path().glob(pattern)]
    except (OSError, ValueError):
        return []


def _from_env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip().strip('"')
    return None


def _windows_candidates() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    roots = []
    for env_name in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))
    if not roots:
        roots.append(Path(r"C:\Program Files"))
    patterns = ["Blender Foundation/Blender 5*/blender.exe", "Blender Foundation/Blender */blender.exe"]
    for root in roots:
        for pattern in patterns:
            for path in sorted(root.glob(pattern), reverse=True):
                found.append(("common install path", str(path)))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
        for pattern in ("Programs/Blender Foundation/Blender 5*/blender.exe", "Programs/Blender Foundation/Blender */blender.exe"):
            for path in sorted(base.glob(pattern), reverse=True):
                found.append(("local install path", str(path)))
        for pattern in ("Microsoft/WindowsApps/blender.exe",):
            for path in base.glob(pattern):
                found.append(("windows store", str(path)))
    for pattern in (
        r"C:\Program Files\Steam\steamapps\common\Blender\blender.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
    ):
        if Path(pattern).is_file():
            found.append(("steam", pattern))
    return found


def _mac_candidates(applications: Optional[os.PathLike[str] | str] = None) -> list[tuple[str, str]]:
    """Return Blender executables found in a macOS ``Applications`` directory."""
    apps = Path(applications) if applications is not None else Path("/Applications")
    found: list[tuple[str, str]] = []
    for path in (apps / "Blender.app/Contents/MacOS/Blender",):
        if path.is_file():
            found.append(("applications", str(path)))
    for path in sorted(apps.glob("Blender*/Blender.app/Contents/MacOS/Blender"), reverse=True):
        found.append(("applications", str(path)))
    return found


def _linux_candidates(
    bin_paths: Optional[Iterable[str]] = None,
    opt_root: Optional[os.PathLike[str] | str] = None,
) -> list[tuple[str, str]]:
    """Return Blender executables from the usual Linux install locations."""
    paths = tuple(bin_paths) if bin_paths is not None else (
        "/usr/bin/blender",
        "/usr/local/bin/blender",
        "/snap/bin/blender",
        "/opt/blender/blender",
    )
    opt = Path(opt_root) if opt_root is not None else Path("/opt")
    found: list[tuple[str, str]] = []
    for path in paths:
        if Path(path).is_file():
            found.append(("system path", path))
    for path in sorted(opt.glob("blender*/blender"), reverse=True):
        found.append(("opt", str(path)))
    return found


def candidate_paths(explicit: Optional[str] = None) -> list[tuple[str, str]]:
    """Return ``(source, path)`` pairs in discovery priority order."""
    pairs: list[tuple[str, str]] = []

    def add(source: str, path: Optional[str]) -> None:
        if path and str(path).strip():
            pairs.append((source, str(path).strip().strip('"')))

    add("--blender argument", explicit)
    add("$GOO_BLENDER", _from_env("GOO_BLENDER"))
    add("$GOO_BLENDER_52", _from_env("GOO_BLENDER_52"))

    try:
        cfg = config_module.load_config()
    except ConfigError:
        cfg = {}
    add("config file", cfg.get("blender"))

    if os.name == "nt":
        pairs.extend(_windows_candidates())
    elif sys.platform == "darwin":
        pairs.extend(_mac_candidates())
    else:
        pairs.extend(_linux_candidates())

    for name in ("blender", "blender.exe", "Blender"):
        found = shutil.which(name)
        if found:
            add("PATH", found)

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for source, path in pairs:
        key = _norm(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append((source, path))
    return unique


def blender_version(path: os.PathLike[str] | str, timeout: float = VERSION_TIMEOUT, use_cache: bool = True) -> Optional[tuple[int, ...]]:
    """Return the ``(major, minor, patch)`` version of *path*, or ``None``.

    Runs ``blender --background --python-expr`` once per executable; results are
    cached for the lifetime of the CLI process.
    """
    path = str(path)
    key = _norm(path)
    if use_cache and key in _VERSION_CACHE:
        return _VERSION_CACHE[key][0]
    if not Path(path).is_file():
        return None
    command = [path, "--factory-startup", "--background", "--python-exit-code", "1", "--python-expr", _VERSION_PROBE]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search(completed.stdout or "") or _VERSION_RE.search(completed.stderr or "")
    if not match:
        return None
    version = tuple(int(part) for part in match.groups())
    _VERSION_CACHE[key] = (version, ".".join(str(part) for part in version))
    return version


def blender_version_string(path: os.PathLike[str] | str) -> str:
    """Return a human readable version string such as ``"5.2.3"``."""
    version = blender_version(path)
    return version_text(version)


def _version_is_supported(version: Optional[tuple[int, ...]]) -> bool:
    if not version:
        return False
    padded = tuple(version) + (0,) * (3 - len(version))
    return padded[:3] >= MIN_BLENDER_VERSION


def discover(
    explicit: Optional[str] = None,
    *,
    validate: bool = True,
    min_version: tuple[int, ...] = MIN_BLENDER_VERSION,
) -> BlenderInstall:
    """Find a usable Blender 5.2+ executable or raise :class:`BlenderNotFound`."""
    tried: list[dict] = []
    pairs = candidate_paths(explicit)
    hard_sources = {"--blender argument", "$GOO_BLENDER", "$GOO_BLENDER_52"}

    for source, path in pairs:
        exists = Path(path).is_file()
        entry: dict[str, Any] = {"source": source, "path": path, "exists": exists}
        if not exists:
            entry["reason"] = "not found"
            tried.append(entry)
            if source in hard_sources:
                raise BlenderNotFound(
                    f"{source} points at {path}, which does not exist",
                    hint=(
                        "Fix the path or unset the variable / 请修正路径或删除该环境变量"
                    ),
                )
            continue
        if not validate:
            return BlenderInstall(path=path, version=(0, 0, 0), version_string="unverified", source=source)
        version = blender_version(path)
        entry["version"] = version_text(version)
        if version is None:
            entry["reason"] = "could not determine version (not a working Blender?)"
            tried.append(entry)
            if source in hard_sources:
                raise BlenderNotFound(
                    f"{source} points at {path} but it did not report a Blender version",
                    hint="The executable is broken or blocked by antivirus / 该可执行文件已损坏或被安全软件拦截",
                )
            continue
        padded = tuple(version) + (0,) * (3 - len(version))
        if padded[:3] < min_version:
            entry["reason"] = f"Blender {version_text(version)} is older than {version_text(min_version)}"
            tried.append(entry)
            if source in hard_sources:
                raise BlenderNotFound(
                    f"{source} points at Blender {version_text(version)}, but the GOO port needs Blender {PREFERRED_VERSION}+",
                    hint="Install Blender 5.2 LTS or pass a 5.2 executable / 请安装 Blender 5.2 LTS 或指定 5.2 的可执行文件",
                )
            continue
        _VERSION_CACHE[_norm(path)] = (version, ".".join(str(part) for part in version))
        return BlenderInstall(path=path, version=version, version_string=".".join(str(part) for part in version), source=source)

    raise BlenderNotFound(
        f"no Blender {PREFERRED_VERSION}+ installation found",
        hint=join_hints(
            "Install Blender 5.2 LTS from blender.org, then run `goo config --blender <path-to-blender.exe>`",
            "or set GOO_BLENDER / 或者安装 Blender 5.2 后运行 goo config --blender 指向 blender.exe",
            "Searched: " + _short_search_summary(pairs),
        ),
    )


def _short_search_summary(pairs: Iterable[tuple[str, str]], limit: int = 6) -> str:
    paths = [f"{source}: {path}" for source, path in pairs]
    if not paths:
        return "no candidates (Blender is not installed or not on PATH)"
    if len(paths) > limit:
        return "; ".join(paths[:limit]) + f"; ... ({len(paths) - limit} more)"
    return "; ".join(paths)


def find_blender(explicit: Optional[str] = None, *, required: bool = True) -> Optional[BlenderInstall]:
    """Like :func:`discover` but returns ``None`` instead of raising."""
    try:
        return discover(explicit)
    except BlenderNotFound:
        if required:
            raise
        return None


def diagnosis() -> dict:
    """Structured discovery report used by ``goo doctor`` and ``goo version``."""
    report: dict[str, Any] = {
        "platform": platform_name(),
        "min_version": version_text(MIN_BLENDER_VERSION),
        "env": {
            "GOO_BLENDER": os.environ.get("GOO_BLENDER") or None,
            "GOO_BLENDER_52": os.environ.get("GOO_BLENDER_52") or None,
        },
        "config_files": [str(p) for p in config_module.config_paths()],
        "candidates": [],
        "blender": None,
    }
    for source, path in candidate_paths():
        entry: dict[str, Any] = {"source": source, "path": path, "exists": Path(path).is_file()}
        if entry["exists"]:
            version = blender_version(path)
            entry["version"] = version_text(version)
            entry["supported"] = _version_is_supported(version)
        report["candidates"].append(entry)
    try:
        install = discover()
    except BlenderNotFound as exc:
        report["error"] = exc.message
        report["hint"] = exc.hint
    else:
        report["blender"] = install.as_dict()
    addon = addon_root() / "goo_npr"
    report["addon"] = {
        "path": str(addon),
        "exists": addon.is_dir(),
        "version": None,
    }
    from .util import read_addon_version

    report["addon"]["version"] = read_addon_version(addon)
    return report


# ---------------------------------------------------------------------------
# running scripts
# ---------------------------------------------------------------------------
def resolve_script(script: os.PathLike[str] | str) -> Path:
    text = str(script)
    path = Path(text)
    if path.is_absolute() or os.sep in text or (os.altsep and os.altsep in text):
        if not path.is_file():
            raise UsageError(f"Blender script not found: {path}")
        return path
    candidate = SCRIPTS_DIR / text
    if candidate.suffix != ".py":
        candidate = candidate.with_suffix(".py")
    if not candidate.is_file():
        raise UsageError(f"Blender script not found: {candidate}", hint="The goo package is incomplete; reinstall it.")
    return candidate


def build_command(
    blender: BlenderInstall | str,
    script: os.PathLike[str] | str,
    blend: Optional[os.PathLike[str] | str] = None,
    job_path: Optional[os.PathLike[str] | str] = None,
    *,
    factory_startup: bool = True,
    extra_args: Optional[Iterable[str]] = None,
) -> list[str]:
    executable = blender.path if isinstance(blender, BlenderInstall) else str(blender)
    command: list[str] = [executable]
    if factory_startup:
        command.append("--factory-startup")
    command.append("--background")
    if blend:
        command.append(str(blend))
    command.extend(["--python-exit-code", "4", "--python", str(script), "--"])
    if job_path:
        command.append(str(job_path))
    if extra_args:
        command.extend(str(arg) for arg in extra_args)
    return command


def _child_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update(extra)
    return env


def run_script(
    script: os.PathLike[str] | str,
    blend: Optional[os.PathLike[str] | str] = None,
    args: Optional[dict[str, Any] | os.PathLike[str] | str] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    capture: bool = True,
    blender: Optional[BlenderInstall | str] = None,
    cwd: Optional[os.PathLike[str] | str] = None,
    env: Optional[dict[str, str]] = None,
    keep_temp: bool = False,
    hide_window: bool = False,
    extra_args: Optional[Iterable[str]] = None,
) -> RunResult:
    """Run *script* inside Blender.

    Parameters
    ----------
    script:
        File name inside ``goo/scripts`` (``"render_passes.py"``) or an absolute
        path to any script.
    blend:
        Optional ``.blend`` file opened before the script runs.
    args:
        Job description.  A dict is serialised to a temporary ``job.json`` whose
        path is passed after ``--``; a path is passed through unchanged.
    capture:
        Capture stdout/stderr (needed to read the result).  When ``False`` the
        child inherits the console and only the result file is read.

    Returns
    -------
    :class:`RunResult` (unpacks as ``(returncode, stdout, stderr)``).
    """
    install = blender if isinstance(blender, BlenderInstall) else discover(blender)
    script_path = resolve_script(script)
    workdir: Optional[Path] = None
    job_path: Optional[Path] = None
    result_path: Optional[Path] = None
    job: Optional[dict[str, Any]] = None

    if isinstance(args, dict):
        workdir = Path(tempfile.mkdtemp(prefix="goo-cli-"))
        job = dict(args)
        job.setdefault("result_file", str(workdir / "result.json"))
        job.setdefault("addon_root", str(addon_root()))
        job.setdefault("cli_python", sys.executable)
        job.setdefault("cli_version", __import__("goo").__version__)
        job_path = workdir / "job.json"
        job_path.write_text(json.dumps(job, indent=2, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8")
        result_path = Path(job["result_file"])
    elif args is not None:
        job_path = Path(args)
        if not job_path.is_file():
            raise UsageError(f"job file not found: {job_path}")
        result_path = None

    command = build_command(install, script_path, blend, job_path, extra_args=extra_args)
    started = time.monotonic()
    stdout = ""
    stderr = ""
    returncode = 0
    timed_out = False

    run_env = _child_env(env)
    popen_kwargs: dict[str, Any] = {"cwd": str(cwd) if cwd else None, "env": run_env}
    if hide_window and os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        if capture:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
                **popen_kwargs,
            )
            returncode = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        else:
            completed = subprocess.run(command, timeout=timeout, **popen_kwargs)
            returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = _decode_partial(exc.stdout)
        stderr = _decode_partial(exc.stderr)
    except FileNotFoundError as exc:
        raise BlenderNotFound(f"cannot execute {install.path}: {exc}", hint="Check the Blender path / 请检查 Blender 路径")
    except OSError as exc:
        raise JobFailed(f"failed to start Blender: {exc}", hint="Check antivirus / permissions / 请检查杀毒软件或权限")
    finally:
        duration = time.monotonic() - started

    payload = _parse_result(stdout, stderr, result_path if result_path and result_path.is_file() else None)
    rr = RunResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        result=payload,
        command=command,
        duration=duration,
        timed_out=timed_out,
        job=job,
        workdir=str(workdir) if workdir else None,
    )
    if workdir is not None and (keep_temp or not rr.ok):
        # keep failing jobs around for debugging (the path is printed by the CLI)
        if not keep_temp:
            rr.workdir = str(workdir)
    elif workdir is not None:
        _cleanup(workdir)
        rr.workdir = None
    return rr


def _decode_partial(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _cleanup(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def count_job_files(job: Optional[dict]) -> str:
    if not job:
        return ""
    return plural(len(job), "job key")
