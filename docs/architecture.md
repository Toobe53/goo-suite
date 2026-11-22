# Architecture / 架构

GOO Suite merges the Blender-side NPR add-on and the external render/queue
manager into one repository with two artifacts (add-on zip and wheel). The
add-on remains self-contained; the wheel adds `goo_bridge` and the unified CLI.

```
┌──────────────────────────── Blender 5.2 ────────────────────────────┐
│  goo_npr (add-on, pure Python)                                      │
│  ├─ nodes/          → GOO groups (shading, procedural, SDF, OKLab, geometry, screen space)
│  ├─ light_groups    → name-based groups on native light linking     │
│  ├─ ui/panels       → Scene / Material / Object / 3D View panels    │
│  ├─ tools/          → GP, F-Curve, line width, pose helpers          │
│  ├─ overrides       → view-layer overrides, camera, flags           │
│  └─ compositor      → screen-space node groups + setup operator     │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ scene data (collections, flags, passes)
┌───────────────────────────────▼─────────────────────────────────────┐
│  goo (unified CLI + Tk GUI, standard library)                       │
│  ├─ blender.py   → find/launch Blender headless (JSON job files)    │
│  ├─ scripts/     → in-Blender scripts (render passes, composite,    │
│  │                 apply, inspect, install)                         │
│  ├─ pipeline.py  → doctor/nodes/apply/render/composite operations   │
│  ├─ install.py   → install add-on into any Blender 5.2 / bundle zip │
│  ├─ verify.py    → hash dist/SHA256SUMS and inspect archive members │
│  └─ gui.py       → Tkinter front-end (render + analyze/compat)       │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ analyze/report/compat/remote
┌───────────────────────────────▼─────────────────────────────────────┐
│  goo_bridge (shared core)                                          │
│  ├─ analyze.py   → scene/asset scan + SQLite cache                 │
│  ├─ assets.py    → SHA-256 inventory, bounded parallel hashing      │
│  ├─ remote.py    → SSH/local queue, status, fetch, clean            │
│  ├─ compat.py    → 39-row L2/L1/L0 audit and HTML report            │
│  └─ gui.py       → 5-tab remote/queue/report window                 │
└─────────────────────────────────────────────────────────────────────┘
```

## Design rules / 设计原则

1. **No compiled code.** Everything is Python that runs inside Blender's bundled
   interpreter or the system Python 3.10+. A copy of the repository is a complete,
   portable installation.
2. **The add-on never owns scene data permanently.** Light-group collections are
   detached collections (not linked to the scene), so they cannot pollute a user's
   outliner or visibility. Every mutation is tagged with `goo_port` custom properties
   and can be rebuilt/reset.
3. **Deterministic group names.** Node groups are named `GOO <Feature>` and carry
   `goo_port` / `goo_builder` / `goo_builder_version` properties. Rebuilding keeps the
   node-tree datablock so materials referencing the group never break.
4. **Renderer differences are documented, not hidden.** Where 5.2 lacks GOO's
   EEVEE-Legacy internals (shader depth output, per-light-group shadow terminator),
   the external CLI produces extra passes and the difference is recorded in
   `docs/compatibility.md`.
5. **Bilingual by construction.** UI strings go through `i18n.t()` with `en_US` and
   `zh_CN` entries; documentation ships both languages in the same file.

## Light Groups data flow / Light Groups 数据流

```
Light object  ── goo_light_group ──►  group name
Material/Object ── goo_light_groups ──►  linked group names
Scene.goo_light_groups ──►  GOO_LightGroup{name, use_shadow, receiver_collection}
        │
        ▼  sync_scene()
receiver objects per group
        │
        ├─► Light.light_linking.receiver_collection = GOO_LG_<name>   (native EEVEE)
        ├─► light.use_shadow per group (with backup/restore)
        └─► collect_passes() → CLI renders one image per group
```

## Rendering pipeline / 渲染流程

1. `goo render scene.blend --light-groups` starts Blender headless with
   `goo/scripts/render_passes.py` (a JSON job file is passed in, a JSON result is read
   back; stdout is not parsed).
2. The script imports `goo_npr.light_groups`, calls `sync_scene()`, then for every pass
   isolates the lights of one group (`hide_render` backups) and renders to
   `out/passes/<group>_beauty.exr`; ungrouped lights form the `ambient` pass.
3. Optional data passes (depth/shadow) are rendered through a generated compositor node
   group whose `NodeGroupOutput` is wired to one `RLayers` socket at a time. They are
   always EXR, even with `--format png`; the manifest masks EEVEE's `1e10` background
   depth instead of reporting it as a mean.
4. `goo composite` adds the group EXRs in linear space (inside Blender's Python, with
   numpy), subtracts the duplicated ambient pass, optionally multiplies the shadow
   matte, and writes `composite.exr` plus a hand-encoded `composite.png` (Blender's PNG
   save of generated float images produced black frames in 5.2, so the PNG is written
   with `zlib`).

## Extending / 扩展

* New node group: create `goo_npr/nodes/<module>.py`, import it in
  `goo_npr/nodes/__init__.py`'s `MODULES`, and decorate a builder with
  `@group("GOO Name", tree_type=..., category=..., label=..., description=...)`.
  The add-on builds it on startup, the Add menu lists it, and `goo nodes <file.blend>`
  reports it without extra registration.
* New light-group behaviour: add a custom property in `light_groups.py` and handle it
  in `sync_scene()`; the CLI automatically sees the resulting passes through
  `collect_passes()`.
* New CLI command: add a `cmd_<name>` function + subparser in `goo/cli.py` and a
  corresponding operation in `goo/pipeline.py`; commands only use the standard library
  and must run without Blender installed (they fail with a clear message when a Blender
  binary is required).

## Portability / 可移植性

| Concern | Handling |
| --- | --- |
| Blender path | `GOO_BLENDER` env var → `--blender` → common install dirs (Windows/macOS/Linux) → `blender` on `PATH` |
| Blender version | hard requirement 5.2+, checked by the add-on and `goo doctor` |
| Python | add-on runs in Blender's Python 3.13; CLI requires 3.10+ and only stdlib (+ numpy for compositing) |
| Paths | `pathlib` everywhere, UTF-8 output, Windows drive letters and POSIX paths both parsed |
| Locale | UI strings through the add-on `i18n` module (`en_US` / `zh_CN`); CLI help and messages are bilingual inline |
