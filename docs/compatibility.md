# Compatibility, differences and evidence / 兼容性、差异与证据

This is the honest contract of the port. GOO Engine 4.4 is a **C++ fork of Blender 4.4
with its own EEVEE-Legacy renderer**; this project is a **pure-Python add-on for Blender
5.2 plus an external CLI**. Anything that lives inside the renderer or in Blender's
top-bar C++ UI cannot be reproduced byte-for-byte. Everything else is rebuilt from public
APIs, and every remaining difference is listed here with its workaround (if one exists).

GOO's renderer patches the exact places where Blender 5.2's EEVEE Next differs: shadow ID
buffers, per-surface shadow splits and shader depth writes. That is why the port separates
"look development" (node groups, inside Blender) from "decomposition" (the CLI renders
individual light groups and composites them).

## 1. Shader Info / 着色信息

| Aspect | GOO Engine 4.4 | This port | Why |
| --- | --- | --- | --- |
| Diffuse shading | renderer extracts EEVEE's diffuse light | `GOO Shader Info`: white Diffuse BSDF → `Shader to RGB`, luminance `L` reused by the Half-Lambert wrapper | EEVEE 5.2 still exposes `Shader to RGB`; EEVEE cannot re-light the scene from inside a shader |
| Half-Lambert | `Half-lambert factor` output, `0.5·dot(L,n)+0.5` accumulated per light | `Half-lambert factor` output, `0.5·L + 0.5` from the Shader-to-RGB luminance; the old alias `Half Lambert` was renamed | same maths, different luminance source |
| Cast shadows | per-light accumulation in `closure_eval_surface_lib.glsl`: `1 − Σ(light_fac·shadow)/Σ light_fac`, only for lights whose group-shadow bit intersects | `(1 − AO_wide) · ShadowStrength`, an **AO/contact proxy**, not a shadow-map lookup | EEVEE Next exposes no "shadows cast onto this surface only" shader socket |
| Self shadows | `calc_self_shadows_only()`: shadow-map sampling only, no distance falloff; off by default, material `Check Self Shadowing` allocates the extra storage | `(1 − AO_narrow)` behind `Check Self Shadowing` (default off, matching GOO) | no per-surface self-shadow AOV in 5.2 |
| Ambient lighting | `probe_evaluate_world_diff(n)` (world diffuse probe, separated before the ramp) | `AmbientColor · Strength · AO_wide`, added after the ramp | the world light cannot be excluded from Shader-to-RGB in 5.2 |
| Shadow IDs / `High ID Bit Depth` | 32-bit shadow IDs: `SCE_EEVEE_SHADOW_ID_HIGH_BITDEPTH = 1<<29`, `Material.check_shadow_id` | `Scene.goo_high_bit_depth` / `Material.goo_check_self_shadowing` flags only; no renderer effect | no shadow ID buffer in EEVEE Next |

**Practical consequence:** the toon look (threshold, Half-Lambert, shadow strength) is
reproducible; the *exact* cast-vs-self separation needs a render pass from the CLI
(`goo render --passes shadow` gives a scene-wide shadow matte), not a single shader graph.

## 2. Light Groups / 灯光组

GOO (verified source): name-based groups on `Material.light_groups`, `Light.light_groups`
and `ShaderNodeShaderInfo.light_groups`, packed into four int32 words
(`light_group_bits[4]`, `light_group_shadow_bits[4]`, default group bit 127) and consumed
**per light inside the shader** with a **per material-link `ignore_shadow` toggle**.

Port: the same two-sided name linking, stored as `Scene.goo_light_groups`,
`Material.goo_light_groups`, `Object.goo_light_groups` and `Object.goo_light_group`
(the light's group name). Each group owns a **detached collection** (`GOO_LG_<name>`)
assigned to that group's lights through native light linking:

```python
light.light_linking.receiver_collection = group.receiver_collection
```

Verified in EEVEE Next 5.2.3, including nested collections and detached collections
(see section 9). Detached collections are deliberate: they never appear in the user's
Outliner and cannot change scene visibility.

Differences and their reasons:

* **Shadow toggle granularity.** GOO can disable one group's shadows for one material
  (`ignore_shadow` → `light_group_shadow_bits`). Here `use_shadow` is per group (all
  receivers). Workaround: split the material,
  or render the group twice (with/without shadows) and composite per object index.
* **Empty groups.** EEVEE ignores an empty receiver collection, which would let the
  group's lights illuminate the whole scene. `sync_scene` detects this (`starved`) and
  sets `light.hide_render = True`, restoring it later via a backup custom property.
* **Ungrouped lights.** With `strict_light_groups` off (default) they keep lighting every
  object, matching GOO's default/ungrouped behaviour; the CLI puts them in the `""`
  (ambient/default) pass, rendered last.
* **Several groups on one light.** GOO evaluates all groups in one render through a
  bitmask. Blender's light linking gives a light one receiver collection, so a light in
  two groups needs the CLI to render one pass per group and add the images. A single
  group per light works fully in real time.
* **Performance.** One render per group plus a linear composite; numbers in section 9.

## 3. Set Depth / 深度前移

GOO writes the shader's pixel depth so, for example, eyebrows draw over hair in the
viewport. Blender 5.2 has **no shader depth output at all**. `GOO Set Depth` computes
`Camera Data ▸ View Z` with `Mode` (0 linear, 1 reciprocal, 2 z/Far), `Distance Scale`,
`Offset`, `Near`, `Far` and outputs `Depth`, `View Z`, `Alpha`. It can drive color/alpha
masks and be written to an EXR/AOV pass that the CLI consumes; it cannot change the
viewport depth order. This is a genuine limitation, not a hidden one.

`Mode` is an `Int` socket because 5.2 group interfaces cannot declare a Menu socket
without enum items (verified: `default_value` fails with `enum "A" not found in ()`).

## 4. Light Info / 灯光信息

GOO's `ShaderNodeLightInfo` has no inputs and outputs `Light Color`, `Light Power`,
`Perceptual Power` (the last equals power in the source); the chosen light is stored as an
Object pointer and its color/energy are baked into GPU uniforms at material compile time.
`GOO Light Info` keeps the GOO output names (`Light Color`, `Light Power`,
`Perceptual Power`) and adds `Scaled Color` / `Strength` helpers. `goo.light_info_from_active`
fills the Material node instance with five drivers:

```python
var.type = "SINGLE_PROP"
var.targets[0].id = light_object          # must be an Object, not Light data
var.targets[0].data_path = "data.energy"  # or "data.color[0..2]", "data.specular_factor"
```

Verified on 5.2.3: changing `light.data.energy` re-evaluated the shader without a
recompile (energy 10 → RGB 0.825/0.559/0.559; energy 1 → 0.492/0.397/0.397). Caveats:

* animated values only update when the depsgraph updates that property (GOO has the same
  first-keyframe requirement);
* drivers live on the group-node instance, so copying the material to another object
  keeps its own light target.

## 5. Screen space and compositing / 屏幕空间与合成

* **Screenspace Info**: GOO reads Scene Color/Depth through EEVEE Legacy's refraction
  buffer. EEVEE Next has no counterpart node. `GOO Screen Space Info` outputs Screen
  Position, Screen UV (`TexCoord.Window`), View Depth, View Distance, World Position,
  Facing, Fresnel and Camera Vector. It does **not** sample scene color/depth; use the
  CLI `depth` pass for occlusion/refraction masks.
* **Curvature**: GOO computes scene-space curvature. Here `GOO Curvature` uses a
  dual-radius AO difference plus a Fresnel rim; no curvature bake is shipped.
* **Holdout for transparent materials**: 5.2 holdout is a whole-object mask, so a
  transparent FX plane still blocks what is behind it. Not reproduced.
* **Material/world overrides per view layer**: the GOO panel edits native
  `ViewLayer.material_override`, `world_override` and `samples`; EEVEE honours the
  overrides when that view layer renders. `goo render --view-layer NAME` selects one
  layer for the whole job, so per-layer overrides can be rendered in separate runs;
  rendering all layers of a batch in one invocation is not implemented.
* **Radiance buffer copy between refraction passes**: renderer-internal, not exposed.
* **Compositor**: 5.2 uses `scene.compositing_node_group` with `CompositorNodeGroup`.
  The add-on ships `GOO Line Composite`, `GOO Kuwahara NPR`, `GOO Depth Fog` and
  `goo.setup_compositor` builds `RLayers → GOO group → NodeGroupOutput`. Pass extraction
  is done by the CLI's compositor node group, one `RLayers` socket at a time (the
  `CompositorNodeOutputFile` node only accepts multilayer EXR in 5.2).

## 6. Procedural nodes / 程序化节点

| Node | Fidelity |
| --- | --- |
| Twirl | exact: 2D rotation around a center with radius falloff (`Vector, Center, Amount`; the port adds Radius/Angle); numerically verified |
| Hex Grid | true hexagonal tiling with GOO's outputs `Value`, `Color`, `Hex Coords`, `Position`, `Cell UV`, `Cell ID` (plus `Mask`/`Cell` aliases); edge handling uses an AA width instead of GOO's exact SDF |
| Water Ripples | one radial ripple field (`Color`, `Height`, `Normal`); GOO's `Mode` (Drops / Ripples / Flow / Caustic), `Intensity`, `Speed`, `Detail`, `Bias` are not reproduced |
| SDF family | `GOO SDF Primitive` (8 representative shapes of GOO's 16 3D + 28 2D), `GOO SDF Op` (8 of 32 operations), `GOO SDF Vector Op` (8 of 25 modifiers), `GOO SDF Noise`; GOO's full enum lists are documented in [goo44-source-diff.md](goo44-source-diff.md) |
| OKLab Color Ramp | exact OKLab transfer matrix, 4 stops; GOO's node uses a full Blender ramp widget with arbitrary stops |
| Scene Time | `GOO Scene Time` tree keeps native Seconds/Frame and syncs `Start Frame`/`End Frame` value nodes from `scene.frame_start/frame_end` in `frame_change_pre`/`render_pre`; verified by evaluating a GN modifier and reading the stored attribute |

## 7. Non-renderer features / 非渲染功能

| GOO feature | Port | Difference |
| --- | --- | --- |
| Add menu "GOO Engine" section | `Add ▸ GOO Engine` + node library panel | same idea, menu layout differs |
| GP auto-close | `goo.gp_auto_close` sets the `cyclic` CURVE boolean on selected strokes | explicit operator, GOO closes while drawing |
| GP texture randomize | `goo.gp_random_texture` writes `goo_tg_random` per stroke + seed | read it with an `Attribute` node; no Texture Mapping modifier UI |
| Viewport line width | draw handler + `goo_viewport_line_width` (1.0–3.0) | viewport only, never the final render |
| F-Curve isolation | `goo.fcurve_isolate` / `goo.fcurve_restore` on the 5.2 path `action.layers[0].strips[0].channelbags[0].fcurves` | same behaviour, different data path |
| Per-view-layer overrides | GOO View Layer panel + `goo.clear_view_layer_overrides` | native Blender properties |
| Force-enable collection children | `goo.collection_force_enable_children` | explicit button; Shift-click cannot be hooked |
| Scene camera drop-down | panel field + `goo.set_scene_camera` | top bar not extendable |
| World transparency | `GOO World Settings` toggles `film_transparent` | native setting |
| Topology mirror | `goo.toggle_topology_mirror` | operator + panel button |
| Material flags | `Material.goo_check_self_shadowing`, `Scene.goo_high_bit_depth` | stored flags only |
| Active camera override | `GOO Active Camera` group + CLI `--camera` | group switches the native Active Camera with an OBJECT switch (`Ignore Scene Override` + `Override Camera`); native nodes created without the group still read `scene.camera` (no C++ injection); `goo render --camera NAME` swaps the render camera for that run |
| GP sculpt smoothing | `goo.gp_smooth_strokes` | explicit Laplacian smoothing (factor/iterations, selected-only, cyclic-aware, endpoints kept); not fused into the sculpt brush |
| Outliner hidden-bones row filtering, Continuous Trackball, Disable Search on Key Press, top-bar material icon option, VSE relative paths | **N/A** | C++ core/UI; the `hide_outliner` flag itself is re-created by the add-on. See `docs/features.md` |

## 8. CLI and add-on boundary / 外置程序与插件边界

* The add-on never launches Blender. It only edits the current file.
* The CLI never imports `bpy`; it launches Blender headless with `--factory-startup` and
  a JSON job file, then reads the JSON result. It re-uses `goo_npr.light_groups`
  (`sync_scene`, `collect_passes`) inside Blender, so there is one implementation of the
  group logic.
* Data passes (depth/normal/shadow/alpha) are always EXR even when beauty passes are PNG,
  because 8-bit sRGB would clip scene-referred depth/normal data; the manifest reports
  `valid_fraction` and masks EEVEE's `1e10` background depth.
* Installing: `goo install` copies the add-on into Blender's user `scripts/addons`,
  enables it and saves preferences; `goo bundle` writes a deterministic zip for
  Preferences ▸ Add-ons. Both were verified in an isolated user directory.

## 9. Verification evidence / 验证证据

Full command log and pixel numbers: [verification.md](verification.md).

| Test | Result |
| --- | --- |
| Light linking receiver restriction | left cube 0.542 vs right 0.1306 with a single Sun restricted to the left cube |
| Detached receiver collection | 0.5417 / 0.1306 — same as a linked collection, scene visibility untouched |
| Nested collection receiver | 0.542 / 0.1307 |
| Empty group semantics | before/after group creation means identical; an unlinked light affects both sides |
| Light-group render | A red-lit `[0.2977, 0.2750, 0.2750]`, B blue-lit `[0.2751, 0.2777, 0.2978]` |
| Light-group pass additivity | composite vs one combined render: mean abs diff **1e-6**, max **1.6e-5** |
| CLI group reconstruction | `key+fill−ambient` vs reference beauty: mean abs **4.06e-4**, max **1.95e-3** (**0.12 %**); naive sum is **81×** worse |
| Compositor pass extraction | combined/depth/normal/shadow PNGs written and read back with numpy |
| Compositor tree | `goo.setup_compositor` sets `scene.compositing_node_group`, 64 px render mean 0.358 |
| Light Info drivers | energy 10 → 0.825/0.559/0.559; energy 1 → 0.492/0.397/0.397, no recompile |
| Light Info New | 11 live drivers per node (color ×3, energy, position ×3, rotation ×3, camera-distance expression); GOO's own node leaves position/rotation/distance unwired |
| Pose tools | `goo.pose_isolate_bones` (toggle, restores), `goo.copy_rotation_invert_all` (sets/clears 3 axis booleans), `goo.pose_constraints_merge` (name-matched copy, target/space_object re-linked) |
| GP smoothing | spike (y=1.0 at the middle point) flattens to < 0.2 after one factor=1 pass; endpoints unchanged |
| CLI camera | `goo render ... --camera DemoCam` renders and records `settings.camera`; an unknown name fails with the available camera list |
| Add-on headless suite | **82 passed, 0 failed** (registration, unregister/re-register lifecycle, exact 22 groups with 17/2/3 tree-type counts, rebuild keeps datablock, light groups, render tint, compositor, overrides, camera, Active Camera switch, bilingual i18n table, GP smoothing, SDF/OKLab, Threshold Mask, Scene Time, Light Info New, pose tools, Outliner flag) |
| CLI smoke suite | **17 tests, 0 failed** (`tests/test_cli.py`; version from foreign cwd, two byte-identical 27-entry plugin bundles plus reproducible 66-file source bundles, shipped-zip install through Blender's native installer, doctor, 22-group registry, installed-copy render smoke with disable/re-enable lifecycle, apply→render→composite e2e with `--camera` and `--view-layer`, named/unknown view-layer selection, PNG burn-in plus rejected burn-in combinations, Tk GUI selftest with clean JSON stdout, Markdown+JSON matrix sync with non-empty rows and matching `status_class`, GOO 4.4 178/159 manifest check, i18n key-table completeness, relative-doc-link check, release `SHA256SUMS`/archive verification) |
| Portable path suite | **13 tests, 0 failed** (`tests/test_paths.py`; Windows/macOS/Linux config paths, per-platform install-location scans, discovery precedence and deduplication, missing explicit executable, Blender 5.2 version gate; host Python, no Blender needed) |
| Add-on discovery | `addon_utils.modules()` finds `goo_npr`, `enable()` succeeds, `bl_info["version"] == (0, 1, 0)` |
| Installed add-on | `goo install` into an isolated `BLENDER_USER_SCRIPTS`: 27 files (335,624 B), enabled, discovered and enabled by Blender |
| Fake users | 22 GOO node groups survive save + reload (`use_fake_user = True`) |
| CLI smoke | `doctor`, `nodes`, `apply`, `render --light-groups`, `composite`, `install`, `bundle`, `config`, `gui --json` all exit 0 |
| Engine enum | 5.2 uses `BLENDER_EEVEE`; `BLENDER_EEVEE_NEXT` does not exist |
| GOO 4.4 source diff | public branch `goo-engine-v4.4-release`, 19,270 files, 178 source-only, 159 source-changed; exact node/Light-Group semantics recorded in [goo44-source-diff.md](goo44-source-diff.md) |

## 10. From behaviour port to line-by-line port / 从行为移植到逐行移植

The GOO 4.4 source tree **is now part of this analysis** (`_work/goo44/`, pinned by the zip
sha256 in [goo44-source-diff.md](goo44-source-diff.md)); socket names, bit-packing and the
per-light shader formulas have been aligned where Python allows it.

What remains is not "unknown source" but **renderer access**. To close the remaining gaps,
build the C++ extension described in [native-extension.md](native-extension.md), in this
order:

1. **P1 Shader Info**: add the G-buffer auxiliary closure (or the multi-variant mask) plus
   the self-shadow map, so Cast/Self Shadows are exact.
2. **P2 Light Groups**: add `Light.goo_group_bits[4]` and consume it in
   `eevee_light_eval.bsl.hh` / `eevee_surf_shadow.bsl.hh`; replace the multi-pass CLI for
   real-time viewport use.
3. **P3 Set Depth**: add `ShaderNodeGooSetDepth` writing `gl_FragDepth` via
   `eevee_surf_depth.bsl.hh`.
4. **P4 Screen Space Info / Curvature**: expose scene color/depth and screen-space curvature
   to material shaders.

The pure-Python layer stays as the installable, portable delivery; the C++ patches are an
optional accelerator, not a dependency of the shipped add-on/CLI.

