# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable path and Blender-discovery tests.

These tests are standard-library only and never start Blender.  They run on
any host so the Windows/macOS/Linux branches of config and discovery can be
checked without those operating systems.  Run with::

    python tests/test_paths.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from goo import blender as blender_module
from goo import config as config_module
from goo_bridge import config as suite_config
from goo.errors import BlenderNotFound


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


class UserConfigPathTest(unittest.TestCase):
    def test_windows_appdata(self) -> None:
        with mock.patch.object(suite_config, "Path", PureWindowsPath), mock.patch.object(
            suite_config.sys, "platform", "win32"
        ), mock.patch.dict(
            os.environ,
            {"APPDATA": r"C:\Users\Case\AppData\Roaming"},
            clear=True,
        ):
            path = suite_config.config_path()
        self.assertEqual(
            _posix(path),
            "C:/Users/Case/AppData/Roaming/GOO Suite/config.json",
        )

    def test_windows_localappdata_fallback(self) -> None:
        with mock.patch.object(suite_config, "Path", PureWindowsPath), mock.patch.object(
            suite_config.sys, "platform", "win32"
        ), mock.patch.dict(
            os.environ,
            {"APPDATA": "", "LOCALAPPDATA": r"C:\Users\Case\AppData\Local"},
            clear=True,
        ):
            path = suite_config.config_path()
        self.assertEqual(
            _posix(path),
            "C:/Users/Case/AppData/Local/GOO Suite/config.json",
        )

    def test_macos_application_support(self) -> None:
        with mock.patch.object(suite_config, "Path", PurePosixPath), mock.patch.object(
            suite_config.sys, "platform", "darwin"
        ), mock.patch.object(
            suite_config, "_home_path", return_value=PurePosixPath("/Users/Case")
        ):
            path = suite_config.config_path()
        self.assertEqual(
            _posix(path),
            "/Users/Case/Library/Application Support/GOO Suite/config.json",
        )

    def test_linux_xdg_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp).as_posix()
            with mock.patch.object(suite_config, "Path", PurePosixPath), mock.patch.object(
                suite_config.sys, "platform", "linux"
            ), mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": xdg, "HOME": "/home/case"}, clear=True
            ):
                path = suite_config.config_path()
            expected = PurePosixPath(xdg) / "goo-suite" / "config.json"
        self.assertEqual(path, expected)

    def test_linux_home_fallback(self) -> None:
        with mock.patch.object(suite_config, "Path", PurePosixPath), mock.patch.object(
            suite_config.sys, "platform", "linux"
        ), mock.patch.object(
            suite_config, "_home_path", return_value=PurePosixPath("/home/case")
        ), mock.patch.dict(os.environ, {}, clear=True):
            path = suite_config.config_path()
        self.assertEqual(_posix(path), "/home/case/.config/goo-suite/config.json")

    def test_cli_config_delegates_to_suite_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"GOO_SUITE_HOME": tmp}, clear=True):
                self.assertEqual(config_module.user_config_path(), suite_config.config_path())


class CandidatePathTest(unittest.TestCase):
    def test_explicit_path_first_and_deduplicated(self) -> None:
        duplicate = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
        with mock.patch.object(blender_module.os, "name", "nt"), mock.patch.object(
            blender_module,
            "_windows_candidates",
            return_value=[("common install path", duplicate)],
        ), mock.patch.object(
            blender_module.config_module, "load_config", return_value={}
        ), mock.patch.object(
            blender_module.shutil, "which", return_value=None
        ), mock.patch.dict(os.environ, {}, clear=True):
            pairs = blender_module.candidate_paths(duplicate)
        self.assertEqual(pairs, [("--blender argument", duplicate)])

    def test_environment_and_config_precede_install_paths(self) -> None:
        with mock.patch.object(blender_module.os, "name", "nt"), mock.patch.object(
            blender_module,
            "_windows_candidates",
            return_value=[("common install path", r"C:\PF\Blender\blender.exe")],
        ), mock.patch.object(
            blender_module.config_module,
            "load_config",
            return_value={"blender": r"C:\Config\blender.exe"},
        ), mock.patch.object(
            blender_module.shutil, "which", return_value=None
        ), mock.patch.dict(
            os.environ,
            {"GOO_BLENDER": r"C:\Env\blender.exe", "GOO_BLENDER_52": ""},
            clear=True,
        ):
            pairs = blender_module.candidate_paths()
        self.assertEqual(
            [source for source, _ in pairs],
            ["$GOO_BLENDER", "config file", "common install path"],
        )


class CandidateDiscoveryTest(unittest.TestCase):
    def test_macos_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "Applications"
            fixed = apps / "Blender.app" / "Contents" / "MacOS" / "Blender"
            fixed.parent.mkdir(parents=True)
            fixed.write_text("", encoding="utf-8")
            versioned = apps / "Blender 5.2" / "Blender.app" / "Contents" / "MacOS" / "Blender"
            versioned.parent.mkdir(parents=True)
            versioned.write_text("", encoding="utf-8")
            found = blender_module._mac_candidates(apps)
        self.assertEqual(found[0], ("applications", str(fixed)))
        self.assertIn(("applications", str(versioned)), found)

    def test_linux_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system = Path(tmp) / "bin" / "blender"
            system.parent.mkdir(parents=True)
            system.write_text("", encoding="utf-8")
            opt = Path(tmp) / "opt"
            opt_blender = opt / "blender-5.2" / "blender"
            opt_blender.parent.mkdir(parents=True)
            opt_blender.write_text("", encoding="utf-8")
            found = blender_module._linux_candidates([str(system)], opt)
        self.assertIn(("system path", str(system)), found)
        self.assertIn(("opt", str(opt_blender)), found)

    def test_windows_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            program_files = Path(tmp) / "Program Files"
            local = Path(tmp) / "Local"
            executable = program_files / "Blender Foundation" / "Blender 5.2" / "blender.exe"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "PROGRAMFILES": str(program_files),
                    "PROGRAMW6432": "",
                    "PROGRAMFILES(X86)": "",
                    "LOCALAPPDATA": str(local),
                },
                clear=True,
            ):
                found = blender_module._windows_candidates()
        self.assertIn(("common install path", str(executable)), found)


class DiscoveryTest(unittest.TestCase):
    def test_unvalidated_explicit_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / ("blender.exe" if os.name == "nt" else "blender")
            executable.write_text("", encoding="utf-8")
            with mock.patch.object(
                blender_module,
                "candidate_paths",
                return_value=[("--blender argument", str(executable))],
            ):
                install = blender_module.discover(str(executable), validate=False)
        self.assertEqual(install.version, (0, 0, 0))
        self.assertEqual(install.version_string, "unverified")
        self.assertEqual(install.source, "--blender argument")

    def test_missing_explicit_path_is_a_hard_error(self) -> None:
        missing = str(Path(tempfile.gettempdir()) / "does-not-exist" / "blender.exe")
        with mock.patch.object(
            blender_module,
            "candidate_paths",
            return_value=[("--blender argument", missing)],
        ):
            with self.assertRaises(BlenderNotFound) as caught:
                blender_module.discover(missing)
        self.assertIn("does not exist", str(caught.exception))

    def test_version_support_matrix(self) -> None:
        self.assertTrue(blender_module._version_is_supported((5, 2, 0)))
        self.assertTrue(blender_module._version_is_supported((5, 2)))
        self.assertTrue(blender_module._version_is_supported((6, 0, 1)))
        self.assertFalse(blender_module._version_is_supported((5, 1, 9)))
        self.assertFalse(blender_module._version_is_supported(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)

