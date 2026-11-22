# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the remote render path: transports, bundles, worker and CLI.

No network and no SSH server are used:

* ``SshTransport`` is driven by a fake runner that records the exact command
  lines (``ssh -p ...``/``scp -P ...``) and returns canned results.
* ``LocalTransport`` executes the complete bundle -> render -> download
  pipeline in a temporary directory; the real Blender is used when it can be
  found, otherwise those tests are skipped.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from goo_bridge import remote as remote_cli  # noqa: E402
from goo_bridge import worker  # noqa: E402
from goo_bridge.assets import Asset, sha256_file  # noqa: E402
from goo_bridge.blender import BlenderError, find_blender  # noqa: E402
from goo_bridge.jobs import RenderJob  # noqa: E402
from goo_bridge.transport import (  # noqa: E402
    LocalTransport,
    ProcResult,
    SshTransport,
    TransportError,
    default_runner,
)
from goo_bridge.worker import (  # noqa: E402
    BUNDLE_ASSETS,
    BUNDLE_JOB,
    BUNDLE_SCENE,
    BUNDLE_SCRIPT,
    RESULT_MARKER,
    build_bundle,
    clean_jobs,
    fetch_results,
    job_status,
    parse_worker_result,
    render_remote,
    test_connection,
    upload_bundle,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def blender_path() -> str | None:
    """Return a usable Blender executable, or ``None`` when unavailable."""
    explicit = os.environ.get("GOO_BRIDGE_BLENDER")
    try:
        return str(find_blender(explicit).path)
    except BlenderError:
        return None


class FakeRunner:
    """Records transport commands and returns canned results."""

    def __init__(self, handler=None, default: ProcResult | None = None) -> None:
        self.calls: list[dict] = []
        self.handler = handler
        self.default = default or ProcResult(argv=[], returncode=0)

    def __call__(self, command, *, timeout=3600, stdin=None, cwd=None):
        argv = [str(part) for part in command]
        self.calls.append(
            {"command": argv, "timeout": timeout, "stdin": stdin, "cwd": cwd}
        )
        if self.handler is not None:
            return self.handler(argv)
        return self.default

    def last(self) -> list[str]:
        return self.calls[-1]["command"]


def fixture_profile(**overrides):
    from goo_bridge.config import RemoteProfile

    data = {
        "name": "studio",
        "host": "render.example.com",
        "user": "artist",
        "port": 2222,
        "key": "",
        "blender": "blender",
        "workspace": "/srv/goo",
    }
    data.update(overrides)
    return RemoteProfile.from_dict(data)


class SshTransportTests(unittest.TestCase):
    def make_transport(self, runner: FakeRunner, **overrides) -> SshTransport:
        return SshTransport(fixture_profile(**overrides), runner=runner)

    def test_run_builds_ssh_command_line(self) -> None:
        runner = FakeRunner()
        transport = self.make_transport(runner)
        argv = ["python3", "-c", "print('hello world')"]
        result = transport.run(argv, timeout=42)
        self.assertTrue(result.ok)
        command = runner.last()
        self.assertEqual(command[:2], ["ssh", "-p"])
        self.assertIn("2222", command)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=accept-new", command)
        self.assertIn("artist@render.example.com", command)
        self.assertEqual(command[-1], " ".join(shlex.quote(part) for part in argv))
        self.assertEqual(runner.calls[-1]["timeout"], 42)

    def test_workspace_tilde_is_resolved_once(self) -> None:
        def handler(argv):
            if argv[-1] == "printenv HOME":
                return ProcResult(argv=argv, stdout="/home/artist\n")
            return ProcResult(argv=argv)

        runner = FakeRunner(handler)
        transport = self.make_transport(runner, workspace="~/renders")
        self.assertEqual(transport.path("jobs/a"), "/home/artist/renders/jobs/a")
        self.assertEqual(transport.path("jobs/b"), "/home/artist/renders/jobs/b")
        home_probes = [item for item in runner.calls if item["command"][-1] == "printenv HOME"]
        self.assertEqual(len(home_probes), 1)

    def test_workspace_probe_failure_is_reported(self) -> None:
        runner = FakeRunner(
            default=ProcResult(argv=[], returncode=255, stderr="Permission denied")
        )
        transport = self.make_transport(runner, workspace="~/renders")
        with self.assertRaises(TransportError) as caught:
            _ = transport.workspace
        self.assertIn("HOME", str(caught.exception))

    def test_parent_escape_is_rejected(self) -> None:
        transport = self.make_transport(FakeRunner())
        with self.assertRaises(TransportError):
            transport.path("../etc/passwd")

    def test_upload_creates_parent_and_uses_scp(self) -> None:
        runner = FakeRunner()
        transport = self.make_transport(runner)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "scene.blend"
            source.write_bytes(b"BLENDER")
            remote = transport.upload(source, "jobs/abc/scene.blend")
        self.assertEqual(remote, "/srv/goo/jobs/abc/scene.blend")
        mkdir_calls = [c for c in runner.calls if "mkdir -p /srv/goo/jobs/abc" in c["command"][-1]]
        self.assertTrue(mkdir_calls)
        scp = runner.last()
        self.assertEqual(scp[0], "scp")
        self.assertIn("-P", scp)
        self.assertIn("2222", scp)
        self.assertEqual(scp[-1], "artist@render.example.com:/srv/goo/jobs/abc/scene.blend")

    def test_download_uses_scp(self) -> None:
        def handler(argv):
            if argv[0] == "scp":
                destination = Path(argv[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"DATA")
            return ProcResult(argv=argv)

        runner = FakeRunner(handler)
        transport = self.make_transport(runner)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nested" / "result.json"
            transport.download("jobs/abc/result.json", target)
            self.assertEqual(target.read_bytes(), b"DATA")
        # scp puts the remote path second to last and the local target last.
        self.assertEqual(
            runner.last()[-2], "artist@render.example.com:/srv/goo/jobs/abc/result.json"
        )

    def test_listdir_missing_directory_is_empty(self) -> None:
        runner = FakeRunner(
            default=ProcResult(
                argv=[], returncode=2, stderr="ls: cannot access: No such file or directory"
            )
        )
        transport = self.make_transport(runner)
        self.assertEqual(transport.listdir("jobs/nope"), [])

    def test_mkdir_failure_raises_with_detail(self) -> None:
        def handler(argv):
            if "mkdir" in argv[-1]:
                return ProcResult(argv=argv, returncode=1, stderr="Permission denied")
            return ProcResult(argv=argv)

        transport = self.make_transport(FakeRunner(handler))
        with self.assertRaises(TransportError) as caught:
            transport.mkdir("jobs/blocked")
        text = str(caught.exception)
        self.assertIn("mkdir", text)
        self.assertIn("Permission denied", text)

    def test_remote_command_shape(self) -> None:
        transport = self.make_transport(FakeRunner())
        command = worker.remote_command(
            transport, {"base": "jobs/abc"}, "/opt/blender/blender"
        )
        self.assertEqual(
            command,
            [
                "/opt/blender/blender",
                "--background",
                "/srv/goo/jobs/abc/scene.blend",
                "--python",
                "/srv/goo/jobs/abc/remote_worker.py",
                "--",
                "--job",
                "/srv/goo/jobs/abc/job.json",
            ],
        )


class LocalTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "workspace"
        self.transport = LocalTransport(self.root)

    def test_file_round_trip(self) -> None:
        source = Path(self._temp.name) / "payload.bin"
        source.write_bytes(b"payload-123")
        remote = self.transport.upload(source, "jobs/a/payload.bin")
        self.assertTrue(Path(remote).is_file())
        self.assertTrue(self.transport.exists("jobs/a/payload.bin"))
        self.assertIn("payload.bin", self.transport.listdir("jobs/a"))
        self.assertEqual(self.transport.read_text("jobs/a/payload.bin"), "payload-123")

        target = Path(self._temp.name) / "downloaded.bin"
        self.transport.download("jobs/a/payload.bin", target)
        self.assertEqual(target.read_bytes(), b"payload-123")

        self.assertTrue(self.transport.remove_tree("jobs/a"))
        self.assertFalse(self.transport.exists("jobs/a"))
        self.assertEqual(self.transport.listdir("jobs/a"), [])

    def test_run_uses_real_subprocess(self) -> None:
        result = self.transport.run([sys.executable, "-c", "print('hello')"], timeout=60)
        self.assertTrue(result.ok)
        self.assertIn("hello", result.stdout)

    def test_missing_download_raises(self) -> None:
        with self.assertRaises(TransportError):
            self.transport.download("jobs/none/result.json", Path(self._temp.name) / "x")

    def test_default_runner_missing_executable(self) -> None:
        with self.assertRaises(TransportError) as caught:
            default_runner(["goo-bridge-definitely-not-a-binary"])
        self.assertIn("not found", str(caught.exception))


class BundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name)
        self.scene = self.temp / "source.blend"
        self.scene.write_bytes(b"BLENDER-SCENE")
        self.asset = self.temp / "wood.png"
        self.asset.write_bytes(b"PNGDATA")

    def test_build_bundle_layout_and_rewrites(self) -> None:
        job = RenderJob(
            scene=str(self.scene),
            output=str(self.temp / "out"),
            samples=8,
            resolution=50,
            frame="1-2",
            format="PNG",
        )
        bundle = build_bundle(
            self.scene,
            job,
            self.temp / "bundle",
            assets=[Asset(path=str(self.asset), kind="texture", name="wood.png")],
        )
        self.assertTrue(bundle["ok"])
        names = {item["name"] for item in bundle["files"]}
        required = {BUNDLE_SCENE, BUNDLE_SCRIPT, BUNDLE_ASSETS, BUNDLE_JOB}
        self.assertTrue(required.issubset(names), names)
        # Assets are part of the bundle manifest and are uploaded with it.
        self.assertIn("assets/wood.png", names)
        bundle_dir = Path(bundle["bundle_dir"])
        for name in names:
            self.assertTrue((bundle_dir / name).is_file(), name)

        stored = json.loads((bundle_dir / BUNDLE_JOB).read_text(encoding="utf-8"))
        self.assertEqual(stored["scene"], BUNDLE_SCENE)
        self.assertEqual(stored["output"], "output")
        self.assertEqual(stored["metadata"]["bundle_version"], 1)
        self.assertEqual(stored["metadata"]["original_scene"], str(self.scene.resolve()))

        assets = json.loads((bundle_dir / BUNDLE_ASSETS).read_text(encoding="utf-8"))
        self.assertEqual(len(assets["bundled"]), 1)
        entry = assets["bundled"][0]
        self.assertEqual(entry["bundle_name"], "assets/wood.png")
        self.assertEqual(entry["sha256"], sha256_file(self.asset))
        self.assertTrue((bundle_dir / "assets" / "wood.png").is_file())
        self.assertEqual(bundle["total_bytes"], sum(item["size"] for item in bundle["files"]))

    def test_missing_asset_blocks_bundle(self) -> None:
        job = RenderJob(scene=str(self.scene), output=str(self.temp / "out"))
        missing = self.temp / "gone.png"
        with self.assertRaises(worker.WorkerError) as caught:
            build_bundle(
                self.scene,
                job,
                self.temp / "bundle2",
                assets=[Asset(path=str(missing), kind="texture", name="gone.png")],
            )
        text = str(caught.exception)
        self.assertIn("missing asset", text)
        self.assertIn("gone.png", text)

    def test_render_remote_stops_on_missing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            transport = LocalTransport(Path(temp) / "ws")
            payload = render_remote(
                self.scene,
                Path(temp) / "out",
                transport=transport,
                assets=[
                    Asset(
                        path=str(self.temp / "gone.png"),
                        kind="texture",
                        name="gone.png",
                    )
                ],
                blender="definitely-missing-blender",
                timeout=60,
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "bundle")
        self.assertIn("missing asset", payload["error"])

    def test_fetch_detects_sha256_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            transport = LocalTransport(Path(temp) / "ws")
            base = "jobs/job-bad"
            transport.mkdir(base)
            target = Path(transport.path(base)) / "output" / "render_0001.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(PNG_MAGIC + b"payload")
            result = {
                "ok": True,
                "job_id": "job-bad",
                "files": [
                    {
                        "path": "output/render_0001.png",
                        "size": target.stat().st_size,
                        "sha256": "0" * 64,
                    }
                ],
                "duration": 1.0,
                "settings": {},
            }
            (Path(transport.path(base)) / "result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            fetched = fetch_results(transport, "job-bad", Path(temp) / "out")
        self.assertFalse(fetched["ok"])
        self.assertTrue(
            any("SHA-256" in error for error in fetched["errors"]), fetched["errors"]
        )
        self.assertEqual(fetched["files"], [])

    def test_missing_scene_raises(self) -> None:
        job = RenderJob(scene=str(self.temp / "nope.blend"), output=str(self.temp / "out"))
        with self.assertRaises(worker.WorkerError):
            build_bundle(self.temp / "nope.blend", job, self.temp / "bundle3")

    def test_upload_bundle_to_local_transport(self) -> None:
        job = RenderJob(scene=str(self.scene), output=str(self.temp / "out"))
        bundle = build_bundle(self.scene, job, self.temp / "bundle4")
        with tempfile.TemporaryDirectory() as temp:
            transport = LocalTransport(Path(temp) / "ws")
            remote = upload_bundle(transport, bundle, "job123")
            self.assertEqual(remote["base"], "jobs/job123")
            for name in (BUNDLE_SCENE, BUNDLE_SCRIPT, BUNDLE_ASSETS, BUNDLE_JOB):
                self.assertTrue(transport.exists(f"jobs/job123/{name}"), name)
            self.assertTrue(transport.exists("jobs/job123"))

    def test_parse_worker_result_takes_last_valid_line(self) -> None:
        stdout = "\n".join(
            [
                "noise",
                f"{RESULT_MARKER} not-json",
                f"{RESULT_MARKER} {{\"ok\": false, \"job_id\": \"old\"}}",
                f"{RESULT_MARKER} {{\"ok\": true, \"job_id\": \"new\"}}",
                "Blender quit",
            ]
        )
        payload = parse_worker_result(stdout)
        self.assertEqual(payload["job_id"], "new")
        self.assertIsNone(parse_worker_result("nothing here"))


class BlenderRenderTests(unittest.TestCase):
    """End-to-end through LocalTransport with the real Blender."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.blender = blender_path()
        if cls.blender is None:
            raise unittest.SkipTest(
                "Blender 5.2 not found (set GOO_BRIDGE_BLENDER to a blender.exe)"
            )

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name)
        self.scene = self.make_scene()
        self.workspace = self.temp / "remote-workspace"
        self.output = self.temp / "output"
        self.transport = LocalTransport(self.workspace)

    def make_scene(self) -> Path:
        blend = self.temp / "tiny.blend"
        script = self.temp / "make_scene.py"
        script.write_text(
            "\n".join(
                [
                    "import bpy",
                    "scene = bpy.context.scene",
                    "scene.render.engine = 'BLENDER_EEVEE'",
                    "scene.render.resolution_x = 64",
                    "scene.render.resolution_y = 48",
                    "scene.render.resolution_percentage = 100",
                    "bpy.ops.wm.save_as_mainfile(filepath=" + json.dumps(str(blend)) + ")",
                    "print('SCENE_OK')",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                self.blender,
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(script),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertIn("SCENE_OK", completed.stdout)
        self.assertTrue(blend.is_file())
        return blend

    def test_render_remote_end_to_end(self) -> None:
        payload = render_remote(
            self.scene,
            self.output,
            transport=self.transport,
            blender=self.blender,
            samples=4,
            resolution=50,
            timeout=300,
        )
        self.assertTrue(payload["ok"], payload.get("errors"))
        self.assertEqual(payload["transport"], "local")
        self.assertGreaterEqual(payload["verified"], 1)
        self.assertTrue(payload["files"])
        for path in payload["files"]:
            data = Path(path).read_bytes()
            self.assertTrue(data.startswith(PNG_MAGIC), path)
            self.assertEqual(sha256_file(Path(path)), sha256_file(Path(path)))
        job_id = payload["job_id"]
        self.assertTrue(self.transport.exists(f"jobs/{job_id}/result.json"))

        status = job_status(self.transport, job_id)
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "completed")

        fetched = self.temp / "fetched"
        result = fetch_results(self.transport, job_id, fetched)
        self.assertTrue(result["ok"], result.get("errors"))
        self.assertEqual(len(result["files"]), len(payload["files"]))
        for path in result["files"]:
            self.assertTrue(Path(path).is_file())

        cleaned = clean_jobs(self.transport, job_id)
        self.assertTrue(cleaned["removed"])
        self.assertFalse(self.transport.exists(f"jobs/{job_id}"))
        self.assertFalse(job_status(self.transport, job_id)["ok"])

    def test_connection_local(self) -> None:
        payload = test_connection(self.transport, blender=self.blender)
        self.assertTrue(payload["ok"], payload.get("errors"))
        self.assertTrue(payload["roundtrip"])
        self.assertTrue(payload["blender"]["ok"])
        self.assertTrue(payload["blender"]["version_ok"])
        self.assertFalse(self.transport.listdir("."))


class RemoteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.temp = Path(self._temp.name)
        self.home = self.temp / "home"
        self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {"GOO_BRIDGE_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workspace = self.temp / "workspace"

    def run_cli(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = remote_cli.cli_remote(argv)
        return code, out.getvalue(), err.getvalue()

    def write_profiles(self, *names: str) -> None:
        (self.home / "config.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "remote": [
                        {
                            "name": name,
                            "host": f"{name}.example.com",
                            "user": "artist",
                            "port": 22,
                        }
                        for name in names
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_empty_argv_prints_help(self) -> None:
        code, out, _err = self.run_cli([])
        self.assertEqual(code, 2)
        self.assertIn("remote", out)

    def test_unknown_profile_is_usage_error(self) -> None:
        self.write_profiles("studio")
        code, out, _err = self.run_cli(["test", "--profile", "nope", "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("unknown remote profile", payload["error"])

    def test_no_profiles_is_usage_error(self) -> None:
        code, out, _err = self.run_cli(["test", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("no remote profiles", json.loads(out)["error"])

    def test_single_profile_is_used(self) -> None:
        import types

        self.write_profiles("studio")
        args = types.SimpleNamespace(
            host=None,
            profile=None,
            user=None,
            port=None,
            key=None,
            blender=None,
            workspace=None,
            local=False,
        )
        profile = remote_cli.resolve_profile(args)
        self.assertEqual(profile.name, "studio")
        self.assertEqual(profile.host, "studio.example.com")
        self.assertEqual(profile.user, "artist")

    def test_add_and_list_profiles(self) -> None:
        code, out, _err = self.run_cli(
            [
                "add",
                "studio",
                "--host",
                "render.example.com",
                "--user",
                "artist",
                "--port",
                "2200",
                "--blender",
                "/opt/blender/blender",
                "--json",
            ]
        )
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["action"], "added")
        self.assertEqual(payload["profile"]["port"], 2200)
        stored = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["remote"][0]["name"], "studio")

        code, out, _err = self.run_cli(["add", "studio", "--host", "other", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("already exists", json.loads(out)["error"])

        code, out, _err = self.run_cli(
            [
                "add",
                "studio",
                "--host",
                "other.example.com",
                "--user",
                "render",
                "--force",
                "--json",
            ]
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["action"], "updated")

        code, out, _err = self.run_cli(["list", "--json"])
        self.assertEqual(code, 0, out)
        listing = json.loads(out)
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["profiles"][0]["host"], "other.example.com")

        code, out, _err = self.run_cli(["list"])
        self.assertEqual(code, 0, out)
        self.assertIn("studio", out)
        self.assertIn("other.example.com", out)

    def test_add_requires_host(self) -> None:
        code, _out, err = self.run_cli(["add", "studio"])
        self.assertEqual(code, 2)
        self.assertIn("--host", err)

    def test_add_help_works(self) -> None:
        code, out, _err = self.run_cli(["add", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage:", out.lower())
        self.assertIn("--host", out)
        self.assertIn("--force", out)

    def test_fetch_requires_output(self) -> None:
        code, out, _err = self.run_cli(
            ["fetch", "deadbeef", "--local", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--output", json.loads(out)["error"])

    def test_clean_requires_a_target(self) -> None:
        code, out, _err = self.run_cli(
            ["clean", "--local", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 2)
        self.assertIn("job id", json.loads(out)["error"])

    def test_render_requires_output(self) -> None:
        code, out, _err = self.run_cli(
            ["render", "scene.blend", "--local", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 2)
        self.assertIn("--output", json.loads(out)["error"])

    def test_missing_scene_is_usage_error(self) -> None:
        code, out, _err = self.run_cli(
            [
                "render",
                str(self.temp / "missing.blend"),
                "-o",
                str(self.temp / "out"),
                "--local",
                "--json",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("scene not found", json.loads(out)["error"])

    def test_status_missing_job(self) -> None:
        code, out, _err = self.run_cli(
            [
                "status",
                "deadbeef",
                "--local",
                "--workspace",
                str(self.workspace),
                "--json",
            ]
        )
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "missing")

    def test_local_render_status_fetch_clean(self) -> None:
        blender = blender_path()
        if blender is None:
            self.skipTest("Blender 5.2 not found")

        # Build a tiny scene with Blender.
        scene = self.temp / "tiny.blend"
        script = self.temp / "make_scene.py"
        script.write_text(
            "\n".join(
                [
                    "import bpy",
                    "scene = bpy.context.scene",
                    "scene.render.engine = 'BLENDER_EEVEE'",
                    "scene.render.resolution_x = 64",
                    "scene.render.resolution_y = 48",
                    "bpy.ops.wm.save_as_mainfile(filepath=" + json.dumps(str(scene)) + ")",
                    "print('SCENE_OK')",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [blender, "--background", "--factory-startup", "--python-exit-code", "1",
             "--python", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])

        code, out, err = self.run_cli(
            [
                "render",
                str(scene),
                "-o",
                str(self.temp / "render-out"),
                "--local",
                "--workspace",
                str(self.workspace),
                "--blender",
                blender,
                "--samples",
                "4",
                "--resolution",
                "50",
                "--json",
                "--quiet",
            ]
        )
        self.assertEqual(code, 0, err + out)
        payload = json.loads(out)
        self.assertTrue(payload["ok"], payload.get("errors"))
        job_id = payload["job_id"]
        self.assertGreaterEqual(len(payload["files"]), 1)

        code, out_missing, _err = self.run_cli(
            ["status", job_id, "--local", "--workspace", str(self.temp / "other"), "--json"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out_missing)["state"], "missing")

        code, out_status, _err = self.run_cli(
            ["status", job_id, "--local", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0, out_status)
        self.assertEqual(json.loads(out_status)["state"], "completed")

        code, out_fetch, _err = self.run_cli(
            [
                "fetch",
                job_id,
                "-o",
                str(self.temp / "fetched"),
                "--local",
                "--workspace",
                str(self.workspace),
                "--json",
                "--quiet",
            ]
        )
        self.assertEqual(code, 0, out_fetch)
        self.assertTrue(json.loads(out_fetch)["ok"])

        code, out_clean, _err = self.run_cli(
            ["clean", job_id, "--local", "--workspace", str(self.workspace), "--json"]
        )
        self.assertEqual(code, 0, out_clean)
        self.assertTrue(json.loads(out_clean)["removed"])

    def test_cli_integration_help(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [sys.executable, "-m", "goo_bridge", "remote", "--help"],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for name in ("add", "list", "test", "render", "status", "fetch", "clean"):
            self.assertIn(name, completed.stdout)


if __name__ == "__main__":
    unittest.main()
