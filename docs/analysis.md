# GOO Engine 4.4 vs. Blender — delta analysis / 差异分析

This is the analysis behind the port: **what GOO Engine 4.4 actually changes** relative to
Blender 4.4.3 (now verified against the public source branch), why stock Blender 5.2 cannot
express some of it, and what this project did about each delta. The item-by-item matrix is
[features.md](features.md); the file-level evidence is
[goo44-source-diff.md](goo44-source-diff.md); measured deviations are
[compatibility.md](compatibility.md) and [verification.md](verification.md).

本文分析 GOO 4.4 相对 Blender 4.4.3 的真实改动（已用公开源码分支逐文件核对），说明
Blender 5.2 为什么无法表达其中一部分，以及本项目对每一项的处理。

## 0. Evidence basis / 依据

| Source | What it gives us | Limits |
| --- | --- | --- |
| GOO public branch `goo-engine-v4.4-release` (downloaded 2026-11-22) | **full 4.4 source**: 19,270 files, sha-pinned archive `56BEDC3A…E91778`, based on Blender `v4.4.3` | branch is a commit-less snapshot via the mirror; archive hash pins the analysed bytes |
| `ungh.cc` file/blob hashes vs. Blender `v4.4.3` | exact added/changed file lists: **178 source-only**, **159 source-changed**, 0 removed | hash-level, not a semantic commit diff |
| Official feature page, <https://www.dillongoostudios.com/gooengine-features> | product-level feature list and wording | marketing level; the source decides semantics |
| Blender 5.2.3 LTS on this machine | target API, probed directly (evidence table in [verification.md](verification.md)) | Windows 11 only |
| `source/blender/draw/engines/gooengine/` | 41 C++ files = a forked Legacy EEVEE with its own shaders | requires a C++ build to reuse |

Classification / 分类:

* **Verified** — read in the GOO 4.4 source (file + line cited in
  [goo44-source-diff.md](goo44-source-diff.md)).
* **Measured** — reproduced on Blender 5.2 in this project (headless tests, pixel diffs).
* **Approx / N-A / Not shipped** — status in [features.md](features.md), reason here.

## 1. What GOO changes, by subsystem / 按子系统

### 1.1 Renderer core — a fork of Legacy EEVEE (largest delta)

GOO ships `draw/engines/gooengine`, a renamed full copy of Blender 4.4's Legacy EEVEE,
and drops Cycles. Its shader loop in `gooengine/shaders/closure_eval_surface_lib.glsl`
(lines ~404–478) evaluates **per light**:

* skip a light unless `(ld.light_group_bits & material.light_group_bits) != 0`;
* accumulate `Diffuse Shading` from `color·l_diff·light_diffuse(...)`;
* accumulate `Cast Shadows` as `1 − Σ(light_fac·shadow)/Σ light_fac`, only for lights whose
  `light_group_shadow_bits` intersect;
* compute `Self Shadows` with `calc_self_shadows_only()` (shadow map only, no falloff);
* accumulate `Half-lambert factor` as `0.5·dot(L,n)+0.5`;
* evaluate `Ambient Lighting` with `probe_evaluate_world_diff(n)`.

| GOO feature | Why stock 5.2 cannot do it | Port decision |
| --- | --- | --- |
| **Shader Info** | EEVEE Next evaluates closures internally and gives the shader one combined radiance; the per-light loop is C++ | Approx: `ShaderToRGB` luminance + AO proxies; all five GOO output names preserved |
| **Light Groups** (bitmask `light_group_bits[4]`, `light_group_shadow_bits[4]`, default group bit 127) | Python cannot add per-light bits to EEVEE's sampler; 5.2 has no material light-group property | Approx + CLI: named groups + detached native receiver collections, one render per group, linear composite verified to `1e-6` mean diff |
| **Curvature** | GOO computes screen-space curvature in its renderer | Approx: dual-radius AO difference + Fresnel rim |
| **Screen Space Info** (Scene Color/Depth sampling) | EEVEE Next material shaders have no scene-buffer binding | Approx: screen position/UV/depth/facing; CLI depth pass replaces buffer sampling |
| **Set Depth** (true `gl_FragDepth` write) | shader depth write is fixed-function in EEVEE | Approx: depth encoding node; true write only with the C++ patch (P3) |
| **Holdout with alpha blend, shadow IDs, refraction buffers, contact shadows, cascaded shadows** | renderer internals | N/A: flags/UI only or omitted, reason documented |
| **Twirl / Water Ripples / Hex Grid / SDF / OKLab** | procedural node families added in C++ | Ported/Approx as shader node groups (SDF and OKLab now shipped approximately) |
| **Compositor additions** | GOO adds screen-space/compositing features | Partly ported: Line Composite, Kuwahara NPR, Depth Fog, `goo.setup_compositor` |

### 1.2 Geometry Nodes

Source shows three real changes:

1. `node_geo_input_active_camera.cc`: new `Ignore Scene Override` input; when false and
   `scene.gn_camera_override` is set, the node returns that camera.
2. `node_geo_input_scene_time.cc`: adds `Start Frame` / `End Frame` outputs
   (`scene->r.sfra/efra`).
3. `node_geo_set_instance_transform.cc`: only a namespace rename (no behaviour change).

Port: neither native node input can be injected by an add-on. Scene Time Start/End is
**Approx** (node group + scene handler). Camera override is **Approx**: a
`GOO Active Camera` geometry-node group reproduces the switch with an Object socket
(`Ignore Scene Override` + `Override Camera`), and `goo render --camera NAME` changes the
render camera without editing the file; native nodes created without the group still read
`scene.camera`.

### 1.3 Grease Pencil

* **Auto-close strokes** and **random texture per stroke** are tool-level, portable through
  the GP v3 API → `goo.gp_auto_close` (sets the `cyclic` CURVE boolean) and
  `goo.gp_random_texture` (`goo_tg_random` attribute); verified headless.
* GP sculpt smoothing / drawing aids → **Approx**: `goo.gp_smooth_strokes` applies
  Laplacian smoothing to the current drawing (factor/iterations, cyclic-aware); GOO blends
  the smoothing into the sculpt brush while drawing, which needs C++ brush changes.

### 1.4 Animation / Graph Editor

* **Single F-Curve isolation** is portable with the 5.2 Action API
  (`action.layers[0].strips[0].channelbags[0].fcurves`) → `goo.fcurve_isolate` /
  `goo.fcurve_restore`, verified.
* Bone tools (isolate bones, Copy Rotation `invert_all`, merge constraints, Outliner
  `hide_outliner` flag) exist in the 4.4 source but live in C++/RNA → **Approx** with
  Python operators; the Outliner flag is re-created on an ID property, while actual
  Outliner row filtering has no Python hook (GOO 4.4's public branch also stores the flag
  without a consumer).

### 1.5 UI / viewport

* Add-menu `Goo Engine` category (`NODE_MT_category_goo_engine` in GOO), node sidebars,
  viewport line width (`U.viewport_line_width` in GOO → draw handler here), per-view-layer
  overrides, topology mirror, scene camera field, film-transparent toggle, force-enable
  collection children → implemented in the add-on.
* GOO's tree-UI scroll zoom and top-bar camera dropdown are C++ layouts → not portable.

### 1.6 Build / platform

GOO ships custom Windows/Linux/macOS builds because the renderer changes are C++. This port
deliberately runs on **stock Blender 5.2**: the add-on provides look-development nodes and
data semantics, the CLI performs the renderer-cooperating work (multi-pass, composite,
install/bundle). The optional C++ route is [native-extension.md](native-extension.md).

## 2. Portability verdict / 可移植性结论

| Class | Count | Meaning |
| --- | --- | --- |
| Ported | 9 | 1:1 through public APIs (menu, overrides UI, Twirl, GP auto-close, line width, transparency, topology mirror, platforms, render burn-in via CLI) |
| Native in 5.2 | 1 | Grease Pencil 3 |
| Approx | 25 | same interface, documented approximation (Shader Info, Light Groups + CLI, Set Depth, Light Info, Light Info New, SDF ×4, OKLab, Hex Grid, Water Ripples, Scene Time, Active Camera group + CLI camera, GP smoothing, isolate bones, Invert All, merge constraints, Outliner flag) |
| N/A | 14 | renderer/core internals (shadow ID, contact/cascaded shadows, holdout, closure data, …) |
| Not shipped | 0 | — |
| **Total** | **49** | full table in [features.md](features.md) |

## 3. What "exact" would require / 精确复刻需要什么

1. ~~Source access~~ — **done**: the 4.4 branch is analysed and sha-pinned; socket names,
   bit packing and shader formulas are in [goo44-source-diff.md](goo44-source-diff.md).
2. **Renderer-level patches** for the closure loop, light group bitmask and depth write:
   specified in [native-extension.md](native-extension.md) (P1–P4) against the verified
   5.2.3 source paths.
3. **Cross-platform CI** on macOS/Linux (only Windows 11 was available here).

The shipped add-on/CLI is therefore a portable behaviour layer with a measured contract —
not a claim of byte-level parity; the exact-parity path is a C++ patch on top of the same
add-on, not a different product.
