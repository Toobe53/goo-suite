# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goo_bridge import __version__  # noqa: E402
from goo_bridge.assets import from_analysis  # noqa: E402
from goo_bridge.blender import BlenderError, find_blender  # noqa: E402
from goo_bridge.config import Config, RemoteProfile, load_config, save_config  # noqa: E402
from goo_bridge.jobs import RenderJob  # noqa: E402
from goo_bridge.report import summarize, write_html  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            config = Config(blender="C:/Blender/blender.exe", language="zh_CN")
            config.set_profile(RemoteProfile(name="studio", host="10.0.0.5", user="render"))
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.blender, "C:/Blender/blender.exe")
            self.assertEqual(loaded.language, "zh_CN")
            self.assertEqual(loaded.profile("studio").host, "10.0.0.5")

    def test_missing_config_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            loaded = load_config(Path(temp) / "none.json")
            self.assertEqual(loaded.remote, [])

    def test_invalid_numeric_fields_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "version": "bad",
                        "remote": [
                            {"name": "x", "host": "h", "user": "u", "port": "bad"},
                            {"name": "y", "host": "h", "user": "u", "port": 70000},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_config(path)
        self.assertEqual(loaded.version, 2)
        self.assertEqual(loaded.profile("x").port, 22)
        self.assertEqual(loaded.profile("y").port, 22)


class JobTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        job = RenderJob(scene="a.blend", output="out", camera="Cam", samples=8)
        with tempfile.TemporaryDirectory() as temp:
            path = job.write(Path(temp) / "job.json")
            loaded = RenderJob.read(path)
        self.assertEqual(loaded.job_id, job.job_id)
        self.assertEqual(loaded.camera, "Cam")
        self.assertEqual(loaded.samples, 8)


class BlenderDiscoveryTests(unittest.TestCase):
    def test_missing_explicit_path_fails(self) -> None:
        with self.assertRaises(BlenderError):
            find_blender("Z:/definitely/not/blender.exe")

    def test_discovers_installed_blender(self) -> None:
        try:
            info = find_blender()
        except BlenderError as exc:
            self.skipTest(str(exc))
        self.assertTrue(info.path.is_file())
        self.assertTrue(info.version)


class ReportTests(unittest.TestCase):
    def test_summary_and_html(self) -> None:
        data = {
            "name": "demo.blend",
            "blender_version": "5.2.3",
            "goo_groups": ["GOO Shader Info"],
            "objects": 2,
            "materials": 1,
            "external_files": [{"path": "/tmp/a.png", "exists": True}],
            "missing_files": [],
            "features": {"has_goo_groups": True},
        }
        summary = summarize(data)
        self.assertEqual(summary["goo_groups"], 1)
        with tempfile.TemporaryDirectory() as temp:
            path = write_html(data, Path(temp) / "r.html")
            text = path.read_text(encoding="utf-8")
        self.assertIn("GOO Shader Info", text)
        self.assertIn("demo.blend", text)


class AssetTests(unittest.TestCase):
    def test_parallel_inventory_preserves_order_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for index in range(5):
                path = Path(temp) / f"asset-{index}.bin"
                path.write_bytes(f"payload-{index}".encode("ascii"))
                paths.append(path)
            report = {
                "external_files": [
                    {"path": str(path), "kind": "texture", "name": path.name}
                    for path in paths
                ]
            }
            expected_hashes = [
                hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
            ]
            assets = from_analysis(report)
        self.assertEqual([asset.path for asset in assets], [str(path) for path in paths])
        self.assertEqual([asset.sha256 for asset in assets], expected_hashes)


class CliTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [sys.executable, "-m", "goo_bridge", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(ROOT),
            timeout=120,
        )

    def test_help(self) -> None:
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("goo-bridge", result.stdout)

    def test_doctor_json(self) -> None:
        result = self._run("doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], __version__)
        self.assertIn("blender", payload)

    def test_analyze_render_demo(self) -> None:
        try:
            info = find_blender()
        except BlenderError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "goo_fixture.blend"
            script = (
                "import bpy; "
                "g = bpy.data.node_groups.new('GOO Test', 'ShaderNodeTree'); "
                "g.use_fake_user = True; "
                "bpy.ops.mesh.primitive_cube_add(); "
                "bpy.ops.object.camera_add(location=(7, -7, 5), rotation=(1.1, 0, 0.8)); "
                "bpy.context.scene.camera = bpy.context.object; "
                "bpy.ops.object.light_add(type='SUN', location=(3, -3, 5)); "
                f"bpy.ops.wm.save_as_mainfile(filepath={str(fixture)!r})"
            )
            made = subprocess.run(
                [str(info.path), "--background", "--factory-startup", "--python-expr", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            analysis = Path(temp) / "analysis.json"
            result = self._run("analyze", str(fixture), "-o", str(analysis), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["goo_groups"], 1)
            self.assertIn("GOO Test", payload["group_names"])
            out = Path(temp) / "out"
            rendered = self._run(
                "render",
                str(fixture),
                "-o",
                str(out),
                "--samples",
                "1",
                "--resolution",
                "10",
                "--json",
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            result_payload = json.loads(rendered.stdout)
            self.assertTrue(result_payload["ok"])
            self.assertTrue(result_payload["files"])


if __name__ == "__main__":
    unittest.main()
