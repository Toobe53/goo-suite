# Optional native extension plan / 可选原生扩展方案

The shipped port is an **add-on + external CLI** and runs on stock Blender 5.2. This
document describes the optional next step: a small C++ patch set that restores the
renderer-level semantics GOO 4.4 has and Python cannot express. It is a design plan, not
shipped code — nothing here is compiled or required by the current add-on.

当前交付是「插件 + 外置 CLI」，可直接跑在官方 Blender 5.2 上。本文是可选的原生扩展
方案：用少量 C++ 补丁恢复 GOO 4.4 的渲染器级语义（纯 Python 无法表达的部分）。这是
设计文档，不是已编译代码；当前插件不依赖它。

## 1. Decision matrix / 何时值得做原生

| Need | Add-on + CLI (current) | Native patch |
| --- | --- | --- |
| Named light groups, per-group shadow toggle | exact final image (1e-6), 1 render per group | in-renderer bitmask, single render |
| Shader Info: diffuse vs cast vs self shadow | AO proxies, documented | true closure/shadow separation |
| Set Depth (write depth from shader) | depth encoded to color; CLI reads real depth pass | real depth write |
| Scene Color / Depth sampling inside shaders | not available | possible via prepass bindings |
| Performance on many groups / animation | linear in group count | single pass |
| Pipeline / farm compatibility | stock Blender, any 5.2 install | custom build on every machine |

Use the native path only if a production requires renderer-exact behaviour or single-pass
performance; otherwise the add-on path is the portable one.

## 2. Target: EEVEE Next, not EEVEE Legacy / 目标引擎

GOO 4.4 is based on **EEVEE Legacy**. Blender 5.2 only ships **EEVEE Next** (engine id
`BLENDER_EEVEE`). Re-introducing Legacy would mean carrying an entire removed renderer;
the plan instead ports the *features* onto EEVEE Next.

Verified 5.2.3 source locations (checked against `blender/blender` tag `v5.2.3`):

```
source/blender/draw/engines/eevee/eevee_material.cc|hh
source/blender/draw/engines/eevee/eevee_light.cc|hh
source/blender/draw/engines/eevee/eevee_light_shared.hh
source/blender/draw/engines/eevee/eevee_shadow.cc|hh
source/blender/draw/engines/eevee/eevee_shader.cc|hh
source/blender/draw/engines/eevee/eevee_instance.cc|hh
source/blender/draw/engines/eevee/shaders/eevee_light_eval.bsl.hh
source/blender/draw/engines/eevee/shaders/eevee_light_lib.bsl.hh
source/blender/draw/engines/eevee/shaders/eevee_shadow.bsl.hh
source/blender/draw/engines/eevee/shaders/eevee_surf_shadow.bsl.hh
source/blender/draw/engines/eevee/shaders/eevee_surf_depth.bsl.hh
source/blender/draw/engines/eevee/shaders/eevee_surf_forward.bsl.hh
source/blender/draw/engines/eevee/shaders/eevee_nodetree_closures_lib.glsl
source/blender/draw/engines/eevee/shaders/infos/eevee_nodetree_infos.hh
source/blender/nodes/shader/nodes/node_shader_shader_to_rgb.cc   (template for a new node)
source/blender/nodes/shader/node_shader_register.cc|hh
source/blender/makesdna/DNA_node_types.h
source/blender/makesdna/DNA_light_types.h
source/blender/makesrna/intern/rna_nodetree.cc
source/blender/makesrna/intern/rna_light.cc
source/blender/gpu/intern/gpu_node_graph.cc|hh
source/blender/gpu/intern/gpu_material.cc
scripts/startup/bl_ui/node_add_menu_shader.py                     (Add-menu entry)
```

If a path moves in a later 5.2.x, locate it with:

```bash
rg -n "eLightType|LightData" source/blender/draw/engines/eevee
rg -n "ShaderToRGB|SH_NODE_SHADER_TO_RGB" source/blender
rg -n "SHD_SHADER_TO_RGB|shader_to_rgb" source/blender/nodes source/blender/makesdna
```

## 3. Patch series / 补丁拆解

Keep each item an independent, reviewable commit; all are GPL-2.0-or-later compatible
with Blender's licence.

### P1 — `Shader Info` node (the hard one)

1. **DNA/RNA**: add `ShaderNodeShaderInfo` to `DNA_node_types.h`, outputs matching the
   add-on group (`Diffuse Shading`, `Cast Shadows`, `Self Shadows`, `Ambient Lighting`,
   `Half Lambert`), register in `rna_nodetree.cc`, add the node to
   `node_shader_register.cc`, and an entry in `node_add_menu_shader.py`.
2. **GLSL codegen**: EEVEE compiles the node tree through `gpu_node_graph.cc` /
   `gpu_material.cc`. A `ShaderInfo` node cannot return the final closure — it needs the
   renderer's per-closure accumulators. Two viable strategies:
   * **A (recommended): auxiliary G-buffer targets.** Extend the material G-buffer
     (`eevee_material.cc`, `eevee_material_shared.hh`, `eevee_nodetree_closures_lib.glsl`)
     with `diffuse_shading`, `cast_shadow`, `self_shadow`, `ambient` channels. The node
     reads the channel selected by its output socket; shadow channels are filled by
     `eevee_surf_shadow.bsl.hh` / `eevee_shadow.bsl.hh`.
   * **B: multi-evaluation masks.** Compile the surface shader in 4 variants with a
     uniform `goo_shader_info_mask` and sum the results. Cheaper to implement, linear
     cost, close to what the CLI does today — good fallback for a first patch.
3. **Self vs cast shadow**: cast shadows come from other objects; self shadows require
   the receiver to be in its own shadow map (or a depth comparison against the same
   object). `eevee_shadow.cc` already tracks receiver/blocker sets — add an object flag
   and a shader branch for the self-shadow map. GOO exposes this as a material option
   ("Check Self Shadowing"), i.e. the same flag the add-on already stores
   (`Material.goo_check_self_shadowing`).
4. **Half-Lambert**: pure shader math on the diffuse channel, no renderer change.

Estimated effort: 2–4 weeks for one renderer engineer, including regression renders.

### P2 — Light Groups as a renderer bitmask

1. **DNA**: `Light.goo_group_bits` (`uint32`) and `Light.use_shadow` already exists;
   add per-group shadow flags. `Object`/`Material` get a receiver mask (reuse the
   `ObjectLightLinking` collections as the authoring UI, or store the mask directly).
2. **Light upload**: `eevee_light.cc` already packs lights into `eevee_light_shared.hh`;
   add the mask there and to the light-culling path.
3. **Shading**: in `eevee_light_eval.bsl.hh` / `eevee_light_lib.bsl.hh`, multiply the
   light contribution by `(receiver_mask & light_mask) != 0`. Per-group shadow toggle
   becomes a bit test in the shadow lookup instead of a render pass.
4. **UI**: expose groups through the existing add-on panels; the patch only consumes the
   IDs, so scenes authored today keep working.

Estimated effort: 1–2 weeks. Removes the CLI's linear cost and the 1e-6 residual.

### P3 — `Set Depth` real depth write

1. Add an output socket to the material output node (`DNA_node_types.h`,
   `rna_nodetree.cc`) and a node that feeds it (`node_shader_set_depth.cc`).
2. In `eevee_surf_depth.bsl.hh` write `gl_FragDepth` from the socket when connected;
   keep the early-Z path untouched for materials that do not use it.
3. Update `eevee_material.cc` variant hashing so depth-writing materials compile the
   correct shader.

Estimated effort: ~1 week, plus driver-specific testing (depth writes disable early-Z).

### P4 — Curvature / Screen Space / Scene Color (optional)

These can be approximated by prepasses (curvature from normal+depth, scene color from a
downsampled copy) exposed as renderer-owned textures. Only worth it if production needs
them animated or in reflections; otherwise the add-on approximations stay.

### P5 — Outliner hidden-bones filter (non-renderer, optional)

GOO's `PoseBone.hide_outliner` maps to `PCHAN_DRAW_SHOW_OUTLINER`
(`DNA_action_types.h:494`), and the public 4.4 branch exposes it in the bone properties UI.
It has no consumer outside DNA/RNA/UI in the public source, so the Python port already
matches the available data behaviour by re-creating `PoseBone.hide_outliner` on an ID
property (`goo.pose_outliner_flag`). A native Outliner row filter would additionally need
a 5.2 `PoseBone` draw flag, a filter toggle in the Outliner, and a check in
`outliner_draw.cc`; this is UI-only and carries none of the renderer risk of P1–P4.

## 4. Build & distribution / 构建与分发

```bash
# Linux/macOS
git clone https://projects.blender.org/blender/blender.git
cd blender && git checkout v5.2.3
git am /path/to/goo-port/native/patches/*.patch
make update && make -j"$(nproc)"

# Windows (Developer Command Prompt)
git clone https://projects.blender.org/blender/blender.git
cd blender && git checkout v5.2.3
git am ..\goo-port\native\patches\*.patch
make update && make -j%NUMBER_OF_PROCESSORS%
```

Distribution requirements:

* Pin the exact Blender tag; a custom build is a *platform*, not a drop-in add-on.
* Name the build `Blender 5.2.x + GOO-port native` and expose
  `bpy.app.goo_native_version` or a `ShaderNodeGooShaderInfo` type so the add-on can
  detect it: `getattr(bpy.types, "ShaderNodeGooShaderInfo", None)`.
* The add-on keeps working on stock Blender; only the renderer-level features switch to
  native paths when detected.
* CI: build on Windows/Linux/macOS, run the existing 37 headless tests plus render
  regression scenes; publish checksums.

## 5. Risks / 风险

| Risk | Impact | Mitigation |
| --- | --- | --- |
| EEVEE Next shader ABI changes between 5.2.x | patch stops applying | pin tag, keep patches per-version |
| Depth writes break early-Z / performance | slower renders | only compile the depth-write variant for flagged materials |
| G-buffer channel budget | banding / memory | pack shadow masks into one RGBA channel, reuse unused bits |
| `.blend` compatibility | files opened without the build lose semantics | store masks as custom properties fallback; add-on can regenerate |
| Maintenance burden | fork drift | keep patch series minimal and documented; upstream what is generic |

## 6. What stays in Python / 仍然留在 Python 的部分

Even with the native build, the add-on keeps: light-group authoring UI, node library and
menu, GP/F-Curve/line-width tools, CLI (install, batch render, composite fallback),
bilingual docs and tests. The native patch only replaces the renderer bridge.
