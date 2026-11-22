# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the GOO Bridge Tkinter GUI.

The tests run under Blender's bundled Python too, which has no tkinter.
Anything that needs a real Tk interpreter is skipped cleanly; the pure
helpers, the lazy-import guarantee and the JSON selftest protocol stay
covered everywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goo_bridge import gui  # noqa: E402
from goo_bridge.config import RemoteProfile  # noqa: E402


def _env(**extra: str) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")
    env.update(extra)
    return env


def _tkinter_available() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


def _run(*args: str, env: dict[str, str] | None = None, timeout: int = 120):
    return subprocess.run(
        list(args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or _env(),
        timeout=timeout,
    )


class ImportTests(unittest.TestCase):
    def test_import_is_lazy_and_cheap(self) -> None:
        code = (
            "import json, sys\n"
            "import goo_bridge.gui as gui\n"
            "print(json.dumps({\n"
            "    'tkinter_imported': 'tkinter' in sys.modules,\n"
            "    'tabs': list(gui.TABS),\n"
            "    'buttons': len(gui.BUTTONS),\n"
            "    'run_gui': callable(gui.run_gui),\n"
            "}))\n"
        )
        result = _run(sys.executable, "-c", code, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertFalse(payload["tkinter_imported"], "gui.py must not import tkinter at module load")
        self.assertEqual(tuple(payload["tabs"]), gui.TABS)
        self.assertEqual(payload["buttons"], len(gui.BUTTONS))
        self.assertTrue(payload["run_gui"])

    def test_constants_cover_all_sections(self) -> None:
        self.assertEqual(gui.TABS, ("Project", "Analyze", "Remote", "Queue", "Report"))
        required = {
            "Browse Scene...",
            "Run Analysis",
            "Save Profile",
            "Test Connection",
            "Add Job",
            "Run Queue",
            "Refresh Compatibility",
            "Save Report...",
        }
        for label in required:
            self.assertIn(label, gui.BUTTONS)


class HelperTests(unittest.TestCase):
    def test_build_remote_profile(self) -> None:
        profile = gui.build_remote_profile(
            name=" studio ",
            host=" render.example ",
            port="2222",
            user="render",
            key="~/.ssh/id_ed25519",
            blender="/opt/blender/blender",
            workspace="~/goo",
        )
        self.assertIsInstance(profile, RemoteProfile)
        self.assertEqual(profile.name, "studio")
        self.assertEqual(profile.host, "render.example")
        self.assertEqual(profile.port, 2222)
        self.assertEqual(profile.python, "python3")
        with self.assertRaises(ValueError):
            gui.build_remote_profile(name="", host="host")
        with self.assertRaises(ValueError):
            gui.build_remote_profile(name="x", host="")
        with self.assertRaises(ValueError):
            gui.build_remote_profile(name="x", host="h", port="not-a-port")

    def test_local_render_kwargs(self) -> None:
        kwargs = gui.local_render_kwargs(
            "scene.blend",
            "out",
            camera="Cam",
            samples="8",
            resolution="50",
            frame="1-3",
            format="png",
        )
        self.assertEqual(kwargs["camera"], "Cam")
        self.assertEqual(kwargs["samples"], 8)
        self.assertEqual(kwargs["resolution"], 50)
        self.assertEqual(kwargs["frame"], "1-3")
        self.assertEqual(kwargs["format"], "PNG")
        with self.assertRaises(ValueError):
            gui.local_render_kwargs("", "out")
        with self.assertRaises(ValueError):
            gui.local_render_kwargs("scene", "out", samples=0)
        with self.assertRaises(ValueError):
            gui.local_render_kwargs("scene", "out", resolution=101)

    def test_remote_commands(self) -> None:
        test_cmd = gui.remote_test_command("studio")
        self.assertEqual(
            test_cmd[1:],
            ["-m", "goo_bridge", "remote", "test", "--profile", "studio"],
        )
        render_cmd = gui.remote_render_command(
            "studio",
            "scene.blend",
            "out",
            camera="Cam",
            samples=64,
            resolution=50,
            frame="1-3",
            format="jpeg",
        )
        self.assertEqual(
            render_cmd[1:6],
            ["-m", "goo_bridge", "remote", "render", "scene.blend"],
        )
        self.assertEqual(render_cmd[6:8], ["-o", "out"])
        self.assertEqual(render_cmd[8:10], ["--profile", "studio"])
        self.assertEqual(render_cmd[render_cmd.index("--camera") + 1], "Cam")
        self.assertEqual(render_cmd[render_cmd.index("--samples") + 1], "64")
        self.assertEqual(render_cmd[render_cmd.index("--resolution") + 1], "50")
        self.assertEqual(render_cmd[render_cmd.index("--frame") + 1], "1-3")
        self.assertEqual(render_cmd[render_cmd.index("--format") + 1], "JPEG")
        self.assertNotIn("--watch", render_cmd)
        with self.assertRaises(ValueError):
            gui.remote_test_command("")
        with self.assertRaises(ValueError):
            gui.remote_render_command("studio", "", "out")

    def test_compat_counts(self) -> None:
        items = [
            {"id": "A1", "level": "L2"},
            {"id": "A2", "level": "L1"},
            {"id": "A3", "level": "L1"},
            {"id": "N1", "level": "L0"},
            {"id": "N2", "level": "?"},
        ]
        counts = gui.compat_counts(items)
        self.assertEqual(counts, {"L2": 1, "L1": 2, "L0": 1})
        payload = {"items": items}
        self.assertEqual(gui.compat_counts(payload), {"L2": 1, "L1": 2, "L0": 1})
        self.assertEqual(len(gui.compat_items(payload)), 5)
        self.assertEqual(gui.compat_counts({"counts": {"L2": 13, "L1": 15, "L0": 11}}),
                         {"L2": 13, "L1": 15, "L0": 11})
        self.assertEqual(gui.compat_counts(None), {"L2": 0, "L1": 0, "L0": 0})

    def test_summary_text(self) -> None:
        text = gui.format_analysis_summary(
            {
                "name": "demo.blend",
                "blender_version": "5.2.3",
                "goo_groups": ["GOO Shader Info"],
                "objects": 2,
                "materials": 1,
                "external_files": [{"path": "tex.png"}],
                "missing_files": [],
            }
        )
        self.assertIn("demo.blend", text)
        self.assertIn("GOO groups: 1", text)
        self.assertIn("GOO Shader Info", text)


class SelftestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        cls.env = _env(
            GOO_BRIDGE_SELFTEST="1",
            GOO_BRIDGE_HOME=cls._temp.name,
        )
        cls.result = _run(sys.executable, "-m", "goo_bridge", "gui", env=cls.env)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def _payload(self) -> dict:
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        lines = [line for line in self.result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, f"expected exactly one JSON line, got: {lines!r}")
        return json.loads(lines[0])

    def test_selftest_prints_one_json_line(self) -> None:
        payload = self._payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(tuple(payload["tabs"]), gui.TABS)
        self.assertIsInstance(payload["buttons"], list)
        for label in gui.BUTTONS:
            self.assertIn(label, payload["buttons"])

    def test_selftest_opens_window_when_possible(self) -> None:
        payload = self._payload()
        if payload.get("skipped"):
            self.skipTest(payload["skipped"])
        self.assertTrue(payload["opened"])

    def test_selftest_skips_without_tkinter(self) -> None:
        payload = self._payload()
        if _tkinter_available() and not payload.get("skipped"):
            self.skipTest("tkinter is available; skip path not applicable")
        self.assertFalse(payload["opened"])
        self.assertIn("skipped", payload)


class GuiWindowTests(unittest.TestCase):
    def test_window_structure(self) -> None:
        if not _tkinter_available():
            self.skipTest("tkinter is not available in this interpreter")
        import tkinter as tk

        try:
            root = tk.Tk()
        except Exception as exc:  # headless CI without a display
            self.skipTest(f"no display: {exc}")
        root.withdraw()
        app = None
        try:
            app = gui._create_app(root)
            root.update_idletasks()
            labels = gui.collect_button_labels(root)
            for label in gui.BUTTONS:
                self.assertIn(label, labels)
            tab_texts = tuple(
                app.notebook.tab(index, "text")
                for index in range(app.notebook.index("end"))
            )
            self.assertEqual(tab_texts, gui.TABS)
            self.assertIsNotNone(app.scene_var)
        finally:
            if app is not None:
                app.close()
            else:
                root.destroy()


class PackagingTests(unittest.TestCase):
    def test_no_pyside6(self) -> None:
        source = (ROOT / "goo_bridge" / "gui.py").read_text(encoding="utf-8")
        self.assertNotIn("PySide6", source)
        self.assertNotIn("PyQt", source)


if __name__ == "__main__":
    unittest.main()
