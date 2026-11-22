# Verification / 验证报告

This page records how GOO Bridge 0.1.0 was verified. The project is a normal
engineering deliverable, not a binary-reverse-engineering investigation.

## Local test suite

Command (from the repository root, Blender-bundled Python 3.13.13):

```bash
& "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe" -m unittest discover -s tests -v
```

Result: **96 tests, OK (skipped=2)** in about 20 seconds. The two skips are the
real-window GUI case (this Python build has no Tkinter) and its skip-path
counterpart. Core, remote, engine, compatibility and CLI tests run for real.

Coverage:

- `tests/test_core.py` — config, Blender discovery, jobs, analysis, report, CLI,
  local Blender render of a generated demo scene.
- `tests/test_remote.py` — SSH/scp command shapes, tilde resolution, path
  traversal rejection, local transport, bundle layout, missing-asset blocking,
  SHA-256 mismatch detection, LocalTransport end-to-end render, `remote
  add/list/test/render/status/fetch/clean`, help routing.
- `tests/test_gui.py` — lazy Tk import, headless `GOO_BRIDGE_SELFTEST=1` JSON,
  tab/button constants, GUI-to-CLI argv builders.
- `tests/test_compat.py` — 39-item matrix integrity, 13 L2 / 15 L1 / 11 L0,
  scan statuses, standalone HTML.
- `tests/test_engine.py` — engine detection, optional `goo` CLI adapter,
  Blender fallback.
- `tests/test_cli_compat.py` — compatibility CLI, JSON output, docs presence.

## Cross-platform CI

Workflow: `.github/workflows/build.yml`.

Successful run: [45052199623](https://github.com/Toobe53/goo-bridge/actions/runs/45052199623)
(head `97463f4`). All seven jobs passed:

| Job | What it proves |
|---|---|
| `unit-ubuntu-latest` | Python 3.12 unit suite, wheel build |
| `unit-windows-latest` | Python 3.12 unit suite, wheel build |
| `unit-macos-latest` | Python 3.12 unit suite, wheel build |
| `unit-python-3.11` | declared Python 3.11 floor |
| `blender-ubuntu-latest` | Blender 5.2.3 download/cache + full 96-test suite under Xvfb |
| `blender-windows-latest` | Blender 5.2.3 + Mesa llvmpipe + full 96-test suite |
| `blender-macos-latest` | Blender 5.2.3 arm64 + Metal + full 96-test suite |

The CI unit suite reports 94 tests with 6 skips; the difference from the local
96 is Blender-dependent tests that are skipped on the unit-test runners and run
in the dedicated Blender jobs. Each Blender job reports `Ran 96 tests ... OK
(skipped=1)` with `GOO_BRIDGE_BLENDER` set, then `goo-bridge doctor --json`; the
Windows job uses `ssciwr/setup-mesa-dist-win@v3` with
`GALLIUM_DRIVER=llvmpipe`.

The unit job also uninstalls the editable package, installs the freshly built
wheel, moves to a neutral directory and runs `--version` plus `compat --json`,
asserting 39 items and 13/15/11 counts. This catches missing package data.

## Wheel

CI builds `goo_bridge-0.1.0-py3-none-any.whl` on each OS and uploads
`goo-bridge-wheel-<os>`. The Ubuntu artifact was installed into an empty
directory and exercised from a neutral working directory:

```text
goo-bridge --version                     -> goo-bridge 0.1.0
goo-bridge doctor --json                 -> ok, Blender 5.2.3, config path
goo-bridge compat -o compat.html --json  -> 39 items, 13 L2 / 15 L1 / 11 L0
goo-bridge remote list --json            -> count 0
goo-bridge analyze demo.blend ...        -> ok
goo-bridge render demo.blend ...         -> PNG + render_manifest.json
```

The wheel is pure Python (`py3-none-any`); the three CI wheels differ only in
archive metadata, not in package contents.

## Release

`v0.1.0` attaches the Ubuntu wheel and a `SHA256SUMS` file. Verify with:

```bash
sha256sum -c SHA256SUMS
python -m pip install goo_bridge-0.1.0-py3-none-any.whl
goo-bridge doctor
```

## Known limits

- The remote path is exercised with `LocalTransport` and a fake SSH runner; a
  live SSH server is not part of CI.
- The GUI is exercised headlessly (JSON selftest); it needs a display/Tkinter for
  interactive use.
- The three CI wheels are not byte-identical because setuptools records build
  timestamps.
