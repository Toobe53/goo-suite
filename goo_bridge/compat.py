# SPDX-License-Identifier: GPL-3.0-or-later
"""GOO Bridge compatibility matrix, scene scan and standalone reports.

The matrix is the bridge-specific 39-item view of the 49-row GOO Engine 4.4
inventory in ``goo-port/docs``.  It keeps the bridge's adoption status separate
from the add-on's port status:

``L2`` (green)
    Fully bridged - the asset/CLI path exists and detection is automatic, or
    stock Blender 5.2 already provides the behaviour.
``L1`` (yellow)
    Partial - an asset or operator reproduces the intent, but the data path
    differs from GOO or one manual step remains.
``L0`` (red)
    Unreachable - GOO's C++ renderer or Blender's core/UI internals; the report
    names the limitation instead of pretending.

The module is standard-library only, like the rest of ``goo_bridge``: it can be
imported by the CLI, the GUI and the test suite without Blender installed.
``scan_blend`` consumes the JSON produced by ``goo_bridge/scripts/analyze_scene.py``.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .fsutil import atomic_write_text

__all__ = [
    "CompatError",
    "DATA_FILE",
    "LEVELS",
    "LEVEL_COLORS",
    "STATUSES",
    "counts",
    "generate_reports",
    "items",
    "level_summary",
    "load_matrix",
    "matrix_summary",
    "render_html",
    "scan_blend",
    "validate_matrix",
    "write_html",
    "write_json",
]

DATA_FILE = Path(__file__).resolve().parent / "data" / "compat.json"

LEVELS = ("L2", "L1", "L0")
STATUSES = ("present", "partial", "missing", "manual", "unsupported", "native")

#: Human names and report colours (green / yellow / red).
LEVEL_COLORS = {
    "L2": {"name": "supported", "color": "green", "hex": "#1a7f37"},
    "L1": {"name": "partial", "color": "yellow", "hex": "#9a6700"},
    "L0": {"name": "unreachable", "color": "red", "hex": "#cf222e"},
}

STATUS_LABELS = {
    "present": "found in scene",
    "partial": "partly found",
    "missing": "expected asset not found",
    "manual": "bridge UI / operator",
    "unsupported": "not reachable from Python",
    "native": "native Blender feature",
}

_TEXT_FIELDS = ("id", "name", "area", "level", "rationale", "external_strategy")
_DETECT_MODES = ("groups", "flags", "scene", "manual", "unsupported", "native", "always")


class CompatError(ValueError):
    """Raised when the compatibility matrix or a scan payload is invalid."""


# ---------------------------------------------------------------------------
# matrix loading
# ---------------------------------------------------------------------------
def load_matrix(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate ``data/compat.json`` (or *path* for tests)."""
    source = Path(path) if path is not None else DATA_FILE
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise CompatError(f"cannot read compatibility matrix {source}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CompatError(f"invalid JSON in {source}: {exc}") from exc
    validate_matrix(data)
    return data


def items(data: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return the 39 compatibility items."""
    matrix = dict(data) if data is not None else load_matrix()
    return list(matrix["items"])


def counts(data: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Return the declared level counts (``{"L2": 13, "L1": 15, "L0": 11}``)."""
    matrix = dict(data) if data is not None else load_matrix()
    return {level: int(matrix["counts"][level]) for level in LEVELS}


def matrix_summary(data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compact matrix description used by reports and the CLI."""
    matrix = dict(data) if data is not None else load_matrix()
    return {
        "title": matrix["title"],
        "source": matrix["source"],
        "target": matrix["target"],
        "total": int(matrix["total"]),
        "counts": counts(matrix),
        "ids": dict(matrix["ids"]),
        "levels": {level: dict(LEVEL_COLORS[level]) for level in LEVELS},
    }


def validate_matrix(data: Mapping[str, Any]) -> None:
    """Validate the matrix structure and raise :class:`CompatError` on problems."""
    if not isinstance(data, Mapping):
        raise CompatError("compatibility matrix must be a JSON object")
    required = ("schema_version", "title", "source", "target", "total", "counts", "items")
    for key in required:
        if key not in data:
            raise CompatError(f"compatibility matrix is missing {key!r}")
    if int(data["schema_version"]) != 1:
        raise CompatError(f"unsupported schema_version: {data['schema_version']!r}")

    matrix_rows = int(data["source"]["total_rows"]) if "total_rows" in data["source"] else 49
    all_items = data["items"]
    if not isinstance(all_items, list):
        raise CompatError("items must be a list")
    if len(all_items) != int(data["total"]):
        raise CompatError(
            f"declared total {data['total']} does not match {len(all_items)} items"
        )

    seen: set[str] = set()
    level_tally = {level: 0 for level in LEVELS}
    rows_seen: set[int] = set()
    for index, item in enumerate(all_items):
        where = f"item #{index + 1}"
        if not isinstance(item, Mapping):
            raise CompatError(f"{where} must be an object")
        for field in _TEXT_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise CompatError(f"{where} needs a non-empty {field!r}")
        item_id = item["id"]
        if item_id in seen:
            raise CompatError(f"duplicate item id {item_id!r}")
        seen.add(item_id)
        if not (item_id.startswith("A") or item_id.startswith("N")):
            raise CompatError(f"{where} id {item_id!r} must start with A or N")
        suffix = item_id[1:]
        if not suffix.isdigit():
            raise CompatError(f"{where} id {item_id!r} must end with a number")
        level = item["level"]
        if level not in LEVELS:
            raise CompatError(f"{where} has invalid level {level!r}")
        level_tally[level] += 1

        rows = item.get("source_rows")
        if not isinstance(rows, list) or not rows:
            raise CompatError(f"{where} needs a non-empty source_rows list")
        for row in rows:
            if not isinstance(row, int) or isinstance(row, bool):
                raise CompatError(f"{where} source_rows must contain integers")
            if not 1 <= row <= matrix_rows:
                raise CompatError(f"{where} row {row} is outside 1..{matrix_rows}")
            rows_seen.add(row)

        detect = item.get("detect")
        if not isinstance(detect, Mapping) or not detect:
            raise CompatError(f"{where} needs a detect object")
        unknown = set(detect) - set(_DETECT_MODES)
        if unknown:
            raise CompatError(f"{where} has unknown detect keys {sorted(unknown)}")
        for key in ("groups", "flags", "scene"):
            if key in detect and not isinstance(detect[key], (str, list)):
                raise CompatError(f"{where} detect.{key} must be a string or list")

    missing_levels = [level for level in LEVELS if level not in data["counts"]]
    if missing_levels:
        raise CompatError(f"counts missing levels {missing_levels}")
    declared = {level: int(data["counts"][level]) for level in LEVELS}
    if declared != level_tally:
        raise CompatError(f"counts {declared} do not match the items {level_tally}")

    missing_rows = sorted(set(range(1, matrix_rows + 1)) - rows_seen)
    if missing_rows:
        raise CompatError(f"matrix rows not represented: {missing_rows}")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------
def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _scene_groups(report: Mapping[str, Any]) -> list[str]:
    groups = report.get("goo_groups")
    if groups is None:
        groups = [
            name
            for name in _as_list(report.get("node_groups"))
            if isinstance(name, str) and name.startswith("GOO ")
        ]
    return [str(name) for name in _as_list(groups) if name]


def _scene_flags(report: Mapping[str, Any]) -> dict[str, bool]:
    raw = report.get("features")
    if raw is None:
        raw = report.get("feature_flags")
    flags: dict[str, bool] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            flags[str(key)] = bool(value)
    elif raw is not None:
        for name in _as_list(raw):
            flags[str(name)] = True
    return flags


def _match_groups(expected: Iterable[str], groups: Iterable[str]) -> tuple[list[str], list[str]]:
    found: list[str] = []
    available = list(groups)
    for needle in expected:
        text = str(needle).casefold()
        for group in available:
            if text in str(group).casefold() and group not in found:
                found.append(group)
                break
    missing = [str(needle) for needle in expected if not any(
        str(needle).casefold() in str(group).casefold() for group in found
    )]
    return found, missing


def _detect(item: Mapping[str, Any], report: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(status, evidence)`` for one matrix item."""
    detect = item.get("detect") or {}
    groups = _scene_groups(report)
    flags = _scene_flags(report)

    if detect.get("unsupported"):
        return "unsupported", "GOO C++ / Blender core internal; no Python hook"
    if detect.get("native"):
        return "native", "provided by Blender 5.2 itself"
    if detect.get("always"):
        return "present", "bridge runtime capability (no scene data needed)"
    if detect.get("manual"):
        return "manual", "bridge UI/operator; not visible in analyze JSON"

    signals: list[tuple[bool, bool, str]] = []  # (any_hit, all_hit, evidence)

    if detect.get("groups"):
        expected = [str(name) for name in _as_list(detect["groups"])]
        found, missing = _match_groups(expected, groups)
        evidence = (
            f"GOO group(s): {', '.join(found)}"
            if found
            else f"no GOO group matching {', '.join(expected)}"
        )
        if missing and found:
            evidence += f"; missing {', '.join(missing)}"
        signals.append((bool(found), not missing and bool(expected), evidence))

    if detect.get("flags"):
        expected_flags = [str(name) for name in _as_list(detect["flags"])]
        present = [name for name in expected_flags if flags.get(name)]
        absent = [name for name in expected_flags if not flags.get(name)]
        evidence = (
            "feature flag(s): " + ", ".join(f"{name}=true" for name in present)
            if present
            else "no matching feature flag in analyze report"
        )
        if absent and present:
            evidence += "; false: " + ", ".join(absent)
        signals.append((bool(present), not absent and bool(expected_flags), evidence))

    if detect.get("scene"):
        keys = [str(key) for key in _as_list(detect["scene"])]
        values = [report.get(key) for key in keys]
        hit = any(bool(value) for value in values)
        evidence = (
            ", ".join(f"{key}={values[index]!r}" for index, key in enumerate(keys))
            if hit
            else f"scene field(s) empty: {', '.join(keys)}"
        )
        signals.append((hit, hit, evidence))

    if not signals:
        return "manual", "no automatic detector declared"

    evidence = "; ".join(text for _, _, text in signals)
    if any(all_hit for _, all_hit, _ in signals):
        return "present", evidence
    if any(any_hit for any_hit, _, _ in signals):
        return "partial", evidence
    return "missing", evidence


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def scan_blend(report: Mapping[str, Any] | None = None, *, matrix: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Map an analyzer JSON report onto the 39 compatibility items.

    The analyzer report only needs to look like the output of
    ``scripts/analyze_scene.py``; missing keys are treated as "nothing found",
    so an empty or partial report is safe.
    """
    data = dict(matrix) if matrix is not None else load_matrix()
    source = report if isinstance(report, Mapping) else {}
    entries: list[dict[str, Any]] = []
    status_tally = {status: 0 for status in STATUSES}
    level_tally: dict[str, dict[str, Any]] = {
        level: {"total": 0, "statuses": {status: 0 for status in STATUSES}} for level in LEVELS
    }

    for item in items(data):
        status, evidence = _detect(item, source)
        entry = {
            "id": item["id"],
            "name": item["name"],
            "area": item["area"],
            "level": item["level"],
            "goo_status": item.get("goo_status", ""),
            "status": status,
            "evidence": evidence,
            "rationale": item["rationale"],
            "external_strategy": item["external_strategy"],
            "source_rows": list(item["source_rows"]),
            "source_features": list(item.get("source_features") or []),
        }
        entries.append(entry)
        status_tally[status] += 1
        level_tally[item["level"]]["total"] += 1
        level_tally[item["level"]]["statuses"][status] += 1

    scene = {
        "file": str(source.get("file") or source.get("scene_path") or ""),
        "name": str(source.get("name") or ""),
        "blender": str(source.get("blender_version") or ""),
        "engine": str(source.get("engine") or ""),
        "goo_groups": _scene_groups(source),
        "goo_materials": len(_as_list(source.get("goo_materials"))),
        "flags": _scene_flags(source),
    }

    return {
        "schema_version": 1,
        "matrix": matrix_summary(data),
        "scene": scene,
        "items": entries,
        "total": len(entries),
        "counts": status_tally,
        "level_summary": level_tally,
    }


def level_summary(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-level totals plus detected statuses (from a scan result)."""
    return dict(result.get("level_summary") or {})


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------
def write_json(result: Mapping[str, Any], path: Path | str) -> Path:
    """Write the machine-readable compatibility document."""
    destination = Path(path)
    atomic_write_text(
        destination,
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
    )
    return destination


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_html(result: Mapping[str, Any], *, title: str | None = None) -> str:
    """Return a complete, self-contained HTML report (inline CSS, no assets)."""
    matrix = result.get("matrix") or {}
    declared = matrix.get("counts") or {}
    scan_counts = result.get("counts") or {}
    scene = result.get("scene") or {}
    heading = title or str(matrix.get("title") or "GOO Bridge compatibility report")
    file_label = scene.get("file") or scene.get("name") or "(unsaved scene)"

    chips = "".join(
        f'<span class="chip {LEVEL_COLORS[level]["color"]}">'
        f'{level} {LEVEL_COLORS[level]["name"]}: {int(declared.get(level, 0))}</span>'
        for level in LEVELS
    )
    status_chips = "".join(
        f'<span class="status-chip">{status}: {int(scan_counts.get(status, 0))}</span>'
        for status in STATUSES
        if scan_counts.get(status)
    )
    legend = "".join(
        f'<span class="legend {LEVEL_COLORS[level]["color"]}">'
        f'{level} - {LEVEL_COLORS[level]["name"]}</span>'
        for level in LEVELS
    )

    rows: list[str] = []
    for entry in result.get("items") or []:
        level = entry.get("level", "L0")
        color = LEVEL_COLORS.get(level, LEVEL_COLORS["L0"])["color"]
        status = entry.get("status", "")
        rows.append(
            '<tr id="item-{id}">'
            '<td class="id">{id}</td>'
            '<td class="feature">{name}<div class="area">{area}</div></td>'
            '<td class="levelcell"><span class="level {color}">{level}</span></td>'
            '<td class="statuscell"><span class="status s-{status}">{status}</span>'
            '<div class="evidence">{evidence}</div></td>'
            '<td class="rationale">{rationale}</td>'
            '<td class="strategy">{strategy}</td>'
            "</tr>".format(
                id=_escape(entry.get("id")),
                name=_escape(entry.get("name")),
                area=_escape(entry.get("area")),
                color=color,
                level=_escape(level),
                status=_escape(status),
                evidence=_escape(entry.get("evidence")),
                rationale=_escape(entry.get("rationale")),
                strategy=_escape(entry.get("external_strategy")),
            )
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(heading)} - {_escape(file_label)}</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 0; padding: 2rem; background: #f6f8fa; color: #1f2328; }}
main {{ max-width: 1180px; margin: 0 auto; }}
h1 {{ font-size: 1.55rem; margin: 0 0 .35rem; }}
h2 {{ font-size: 1.1rem; margin: 2rem 0 .6rem; }}
p.meta {{ color: #59636e; margin: .2rem 0; }}
.chip, .status-chip, .legend {{ display: inline-block; border-radius: 999px;
       padding: .18rem .6rem; margin: .15rem .3rem .15rem 0; font-size: .82rem; }}
.chip, .legend {{ color: #fff; font-weight: 600; }}
.green {{ background: #1a7f37; color: #fff; }}
.yellow {{ background: #9a6700; color: #fff; }}
.red {{ background: #cf222e; color: #fff; }}
.status-chip {{ background: #eaeef2; color: #1f2328; }}
.legend {{ margin-right: .5rem; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; font-size: .88rem; }}
th, td {{ border: 1px solid #d0d7de; padding: .45rem .55rem; vertical-align: top; text-align: left; }}
th {{ background: #f0f3f6; position: sticky; top: 0; }}
td.id {{ font-weight: 700; white-space: nowrap; }}
td.feature {{ min-width: 13rem; }}
.area {{ color: #59636e; font-size: .78rem; }}
.level {{ color: #fff; border-radius: 4px; padding: .1rem .45rem; font-weight: 700; }}
.s-present {{ color: #1a7f37; font-weight: 600; }}
.s-partial {{ color: #9a6700; font-weight: 600; }}
.s-missing {{ color: #cf222e; font-weight: 600; }}
.s-unsupported {{ color: #cf222e; font-weight: 600; }}
.s-native {{ color: #0969da; font-weight: 600; }}
.s-manual {{ color: #59636e; font-weight: 600; }}
.evidence {{ color: #59636e; font-size: .78rem; margin-top: .15rem; }}
.rationale {{ min-width: 18rem; }}
.strategy {{ min-width: 18rem; }}
footer {{ color: #59636e; font-size: .8rem; margin-top: 2rem; }}
</style>
</head>
<body>
<main>
<h1>{_escape(heading)}</h1>
<p class="meta"><b>Scene:</b> {_escape(file_label)}
&nbsp; <b>Blender:</b> {_escape(scene.get("blender") or "-")}
&nbsp; <b>Engine:</b> {_escape(scene.get("engine") or "-")}
&nbsp; <b>GOO groups:</b> {len(scene.get("goo_groups") or [])}</p>
<p class="meta">Bridge-specific 39-item view of the GOO Engine 4.4 inventory
(source: {_escape((matrix.get("source") or {}).get("matrix", "goo-port docs"))}).</p>
<h2>Matrix coverage</h2>
<p>{chips}</p>
<p>{legend}</p>
<h2>Scene detection</h2>
<p>{status_chips or '<span class="status-chip">no detection data</span>'}</p>
<h2>Items</h2>
<table>
<thead><tr><th>ID</th><th>Feature</th><th>Level</th><th>Detected</th>
<th>Rationale</th><th>Bridge strategy</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
<footer>Generated by GOO Bridge compatibility report. Counts: 13 L2 / 15 L1 / 11 L0
across 39 bridge-specific items; every item is red, yellow or green by design.</footer>
</main>
</body>
</html>
"""
    return document


def write_html(result: Mapping[str, Any], path: Path | str) -> Path:
    """Write the standalone HTML report."""
    destination = Path(path)
    atomic_write_text(destination, render_html(result))
    return destination


def generate_reports(
    analysis: Mapping[str, Any] | None,
    json_path: Path | str,
    html_path: Path | str,
    *,
    matrix: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan *analysis* and write both the JSON and HTML compatibility reports."""
    result = scan_blend(analysis, matrix=matrix)
    write_json(result, json_path)
    write_html(result, html_path)
    return result
