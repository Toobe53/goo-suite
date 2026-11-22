# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface for GOO Bridge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .analyze import AnalysisError, analyze
from .blender import BlenderError, find_blender
from .config import config_path, load_config
from .jobs import RenderResult
from .render import render
from .report import summarize, write_html, write_json
from .verify import verify_dist


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def _error(message: str, code: int = 2, *, as_json: bool = False) -> int:
    if as_json:
        _json({"ok": False, "error": message})
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    payload: dict[str, Any] = {
        "ok": True,
        "version": __version__,
        "config": str(config_path()),
        "blender": {"found": False, "path": "", "version": ""},
        "engine": {"found": False, "path": "", "version": ""},
        "remote_profiles": [profile.name for profile in config.remote],
    }
    try:
        info = find_blender(config.blender or None)
        payload["blender"] = {"found": True, **info.to_dict()}
    except BlenderError as exc:
        payload["blender_error"] = str(exc)
    try:
        from .engine import detect_engine

        payload["engine"] = detect_engine().to_dict()
    except Exception:
        pass
    if args.json:
        _json(payload)
    else:
        print(f"GOO Bridge {__version__}")
        print(f"config: {payload['config']}")
        print(f"blender: {payload['blender'].get('path') or 'not found'}")
        print(f"engine: {payload['engine'].get('path') or 'not found'}")
        print(f"remote profiles: {', '.join(payload['remote_profiles']) or 'none'}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    try:
        report = analyze(args.scene)
    except (AnalysisError, BlenderError) as exc:
        return _error(str(exc), as_json=args.json)
    output = Path(args.output).resolve() if args.output else None
    if output:
        if output.suffix.lower() in {".html", ".htm"}:
            path = write_html(report, output)
        else:
            path = write_json(report, output)
        report["written"] = str(path)
    if args.json:
        _json({"ok": True, **summarize(report), "report": report})
    else:
        summary = summarize(report)
        print(f"file: {summary['file']}")
        print(f"blender: {summary['blender']}")
        print(f"GOO groups: {summary['goo_groups']}")
        print(f"missing files: {summary['missing_files']}")
        for name in summary["missing"]:
            print(f"  missing: {name}")
        if output:
            print(f"report: {output}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        result = render(
            args.scene,
            args.output,
            camera=args.camera,
            view_layer=args.view_layer,
            samples=args.samples,
            resolution=args.resolution,
            frame=args.frame,
            format=args.format,
        )
    except BlenderError as exc:
        return _error(str(exc), as_json=args.json)
    if args.json:
        _json(result.to_dict())
    else:
        if result.ok:
            print(f"rendered {len(result.files)} file(s) in {result.duration:.2f}s")
            for path in result.files:
                print(f"  {path}")
        else:
            print(f"render failed: {result.error}", file=sys.stderr)
            if result.log:
                print(result.log[-2000:], file=sys.stderr)
    return 0 if result.ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    source = Path(args.analysis).resolve()
    try:
        report = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _error(f"cannot read analysis: {exc}", as_json=args.json)
    output = Path(args.output).resolve() if args.output else source.with_suffix(".html")
    path = write_html(report, output) if output.suffix.lower() in {".html", ".htm"} else write_json(report, output)
    if args.json:
        _json({"ok": True, "written": str(path), **summarize(report)})
    else:
        print(f"report: {path}")
    return 0


def cmd_compat(args: argparse.Namespace) -> int:
    from .compat import scan_blend, write_html as write_compat_html, write_json as write_compat_json

    report: dict[str, Any] = {}
    if args.analysis:
        source = Path(args.analysis).resolve()
        try:
            report = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _error(f"cannot read analysis: {exc}", as_json=args.json)
    result = scan_blend(report)
    output = Path(args.output).resolve() if args.output else None
    written: Optional[str] = None
    if output:
        if output.suffix.lower() in {".json"}:
            written = str(write_compat_json(result, output))
        else:
            written = str(write_compat_html(result, output))
    if args.json:
        _json({"ok": True, "written": written, **result})
    else:
        counts = result["matrix"]["counts"]
        print(f"compatibility: {result['total']} items / {counts['L2']} L2 / {counts['L1']} L1 / {counts['L0']} L0")
        for item in result["items"]:
            print(f"  {item['id']:>3}  {item['level']}  {item['name']}")
        if written:
            print(f"report: {written}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    payload = verify_dist(Path(args.dist).resolve())
    if args.json:
        _json(payload)
    else:
        for check in payload["checks"]:
            print(f"{'OK' if check['ok'] else 'FAIL'} {check['file']}")
        for error in payload["errors"]:
            print(f"error: {error}", file=sys.stderr)
    return 0 if payload["ok"] else 2


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run_gui

    try:
        return run_gui([])
    except RuntimeError as exc:
        return _error(str(exc))


def cmd_remote(args: argparse.Namespace) -> int:
    try:
        from .remote import cli_remote
    except Exception as exc:
        return _error(f"remote support is not available: {exc}")
    return int(cli_remote(args.remote_args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goo-bridge", description="GOO Bridge: local and remote NPR rendering for Blender")
    parser.add_argument("--version", action="version", version=f"goo-bridge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check Blender, engine and remote profiles")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    analyze_cmd = sub.add_parser("analyze", help="inspect a .blend for GOO groups and dependencies")
    analyze_cmd.add_argument("scene")
    analyze_cmd.add_argument("-o", "--output")
    analyze_cmd.add_argument("--json", action="store_true")
    analyze_cmd.set_defaults(func=cmd_analyze)

    render_cmd = sub.add_parser("render", help="render one scene locally")
    render_cmd.add_argument("scene")
    render_cmd.add_argument("-o", "--output", required=True)
    render_cmd.add_argument("--camera", default="")
    render_cmd.add_argument("--view-layer", default="")
    render_cmd.add_argument("--samples", type=int, default=32)
    render_cmd.add_argument("--resolution", type=int, default=100, help="resolution percentage")
    render_cmd.add_argument("--frame", default="1", help="1, 1-10 or 1,3,5")
    render_cmd.add_argument("--format", default="PNG")
    render_cmd.add_argument("--json", action="store_true")
    render_cmd.set_defaults(func=cmd_render)

    report_cmd = sub.add_parser("report", help="turn an analysis JSON into HTML/JSON")
    report_cmd.add_argument("analysis")
    report_cmd.add_argument("-o", "--output")
    report_cmd.add_argument("--json", action="store_true")
    report_cmd.set_defaults(func=cmd_report)

    compat_cmd = sub.add_parser("compat", help="generate the 39-item L2/L1/L0 compatibility report")
    compat_cmd.add_argument("--analysis", help="optional analysis JSON from `goo-bridge analyze`")
    compat_cmd.add_argument("-o", "--output", default="compat.html")
    compat_cmd.add_argument("--json", action="store_true")
    compat_cmd.set_defaults(func=cmd_compat)

    verify_cmd = sub.add_parser("verify", help="verify SHA256SUMS and release archives")
    verify_cmd.add_argument("--dist", default="dist")
    verify_cmd.add_argument("--json", action="store_true")
    verify_cmd.set_defaults(func=cmd_verify)

    gui_cmd = sub.add_parser("gui", help="open the desktop application")
    gui_cmd.set_defaults(func=cmd_gui)

    # No add_help: ``remote --help``/``-h`` must reach ``remote.cli_remote`` so
    # the inner parser (add/list/test/render/status/fetch/clean) prints help.
    remote = sub.add_parser("remote", help="remote render host commands", add_help=False)
    remote.add_argument("remote_args", nargs=argparse.REMAINDER)
    remote.set_defaults(func=cmd_remote)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    _configure_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    # ``remote --help`` / ``remote -h`` / bare ``remote`` must reach the inner
    # parser (add/list/test/render/status/fetch/clean); the REMAINDER positional
    # would otherwise treat a leading option as an outer-parser option.
    if raw[:1] == ["remote"] and (len(raw) == 1 or raw[1] in {"-h", "--help"}):
        try:
            from .remote import cli_remote
        except Exception as exc:  # pragma: no cover - import guard
            return _error(f"remote support is not available: {exc}")
        return int(cli_remote(raw[1:]))
    parser = build_parser()
    args = parser.parse_args(raw)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - top-level safety net
        return _error(f"{type(exc).__name__}: {exc}")
