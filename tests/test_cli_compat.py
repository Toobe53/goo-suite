# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI-level coverage for the compatibility command and docs surface."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from goo_bridge import cli


class CompatCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="goo-bridge-compat-cli-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compat_command_writes_html_and_json(self) -> None:
        output = self.root / "compat.html"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main(["compat", "-o", str(output)])
        self.assertEqual(code, 0)
        self.assertTrue(output.is_file())
        text = output.read_text(encoding="utf-8")
        self.assertIn("13 L2", text)
        self.assertIn("15 L1", text)
        self.assertIn("11 L0", text)
        for item_id in ("A1", "A25", "N1", "N14"):
            self.assertIn(f'id="item-{item_id}"', text)
        self.assertIn("compatibility: 39 items", stdout.getvalue())

    def test_compat_analysis_and_json_output(self) -> None:
        analysis = self.root / "analysis.json"
        analysis.write_text(
            json.dumps(
                {
                    "ok": True,
                    "file": "sample.blend",
                    "blender_version": "5.2.3",
                    "goo_groups": ["GOO Line Composite", "GOO Light Info New"],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "compat.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli.main(
                ["compat", "--analysis", str(analysis), "-o", str(output), "--json"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["matrix"]["counts"], {"L2": 13, "L1": 15, "L0": 11})
        self.assertEqual(payload["total"], 39)
        self.assertTrue(any(item["status"] == "present" for item in payload["items"]))

    def test_docs_links_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        docs = [
            "README.md",
            "README.zh-CN.md",
            "docs/tutorial.md",
            "docs/remote.md",
            "docs/cli.md",
            "docs/compatibility.md",
            "docs/verification.md",
            "docs/optimizations.md",
        ]
        for name in docs:
            self.assertTrue((root / name).is_file(), name)
        text = (root / "docs/tutorial.md").read_text(encoding="utf-8")
        self.assertIn("goo-bridge", text)
        self.assertIn("远程渲染", text)
        chinese = (root / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("安装", chinese)
        self.assertIn("远程渲染", chinese)
        self.assertIn("39 项", chinese)


if __name__ == "__main__":
    unittest.main()
