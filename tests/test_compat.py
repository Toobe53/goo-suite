# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the GOO Bridge compatibility matrix and reports.

Run from the repository root with either CPython or Blender's bundled Python::

    python -m unittest tests.test_compat
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from goo_bridge import compat

ROOT = Path(__file__).resolve().parent.parent
MATRIX_PATH = ROOT / "goo_bridge" / "data" / "compat.json"
EXPECTED_IDS = [
    *[f"A{index}" for index in range(1, 26)],
    *[f"N{index}" for index in range(1, 15)],
]
FULL_GROUPS = [
    "GOO Active Camera",
    "GOO Curvature",
    "GOO Depth Fade",
    "GOO Hex Grid",
    "GOO Light Info",
    "GOO Light Info New",
    "GOO OKLab Color Ramp",
    "GOO SDF Noise",
    "GOO SDF Op",
    "GOO SDF Primitive",
    "GOO SDF Vector Op",
    "GOO Scene Time",
    "GOO Screen Space Info",
    "GOO Set Depth",
    "GOO Shader Info",
    "GOO Threshold Mask",
    "GOO Twirl",
    "GOO Water Ripples",
]
FULL_FLAGS = {
    "has_goo_groups": True,
    "light_groups": True,
    "active_camera_override": True,
    "scene_time": True,
    "sdf": True,
    "oklab": True,
    "missing_external_files": False,
    "compositor_nodes": True,
}
FULL_REPORT = {
    "ok": True,
    "file": "C:/scenes/goo_scene.blend",
    "name": "goo_scene.blend",
    "blender_version": "5.2.3 LTS",
    "engine": "BLENDER_EEVEE",
    "objects": 12,
    "materials": 5,
    "node_groups": FULL_GROUPS,
    "goo_groups": FULL_GROUPS,
    "goo_group_count": len(FULL_GROUPS),
    "goo_materials": [{"material": "Body", "groups": ["GOO Shader Info"]}],
    "view_layers": ["ViewLayer"],
    "active_camera": "Camera",
    "features": FULL_FLAGS,
    "external_files": [],
    "missing_files": [],
}


def _matrix_copy() -> dict:
    return copy.deepcopy(compat.load_matrix(MATRIX_PATH))


class MatrixFileTests(unittest.TestCase):
    def test_matrix_file_is_valid_json(self) -> None:
        data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertEqual(data["total"], 39)

    def test_ids_are_exactly_a1_a25_n1_n14(self) -> None:
        ids = [item["id"] for item in compat.items(compat.load_matrix(MATRIX_PATH))]
        self.assertEqual(ids, EXPECTED_IDS)
        self.assertEqual(len(set(ids)), 39)

    def test_level_counts_are_13_15_11(self) -> None:
        data = compat.load_matrix(MATRIX_PATH)
        self.assertEqual(compat.counts(data), {"L2": 13, "L1": 15, "L0": 11})
        tally = {"L2": 0, "L1": 0, "L0": 0}
        for item in compat.items(data):
            tally[item["level"]] += 1
        self.assertEqual(tally, {"L2": 13, "L1": 15, "L0": 11})
        self.assertEqual(sum(tally.values()), 39)

    def test_every_item_has_required_fields_and_no_nulls(self) -> None:
        for item in compat.items(compat.load_matrix(MATRIX_PATH)):
            for field in ("id", "name", "area", "level", "rationale", "external_strategy"):
                self.assertIsInstance(item[field], str, field)
                self.assertTrue(item[field].strip(), f"{item['id']} {field} is empty")
            self.assertIn(item["level"], compat.LEVELS)
            self.assertIsInstance(item["source_rows"], list)
            self.assertTrue(item["source_rows"], item["id"])
            self.assertTrue(all(isinstance(row, int) for row in item["source_rows"]))
            self.assertIsInstance(item["detect"], dict)
            self.assertTrue(item["detect"], item["id"])

    def test_all_49_source_rows_are_represented(self) -> None:
        rows = {
            row
            for item in compat.items(compat.load_matrix(MATRIX_PATH))
            for row in item["source_rows"]
        }
        self.assertEqual(rows, set(range(1, 50)))

    def test_a_items_are_addon_scope_and_n_items_are_native(self) -> None:
        data = compat.load_matrix(MATRIX_PATH)
        self.assertEqual(data["ids"], {"A": 25, "N": 14})
        for item in compat.items(data):
            if item["id"].startswith("N") and item["level"] == "L0":
                self.assertTrue(item["detect"].get("unsupported"), item["id"])

    def test_validate_rejects_bad_counts(self) -> None:
        data = _matrix_copy()
        data["counts"]["L2"] = 99
        with self.assertRaises(compat.CompatError):
            compat.validate_matrix(data)

    def test_validate_rejects_duplicate_id(self) -> None:
        data = _matrix_copy()
        data["items"][1]["id"] = data["items"][0]["id"]
        with self.assertRaises(compat.CompatError):
            compat.validate_matrix(data)

    def test_validate_rejects_missing_rows(self) -> None:
        data = _matrix_copy()
        platform = next(item for item in data["items"] if item["id"] == "A12")
        platform["source_rows"] = [1]
        with self.assertRaises(compat.CompatError):
            compat.validate_matrix(data)


class ScanTests(unittest.TestCase):
    def test_scan_returns_all_39_items_with_valid_status(self) -> None:
        result = compat.scan_blend(FULL_REPORT)
        self.assertEqual(result["total"], 39)
        self.assertEqual([entry["id"] for entry in result["items"]], EXPECTED_IDS)
        for entry in result["items"]:
            self.assertIn(entry["status"], compat.STATUSES, entry["id"])
            self.assertIn(entry["level"], compat.LEVELS, entry["id"])
            self.assertTrue(entry["evidence"].strip(), entry["id"])
        self.assertEqual(sum(result["counts"].values()), 39)

    def test_full_goo_scene_detection(self) -> None:
        result = compat.scan_blend(FULL_REPORT)
        by_id = {entry["id"]: entry for entry in result["items"]}
        for item_id in ("A1", "A3", "A4", "A7", "A11", "A13", "A14", "A15",
                        "A16", "A17", "A18", "A19", "A20", "A21", "A22", "A23"):
            self.assertEqual(by_id[item_id]["status"], "present", item_id)
        self.assertEqual(by_id["A12"]["status"], "present")  # always-on platform
        self.assertEqual(by_id["A2"]["status"], "manual")
        self.assertEqual(by_id["N12"]["status"], "native")
        for item_id in ("N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9",
                        "N11", "N13"):
            self.assertEqual(by_id[item_id]["status"], "unsupported", item_id)
        self.assertEqual(by_id["N10"]["status"], "manual")
        self.assertEqual(by_id["N14"]["status"], "manual")
        self.assertEqual(result["counts"]["missing"], 0)

    def test_empty_report_is_safe(self) -> None:
        for report in ({}, None, {"goo_groups": []}):  # type: ignore[arg-type]
            result = compat.scan_blend(report)  # type: ignore[arg-type]
            self.assertEqual(result["total"], 39)
            self.assertEqual(sum(result["counts"].values()), 39)
            by_id = {entry["id"]: entry for entry in result["items"]}
            self.assertEqual(by_id["A13"]["status"], "missing")
            self.assertEqual(by_id["A12"]["status"], "present")
            self.assertEqual(by_id["N12"]["status"], "native")
            self.assertEqual(by_id["N2"]["status"], "unsupported")

    def test_partial_detection_for_group_cluster(self) -> None:
        report = {"goo_groups": ["GOO SDF Op", "GOO Shader Info"], "features": {}}
        result = compat.scan_blend(report)
        by_id = {entry["id"]: entry for entry in result["items"]}
        self.assertEqual(by_id["A19"]["status"], "partial")
        self.assertEqual(by_id["A13"]["status"], "present")
        self.assertEqual(by_id["A14"]["status"], "missing")

    def test_flags_and_camera_detection(self) -> None:
        report = {"goo_groups": [], "features": {"light_groups": True},
                  "active_camera": "ShotCam"}
        result = compat.scan_blend(report)
        by_id = {entry["id"]: entry for entry in result["items"]}
        self.assertEqual(by_id["A11"]["status"], "present")
        self.assertEqual(by_id["A7"]["status"], "present")
        self.assertEqual(by_id["A22"]["status"], "missing")

    def test_features_as_list(self) -> None:
        report = {"goo_groups": [], "features": ["oklab", "scene_time"]}
        result = compat.scan_blend(report)
        by_id = {entry["id"]: entry for entry in result["items"]}
        self.assertEqual(by_id["A21"]["status"], "present")
        self.assertEqual(by_id["A22"]["status"], "present")

    def test_node_groups_fallback(self) -> None:
        report = {"node_groups": ["GOO Twirl", "SomeOtherGroup"], "features": {}}
        result = compat.scan_blend(report)
        by_id = {entry["id"]: entry for entry in result["items"]}
        self.assertEqual(by_id["A4"]["status"], "present")
        self.assertEqual(by_id["A3"]["status"], "missing")

    def test_level_summary_totals(self) -> None:
        result = compat.scan_blend(FULL_REPORT)
        summary = compat.level_summary(result)
        self.assertEqual(sum(part["total"] for part in summary.values()), 39)
        self.assertEqual(summary["L2"]["total"], 13)
        self.assertEqual(summary["L1"]["total"], 15)
        self.assertEqual(summary["L0"]["total"], 11)


class ReportTests(unittest.TestCase):
    def test_json_and_html_reports_are_written(self) -> None:
        result = compat.scan_blend(FULL_REPORT)
        with tempfile.TemporaryDirectory(prefix="goo-bridge-compat-") as temp:
            folder = Path(temp) / "nested"
            json_path = folder / "compat.json"
            html_path = folder / "compat.html"
            written = compat.generate_reports(FULL_REPORT, json_path, html_path)
            self.assertTrue(json_path.is_file())
            self.assertTrue(html_path.is_file())
            self.assertEqual(written["total"], 39)
            reloaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["items"], result["items"])
            self.assertEqual(reloaded["matrix"]["counts"], {"L2": 13, "L1": 15, "L0": 11})
            document = html_path.read_text(encoding="utf-8")
            self.assertTrue(document.startswith("<!doctype html>"))
            self.assertTrue(document.rstrip().endswith("</html>"))

    def test_html_contains_every_id(self) -> None:
        result = compat.scan_blend(FULL_REPORT)
        document = compat.render_html(result)
        for item_id in EXPECTED_IDS:
            self.assertIn(f'id="item-{item_id}"', document, item_id)
            self.assertIn(f">{item_id}<", document, item_id)

    def test_html_has_counts_and_colors(self) -> None:
        document = compat.render_html(compat.scan_blend(FULL_REPORT))
        self.assertIn("L2 supported: 13", document)
        self.assertIn("L1 partial: 15", document)
        self.assertIn("L0 unreachable: 11", document)
        self.assertIn("13 L2 / 15 L1 / 11 L0", document)
        for color in ("green", "yellow", "red"):
            self.assertIn(color, document)

    def test_html_is_self_contained(self) -> None:
        document = compat.render_html(compat.scan_blend(FULL_REPORT))
        self.assertIn("<style>", document)
        self.assertNotIn("<script", document)
        self.assertNotIn("<link", document)
        self.assertNotIn("http://", document)
        self.assertNotIn("https://", document)
        self.assertNotIn("src=", document)

    def test_report_has_no_none_values(self) -> None:
        result = compat.scan_blend({})

        def walk(value, path="root"):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertIsNotNone(child, f"{path}.{key}")
                    walk(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    self.assertIsNotNone(child, f"{path}[{index}]")
                    walk(child, f"{path}[{index}]")

        walk(result)

    def test_matrix_summary(self) -> None:
        summary = compat.matrix_summary()
        self.assertEqual(summary["total"], 39)
        self.assertEqual(summary["counts"], {"L2": 13, "L1": 15, "L0": 11})
        self.assertEqual(summary["ids"], {"A": 25, "N": 14})
        self.assertEqual(summary["levels"]["L2"]["color"], "green")


if __name__ == "__main__":
    unittest.main()
