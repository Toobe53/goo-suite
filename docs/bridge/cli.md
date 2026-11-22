# CLI reference / 命令行参考

All commands accept `--json` for machine-readable output. Run
`goo-bridge <command> --help` for the parser's current options.

所有命令都支持 `--json`，便于脚本调用。

## `goo-bridge doctor`

Checks Python, the config path, Blender discovery, the optional GOO engine and
saved remote profiles.

```bash
goo-bridge doctor
goo-bridge doctor --json
```

Exit code is 0 even when Blender is missing (the JSON explains why); use the
`found` field in automation.

## `goo-bridge analyze`

```bash
goo-bridge analyze scene.blend -o analysis.json
goo-bridge analyze scene.blend -o analysis.html
goo-bridge analyze scene.blend --json
```

The report contains the Blender version, render engine, resolution, GOO node
groups, materials using them, external files, missing files, cameras, view
layers and feature flags. A missing file is reported, never hidden.

## `goo-bridge report`

```bash
goo-bridge report analysis.json -o analysis.html
goo-bridge report analysis.json -o analysis.json
```

Converts an analysis JSON into an HTML or JSON summary.

## `goo-bridge compat`

```bash
goo-bridge compat --analysis analysis.json -o compat.html
goo-bridge compat -o compat.html
```

Generates the 39-item L2/L1/L0 report. Without `--analysis`, every item is shown
with the matrix's declared level and a `manual`/`missing` detection status.

Levels:

- **L2** — within the agreed 1%–2% visual tolerance
- **L1** — same intent, visible difference
- **L0** — explicitly unsupported

## `goo-bridge render`

```bash
goo-bridge render scene.blend -o renders/ \
  --camera Camera --view-layer ViewLayer \
  --samples 32 --resolution 100 --frame 1 --format PNG
```

| Option | Default | Meaning |
|---|---|---|
| `scene` | required | `.blend` to render |
| `-o`, `--output` | required | output directory |
| `--camera` | scene camera | named camera object |
| `--view-layer` | active layer | named view layer |
| `--samples` | 32 | render samples |
| `--resolution` | 100 | resolution percentage |
| `--frame` | `1` | `1`, `1-10`, or `1,3,5` |
| `--format` | `PNG` | `PNG`, `JPEG`, `OPEN_EXR` |
| `--json` | off | machine-readable result |

Writes `render_0001.png` (or the selected format) and
`render_manifest.json`.

## `goo-bridge remote`

```bash
goo-bridge remote add NAME --host HOST --user USER [--port 22] [--key PATH]
                           [--blender COMMAND] [--workspace DIR]
goo-bridge remote list
goo-bridge remote test --profile NAME
goo-bridge remote render --profile NAME SCENE -o OUTPUT [render options]
goo-bridge remote status --profile NAME JOB_ID
goo-bridge remote fetch --profile NAME JOB_ID -o OUTPUT
goo-bridge remote clean --profile NAME JOB_ID
```

See [remote.md](remote.md) for SSH setup and security.

## `goo-bridge verify`

```bash
goo-bridge verify --dist dist
goo-bridge verify --dist dist --json
```

Reads `dist/SHA256SUMS`, re-hashes every file, and checks the archive members
that must exist. Exit code 2 when a file is missing, a hash differs, or an
archive is corrupt.

## `goo-bridge gui`

```bash
goo-bridge gui
GOO_BRIDGE_SELFTEST=1 goo-bridge gui
```

The GUI has Project, Analyze, Remote, Queue and Report sections. The self-test
builds the window, prints one JSON line and exits; it is used by CI. Set
`GOO_BRIDGE_SKIP_GUI=1` to skip GUI tests on a headless host without Tk.

## Environment variables

| Variable | Meaning |
|---|---|
| `GOO_BRIDGE_BLENDER` | explicit Blender executable |
| `GOO_BRIDGE_HOME` | config/state directory (default per OS) |
| `GOO_BRIDGE_SELFTEST` | `1` runs the GUI self-test and exits |
| `GOO_BRIDGE_SKIP_GUI` | `1` skips GUI tests |
| `GOO_BRIDGE_SSH` | override the `ssh` executable for tests |
| `GOO_BRIDGE_SCP` | override the `scp` executable for tests |
