# GOO Engine 4.4 source diff and port mapping
# GOO Engine 4.4 源码差异与移植映射

This document is the **source-verified** companion to [features.md](features.md). It
records what GOO Engine 4.4 actually changed relative to Blender 4.4.3, which of those
changes this repository reproduces, and which ones need a C++ build
(see [native-extension.md](native-extension.md)).

本文是[features.md](features.md)的**源码实证**版本：记录 GOO Engine 4.4 相对 Blender
4.4.3 的真实改动、本仓库复现了哪些、哪些必须走 C++ 编译路线。

---

## 1. Provenance / 来源与取证方法

| Item | Value |
| --- | --- |
| GOO repository | `dillongoostudios/goo-engine` |
| Branch | `goo-engine-v4.4-release` (public, downloaded 2026-11-22) |
| GOO version | 4.4.3 — `BKE_blender_version.h`: `BLENDER_VERSION 404`, `BLENDER_VERSION_PATCH 3`, `BLENDER_VERSION_CYCLE release` |
| Upstream base | Blender `v4.4.3` (`https://github.com/blender/blender`) |
| Archive | `_work/goo44/goo-engine-v4.4-release.zip`, 87,763,182 bytes, sha256 `56BEDC3A9946A40DD38EC199953CA34030E0ABF8D350AD5E30BB2D9B35E91778` |
| Extracted tree | `_work/goo44/src/goo-engine-goo-engine-v4.4-release/` (19,270 files) |
| Diff lists | `_work/goo44/diff_goo_only.txt`, `_work/goo44/diff_changed.txt` |
| Delivered manifest | [goo44-source-manifest.txt](goo44-source-manifest.txt) — the filtered **178 source-only + 159 source-changed** paths, so the diff can be audited without downloading the 87 MB archive |
| Diff generator | `_work/goo44/make_diff.py`, `_work/goo44/make_ui_diff.py` → `_work/goo44/source_diffs/*.diff`, `_work/goo44/ui_diffs/*.diff` |

Method / 方法: GitHub returned HTTP 403 for the REST API (rate limit), so the file lists
and blob hashes were read through the `ungh.cc` GitHub proxy, then compared against the
Blender `v4.4.3` tag file-by-file by blob sha. Only paths whose sha differs, or that do
not exist upstream, are counted as GOO changes.

> Because `ungh.cc` exposes a commit-less snapshot, the exact commit sha of the branch
> head is not recorded here; the archive hash above pins the analysed bytes.

### Global counts / 全库统计

| Set | Files |
| --- | --- |
| GOO tree | 19,270 |
| Blender 4.4.3 tree | 13,392 |
| Only in GOO | 5,878 (tests 5,694; `source/blender/draw` 140; `gpu` 23; `nodes` 14; misc 7) |
| Changed | 545 (release assets 340; scripts 42; draw 31; editors 29; gpu 28; makesdna 16; makesrna 13; blenkernel 11; nodes 10; blenloader/depsgraph 6 each; ...) |
| Only in Blender | **0** (GOO is a strict superset of the 4.4.3 tree) |
| `source/` only | **178** (`draw/engines` 136, `gpu/shaders` 14, `nodes/shader` 14, `draw/intern` 4, `gpu` 10) |
| `source/` changed | **159** (`draw/intern` 26, `makesrna` 12, `editors/interface` 11, `gpu/shaders` 10, `blenkernel` 7, `nodes/shader` 7, `blenloader` 6, `depsgraph` 6, ...) |

---

## 2. Renderer: a forked Legacy EEVEE / 渲染器是 Legacy EEVEE 的 fork

`source/blender/draw/engines/gooengine/` contains **41 C++ files** — a full copy of the
Blender 4.4 Legacy EEVEE engine (`eevee_*.cc`, `eevee_shaders_extra.cc`,
`eevee_lights.cc`, `eevee_materials.cc`, `eevee_shadows*.cc`), plus its own `shaders/`
directory. GOO registers this as the `BLENDER_EEVEE` engine implementation, so the
"hidden" renderer features below are modifications of that fork, not Python-level
additions.

`gooengine/shaders/closure_eval_surface_lib.glsl` (lines ~404–478) is the ground truth
for the Shader Info node:

```glsl
/* per light: skip lights that do not share a light-group bit */
if ((ld.light_group_bits & light_groups) == 0) { continue; }
...
light_fac = max(color.r, max(color.g, color.b)) * ld.l_diff;
light_accum += light_fac;
/* shadow contribution only for lights whose group-shadow bit intersects */
if ((ld.light_group_bits & light_group_shadows) != 0) {
  shadow_accum += light_fac * shadow;
}
...
shadows = 1.0 - shadow_accum / max(light_accum, 1.0);
self_shadows = calc_self_shadows_only(...);   /* shadow-map only, no falloff */
half_light += color * l_diff * light_diffuse(...);
half_lambert += 0.5 * dot(L, n) + 0.5;
ambient = probe_evaluate_world_diff(n);
```

→ GOO splits lighting **per light inside the shader**. The Python port cannot hook this
loop; it uses `Shader to RGB` + AO proxies (section 3).

---

## 3. Shader nodes added by GOO / GOO 新增着色器节点

All files below are **new** relative to Blender 4.4.3 and are registered in
`source/blender/nodes/shader/node_shader_register.cc` (+13 registrations).

| GOO node (C++ type) | Source file | Interface (verified) | Port group | Fidelity |
| --- | --- | --- | --- | --- |
| Shader Info (`ShaderNodeShaderInfo`) | `node_shader_shader_info.cc` | in: `WorldPosition`, `Normal` (hidden); out: `Diffuse Shading`(Color), `Cast Shadows`(Float), `Self Shadows`(Float), `Ambient Lighting`(Color), `Half-lambert factor`(Float). Storage: `NodeShaderInfo{light_group_bits[4], light_group_shadow_bits[4], use_own_light_groups}` | `GOO Shader Info` | Approx (AO/exposure proxy) |
| Curvature (`ShaderNodeCurvature`) | `node_shader_curvature.cc` | in: `Samples` (0–64), `Sample Radius`, `Thickness`, `Scale`(Vector); out: `Scene Curvature`, `Scene Rim` | `GOO Curvature` | Approx (AO + Fresnel) |
| Screen Space Info (`ShaderNodeScreenspaceInfo`) | `node_shader_screenspace_info.cc` | in: `View Position`; out: `Scene Color`, `Scene Depth` (samples the scene color/depth buffers) | `GOO Screen Space Info` | Approx (coordinates only; no buffer sampling) |
| Set Depth (`ShaderNodeSetDepth`) | `node_shader_set_depth.cc` + `gpu_shader_material_set_depth.glsl` | in: `Shader`, `View Depth` (hidden); out: `Shader`; writes `FRAG_DEPTH = get_depth_from_view_z(clamp(-z, near, far))` | `GOO Set Depth` | Approx (encodes depth; no depth write) |
| Light Info (legacy, `ShaderNodeLightInfo`) | `node_shader_light_info.cc` | no inputs; out: `Light Color`, `Light Power`, `Perceptual Power`; RNA `light_object`; uniforms are baked at material compile | `GOO Light Info` (+ drivers) | Approx (live drivers instead of recompile) |
| Light Info New (`ShaderNodeLightInfoNew`?) | `node_shader_light_info_new.cc` | out: `Light Color`, `Light Power`, `Light Position`, `Light Rotation`, `Light Distance`. Note: the 4.4 GPU function only passes color/power to `node_light_info_simple`; position/rotation/distance are declared but unwired | `GOO Light Info New` (+ drivers) | Approx (all five outputs driven live; distance = camera→light, an extension) |
| Twirl (`ShaderNodeTwirl`) | `node_shader_twirl.cc` | in: `Vector`, `Center`, `Amount`; out: `Vector` | `GOO Twirl` | Ported (+ extra Radius/Angle) |
| Water Ripples (`ShaderNodeWaterRipples`) | `node_shader_water_ripples.cc` | in: `Vector`, `Time`, `Mode` (Drops/Ripples/Flow/Caustic), `Scale`, `Intensity`, `Speed`, `Detail`, `Bias`; out: `Distorted Vector`, `Mask` | `GOO Water Ripples` | Approx (single radial mode) |
| Tex Hexagon (`ShaderNodeTexHexagon`) | `node_shader_tex_hexagon.cc` | in: `Vector`, `Scale`, `Size`, `Radius`, `Roundness`; out: `Value`, `Color`, `Hex Coords`, `Position`, `Cell UV`, `Cell ID` | `GOO Hex Grid` | Approx (all GOO outputs present; edges AA instead of exact SDF) |
| OKLab Color Ramp (`ShaderNodeOkLabColorRamp`) | `node_shader_oklab_color_ramp.cc` | in: `Fac` + `color_ramp` (RNA pointer); out: `Color`, `Alpha` | `GOO OKLab Color Ramp` | Approx (4-stop UI) |
| SDF Primitive (`ShaderNodeSdfPrimitive`) | `node_shader_sdf_primitive.cc` | in: `Vector`, `Size`, `Radius`, `Value1-4`, `Point` ×4, `Angle`, `Roundness`, `Linewidth`; out: `Distance`. 16 3D + 28 2D shapes | `GOO SDF Primitive` | Approx (8 representative shapes) |
| SDF Op (`ShaderNodeSdfOp`) | `node_shader_sdf_op.cc` | in: `Distance`, `Distance_001`, `Value`, `Value_001`, `Count`; out: `Distance`; 32 operations | `GOO SDF Op` | Approx (8 operations) |
| SDF Vector Op (`ShaderNodeSdfVectorOp`) | `node_shader_sdf_vector_op.cc` | in: `Vector1-3`, `Scale`, `Value1-2`, `Angle`, `Count1-2`; out: `Vector`, `Position`, `Value`; 25 modifiers | `GOO SDF Vector Op` | Approx (8 modifiers) |
| SDF Noise (`ShaderNodeSdfNoise`) | `node_shader_sdf_noise.cc` | in: `Position`, `Distance`, `Detail`, `Roughness`, `Detail Inflation`, `Detail Blend`; out: `Distance` | `GOO SDF Noise` | Approx (fBM-based) |

### Menu / UI changes / 菜单与界面

* `scripts/startup/bl_ui/node_add_menu_shader.py` adds `NODE_MT_category_goo_engine`
  with Shader Info, Screenspace Info, Curvature, Light Info, Set Depth, four SDF nodes,
  Twirl and Water Ripples; `ShaderNodeTexHexagon` and `ShaderNodeOkLabColorRamp` are
  inserted into the normal Shader/Geometry menus.
* Its `eevee_shader_nodes_poll` accepts both `BLENDER_EEVEE` and `BLENDER_EEVEE_NEXT`.
* Python port: `goo_npr/add_menu.py` creates the `Add ▸ GOO Engine` sub-menu and
  `goo_npr/ui/panels.py` the node-library sidebar (see [features.md](features.md)).

---

## 4. Shader Info: exact GOO semantics vs the port / 精确语义与移植偏差

GOO's node is evaluated inside `closure_eval_surface_lib.glsl` for every light:

| Output | GOO formula (source) | Python port approximation |
| --- | --- | --- |
| `Diffuse Shading` | sum over lights of `color * l_diff * light_diffuse(...)`; only lights whose `light_group_bits` intersect the material's bits | white Diffuse BSDF → `Shader to RGB` → luminance |
| `Cast Shadows` | `1 - Σ(light_fac·shadow)/max(Σ light_fac,1)` for lights in the group *and* whose group-shadow bit intersects | `(1 - AO_wide) * ShadowStrength` |
| `Self Shadows` | `calc_self_shadows_only(...)`: shadow-map sampling only, **no distance falloff**; default off; material `Check Self Shadowing` enables the storage | `(1 - AO_narrow)`, gated by `Check Self Shadowing` input |
| `Ambient Lighting` | `probe_evaluate_world_diff(n)` (world probe, diffuse) | `AmbientColor * AmbientStrength * AO_wide` |
| `Half-lambert factor` | `0.5*dot(L,n)+0.5` accumulated per light | `0.5*L+0.5` from the shader-to-RGB luminance |

Consequence: the port's Shader Info is an **approximation of the channels**, not a
re-shading of the scene. `Cast Shadows` equals `0` when the AO distance is large relative
to the object; it is not per-light.

### Light group settings on Material / 材质灯光组

GOO adds to `DNA_material_types.h`:

```c
char check_shadow_id;               /* "Check Self Shadowing" */
int  light_group_bits[4];
int  light_group_shadow_bits[4];    /* MA_GROUPS_ALL default */
```

and to `DNA_light_types.h`:

```c
int light_group_bits[4];            /* LA_GROUPS_ALL default */
```

The Shader Info node storage carries the same four ints plus `use_own_light_groups`.
`eevee_lights.cc` copies `light->light_group_bits` into `evli->light_group_bits`; the
material bits feed the closure loop. The Python port stores the equivalent state on
`Material.goo_light_groups` / `Object.goo_light_groups` and renders one pass per group
instead of a bitmask (see section 5).

---

## 5. Light Groups: exact GOO implementation / GOO 灯光组精确实现

Source: `scripts/startup/goo_engine_light_groups.py` (441 lines, new file).

| Concept | GOO implementation | Port implementation |
| --- | --- | --- |
| Group item | `LightGroup{StringProperty name, viz_name, BoolProperty ignore_shadow}` | `GOO_LightGroup{name, use_shadow, receiver_collection}` |
| Group list | `LightGroups{CollectionProperty groups, IntProperty group_index, BoolProperty use_default=True, BoolProperty ignore_default_shadow}` | `Scene.goo_light_groups` + `goo_light_groups_index` + `goo_auto_sync` |
| Owners | `Material.light_groups`, `Light.light_groups`, `ShaderNodeShaderInfo.light_groups` | `Material.goo_light_groups`, `Object.goo_light_groups`, per-light `Object.goo_light_group` |
| Bit packing | `set_bit(vec, bit)`: `index=bit//32`, `mask=1<<((bit+1)%32)`, 4×int32; default group reserved at `MAX_LIGHT_GROUP_BIT=127` | not used; native `Light.light_linking.receiver_collection` per group + multi-pass |
| Default group | `use_default=True` → all lights not listed still light the material (bit 127) | lenient mode: objects with no link receive every group |
| Shadow toggle | `ignore_shadow` toggles the material's `light_group_shadow_bits`; group bits still include the light | group `use_shadow` is written to `Light.use_shadow` with backup/restore (`goo_orig_use_shadow`) |
| Sync | handlers `render_init`, `load_post`, `depsgraph_update_post` (skipped during render; triggered by Material/Light updates) | `bpy.app.handlers.render_pre` + explicit `goo.light_group_sync` |
| Operators | `light_groups.new/link/remove/unlink/resync` | `goo.light_group_add/remove/sync/assign/assign_lights/unassign/select` |
| Panels | Material, Light, Node Editor (uses `active_node.light_groups`) | Material, Light (prop_search), Object; `GOO_UL_light_groups` list |

**Verification / 验证**: with one red key light linked to object A and one blue fill to
object B, the port yields A `[0.2977, 0.2750, 0.2750]` (red) and B
`[0.2751, 0.2777, 0.2978]` (blue). A per-group render + linear composite reproduces the
single combined render to `mean abs diff 1e-6`, `max abs diff 1.6e-5`
(`_work/lead/compare_images.py`, [verification.md](verification.md)).

---

## 6. Non-render changes / 非渲染改动

| GOO feature | Source evidence (4.4.3) | Port status |
| --- | --- | --- |
| 32-bit shadow IDs (`High ID Bit Depth`) | `DNA_scene_types.h:2945 SCE_EEVEE_SHADOW_ID_HIGH_BITDEPTH = 1<<29`; `properties_render.py:1316 use_shadow_id_high_bitdepth`; `DNA_material_types.h:225 check_shadow_id` | N/A (EEVEE Next has no shadow ID buffer); stored as `Scene.goo_high_bit_depth` + material flag |
| Contact shadows | `DNA_light_types.h`: `use_contact_shadow`, `contact_shadow_distance/bias/thickness`; `properties_data_light.py` panel | N/A (renderer); UI fields not exposed |
| Shadow buffer bias / cascaded shadow maps | `Light.shadow_buffer_bias`, `DATA_PT_EEVEE_shadow` UI, `render_shading.cc` light-cache bake UI | N/A (renderer internals) |
| Material `Check Self Shadowing` | `check_shadow_id` + material panel | Approx flag: `Material.goo_check_self_shadowing` |
| Viewport line width 1.0–3.0 | `DNA_userdef_types.h:905`, `rna_userdef.cc:5149`, `draw/intern/draw_common.cc:199` (`gb->size_viewport_line = max_ff(U.viewport_line_width,1.0f)`) | Ported (`goo_viewport_line_width` + draw handler) |
| Topology mirror toggle | `use_mirror_topology` in mesh RNA + UI | Ported (native 5.2 property, `goo.toggle_topology_mirror`) |
| GP auto-close strokes | `use_gpencil_autoclose_strokes` preference + GP code | Approx (explicit `goo.gp_auto_close`) |
| Context-based GP sculpt smoothing | `use_gpencil_sculpt_smooth` context path in GP code | Approx: `goo.gp_smooth_strokes` (Laplacian, factor/iterations, cyclic-aware); not fused into the brush |
| Isolate bones overlay | `overlay.isolate_bones` + UI | Approx: `goo.pose_isolate_bones` hides real bone visibility and restores |
| Hide pose bones / Outliner filter | `hide_outliner` (`PCHAN_DRAW_SHOW_OUTLINER`, `DNA_action_types.h:494`), bone-properties UI; the downloaded 4.4 branch contains **no consumer** of the flag outside DNA/RNA/UI | Approx: `PoseBone.hide_outliner` re-created on an ID property + `goo.pose_outliner_flag`; Outliner row filtering has no 5.2 Python hook, but the flag/data model matches |
| Copy Rotation `Invert All` | `invert_all` property in constraint RNA | Approx: `goo.copy_rotation_invert_all` sets the three per-axis booleans |
| Merge bone constraints | `pose.constraints_merge` (source-object enum, name matching, ID relink) | Approx: `goo.pose_constraints_merge` copies by bone name and re-links `target`/`space_object`; one destination rig at a time |
| Camera override for GN Active Camera | `node_geo_input_active_camera.cc`: new input `Ignore Scene Override` (default true) + `scene->gn_camera_override`; field also in `DNA_scene_types.h:2046` / `properties_scene.py:52` | Approx: `GOO Active Camera` group switches between native Active Camera and an Object socket `Override Camera`; CLI `goo render --camera NAME`; native nodes created without the group still read `scene.camera` |
| Scene Time Start/End Frame | `node_geo_input_scene_time.cc`: adds `Start Frame` = `scene->r.sfra`, `End Frame` = `scene->r.efra` | Approx: `GOO Scene Time` re-exposes native Seconds/Frame and syncs two value nodes from the scene in `frame_change_pre`/`render_pre` (evaluated-GN verified) |
| Principled/SSS shader internals | `node_shader_bsdf_principled.cc` adds `GPU_MATFLAG_PRINCIPLED_*` and `use_sss` uniforms; `node_shader_subsurface_scattering.cc` adds `use_sss` | N/A (C++ renderer) |
| Set Depth participates in output/link checks | `node_shader_tree.cc` adds `SH_NODE_SET_DEPTH` to output/link validation | Approx (API group; no depth write) |

Also verified as real 4.4.3 additions (ported or documented):
`viewport_line_width` User Preference, `use_mirror_topology`, `use_gpencil_autoclose_strokes`,
`overlay.isolate_bones`, `pose.constraints_merge`, Copy Rotation `invert_all`, `hide_outliner`.

---

## 7. What pure Python cannot reproduce / 纯 Python 无法复现的部分

1. **Per-light shader loop** (Shader Info, exact Cast/Self Shadows, group bitmask):
   requires the `gooengine` EEVEE fork or a 5.2 EEVEE patch — see
   [native-extension.md](native-extension.md) P1/P2.
2. **True depth write** (`Set Depth`): needs a shader node with `gl_FragDepth` support
   (P3).
3. **Scene buffer sampling** (`Screen Space Info` Scene Color/Depth): EEVEE Next does not
   expose scene color/depth to material shaders (P4).
4. **Shadow ID buffer / contact shadows / cascaded shadow UI**: renderer internals.
5. **GN `Active Camera` override input**: native C++ node input; the port's
   `GOO Active Camera` group reproduces the switch for nodes that use it, and
   `goo render --camera` covers the CLI case. **Scene Time extra outputs** cannot be added
   to the native node either, but `GOO Scene Time` reproduces them with a group plus a
   frame handler. **GP sculpt smoothing** is a brush-internal path, reproduced as an
   explicit smoothing operator.

Everything else in GOO 4.4 is either reproduced (Ported/Approx), provided by the CLI
(multi-pass, compositing, install/bundle), or listed as N/A with the reason above.

---

## 8. Reproducing this analysis / 复现方式

```powershell
# 1) download the public 4.4 branch (codeload)
curl -L -o _work/goo44/goo-engine-v4.4-release.zip `
  https://codeload.github.com/dillongoostudios/goo-engine/zip/refs/heads/goo-engine-v4.4-release
# 2) extract and compare (scripts in _work/goo44)
python _work/goo44/make_diff.py
python _work/goo44/make_ui_diff.py
# 3) inspect a specific diff
Get-Content _work/goo44/source_diffs/node_shader_register.cc.diff
```

The comparison scripts use `https://ungh.cc/repos/dillongoostudios/goo-engine/files/<branch>`
(no API token) and `https://raw.githubusercontent.com/blender/blender/v4.4.3/...` as the
upstream baseline.
