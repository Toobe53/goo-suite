# SPDX-License-Identifier: GPL-3.0-or-later
"""Build, transfer, execute and verify a remote render job bundle.

A bundle is an ordinary directory::

    scene.blend          the file Blender opens
    job.json             render settings (RenderJob)
    assets.json          external asset manifest with SHA-256 hashes
    remote_worker.py     Blender-side script that renders and writes results
    assets/...           copied input files (optional)

The same bundle is uploaded over SSH or copied into a :class:`LocalTransport`
directory; the remote side only needs stock Blender 5.2, no Python packages.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from . import __version__
from .assets import Asset, manifest, sha256_file
from .fsutil import atomic_write_text
from .jobs import RenderJob
from .transport import ProcResult, Transport, TransportError

__all__ = [
    "WorkerError",
    "BUNDLE_SCENE",
    "BUNDLE_JOB",
    "BUNDLE_ASSETS",
    "BUNDLE_SCRIPT",
    "BUNDLE_RESULT",
    "BUNDLE_STATUS",
    "BUNDLE_OUTPUT",
    "JOBS_DIR",
    "RESULT_MARKER",
    "worker_script",
    "build_bundle",
    "upload_bundle",
    "remote_command",
    "render_remote",
    "fetch_results",
    "job_status",
    "clean_jobs",
    "test_connection",
    "parse_worker_result",
]

BUNDLE_SCENE = "scene.blend"
BUNDLE_JOB = "job.json"
BUNDLE_ASSETS = "assets.json"
BUNDLE_SCRIPT = "remote_worker.py"
BUNDLE_RESULT = "result.json"
BUNDLE_STATUS = "status.json"
BUNDLE_OUTPUT = "output"
JOBS_DIR = "jobs"
RESULT_MARKER = "GOO_REMOTE_RESULT:"


class WorkerError(RuntimeError):
    """Raised for local problems while preparing or collecting a bundle."""


Progress = Callable[[str], None]


def _log(log: Optional[Progress], message: str) -> None:
    if log is not None:
        log(message)


def worker_script() -> Path:
    return Path(__file__).resolve().parent / "scripts" / "remote_worker.py"


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _split_assets(assets: Iterable[Asset]) -> list[Asset]:
    return [asset for asset in assets]


def build_bundle(
    scene: str | Path,
    job: RenderJob,
    dest: str | Path,
    *,
    assets: Iterable[Asset] = (),
    log: Optional[Progress] = None,
) -> dict[str, Any]:
    """Create a self-contained render bundle and return its manifest.

    The scene is copied (never moved) as ``scene.blend``; the job's ``scene``
    and ``output`` fields are rewritten to bundle-relative paths so the bundle
    can be moved to another machine unchanged.  A missing asset is a hard
    error (:class:`WorkerError`) so a render never silently loses a texture.
    """
    scene_path = Path(scene).expanduser().resolve()
    if not scene_path.is_file():
        raise WorkerError(f"scene not found: {scene_path}")
    script = worker_script()
    if not script.is_file():
        raise WorkerError(f"remote worker script missing: {script}")

    asset_list: list[Asset] = []
    missing_assets: list[Asset] = []
    for asset in _split_assets(assets):
        source = Path(asset.path).expanduser()
        if source.is_file():
            asset_list.append(asset)
        else:
            missing_assets.append(asset)
    if missing_assets:
        names = ", ".join(
            asset.name or Path(asset.path).name or str(asset.path)
            for asset in missing_assets
        )
        raise WorkerError(
            f"missing asset(s): {names}. Copy them into the project or pass "
            "existing files with --asset."
        )

    bundle = Path(dest).expanduser().resolve()
    bundle.mkdir(parents=True, exist_ok=True)

    local_job = RenderJob.from_dict(job.to_dict())
    local_job.scene = BUNDLE_SCENE
    local_job.output = BUNDLE_OUTPUT
    metadata = dict(local_job.metadata or {})
    metadata.update(
        {
            "bundle_version": 1,
            "bridge_version": __version__,
            "original_scene": str(scene_path),
        }
    )
    local_job.metadata = metadata

    files: list[dict[str, Any]] = []

    def _register(name: str, source: Path) -> None:
        files.append(
            {
                "name": name,
                "size": source.stat().st_size,
                "sha256": _sha256(source),
            }
        )

    shutil.copyfile(scene_path, bundle / BUNDLE_SCENE)
    _register(BUNDLE_SCENE, bundle / BUNDLE_SCENE)

    shutil.copyfile(script, bundle / BUNDLE_SCRIPT)
    _register(BUNDLE_SCRIPT, bundle / BUNDLE_SCRIPT)

    bundled_assets: list[dict[str, Any]] = []
    asset_root = bundle / "assets"
    used: set[str] = set()
    for index, asset in enumerate(asset_list, start=1):
        source = Path(asset.path).expanduser()
        name = source.name or f"asset_{index:02d}"
        if name in used:
            name = f"{index:02d}_{name}"
        used.add(name)
        asset_root.mkdir(parents=True, exist_ok=True)
        target = asset_root / name
        shutil.copy2(source, target)
        rel = posixpath.join("assets", name)
        _register(rel, target)
        bundled_assets.append(
            {
                "source": str(source),
                "bundle_name": rel,
                "name": asset.name or name,
                "kind": asset.kind,
                "size": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )

    assets_payload = manifest(assets)
    assets_payload["bundled"] = bundled_assets
    atomic_write_text(
        bundle / BUNDLE_ASSETS,
        json.dumps(assets_payload, indent=2, ensure_ascii=True) + "\n",
    )
    _register(BUNDLE_ASSETS, bundle / BUNDLE_ASSETS)

    local_job.write(bundle / BUNDLE_JOB)
    _register(BUNDLE_JOB, bundle / BUNDLE_JOB)

    _log(log, f"bundle: {len(files)} file(s), scene {scene_path.name}")
    return {
        "ok": True,
        "bundle_dir": str(bundle),
        "scene": {
            "name": BUNDLE_SCENE,
            "original": str(scene_path),
            "size": (bundle / BUNDLE_SCENE).stat().st_size,
            "sha256": _sha256(bundle / BUNDLE_SCENE),
        },
        "job": local_job.to_dict(),
        "job_file": BUNDLE_JOB,
        "script": BUNDLE_SCRIPT,
        "assets": assets_payload,
        "files": files,
        "total_bytes": sum(int(item["size"]) for item in files),
    }


def upload_bundle(
    transport: Transport,
    bundle: dict[str, Any],
    job_id: str,
    *,
    log: Optional[Progress] = None,
) -> dict[str, Any]:
    """Upload every bundle file below ``jobs/<job_id>`` on the target."""
    base = posixpath.join(JOBS_DIR, job_id)
    transport.mkdir(base)
    root = Path(bundle["bundle_dir"])
    uploaded: list[str] = []
    for item in bundle["files"]:
        name = str(item["name"])
        remote = posixpath.join(base, name)
        transport.upload(root / name, remote)
        uploaded.append(remote)
    _log(log, f"uploaded {len(uploaded)} file(s) to {transport.name}:{base}")
    return {"job_id": job_id, "base": base, "files": uploaded}


def remote_command(transport: Transport, remote: dict[str, Any], blender: str) -> list[str]:
    """Return the Blender command line that executes the uploaded bundle."""
    base = str(remote["base"])
    return [
        str(blender),
        "--background",
        transport.path(posixpath.join(base, BUNDLE_SCENE)),
        "--python",
        transport.path(posixpath.join(base, BUNDLE_SCRIPT)),
        "--",
        "--job",
        transport.path(posixpath.join(base, BUNDLE_JOB)),
    ]


def parse_worker_result(stdout: str) -> Optional[dict[str, Any]]:
    """Return the payload of the **last** ``GOO_REMOTE_RESULT:`` line."""
    for line in reversed((stdout or "").splitlines()):
        marker = line.find(RESULT_MARKER)
        if marker < 0:
            continue
        payload = line[marker + len(RESULT_MARKER) :].strip()
        try:
            value = json.loads(payload)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _result_payload(
    transport: Transport,
    remote: dict[str, Any],
    bundle: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], str]:
    """Download ``result.json`` from the job directory and parse it."""
    local = Path(bundle["bundle_dir"]) / BUNDLE_RESULT
    try:
        transport.download(posixpath.join(str(remote["base"]), BUNDLE_RESULT), local)
    except TransportError as exc:
        return None, str(exc)
    try:
        payload = json.loads(local.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"invalid {BUNDLE_RESULT}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{BUNDLE_RESULT} is not a JSON object"
    if "ok" not in payload and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    return payload, ""


def _download_outputs(
    transport: Transport,
    remote: dict[str, Any],
    result: dict[str, Any],
    output_dir: Path,
    *,
    log: Optional[Progress] = None,
) -> tuple[list[str], list[str]]:
    """Download the files listed in the worker result and verify them."""
    base = str(remote["base"])
    files: list[str] = []
    errors: list[str] = []
    entries = result.get("files") or []
    if not isinstance(entries, list):
        errors.append("worker result has no file list")
        return files, errors
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"invalid file entry: {entry!r}")
            continue
        name = str(entry.get("path") or entry.get("name") or "").replace("\\", "/")
        if not name:
            errors.append("file entry without a path")
            continue
        local = output_dir / Path(name).name
        try:
            transport.download(posixpath.join(base, name), local)
        except TransportError as exc:
            errors.append(f"{name}: {exc}")
            continue
        size = int(entry.get("size") or 0)
        if size and local.stat().st_size != size:
            errors.append(
                f"{name}: size mismatch (expected {size}, got {local.stat().st_size})"
            )
            continue
        expected = str(entry.get("sha256") or "")
        if expected and _sha256(local) != expected:
            errors.append(f"{name}: SHA-256 mismatch")
            continue
        files.append(str(local))
    if files:
        _log(log, f"downloaded {len(files)} file(s) to {output_dir}")
    return files, errors


def render_remote(
    scene: str | Path,
    output: str | Path,
    *,
    transport: Transport,
    job: Optional[RenderJob] = None,
    camera: str = "",
    view_layer: str = "",
    samples: int = 32,
    resolution: int = 100,
    frame: str = "1",
    format: str = "PNG",
    assets: Iterable[Asset] = (),
    blender: str = "blender",
    timeout: int = 3600,
    log: Optional[Progress] = None,
    keep_remote: bool = True,
    bundle_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Upload a bundle, run Blender on the target and collect verified outputs.

    Returns a JSON-serialisable payload.  Every failure (upload, Blender exit
    code, missing result file, SHA-256 mismatch) is reported in ``ok`` and
    ``errors`` instead of raising, so the CLI and GUI can print it directly.
    """
    scene_path = Path(scene).expanduser().resolve()
    if not scene_path.is_file():
        return {"ok": False, "transport": transport.name, "error": f"scene not found: {scene_path}"}
    output_dir = Path(output).expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "transport": transport.name, "error": f"cannot create output directory: {exc}"}

    if job is None:
        job = RenderJob(
            scene=str(scene_path),
            output=str(output_dir),
            camera=camera,
            view_layer=view_layer,
            samples=samples,
            resolution=resolution,
            frame=str(frame),
            format=format,
        )
    job_id = job.job_id or uuid.uuid4().hex

    temp_handle: Optional[tempfile.TemporaryDirectory[str]] = None
    if bundle_dir is None:
        temp_handle = tempfile.TemporaryDirectory(prefix="goo-bridge-bundle-")
        bundle_path = Path(temp_handle.name)
    else:
        bundle_path = Path(bundle_dir).expanduser().resolve()
        bundle_path.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    try:
        try:
            bundle = build_bundle(scene_path, job, bundle_path, assets=assets, log=log)
        except WorkerError as exc:
            return {"ok": False, "transport": transport.name, "job_id": job_id, "error": str(exc), "stage": "bundle"}
        try:
            remote = upload_bundle(transport, bundle, job_id, log=log)
        except TransportError as exc:
            return {
                "ok": False,
                "transport": transport.name,
                "job_id": job_id,
                "error": str(exc),
                "stage": "upload",
            }
        command = remote_command(transport, remote, blender)
        _log(log, "remote: " + " ".join(shlex.quote(part) for part in command))
        run_error = ""
        try:
            result = transport.run(command, timeout=timeout)
        except TransportError as exc:
            result = ProcResult(argv=command, returncode=-1, stderr=str(exc))
            run_error = str(exc)
        _log(
            log,
            f"blender exit {result.returncode} in {result.duration:.2f}s"
            + (f" ({run_error})" if run_error else ""),
        )
        if result.tail(2000):
            _log(log, result.tail(2000))

        payload, result_error = _result_payload(transport, remote, bundle)
        if payload is None:
            return {
                "ok": False,
                "transport": transport.name,
                "job_id": job_id,
                "remote_base": remote["base"],
                "error": result_error
                or f"Blender finished without {BUNDLE_RESULT} (exit {result.returncode})",
                "stage": "run",
                "returncode": result.returncode,
                "log": result.tail(4000),
                "duration": round(time.monotonic() - started, 3),
            }

        files, errors = _download_outputs(transport, remote, payload, output_dir, log=log)
        if not payload.get("ok"):
            errors.insert(0, str(payload.get("error") or "remote render failed"))
        if not keep_remote:
            transport.remove_tree(str(remote["base"]))
            _log(log, f"removed remote job {remote['base']}")
        total = sum(Path(item).stat().st_size for item in files)
        return {
            "ok": bool(payload.get("ok")) and not errors,
            "transport": transport.name,
            "job_id": job_id,
            "remote_base": remote["base"],
            "files": files,
            "remote_files": [entry.get("path") for entry in payload.get("files") or [] if isinstance(entry, dict)],
            "verified": len(files),
            "errors": errors,
            "warning": payload.get("warning", ""),
            "log": result.tail(4000),
            "duration": float(payload.get("duration") or round(time.monotonic() - started, 3)),
            "settings": payload.get("settings", {}),
            "blender": payload.get("blender", {}),
            "bytes": total,
            "bundle": bundle,
        }
    finally:
        if temp_handle is not None:
            temp_handle.cleanup()


def fetch_results(
    transport: Transport,
    job_id: str,
    output: str | Path,
    *,
    log: Optional[Progress] = None,
) -> dict[str, Any]:
    """Download and verify the outputs of an existing remote job."""
    output_dir = Path(output).expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "job_id": job_id, "error": f"cannot create output directory: {exc}"}
    base = posixpath.join(JOBS_DIR, job_id)
    if not transport.exists(base):
        return {
            "ok": False,
            "job_id": job_id,
            "error": f"no remote job {job_id!r} in {transport.name}:{transport.workspace}",
        }
    remote = {"job_id": job_id, "base": base, "files": []}
    with tempfile.TemporaryDirectory(prefix="goo-bridge-fetch-") as temp:
        bundle = {"bundle_dir": temp}
        payload, error = _result_payload(transport, remote, bundle)
        if payload is None:
            return {
                "ok": False,
                "job_id": job_id,
                "remote_base": base,
                "error": error or f"{BUNDLE_RESULT} is missing",
            }
        files, errors = _download_outputs(transport, remote, payload, output_dir, log=log)
        if not payload.get("ok"):
            errors.insert(0, str(payload.get("error") or "remote render failed"))
        return {
            "ok": bool(payload.get("ok")) and not errors,
            "job_id": job_id,
            "remote_base": base,
            "files": files,
            "verified": len(files),
            "errors": errors,
            "duration": float(payload.get("duration") or 0.0),
            "settings": payload.get("settings", {}),
            "bytes": sum(Path(item).stat().st_size for item in files),
        }


def job_status(transport: Transport, job_id: str) -> dict[str, Any]:
    """Return the state of a job without downloading its render outputs."""
    base = posixpath.join(JOBS_DIR, job_id)
    payload: dict[str, Any] = {
        "ok": True,
        "job_id": job_id,
        "remote_base": base,
        "state": "unknown",
    }
    if not transport.exists(base):
        payload.update({"ok": False, "state": "missing", "error": "job directory not found"})
        return payload
    with tempfile.TemporaryDirectory(prefix="goo-bridge-status-") as temp:
        bundle = {"bundle_dir": temp}
        remote = {"job_id": job_id, "base": base, "files": []}
        result, _error = _result_payload(transport, remote, bundle)
        if result is not None:
            payload["result"] = {
                "ok": bool(result.get("ok")),
                "job_id": result.get("job_id", job_id),
                "files": result.get("files", []),
                "duration": result.get("duration", 0.0),
                "error": result.get("error", ""),
                "settings": result.get("settings", {}),
                "blender": result.get("blender", {}),
            }
            payload["state"] = "completed" if result.get("ok") else "failed"
            if result.get("error"):
                payload["error"] = str(result["error"])
            return payload
        status_path = posixpath.join(base, BUNDLE_STATUS)
        if transport.exists(status_path):
            try:
                status = json.loads(transport.read_text(status_path))
            except (TransportError, ValueError) as exc:
                payload["error"] = f"invalid {BUNDLE_STATUS}: {exc}"
                return payload
            if isinstance(status, dict):
                payload["status"] = status
                payload["state"] = str(status.get("state") or "running")
    return payload


def clean_jobs(transport: Transport, job_id: Optional[str] = None) -> dict[str, Any]:
    """Remove one job directory, or the whole ``jobs`` directory."""
    rel = posixpath.join(JOBS_DIR, job_id) if job_id else JOBS_DIR
    existed = transport.exists(rel)
    removed = transport.remove_tree(rel)
    return {
        "ok": bool(removed) or not existed,
        "transport": transport.name,
        "job_id": job_id or "",
        "path": rel,
        "existed": existed,
        "removed": bool(removed),
    }


def test_connection(
    transport: Transport,
    *,
    blender: str = "blender",
    timeout: int = 120,
) -> dict[str, Any]:
    """Check file round-trip and the remote Blender version.

    The round-trip uploads a tiny file into a temporary workspace directory and
    downloads it again, so it exercises authentication and both ``scp``
    directions without touching an existing job.
    """
    started = time.monotonic()
    errors: list[str] = []
    probe = f"probe-{uuid.uuid4().hex[:12]}"
    roundtrip = False
    try:
        transport.mkdir(probe)
        text = f"goo-bridge probe {__version__}\n"
        transport.upload_text(text, posixpath.join(probe, "probe.txt"))
        with tempfile.TemporaryDirectory(prefix="goo-bridge-probe-") as temp:
            local = Path(temp) / "probe.txt"
            transport.download(posixpath.join(probe, "probe.txt"), local)
            roundtrip = local.read_text(encoding="utf-8", errors="replace") == text
    except TransportError as exc:
        errors.append(f"file round-trip failed: {exc}")
    finally:
        try:
            transport.remove_tree(probe)
        except TransportError:
            pass

    blender_info: dict[str, Any] = {
        "command": str(blender),
        "ok": False,
        "version": "",
        "version_ok": None,
        "returncode": None,
        "tail": "",
    }
    try:
        result = transport.run([str(blender), "--version"], timeout=timeout)
        match = re.search(r"Blender\s+([0-9][0-9A-Za-z.\-]*)", result.stdout or "")
        version = match.group(1) if match else ""
        parts = tuple(int(part) for part in re.findall(r"\d+", version)[:2])
        blender_info.update(
            {
                "ok": result.ok and bool(version),
                "version": version,
                "version_ok": bool(parts and parts >= (5, 2)) if parts else None,
                "returncode": result.returncode,
                "tail": result.tail(1200),
            }
        )
        if not result.ok:
            errors.append(f"Blender command failed (exit {result.returncode}): {result.tail(600)}")
        elif not version:
            errors.append(f"cannot read a Blender version from {blender!r}")
        elif blender_info["version_ok"] is False:
            errors.append(f"Blender {version} is too old; GOO Bridge requires 5.2 or newer")
    except TransportError as exc:
        errors.append(str(exc))
        blender_info["tail"] = str(exc)

    ok = roundtrip and bool(blender_info["ok"]) and blender_info["version_ok"] is not False
    return {
        "ok": ok,
        "transport": transport.name,
        "workspace": transport.workspace,
        "roundtrip": roundtrip,
        "blender": blender_info,
        "errors": errors,
        "duration": round(time.monotonic() - started, 3),
    }
