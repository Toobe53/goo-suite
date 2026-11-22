# GOO Suite

[English](README.md) | [简体中文](README.zh-CN.md)

[![build](https://github.com/Toobe53/goo-suite/actions/workflows/build.yml/badge.svg)](https://github.com/Toobe53/goo-suite/actions/workflows/build.yml)

GOO Suite combines the **GOO Engine 4.4 NPR port for Blender 5.2** with an external
**render manager** in one portable repository:

| Component | Role | How to use |
| --- | --- | --- |
| `goo_npr` | Blender 5.2 add-on: 22 GOO node groups, light groups, camera/view-layer tools | Install `goo_npr-1.0.0.zip` |
| `goo` | CLI + Tk GUI: apply nodes, multi-pass render, composite, install, bundle, analyze, compatibility | `goo ...` |
| `goo_bridge` | Remote/local render queue: SSH or local worker, asset checks, job status, HTML reports | `goo remote ...` / `goo-bridge ...` |

> No Blender fork, no patched renderer, no bundled Blender. Everything is standard-library
> Python and ships from this repository.

## Install

Requires Blender **5.2.x** and Python **3.11+**.

```bash
# From the source tree, or install the release wheel
python -m pip install .
# or
python -m pip install goo_suite-1.0.0-py3-none-any.whl

# Install the Blender add-on
goo install --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"

# Check Blender, the add-on and the node library
goo doctor
goo nodes
```

You can also drag `goo_npr-1.0.0.zip` into **Preferences ▸ Add-ons ▸ Install from Disk**.

## Quick start

```bash
# 1. Apply the GOO node library / light groups
goo apply scene.blend -o scene_goo.blend

# 2. One EXR per light group, then composite
goo render scene.blend --light-groups -o out/passes
goo composite out/passes --png

# 3. Analyze assets and compatibility
goo analyze scene.blend -o analysis.json
goo report analysis.json -o report.html
goo compat --analysis analysis.json -o compat.html

# 4. Remote render (SSH, key auth; no password stored)
goo remote add studio --host render.example.com --user artist --key ~/.ssh/id_ed25519
goo remote test --profile studio
goo remote render scene.blend -o out/remote --profile studio

# 5. GUI
goo gui
```

Inside Blender: `Node Editor ▸ Add ▸ GOO Engine`, `Properties ▸ Scene ▸ GOO Light Groups`,
`Properties ▸ View Layer ▸ GOO View Layer Overrides`, `3D Viewport ▸ Sidebar ▸ GOO`.

## Status and compatibility

- 49-row feature matrix: 9 Ported, 1 Native, 25 Approx, 14 N/A; see [docs/compatibility.md](docs/compatibility.md).
- 39-row external compatibility audit: 13 L2, 15 L1, 11 L0; `goo compat` writes the report.
- GOO 4.4 uses EEVEE Legacy, Blender 5.2 uses EEVEE Next; Approx entries document each difference.
- Existing `goo-bridge` configuration is read and migrated to the `GOO Suite` directory; the old command remains for one release.

## Documentation

- [User tutorial (Chinese, with English command reference)](docs/tutorial.md)
- [CLI reference](docs/cli.md)
- [Remote rendering](docs/remote.md)
- [Architecture](docs/architecture.md)
- [Optimization log](docs/optimizations.md)
- [Verification evidence](docs/verification.md)

## Development

```bash
python -m unittest discover -s tests -v
blender --factory-startup --background --python tests/test_addon.py
python tests/test_paths.py
python tools/build_release.py
goo verify
```

## License

GPL-3.0-or-later. The original GOO Engine design belongs to DillonGoo Studios; this
repository is an independent reimplementation.
