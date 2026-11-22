"""Smoke tests for the external CLI.

Run with any Python >= 3.10 (the CLI itself has no bpy dependency):

    python tests/test_cli.py

The doctor/nodes tests need Blender 5.2.  Set GOO_BLENDER to override the
default ``C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe``.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BLENDER = Path(
    os.environ.get(
        "GOO_BLENDER",
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    )
)


def run_goo(*args: str, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not old else str(ROOT) + os.pathsep + old
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "goo", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def load_json(proc: subprocess.CompletedProcess) -> dict:
    if proc.returncode != 0:
        raise AssertionError(
            f"goo exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


class CliSmokeTest(unittest.TestCase):
    @unittest.skipIf(
        os.environ.get("GOO_SKIP_GUI") == "1",
        "GUI selftest disabled by GOO_SKIP_GUI / 已通过 GOO_SKIP_GUI 禁用 GUI 自检",
    )
    @unittest.skipUnless(
        sys.platform in {"win32", "darwin"} or os.environ.get("DISPLAY"),
        "Tk needs a desktop session / 需要桌面会话",
    )
    def test_gui_selftest(self) -> None:
        """The Tk GUI builds its widgets and closes under GOO_GUI_SELFTEST."""
        with tempfile.TemporaryDirectory() as td:
            payload = load_json(
                run_goo(
                    "gui",
                    "--json",
                    cwd=Path(td),
                    env_extra={"GOO_GUI_SELFTEST": "1"},
                )
            )
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["opened"])
            self.assertIn("Verify", payload["buttons"])
            self.assertIn("View layer / 视图层", payload["fields"])

    def test_version_from_foreign_cwd(self) -> None:
        """The checkout is importable through PYTHONPATH alone."""
        with tempfile.TemporaryDirectory() as td:
            payload = load_json(run_goo("version", "--json", cwd=Path(td)))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["cli"]["version"], "1.0.0")
            self.assertEqual(payload["addon"]["version"], "1.0.0")
            self.assertIn("python", payload["cli"])

    def test_verify_release(self) -> None:
        """dist/SHA256SUMS matches the shipped files and archives are complete."""
        dist = ROOT / "dist"
        if not (dist / "SHA256SUMS").is_file():
            self.skipTest("release artifacts are not present in this tree")
        payload = load_json(run_goo("verify", "--json", cwd=ROOT))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["files"], 3)
        names = {check["name"] for check in payload["checks"]}
        self.assertEqual(
            names,
            {
                "goo_npr-1.0.0.zip",
                "goo_suite-1.0.0-py3-none-any.whl",
                "goo-suite-1.0.0-src.zip",
            },
        )
        self.assertTrue(all(check["ok"] for check in payload["checks"]))
        self.assertFalse(payload["errors"])
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertIn("goo/verify.py", by_name["goo_suite-1.0.0-py3-none-any.whl"]["required"])
        self.assertIn("goo_npr/nodes/camera.py", by_name["goo_suite-1.0.0-py3-none-any.whl"]["required"])
        self.assertIn("goo_bridge/cli.py", by_name["goo_suite-1.0.0-py3-none-any.whl"]["required"])
        self.assertIn("goo/verify.py", by_name["goo-suite-1.0.0-src.zip"]["required"])
        self.assertIn("goo_npr/nodes/camera.py", by_name["goo-suite-1.0.0-src.zip"]["required"])

        with tempfile.TemporaryDirectory() as td:
            proc = run_goo("verify", "--dist", td, "--json", cwd=Path(td))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            failed = json.loads(proc.stdout)
            self.assertFalse(failed["ok"])
            self.assertTrue(failed["errors"])

        with tempfile.TemporaryDirectory() as td:
            tampered = Path(td)
            sums = (dist / "SHA256SUMS").read_text(encoding="utf-8")
            for artifact in ("goo_npr-1.0.0.zip", "goo_suite-1.0.0-py3-none-any.whl", "goo-suite-1.0.0-src.zip"):
                shutil.copy2(dist / artifact, tampered / artifact)
            expected = payload["checks"][0]["sha256"]
            (tampered / "SHA256SUMS").write_text(sums.replace(expected, "0" * 64, 1), encoding="utf-8")
            proc = run_goo("verify", "--dist", td, "--json", cwd=Path(td))
            self.assertEqual(proc.returncode, 2, proc.stderr)
            failed = json.loads(proc.stdout)
            self.assertFalse(failed["ok"])
            self.assertTrue(any("hash mismatch" in error for error in failed["errors"]), failed["errors"])

    def test_bundle_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "goo_npr.zip"
            proc = run_goo("bundle", "-o", str(out), cwd=Path(td))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            self.assertEqual(len(names), 27)
            self.assertIn("goo_npr/nodes/camera.py", names)
            self.assertIn("goo_npr/tools/pose_tools.py", names)
            self.assertFalse(any("__pycache__" in n for n in names))

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_doctor_reports_blender(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = load_json(
                run_goo("doctor", "--json", "--blender", str(BLENDER), cwd=Path(td))
            )
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["blender"]["version"].split(".")[0], "5")
            self.assertTrue(payload["addon"]["exists"])

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_nodes_registry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            payload = load_json(
                run_goo(
                    "nodes",
                    str(ROOT / "tests" / "assets" / "demo_lightgroups.blend"),
                    "--json",
                    "--blender",
                    str(BLENDER),
                    cwd=Path(td),
                )
            )
            self.assertEqual(payload["spec_count"], 22)
            names = {spec["name"] for spec in payload["specs"]}
            self.assertIn("GOO Active Camera", names)
            self.assertIn("GOO Light Info New", names)

    def test_bundle_reproducible(self) -> None:
        """Two bundle builds are byte-identical and contain exactly 27 files."""
        with tempfile.TemporaryDirectory() as td:
            first, second = Path(td) / "one.zip", Path(td) / "two.zip"
            one = load_json(run_goo("bundle", "--out", str(first), "--json", cwd=Path(td)))
            two = load_json(run_goo("bundle", "--out", str(second), "--json", cwd=Path(td)))
            self.assertEqual(one["files"], 27)
            self.assertEqual(one["sha256"], two["sha256"])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
            self.assertEqual(len(names), 27)
            self.assertIn("goo_npr/nodes/camera.py", names)
            self.assertIn("goo_npr/tools/pose_tools.py", names)

    def test_source_bundle(self) -> None:
        """``bundle --source`` is reproducible and includes docs and tests."""
        with tempfile.TemporaryDirectory() as td:
            first, second = Path(td) / "src1.zip", Path(td) / "src2.zip"
            one = load_json(run_goo("bundle", "--source", "-o", str(first), "--json", cwd=Path(td)))
            two = load_json(run_goo("bundle", "--source", "-o", str(second), "--json", cwd=Path(td)))
            self.assertEqual(one["tool"], "source-bundle")
            self.assertGreaterEqual(one["files"], 90)
            self.assertIn("goo_bridge/cli.py", one["entries"])
            self.assertEqual(one["sha256"], two["sha256"])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            self.assertIn("README.md", names)
            self.assertIn("docs/feature-matrix.json", names)
            self.assertIn("goo_npr/tools/pose_tools.py", names)
            self.assertIn("tests/assets/demo_lightgroups.blend", names)
            self.assertIn("tests/smoke_zip.py", names)
            self.assertIn("tests/test_paths.py", names)
            self.assertFalse(any(name.startswith("_work/") or "/_work/" in name for name in names))

            conflicted = run_goo(
                "bundle",
                "--source",
                "--addon",
                str(ROOT),
                "-o",
                str(Path(td) / "bad.zip"),
                "--json",
                cwd=Path(td),
            )
            self.assertEqual(conflicted.returncode, 2)

    def test_i18n_keys(self) -> None:
        """Every literal tr()/t() key has an entry in a module string table."""
        import ast
        import re

        addon = ROOT / "goo_npr"
        defined: set[str] = set()
        for path in addon.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key in node.keys:
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value.startswith("goo.")
                    ):
                        defined.add(key.value)

        key_re = re.compile(r"\b(?:tr|i18n\.t|t)\(\s*[\"']([^\"']+)[\"']")
        used: dict[str, str] = {}
        for path in addon.rglob("*.py"):
            for match in key_re.finditer(path.read_text(encoding="utf-8")):
                used.setdefault(match.group(1), str(path.relative_to(addon)))
        missing = {key: origin for key, origin in used.items() if key not in defined}
        self.assertEqual(missing, {}, "\n".join(f"{k} ({v})" for k, v in missing.items()))
        self.assertGreater(len(used), 100)
        self.assertGreater(len(defined), len(used))

    def test_docs_links(self) -> None:
        """Every relative Markdown link in README/docs resolves to a real file."""
        import re
        import urllib.parse

        link_re = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
        files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
        failures: list[str] = []
        checked = 0
        for md in files:
            text = md.read_text(encoding="utf-8")
            for raw in link_re.findall(text):
                target = raw.strip()
                if target.startswith("<") and ">" in target:
                    target = target[1 : target.index(">")]
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                path_part = urllib.parse.unquote(target.split("#", 1)[0].strip())
                if not path_part:
                    continue
                checked += 1
                resolved = (md.parent / path_part).resolve()
                if not resolved.exists():
                    failures.append(f"{md.relative_to(ROOT)} -> {target}")
        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreater(checked, 30)

    def test_feature_matrix_counts(self) -> None:
        """The 49-row inventory and its status counts stay in sync."""
        text = (ROOT / "docs" / "features.md").read_text(encoding="utf-8")
        inventory = text[text.index("## 1.") :].split("## Summary", 1)[0]
        statuses = []
        expected_sections = []
        current_section = None
        for line in inventory.splitlines():
            row = line.strip()
            if row.startswith("## "):
                match = re.match(r"^\d+\.\s+(.+?)\s+/\s+", row[3:].strip())
                self.assertIsNotNone(match, row)
                current_section = match.group(1).strip()
                continue
            if not (row.startswith("|") and row.endswith("|")):
                continue
            if row.startswith("| ---") or row.startswith("| GOO Engine 4.4 feature"):
                continue
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            self.assertEqual(len(cells), 3, row)
            statuses.append(cells[-1])
            expected_sections.append(current_section)
        self.assertEqual(len(statuses), 49)
        self.assertEqual(len(expected_sections), 49)
        self.assertTrue(all(expected_sections))
        self.assertEqual(
            Counter(status.split()[0] for status in statuses),
            {"Ported": 9, "Native": 1, "Approx": 25, "N/A": 14},
        )
        self.assertNotIn("Not shipped", " ".join(statuses))

        matrix = json.loads(
            (ROOT / "docs" / "feature-matrix.json").read_text(encoding="utf-8")
        )
        self.assertEqual(matrix["total"], 49)
        self.assertEqual(
            matrix["counts"],
            {"Ported": 9, "Native": 1, "Approx": 25, "N/A": 14, "Not shipped": 0},
        )
        self.assertEqual(len(matrix["rows"]), 49)
        self.assertEqual([row["status"] for row in matrix["rows"]], statuses)
        self.assertEqual([row["section"] for row in matrix["rows"]], expected_sections)
        for row in matrix["rows"]:
            self.assertTrue(row["feature"].strip())
            self.assertTrue(row["implementation"].strip())
            self.assertEqual(row["status_class"], row["status"].split()[0], row)
        self.assertEqual(
            Counter(row["status_class"] for row in matrix["rows"]),
            {"Ported": 9, "Native": 1, "Approx": 25, "N/A": 14},
        )

    def test_goo44_manifest_counts(self) -> None:
        """The delivered GOO 4.4 diff manifest keeps its 178/159 split."""
        text = (ROOT / "docs" / "goo44-source-manifest.txt").read_text(encoding="utf-8")
        only, changed = text.split("## Source-changed files", 1)
        only_paths = [line for line in only.splitlines() if line.startswith("source/")]
        changed_paths = [line for line in changed.splitlines() if line.startswith("source/")]
        self.assertEqual(len(only_paths), 178)
        self.assertEqual(len(changed_paths), 159)
        self.assertIn("source/blender/draw/engines/gooengine/eevee_lights.cc", only_paths)
        self.assertIn("source/blender/nodes/shader/nodes/node_shader_shader_info.cc", only_paths)

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_apply_render_composite(self) -> None:
        """The primary workflow: build the library, split light groups, composite."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            synced = out / "synced.blend"
            applied = load_json(
                run_goo(
                    "apply",
                    str(ROOT / "tests" / "assets" / "demo_lightgroups.blend"),
                    "-o",
                    str(synced),
                    "--json",
                    "--blender",
                    str(BLENDER),
                    cwd=out,
                )
            )
            self.assertTrue(applied["ok"])
            self.assertTrue(synced.is_file())
            self.assertEqual(applied["light_groups"]["groups"], 2)

            passes_dir = out / "groups"
            rendered = load_json(
                run_goo(
                    "render",
                    str(synced),
                    "-o",
                    str(passes_dir),
                    "--light-groups",
                    "--camera",
                    "DemoCam",
                    "--view-layer",
                    "ViewLayer",
                    "--samples",
                    "2",
                    "--resolution-percentage",
                    "25",
                    "--format",
                    "png",
                    "--json",
                    "--blender",
                    str(BLENDER),
                    cwd=out,
                )
            )
            self.assertTrue(rendered["ok"])
            self.assertEqual(rendered["settings"]["camera"], "DemoCam")
            self.assertEqual(rendered["settings"]["view_layer"], "ViewLayer")
            groups = {item["group"] for item in rendered["passes"]}
            self.assertEqual(groups, {"key", "fill", "ambient"})
            for item in rendered["passes"]:
                self.assertEqual(item["stats"]["width"], 64)
                self.assertEqual(item["stats"]["height"], 64)
                self.assertTrue(Path(item["path"]).is_file())

            composited = load_json(
                run_goo("composite", str(passes_dir), "--png", "--json", cwd=out)
            )
            self.assertTrue(composited["ok"])
            self.assertTrue((passes_dir / "composite.png").is_file())
            self.assertTrue((passes_dir / "composite.exr").is_file())

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_render_view_layer(self) -> None:
        """--view-layer selects a named layer and rejects an unknown one."""
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            two_layers = work / "two_layers.blend"
            expr = (
                "import bpy\n"
                "scene = bpy.context.scene\n"
                "scene.view_layers.new('Alt')\n"
                f"bpy.ops.wm.save_as_mainfile(filepath={str(two_layers)!r})\n"
            )
            created = subprocess.run(
                [
                    str(BLENDER),
                    "--background",
                    "--factory-startup",
                    str(ROOT / "tests" / "assets" / "demo_lightgroups.blend"),
                    "--python-expr",
                    expr,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            self.assertEqual(created.returncode, 0, created.stderr[-2000:])
            self.assertTrue(two_layers.is_file())

            out = work / "render"
            rendered = load_json(
                run_goo(
                    "render",
                    str(two_layers),
                    "-o",
                    str(out),
                    "--view-layer",
                    "Alt",
                    "--samples",
                    "1",
                    "--resolution-percentage",
                    "25",
                    "--format",
                    "png",
                    "--json",
                    cwd=work,
                )
            )
            self.assertTrue(rendered["ok"])
            self.assertEqual(rendered["settings"]["view_layer"], "Alt")
            self.assertTrue((out / "beauty.png").is_file())

            failed = run_goo(
                "render",
                str(two_layers),
                "-o",
                str(work / "missing"),
                "--view-layer",
                "Missing",
                "--samples",
                "1",
                "--resolution-percentage",
                "25",
                "--format",
                "png",
                cwd=work,
            )
            self.assertEqual(failed.returncode, 4, failed.stdout + failed.stderr)
            self.assertIn("view layer 'Missing' not found", failed.stderr)

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_burn_in_option(self) -> None:
        """PNG burn-in works; EXR and light-group combinations are rejected."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "still"
            payload = load_json(
                run_goo(
                    "render",
                    str(ROOT / "tests" / "assets" / "demo_lightgroups.blend"),
                    "-o",
                    str(out),
                    "--format",
                    "png",
                    "--burn-in",
                    "--samples",
                    "2",
                    "--resolution-percentage",
                    "25",
                    "--json",
                    "--blender",
                    str(BLENDER),
                    cwd=Path(td),
                )
            )
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["settings"]["burn_in"])
            self.assertTrue((out / "beauty.png").is_file())

            for extra in (
                ("--format", "exr", "--burn-in"),
                ("--format", "png", "--burn-in", "--light-groups"),
            ):
                proc = run_goo(
                    "render",
                    str(ROOT / "tests" / "assets" / "demo_lightgroups.blend"),
                    "-o",
                    str(Path(td) / "bad"),
                    *extra,
                    "--json",
                    "--blender",
                    str(BLENDER),
                    cwd=Path(td),
                )
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("--burn-in", proc.stdout + proc.stderr)

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_bundle_zip_installs(self) -> None:
        """The published zip installs through Blender's native add-on installer."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            archive = out / "goo_npr.zip"
            payload = load_json(
                run_goo("bundle", "-o", str(archive), "--json", cwd=out)
            )
            self.assertEqual(payload["files"], 27)

            scripts = out / "scripts"
            env = dict(os.environ)
            env["BLENDER_USER_SCRIPTS"] = str(scripts)
            env["BLENDER_USER_CONFIG"] = str(out / "config")
            env["GOO_ZIP_PATH"] = str(archive)
            env["GOO_SMOKE_OUT"] = str(out / "zip_smoke.png")
            proc = subprocess.run(
                [
                    str(BLENDER),
                    "--background",
                    "--factory-startup",
                    "--python",
                    str(ROOT / "tests" / "smoke_zip.py"),
                ],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output)
            self.assertIn("ZIP_SMOKE_OK", output)
            self.assertIn('"groups": 22', output)
            self.assertIn('"has_active_camera": true', output)
            self.assertTrue((out / "zip_smoke.png").is_file())

    @unittest.skipUnless(BLENDER.is_file(), f"Blender not found: {BLENDER}")
    def test_installed_package_smoke(self) -> None:
        """Install into an empty user-scripts dir and use the installed copy."""
        with tempfile.TemporaryDirectory() as td:
            scripts = Path(td) / "scripts"
            addons = scripts / "addons"
            proc = run_goo(
                "install",
                "--target",
                str(addons),
                "--no-enable",
                "--blender",
                str(BLENDER),
                cwd=Path(td),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((addons / "goo_npr" / "__init__.py").is_file())

            env = dict(os.environ)
            env["BLENDER_USER_SCRIPTS"] = str(scripts)
            env["GOO_INSTALL_ROOT"] = str(addons / "goo_npr")
            env["GOO_SMOKE_OUT"] = str(Path(td) / "smoke.png")
            proc = subprocess.run(
                [
                    str(BLENDER),
                    "--background",
                    "--factory-startup",
                    "--python",
                    str(ROOT / "tests" / "smoke_installed.py"),
                ],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output)
            self.assertIn("SMOKE_OK", output)
            self.assertIn("LIFECYCLE_OK", output)
            self.assertIn('"groups": 22', output)
            self.assertTrue((Path(td) / "smoke.png").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
