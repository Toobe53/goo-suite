# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goo_bridge.blender import BlenderError, find_blender  # noqa: E402
from goo_bridge.engine import (  # noqa: E402
    ENGINE_ENV,
    PACKAGE_ENV,
    EngineInfo,
    apply_scene,
    detect_engine,
    render_scene,
)

_FAKE_ENGINE = '''\
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if not args:
    print("goo 9.9.9")
    raise SystemExit(0)
command = args[0]
if command in ("version", "--version", "-V"):
    print(json.dumps({
        "ok": True,
        "command": "version",
        "cli": {"version": "9.9.9"},
        "addon": {"version": "9.9.9", "path": ""},
        "groups": 7,
    }))
    raise SystemExit(0)
if command == "apply":
    out = args[args.index("-o") + 1] if "-o" in args else ""
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"BLENDER")
    print(json.dumps({"ok": True, "command": "apply", "blend_out": out}))
    raise SystemExit(0)
if command == "render":
    out = args[args.index("-o") + 1] if "-o" in args else "."
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / "render_0001.png"
    image.write_bytes(b"PNG")
    print(json.dumps({
        "ok": True,
        "command": "render",
        "output_dir": str(directory),
        "passes": [{"file": str(image)}],
        "settings": {"samples": 1, "format": "PNG"},
    }))
    raise SystemExit(0)
print("unknown command", command, file=sys.stderr)
raise SystemExit(2)
'''


def _fake_engine(directory: Path) -> Path:
    path = directory / "fake_goo.py"
    path.write_text(_FAKE_ENGINE, encoding="utf-8")
    return path


def _run_bridge(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONPATH"] = str(ROOT)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "goo_bridge", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
        cwd=str(ROOT),
        timeout=120,
    )


class EngineInfoTests(unittest.TestCase):
    def test_to_dict_is_stable_minimal_shape(self) -> None:
        info = EngineInfo(found=True, path="C:/goo/goo.exe", version="1.2.3", groups=4)
        self.assertEqual(
            info.to_dict(),
            {"found": True, "path": "C:/goo/goo.exe", "version": "1.2.3", "groups": 4},
        )

    def test_package_command_and_environment(self) -> None:
        info = EngineInfo(found=True, path="C:/src/goo-port/goo", source="package")
        self.assertEqual(info.command()[1:], ["-m", "goo"])
        env = info.environment({})
        self.assertIn("C:/src/goo-port", env["PYTHONPATH"].replace("\\", "/"))


class DetectionTests(unittest.TestCase):
    def test_detect_fake_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake = _fake_engine(Path(temp))
            info = detect_engine(fake)
            self.assertTrue(info.found)
            self.assertEqual(info.source, "executable")
            self.assertEqual(info.version, "9.9.9")
            self.assertEqual(info.groups, 7)
            self.assertTrue(info.path.endswith("fake_goo.py"))

    def test_missing_explicit_path_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            info = detect_engine(Path(temp) / "does-not-exist")
            self.assertFalse(info.found)
            self.assertIn("not found", info.error)

    def test_detects_local_package_and_counts_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "goo"
            package.mkdir()
            (package / "__init__.py").write_text(
                '__version__ = "1.2.3"\nCLI_NAME = "goo"\n', encoding="utf-8"
            )
            nodes = root / "goo_npr" / "nodes"
            nodes.mkdir(parents=True)
            (nodes / "demo.py").write_text(
                "@group('One')\ndef one(tree):\n    pass\n\n@group('Two')\ndef two(tree):\n    pass\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {PACKAGE_ENV: str(root)}, clear=False):
                os.environ.pop(ENGINE_ENV, None)
                with mock.patch("goo_bridge.engine._which", return_value=None):
                    info = detect_engine()
            self.assertTrue(info.found)
            self.assertEqual(info.source, "package")
            self.assertEqual(info.version, "1.2.3")
            self.assertEqual(info.groups, 2)
            self.assertEqual(Path(info.addon_path).name, "goo_npr")


class AdapterTests(unittest.TestCase):
    def test_apply_scene_through_fake_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            fake = _fake_engine(workspace)
            info = detect_engine(fake)
            source = workspace / "source.blend"
            source.write_bytes(b"BLENDER")
            target = workspace / "applied.blend"
            result = apply_scene(source, target, engine=info)
            self.assertTrue(result.ok, result.get("error"))
            self.assertEqual(result.mode, "engine")
            self.assertTrue(target.is_file())
            self.assertTrue(os.path.samefile(result["blend_out"], target))
            self.assertEqual(result["engine"]["version"], "9.9.9")

    def test_render_scene_through_fake_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            fake = _fake_engine(workspace)
            info = detect_engine(fake)
            source = workspace / "source.blend"
            source.write_bytes(b"BLENDER")
            output = workspace / "render"
            result = render_scene(source, output, engine=info, samples=2, resolution=25)
            self.assertTrue(result.ok, result.get("error"))
            self.assertEqual(result.mode, "engine")
            self.assertTrue(result.files)
            self.assertTrue(Path(result.files[0]).is_file())
            self.assertEqual(result["engine"]["groups"], 7)

    def test_apply_without_engine_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            source = workspace / "source.blend"
            source.write_bytes(b"BLENDER")
            result = apply_scene(source, workspace / "out.blend", engine=EngineInfo())
            self.assertFalse(result.ok)
            self.assertIn("GOO engine not found", result.error)

    def test_missing_scene_fails_cleanly(self) -> None:
        result = render_scene("Z:/definitely/not/a/scene.blend", "Z:/out", engine=EngineInfo())
        self.assertFalse(result.ok)
        self.assertIn("scene not found", result.error)


class FallbackTests(unittest.TestCase):
    def test_blender_background_fallback(self) -> None:
        try:
            blender = find_blender()
        except BlenderError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            fixture = workspace / "tiny.blend"
            script = (
                "import bpy; "
                "bpy.ops.mesh.primitive_cube_add(); "
                "bpy.ops.object.camera_add(location=(7, -7, 5), rotation=(1.1, 0, 0.8)); "
                "bpy.context.scene.camera = bpy.context.object; "
                "bpy.ops.object.light_add(type='SUN', location=(3, -3, 5)); "
                f"bpy.ops.wm.save_as_mainfile(filepath={str(fixture)!r})"
            )
            made = subprocess.run(
                [str(blender.path), "--background", "--factory-startup", "--python-expr", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            output = workspace / "out"
            result = render_scene(
                fixture,
                output,
                engine=EngineInfo(),
                samples=1,
                resolution=10,
                format="PNG",
            )
            self.assertTrue(result.ok, result.get("error"))
            self.assertEqual(result.mode, "blender")
            self.assertTrue(result.files)
            for path in result.files:
                self.assertTrue(Path(path).is_file(), path)
            self.assertTrue((output / "render_manifest.json").is_file())


class CliIntegrationTests(unittest.TestCase):
    def test_doctor_reports_engine_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake = _fake_engine(Path(temp))
            env = {
                ENGINE_ENV: str(fake),
                "PYTHONPATH": str(ROOT),
            }
            env.pop(PACKAGE_ENV, None)
            completed = _run_bridge("doctor", "--json", env=env)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["engine"]["found"])
            self.assertEqual(payload["engine"]["version"], "9.9.9")
            self.assertEqual(payload["engine"]["groups"], 7)


if __name__ == "__main__":
    unittest.main()
