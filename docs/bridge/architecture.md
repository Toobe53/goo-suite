# GOO Bridge architecture

GOO Bridge is a standalone desktop/CLI application.  It never links against
Blender and never modifies the Blender installation.  Blender 5.2 is started as
a child process for analysis and rendering; the project file stays the user's
own file.

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Entry points | `cli.py`, `gui.py` | Parse commands / collect form values, show results |
| Project model | `jobs.py` | Serializable render job, result and timestamps |
| Discovery | `blender.py` | Find Blender 5.2 on Windows, macOS, Linux; validate version |
| Analysis | `analyze.py` + `scripts/analyze_scene.py` | Inspect GOO node groups, materials, external files, render settings |
| Local render | `render.py` + `scripts/render_scene.py` | Set camera/view layer/samples/resolution and render frames |
| Engine adapter | `engine.py` | Prefer an installed `goo` CLI for GOO node groups; fall back to plain Blender |
| Remote | `transport.py`, `remote.py`, `worker.py`, `scripts/remote_worker.py` | Bundle, upload, run headless Blender, fetch outputs |
| Report | `compat.py`, `report.py` | 39-item compatibility matrix and standalone HTML |
| Release check | `verify.py` | `SHA256SUMS` and archive member validation |

## Local flow

1. `doctor` finds Blender and (optionally) the GOO engine.
2. `analyze` runs `analyze_scene.py` in Blender background mode and stores a
   JSON report.  Missing external files are preserved as data, not silently
   dropped.
3. `render` writes a temporary `job.json`, runs `render_scene.py` against the
   original `.blend`, and reads `render_manifest.json` back.
4. The CLI prints machine-readable JSON with `--json`, or a short human summary.

## Remote flow

The remote design is agentless: the client needs only OpenSSH (`ssh`/`scp`).
A job bundle contains the scene, external assets referenced by the scene, a
JSON job description and a worker script.  The remote directory is
`~/.goo-bridge/jobs/<job-id>`.  The worker runs Blender headless, writes a
manifest and output files, and the client verifies SHA-256 before accepting the
result.  Credentials are never stored in job files; SSH keys and the user's
`known_hosts` remain in the SSH client.

## Compatibility levels

- **L2** — visual parity within the agreed 1–2% tolerance for the supported
  feature; implemented through the external engine or a native Blender
  replacement.
- **L1** — same intent but a documented visual/API difference.
- **L0** — explicitly unsupported; the bridge reports it rather than pretending
  to reproduce it.

The exact 39 items live in `goo_bridge/data/compat.json` and are rendered into a
standalone HTML report.

## Security boundaries

- No remote arbitrary shell: the worker executes a fixed Blender command.
- No bundled Blender: the user supplies Blender 5.2 and accepts its license.
- No silent missing assets: analysis lists them and remote jobs can block.
- JSON output is ASCII-safe for Windows code pages.
