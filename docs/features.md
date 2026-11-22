# GOO Engine 4.4 feature inventory and port status
# GOO Engine 4.4 功能清单与移植状态

This is the **source-verified** inventory (GOO `goo-engine-v4.4-release`, based on Blender
4.4.3 — see [goo44-source-diff.md](goo44-source-diff.md)). It lists every addition/change
that GOO ships, what this repository implements on Blender 5.2, and how faithful that
implementation is.

本表依据已下载的 GOO 4.4 源码逐项核对（见 [goo44-source-diff.md](goo44-source-diff.md)），
列出 GOO 的全部新增/改动、本仓库在 Blender 5.2 上的实现与保真度。

Machine-readable export / 机器可读版本: [feature-matrix.json](feature-matrix.json) (49 rows with
`section`, `feature`, `implementation`, `status`, `status_class`). `tests/test_cli.py`
keeps this JSON and the Markdown table in sync.

Status legend / 状态图例:

| Status | Meaning |
| --- | --- |
| **Ported** | Behaviour reproduced with public Blender 5.2 APIs; usable in viewport and final render. |
| **Approx** | Same intent/UI, different data path; visually close, not identical. |
| **CLI** | The result needs the external `goo` program (multi-pass render / composite / install). |
| **Native** | Blender 5.2 already ships it; only a matching UI entry is added. |
| **N/A** | C++ renderer or core internals, not reachable from Python. |
| **Not shipped** | Reserved for future gaps; no rows in this version (kept so audits can classify any new GOO feature) |

> Honesty note / 说明：this is a **pure-Python add-on + external CLI**; GOO is a C++ fork
> of Legacy EEVEE. Approx/N-A/Not-shipped rows state the deviation; the reason is always
> in [compatibility.md](compatibility.md) or [native-extension.md](native-extension.md).

---

## 1. Shading and lighting / 着色与灯光

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| **Goo Engine Add-menu section** | `Add ▸ GOO Engine` sub-menu (Shader / Textures / Compositor / Utility), `goo.add_node_group` | Ported |
| **Shader Info** — Diffuse Shading / Cast Shadows / Self Shadows / Ambient Lighting / Half-lambert factor | `GOO Shader Info` (24 nodes): white Diffuse → `Shader to RGB` gives EEVEE luminance; `Cast Shadows=(1-AO_wide)·ShadowStrength`, `Self Shadows=(1-AO_narrow)` (off by default), `Ambient=AmbientColor·Strength·AO_wide`. GOO itself evaluates these **per light in C++** (`closure_eval_surface_lib.glsl`) | Approx |
| Material **Check Self Shadowing** | `Material.goo_check_self_shadowing` + `GOO Shader Info ▸ Check Self Shadowing` input; a metadata flag, it does not change the renderer | Approx |
| **Light Groups** — name-based groups on lights/materials, per-group shadow toggle, default group | `Scene.goo_light_groups` + material/object links; each group owns a **detached** receiver collection assigned via native `Light.light_linking.receiver_collection`; `use_shadow` written with backup/restore; empty groups hide their lights; `goo render --light-groups` + `goo composite` reproduce the image linearly (1e-6 mean diff) | Approx (+CLI) |
| **Shadow ID** `High ID Bit Depth` (32-bit shadow IDs) | `Scene.goo_high_bit_depth` / `Material.goo_check_self_shadowing` flags only; EEVEE Next has no shadow ID buffer | N/A (flag only) |
| **Contact shadows** (distance/bias/thickness) | EEVEE Next has no contact-shadow controls | N/A |
| **Shadow buffer bias / Cascaded Shadow Maps / light-cache bake UI** | Renderer internals of the `gooengine` fork | N/A |
| **Set Depth** — push a shader's pixel depth | `GOO Set Depth` (16 nodes): Camera Data View Z + Mode/Scale/Offset/Near/Far → `Depth`, `View Z`, `Alpha`. It **encodes** depth for an EXR/AOV pass; no `gl_FragDepth` write | Approx |
| **Light Info (legacy)** — chosen light's Color/Power/Perceptual Power | `GOO Light Info` (8 nodes) + `goo.light_info_from_active`: 5 drivers (`data.energy`, `data.color[i]`, `data.specular_factor`) update live, where GOO bakes uniforms at compile time | Approx |
| **Light Info New** — Color/Power/Position/Rotation/Distance | `GOO Light Info New` + `goo.light_info_new_from_active`: 11 drivers (color, energy, `location[i]`, `rotation_euler[i]`, camera-to-light distance expression). GOO's node declares the five outputs but its GPU function only feeds color/power; this port supplies all five | Approx |
| **Holdout for transparent materials** | 5.2 holdout is a whole-object mask; no alpha-blend holdout | N/A |
| **Material/world overrides per view layer** | GOO panel edits native `ViewLayer.material_override`, `world_override`, `samples`; `goo.clear_view_layer_overrides`; CLI `--view-layer NAME` selects one layer per render | Ported (UI) |
| **View-layer sample override for EEVEE** | 5.2 exposes `ViewLayer.samples` (Cycles semantics); EEVEE uses `scene.eevee.taa_render_samples`; CLI takes `--samples` | N/A for EEVEE |
| Shift-click collection visibility to force-enable children | `goo.collection_force_enable_children` recursively clears `hide_viewport`/`hide_render`; the Shift-click gesture is C++ | Approx |
| Radiance buffer copied between Refraction passes | Renderer internal | N/A |
| Principled/SSS shader internals (`GPU_MATFLAG_PRINCIPLED_*`, `use_sss`) | C++ renderer; not exposed | N/A |

## 2. Screen space and compositing / 屏幕空间与合成

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| **Screen Space Info** — Scene Color / Scene Depth | `GOO Screen Space Info` (10 nodes, no inputs): Screen Position, Screen UV, View Depth, View Distance, World Position, Facing, Fresnel, Camera Vector. EEVEE Next does not expose scene buffers to shaders; use `goo render --passes depth` | Approx |
| **Curvature** — Scene Curvature / Scene Rim | `GOO Curvature` (17 nodes): narrow-minus-wide AO + Layer Weight Fresnel, `Radius/Samples/Contrast`. GOO uses screen-space curvature | Approx |
| Screen-space shadows / refraction internals | Not exposed by 5.2 | N/A |

## 3. Procedural textures / 程序化纹理

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| **SDF Primitive** — 16 3D + 28 2D shapes | `GOO SDF Primitive` (8 representative shapes: sphere, box, rounded box, cylinder, torus, capsule, plane, cone), `Vector/Shape/Size/Radius/Roundness` inputs | Approx |
| **SDF Op** — 32 operations | `GOO SDF Op` (8: union/intersect/diff/smooth variants/onion/dilate) | Approx |
| **SDF Vector Op** — 25 modifiers | `GOO SDF Vector Op` (8: repeat, rotate, mirror, polar, twist, bend, grid, swizzle) | Approx |
| **SDF Noise** | `GOO SDF Noise` (fBM-based distance perturbation) | Approx |
| **Hex Grid Texture** — Cell UV + integer Cell ID | `GOO Hex Grid` (true hex tiling; outputs `Value`, `Color`, `Hex Coords`, `Position`, `Cell UV`, `Cell ID` + `Mask`/`Cell` aliases) | Approx |
| **Water Ripples** — Drops / Ripples / Flow / Caustic | `GOO Water Ripples` (single radial field: `Vector, Scale, Time, Wavelength, Decay, Center → Color, Height, Normal`); no mode switch | Approx |
| **Twirl** | `GOO Twirl` (real 2D rotation around a center, radius falloff) | Ported (+Radius/Angle) |
| **OKLab Color Ramp** | `GOO OKLab Color Ramp` (4 stops, exact OKLab matrix); GOO has a full ramp widget with arbitrary stops | Approx |
| **Scene Time** — Start Frame / End Frame | `GOO Scene Time` (GeometryNodeTree): native Seconds/Frame plus Start/End Frame values refreshed from `scene.frame_start/frame_end` by `frame_change_pre`/`render_pre` handlers; verified in an evaluated GN modifier | Approx |
| Float inputs accept inf / -inf | Blender socket defaults reject `inf` | N/A |

## 4. Geometry nodes / 几何节点

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| **Active camera override** — GN `Active Camera` uses `scene.gn_camera_override` when its new `Ignore Scene Override` input is off | `GOO Active Camera` group: `Ignore Scene Override` + `Override Camera` (Object) → `Active Camera` (Object), implemented with the native Active Camera node and an OBJECT switch; plus CLI `goo render --camera NAME`. Native nodes created without the group still read `scene.camera` (no C++ injection) | Approx |

## 5. Grease Pencil / 蜡笔

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| Grease Pencil 3.0 | Native in 5.2; all GOO GP tools are written against GP v3 data | Native |
| **Texture Mapping randomize** — Stroke/Fill offset, scale, rotation, seed | `goo.gp_random_texture` writes a per-stroke `goo_tg_random` (CURVE, FLOAT) attribute + seed; material reads it via `Attribute` | Approx |
| **Context-based sculpt mode smoothing** | `goo.gp_smooth_strokes`: Laplacian smoothing of the current drawing (factor, iterations, selected-only, cyclic-aware, endpoints preserved); GOO blends it into the sculpt brush instead | Approx |
| **Auto-close strokes** | `goo.gp_auto_close` sets the `cyclic` CURVE boolean on selected strokes; explicit, where GOO closes while drawing | Ported (explicit) |

## 6. Animation, rigging and viewport / 动画、绑定与视图

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| **Viewport Line Width** 1.0–3.0 | `goo_viewport_linewidth_enabled` / `goo_viewport_line_width` + `gpu.state.line_width_set` draw handler; viewport only | Ported |
| **Continuous Trackball** | Input/transform core is C++ | N/A |
| **Isolate bones overlay** | `goo.pose_isolate_bones`: hides unselected bones and restores them on a second run (GOO keeps a view-layer flag that is active only during a modal move) | Approx |
| **Hide pose bones / Outliner hidden-bones filter** | `PoseBone.hide_outliner` re-created on an ID property, set/cleared by `goo.pose_outliner_flag`, shown in the GOO Pose panel. Blender 5.2 has no Outliner row-filter hook; the GOO 4.4 public branch stores the flag and exposes the UI but has no consumer either, so the data model matches and only the row filter is unavailable | Approx |
| **Copy Rotation constraint: Invert All** | `goo.copy_rotation_invert_all` sets/clears the three per-axis invert booleans on selected bones' Copy Rotation constraints (GOO stores one `ROTLIKE_INVERT_ALL` bit) | Approx |
| **Merge Bone Constraints** | `goo.pose_constraints_merge`: copies constraints from a source armature by bone name onto the selected bones of the active rig, re-linking `target`/`space_object`; GOO also walks selected bones across multiple objects | Approx |
| Library Override Resync Enforce ignores Action ID pointers | C++ resync logic | N/A |

## 7. Interface and pipeline / 界面与流程

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| **Disable Search on Key Press** | Add-menu search is C++ | N/A |
| **Scene camera drop-down** in the top bar | `GOO View Layer Overrides` panel: native `scene.camera` + `goo.set_scene_camera`; the top bar cannot be extended from Python | Approx |
| **Transparency toggle in the World tab** | `GOO World Settings` toggles `scene.render.film_transparent` | Ported |
| **Topology Mirror** next to X/Y/Z mirror | `goo.toggle_topology_mirror` writes the native `use_mirror_topology` property | Ported |
| Option to turn off material icon rendering | C++ Outliner drawing | N/A |
| **Render burn-in** — frame + file name | `goo render --format png --burn-in`: native Blender render stamp burned into the image (file name + frame, font scaled to output height); not available for EXR or light-group composites | Ported (CLI) |
| Linked scene strips with relative paths in the VSE | C++ VSE path handling | N/A |

## 8. Platforms / 跨平台

| GOO Engine 4.4 feature | Port implementation | Status |
| --- | --- | --- |
| Windows, macOS, Linux | Pure-Python add-on + standard-library CLI; no platform binaries. Verified on Windows 11 + Blender 5.2.3 | Ported (Windows verified) |

---

## Summary / 汇总

**49** comparable entries (source diff + official page; each row is one behaviour).

| Status | Count | Rows |
| --- | --- | --- |
| Ported | 9 | Add menu, overrides UI, Twirl, GP auto-close, line width, film-transparent toggle, topology mirror, platforms, Render burn-in (CLI) |
| Native | 1 | Grease Pencil 3 |
| Approx | 25 | Shader Info, self-shadow flag, Light Groups (+CLI), Set Depth, Light Info, Light Info New, collection force-enable, Screen Space Info, Curvature, 4 SDF groups, Hex Grid, Water Ripples, OKLab ramp, Scene Time, GP randomize, camera field, Active Camera group (+CLI camera), GP smoothing, isolate bones, Invert All, merge constraints, Outliner flag |
| N/A | 14 | shadow ID buffer, contact shadows, shadow bias/cascaded/light-cache, holdout, EEVEE sample override, radiance copy, Principled/SSS internals, screen-space shadows/refraction, inf sockets, trackball, resync enforce, disable search, material icons, VSE paths |
| Not shipped | 0 | — |

Nothing is silently dropped: **N/A** rows carry a reason, and every **Approx** row maps to
a stated workaround in [compatibility.md](compatibility.md). The C++ route for the biggest
renderer gaps is specified in [native-extension.md](native-extension.md). The one remaining
UI limitation — filtering Outliner rows — is documented in the Outliner row above; the
per-bone flag itself is implemented.

* 源码核对后共 **49** 个可对照条目：9 项完整移植、1 项 5.2 原生、25 项近似、
  14 项 C++ 内部无法从 Python 触达、0 项本版本未实现。
