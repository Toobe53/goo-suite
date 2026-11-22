# SPDX-License-Identifier: GPL-3.0-or-later
"""``goo-bridge remote`` subcommands: add, list, test, render, status, fetch, clean.

``cli_remote`` is called by ``goo_bridge.cli.cmd_remote`` with the remaining
argv (``argparse.REMAINDER``), so it owns its own parser and exit codes.

A profile comes from ``config.json`` (``--profile NAME``), from
``GOO_BRIDGE_PROFILE``, or from explicit ``--host/--user/...`` flags.
``--local`` swaps the SSH transport for a local workspace directory, which is
what CI and the unit tests use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Sequence

from . import __version__
from .assets import Asset, sha256_file
from .blender import BlenderError, find_blender
from .config import (
    Config,
    RemoteProfile,
    config_path,
    load_config,
    save_config,
)
from .jobs import RenderJob
from .transport import LocalTransport, SshTransport, Transport, TransportError
from .worker import (
    JOBS_DIR,
    WorkerError,
    clean_jobs,
    fetch_results,
    job_status,
    render_remote,
    test_connection,
)

__all__ = ["cli_remote", "build_parser"]

DEFAULT_BLENDER = "blender"


class RemoteUsageError(RuntimeError):
    """A configuration or command-line problem (exit code 2)."""


def _json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def _error(message: str, code: int = 2, *, as_json: bool = False) -> int:
    if as_json:
        _json({"ok": False, "error": message})
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def _remote_help() -> None:
    build_parser().print_help()


def _local_workspace(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(tempfile.gettempdir()) / "goo-bridge-local"


def _local_blender(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    try:
        return str(find_blender().path)
    except BlenderError:
        return DEFAULT_BLENDER


def _profile_from_flags(args: argparse.Namespace) -> RemoteProfile:
    return RemoteProfile(
        name=args.profile or "cli",
        host=args.host or "",
        user=args.user or "",
        port=int(args.port or 22),
        key=args.key or "",
        blender=args.blender or DEFAULT_BLENDER,
        workspace=args.workspace or "~/.goo-bridge",
    )


def _apply_overrides(profile: RemoteProfile, args: argparse.Namespace) -> RemoteProfile:
    data = profile.to_dict()
    if args.user:
        data["user"] = args.user
    if args.port:
        data["port"] = int(args.port)
    if args.key:
        data["key"] = args.key
    if args.blender:
        data["blender"] = args.blender
    if args.workspace:
        data["workspace"] = args.workspace
    return RemoteProfile.from_dict(data)


def resolve_profile(args: argparse.Namespace) -> RemoteProfile:
    """Pick the configured profile or build one from CLI flags."""
    if args.host:
        return _profile_from_flags(args)

    config: Config = load_config()
    name = args.profile or os.environ.get("GOO_BRIDGE_PROFILE") or ""
    if not name:
        if len(config.remote) == 1:
            return _apply_overrides(config.remote[0], args)
        if not config.remote:
            raise RemoteUsageError(
                "no remote profiles configured. Add one to "
                f"{_config_hint()} or pass --host HOST [--user USER] [--port PORT]."
            )
        names = ", ".join(item.name for item in config.remote)
        raise RemoteUsageError(
            f"multiple remote profiles configured ({names}); choose one with --profile NAME"
        )
    try:
        profile = config.profile(name)
    except KeyError as exc:
        names = ", ".join(item.name for item in config.remote) or "none"
        raise RemoteUsageError(
            f"unknown remote profile: {name!r} (configured: {names}). "
            "Pass --host to use an ad-hoc host."
        ) from exc
    return _apply_overrides(profile, args)


def _config_hint() -> str:
    from .config import config_path

    return str(config_path())


def make_transport(args: argparse.Namespace, profile: Optional[RemoteProfile]) -> Transport:
    if args.local:
        return LocalTransport(_local_workspace(args.workspace))
    if profile is None:
        raise RemoteUsageError("a remote profile is required without --local")
    return SshTransport(profile)


def _assets(args: argparse.Namespace) -> list[Asset]:
    items: list[Asset] = []
    for value in getattr(args, "asset", None) or []:
        path = Path(value).expanduser().resolve()
        exists = path.is_file()
        items.append(
            Asset(
                path=str(path),
                kind="user",
                name=path.name,
                exists=exists,
                size=path.stat().st_size if exists else 0,
                sha256=sha256_file(path) if exists else "",
            )
        )
    return items


def _logger(args: argparse.Namespace):
    if getattr(args, "quiet", False):
        return None

    def _log(message: str) -> None:
        print(f"[remote] {message}", file=sys.stderr)

    return _log


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def cmd_add(args: argparse.Namespace) -> int:
    """Create or update a profile in ``config.json``."""
    name = str(args.name or "").strip()
    if not name:
        raise RemoteUsageError("profile name must not be empty")
    if not args.host:
        raise RemoteUsageError("--host is required (for example: --host render.example.com)")
    profile = RemoteProfile(
        name=name,
        host=args.host,
        user=args.user or "",
        port=int(args.port or 22),
        key=args.key or "",
        blender=args.blender or DEFAULT_BLENDER,
        workspace=args.workspace or "~/.goo-bridge",
    )
    config: Config = load_config()
    existing = next((item for item in config.remote if item.name == name), None)
    if existing is not None and not args.force:
        raise RemoteUsageError(
            f"remote profile {name!r} already exists; pass --force to replace it"
        )
    config.set_profile(profile)
    target = save_config(config)
    payload = {
        "ok": True,
        "action": "updated" if existing is not None else "added",
        "profile": profile.to_dict(),
        "config": str(target),
        "count": len(config.remote),
    }
    if args.json:
        _json(payload)
    else:
        print(f"{payload['action']} profile {name!r} in {target}")
        print(f"  host: {profile.host}")
        print(f"  user: {profile.user or '(current user)'}")
        print(f"  port: {profile.port}")
        print(f"  blender: {profile.blender}")
        print(f"  workspace: {profile.workspace}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List the configured remote profiles."""
    config: Config = load_config()
    payload = {
        "ok": True,
        "config": str(config_path()),
        "count": len(config.remote),
        "profiles": [profile.to_dict() for profile in config.remote],
    }
    if args.json:
        _json(payload)
        return 0
    print(f"config: {payload['config']}")
    if not config.remote:
        print("no remote profiles configured")
        return 0
    print(f"{len(config.remote)} profile(s):")
    names = [profile.name for profile in config.remote]
    width = max(len("NAME"), *(len(name) for name in names))
    print(f"{'NAME':<{width}}  {'HOST':<28}  {'USER':<12}  PORT  BLENDER")
    for profile in config.remote:
        print(
            f"{profile.name:<{width}}  {profile.host:<28}  "
            f"{profile.user or '-':<12}  {profile.port:<4}  {profile.blender}"
        )
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    if args.profile_name and not args.profile:
        args.profile = args.profile_name
    profile = None if args.local else resolve_profile(args)
    transport = make_transport(args, profile)
    blender = args.blender or (profile.blender if profile else _local_blender(None))
    payload = test_connection(transport, blender=blender, timeout=min(args.timeout, 120))
    payload["profile"] = profile.name if profile else "local"
    if args.json:
        _json(payload)
    else:
        print(f"transport: {payload['transport']}")
        print(f"workspace: {payload['workspace']}")
        print(f"file round-trip: {'ok' if payload['roundtrip'] else 'FAILED'}")
        info = payload["blender"]
        print(
            f"blender: {info.get('version') or 'unknown'}"
            f" ({info.get('command')})"
            + ("" if info.get("ok") else " - FAILED")
        )
        for error in payload["errors"]:
            print(f"error: {error}", file=sys.stderr)
    return 0 if payload["ok"] else 1


def cmd_render(args: argparse.Namespace) -> int:
    if not args.output:
        raise RemoteUsageError("--output/-o is required")
    scene = Path(args.scene).expanduser().resolve()
    if not scene.is_file():
        raise RemoteUsageError(f"scene not found: {scene}")
    profile = None if args.local else resolve_profile(args)
    transport = make_transport(args, profile)
    blender = args.blender or (profile.blender if profile else _local_blender(None))
    job_id = args.job_id or uuid.uuid4().hex
    job = RenderJob(
        scene=str(scene),
        output=args.output,
        camera=args.camera,
        view_layer=args.view_layer,
        samples=args.samples,
        resolution=args.resolution,
        frame=args.frame,
        format=args.format,
        job_id=job_id,
        metadata={"remote": profile.name if profile else "local"},
    )
    started = time.monotonic()
    payload = render_remote(
        scene,
        args.output,
        transport=transport,
        job=job,
        assets=_assets(args),
        blender=blender,
        timeout=args.timeout,
        log=_logger(args),
        keep_remote=not args.clean,
    )
    payload["elapsed"] = round(time.monotonic() - started, 3)
    payload.pop("bundle", None)
    if args.json:
        _json(payload)
    else:
        if not payload.get("ok"):
            print(f"remote render failed: {payload.get('error') or 'see errors'}", file=sys.stderr)
            for error in payload.get("errors") or []:
                print(f"  {error}", file=sys.stderr)
            if payload.get("log"):
                print(payload["log"], file=sys.stderr)
        else:
            print(f"job: {payload['job_id']}")
            print(
                f"rendered {payload['verified']} file(s) in "
                f"{payload.get('duration', 0.0):.2f}s "
                f"({_human_size(int(payload.get('bytes') or 0))})"
            )
            for path in payload["files"]:
                print(f"  {path}")
            print(f"remote: {payload['transport']}:{payload['remote_base']}")
            print("verified: SHA-256")
    return 0 if payload.get("ok") else 1


def _resolve_job_transport(args: argparse.Namespace) -> tuple[Transport, Optional[RemoteProfile]]:
    profile = None if args.local else resolve_profile(args)
    return make_transport(args, profile), profile


def cmd_status(args: argparse.Namespace) -> int:
    transport, profile = _resolve_job_transport(args)
    payload = job_status(transport, args.job_id)
    payload["profile"] = profile.name if profile else "local"
    if args.json:
        _json(payload)
    else:
        result = payload.get("result") or {}
        print(f"job: {args.job_id}")
        print(f"state: {payload['state']}")
        if result:
            print(f"files: {len(result.get('files') or [])}")
            print(f"duration: {result.get('duration', 0.0):.2f}s")
        if payload.get("error"):
            print(f"error: {payload['error']}", file=sys.stderr)
    return 0 if payload.get("ok") else 1


def cmd_fetch(args: argparse.Namespace) -> int:
    if not args.output:
        raise RemoteUsageError("--output/-o is required")
    transport, _profile = _resolve_job_transport(args)
    payload = fetch_results(transport, args.job_id, args.output, log=_logger(args))
    if args.json:
        _json(payload)
    else:
        if payload.get("ok"):
            print(f"fetched {payload['verified']} file(s) to {Path(args.output).resolve()}")
            for path in payload["files"]:
                print(f"  {path}")
            print("verified: SHA-256")
        else:
            print(f"fetch failed: {payload.get('error') or 'see errors'}", file=sys.stderr)
            for error in payload.get("errors") or []:
                print(f"  {error}", file=sys.stderr)
    return 0 if payload.get("ok") else 1


def cmd_clean(args: argparse.Namespace) -> int:
    transport, _profile = _resolve_job_transport(args)
    if not args.job_id and not args.all:
        raise RemoteUsageError("pass a job id or --all")
    job_id = None if args.all else args.job_id
    payload = clean_jobs(transport, job_id)
    if args.json:
        _json(payload)
    else:
        target = payload["path"]
        if payload["removed"]:
            print(f"removed {transport.name}:{target}")
        elif not payload["existed"]:
            print(f"nothing to remove: {transport.name}:{target}")
        else:
            print(f"could not remove {transport.name}:{target}", file=sys.stderr)
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goo-bridge remote",
        description="Remote render host commands (SSH, or --local for a local workspace)",
    )
    parser.add_argument("--version", action="version", version=f"goo-bridge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", "-p", default=None, help="configured remote profile name")
    common.add_argument("--host", default=None, help="ad-hoc SSH host (skips profiles)")
    common.add_argument("--user", default=None, help="SSH user")
    common.add_argument("--port", type=int, default=None, help="SSH port")
    common.add_argument("--key", default=None, help="SSH private key path")
    common.add_argument("--blender", default=None, help="Blender command on the target")
    common.add_argument("--workspace", default=None, help="remote workspace directory")
    common.add_argument("--local", action="store_true", help="run through LocalTransport")
    common.add_argument("--json", action="store_true", help="machine readable output")
    common.add_argument("--timeout", type=int, default=3600, help="command timeout in seconds")
    common.add_argument("--quiet", action="store_true", help="hide progress lines")

    add_common = argparse.ArgumentParser(add_help=False)
    add_common.add_argument("--host", required=True, help="SSH host or IP address")
    add_common.add_argument("--user", default=None, help="SSH user")
    add_common.add_argument("--port", type=int, default=None, help="SSH port (default 22)")
    add_common.add_argument("--key", default=None, help="SSH private key path")
    add_common.add_argument("--blender", default=None, help="Blender command on the target")
    add_common.add_argument("--workspace", default=None, help="remote workspace directory")
    add_common.add_argument("--force", action="store_true", help="replace an existing profile")
    add_common.add_argument("--json", action="store_true", help="machine readable output")

    add_cmd = sub.add_parser(
        "add",
        parents=[add_common],
        help="add or update a remote profile in config.json",
    )
    add_cmd.add_argument("name", help="profile name, e.g. studio")
    add_cmd.set_defaults(func=cmd_add)

    list_common = argparse.ArgumentParser(add_help=False)
    list_common.add_argument("--json", action="store_true", help="machine readable output")
    list_cmd = sub.add_parser(
        "list", parents=[list_common], help="list the configured remote profiles"
    )
    list_cmd.set_defaults(func=cmd_list)

    test_cmd = sub.add_parser("test", parents=[common], help="check SSH, files and Blender")
    test_cmd.add_argument("profile_name", nargs="?", default=None, help=argparse.SUPPRESS)
    test_cmd.set_defaults(func=cmd_test)

    render_cmd = sub.add_parser("render", parents=[common], help="render a scene on the target")
    render_cmd.add_argument("scene", help="local .blend to upload")
    render_cmd.add_argument("-o", "--output", default=None, help="local output directory")
    render_cmd.add_argument("--camera", default="")
    render_cmd.add_argument("--view-layer", default="")
    render_cmd.add_argument("--samples", type=int, default=32)
    render_cmd.add_argument("--resolution", type=int, default=100, help="resolution percentage")
    render_cmd.add_argument("--frame", default="1", help="1, 1-10 or 1,3,5")
    render_cmd.add_argument("--format", default="PNG")
    render_cmd.add_argument("--job-id", default="", help="override the generated job id")
    render_cmd.add_argument("--asset", action="append", default=[], help="extra file to upload")
    render_cmd.add_argument("--clean", action="store_true", help="remove remote files afterwards")
    render_cmd.set_defaults(func=cmd_render)

    status_cmd = sub.add_parser("status", parents=[common], help="show a job's state")
    status_cmd.add_argument("job_id")
    status_cmd.set_defaults(func=cmd_status)

    fetch_cmd = sub.add_parser("fetch", parents=[common], help="download a job's outputs")
    fetch_cmd.add_argument("job_id")
    fetch_cmd.add_argument("-o", "--output", default=None, help="local output directory")
    fetch_cmd.set_defaults(func=cmd_fetch)

    clean_cmd = sub.add_parser("clean", parents=[common], help="remove remote job folders")
    clean_cmd.add_argument("job_id", nargs="?", default=None)
    clean_cmd.add_argument("--all", action="store_true", help=f"remove the whole {JOBS_DIR}/ tree")
    clean_cmd.set_defaults(func=cmd_clean)
    return parser


def cli_remote(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``goo-bridge remote ...`` and return a process exit code."""
    args_list = [str(item) for item in (argv if argv is not None else sys.argv[1:])]
    if not args_list:
        _remote_help()
        return 2
    parser = build_parser()
    try:
        args = parser.parse_args(args_list)
    except SystemExit as exc:
        # argparse already printed usage/help; keep the CLI contract of
        # returning an exit code instead of letting SystemExit escape.
        code = exc.code if isinstance(exc.code, int) else 2
        if code == 0:
            return 0
        if "--json" in args_list:
            _json({"ok": False, "error": "invalid arguments (see usage above)"})
        return 2
    try:
        return int(args.func(args))
    except RemoteUsageError as exc:
        return _error(str(exc), 2, as_json=getattr(args, "json", False))
    except (TransportError, WorkerError) as exc:
        return _error(str(exc), 1, as_json=getattr(args, "json", False))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - top-level safety net
        return _error(f"{type(exc).__name__}: {exc}", 1, as_json=getattr(args, "json", False))


if __name__ == "__main__":
    raise SystemExit(cli_remote(sys.argv[1:]))
