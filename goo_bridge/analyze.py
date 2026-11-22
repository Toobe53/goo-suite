# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the Blender-side scene inspector and return a JSON report."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Optional

from .blender import BlenderError, BlenderInfo, find_blender, run_blender


class AnalysisError(RuntimeError):
    pass


def analyzer_script() -> Path:
    return Path(__file__).resolve().parent / "scripts" / "analyze_scene.py"


def analyze(
    scene: Path | str,
    *,
    blender: BlenderInfo | None = None,
    timeout: int = 300,
    use_cache: bool = True,
    cache_store: Path | None = None,
) -> dict[str, Any]:
    path = Path(scene).expanduser().resolve()
    if not path.is_file():
        raise AnalysisError(f"scene not found: {path}")
    info = blender or find_blender()
    version = str(getattr(info, "version_string", "") or "")
    if use_cache:
        from .cache import get_analysis

        cached = get_analysis(path, version, cache_store)
        if cached is not None:
            cached = dict(cached)
            cached["scene_path"] = str(path)
            cached["cached"] = True
            return cached
    script = analyzer_script()
    if not script.is_file():
        raise AnalysisError(f"analyzer script missing: {script}")
    with tempfile.TemporaryDirectory(prefix="goo-suite-analyze-") as temp:
        out = Path(temp) / "analysis.json"
        result = run_blender(
            info,
            ["--background", str(path), "--python", str(script), "--", "--out", str(out)],
            timeout=timeout,
        )
        if result.returncode != 0 or not out.is_file():
            log = (result.stderr or result.stdout or "").strip()
            raise AnalysisError(
                f"Blender analysis failed (exit {result.returncode}): {log[-2000:]}"
            )
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AnalysisError(f"invalid analysis JSON: {exc}") from exc
    if not isinstance(report, dict) or not report.get("ok"):
        raise AnalysisError(f"analyzer returned an invalid report: {report!r}")
    report["scene_path"] = str(path)
    report["cached"] = False
    if use_cache:
        from .cache import put_analysis

        put_analysis(path, report, version, cache_store)
    return report
