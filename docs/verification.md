# Verification / 验证记录

## GOO Suite 1.0.0 release verification / 发布验证

The merged **GOO Suite** tree is at `goo-suite/` and ships one add-on zip and one
wheel built from the same commit. The numbers below are from the final 1.0.0
build; older sections in this file describe the GOO Port add-on and remain valid
for the add-on internals.

| Check | Result |
| --- | --- |
| Full Python suite | `128 tests, OK (skipped=4)`; skips are GUI without tkinter and Blender-only checks |
| Blender add-on checks | `82 passed, 0 failed` with `--factory-startup --background` |
| Portable path tests | 13 tests, OK |
| Add-on zip | `goo_npr-1.0.0.zip` — 27 files; SHA-256 in `dist/SHA256SUMS` |
| Wheel | `goo_suite-1.0.0-py3-none-any.whl` — 77 entries; SHA-256 in `dist/SHA256SUMS` |
| Source zip | `goo-suite-1.0.0-src.zip` — 114 files; SHA-256 in `dist/SHA256SUMS` |
| `goo verify` | 3 files, 27/77/114 entries, SHA-256 and required members OK |
| Wheel install smoke | installed into a clean target, `goo 1.0.0`, `goo-bridge 1.0.0`, `goo doctor --json` finds Blender 5.2.3 |

The wheel smoke test is deliberately run outside the source tree so the installed
copy, not the checkout, resolves the entry points. The `goo-bridge` entry point is
kept for one release and reads the new `GOO Suite` config directory (migrating the
old file on save).

All numbers below were produced on this machine, not estimated. Environment:

* Windows 11, Blender **5.2.3 LTS** (hash `e13b728c6880`, built 2026-10-22),
  bundled Python **3.13.13**, numpy **2.3.4**.
* Repository root `goo-port/`; the add-on is loaded from source (`sys.path`) by the
  tests, and the CLI runs with the host Python
  `C:\Users\zbq\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`
  (3.12.14), which in turn launches Blender headless.
* Reproduce: `blender --factory-startup --background --python tests/test_addon.py`
  (add-on suite) and `python tests/test_cli.py` (CLI smoke tests), then the CLI commands
  quoted in each section.

## 1. Blender 5.2 API probes / API 探测

| Probe | Result |
| --- | --- |
| Render engines | `('BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES')` — **no `BLENDER_EEVEE_NEXT`, no EEVEE Legacy** |
| Light linking | `Light.light_linking.receiver_collection` works and is honoured by EEVEE |
| Detached collection receiver | works: means `[0.5417, 0.1306]` with/without linking the collection to the scene |
| Nested collection receiver | works: `[0.542, 0.1307]` |
| Empty receiver collection | **ignored by EEVEE** (light illuminates the whole scene); handled by `hide_render` in `sync_scene` |
| Light Info driver target | must be the light **Object** (`data.energy`, `data.color[i]`, `data.specular_factor`) |
| Driver live update | energy 10 → RGB `0.825/0.559/0.559`; energy 1 → `0.492/0.397/0.397`, no shader recompile |
| Compositor tree | `scene.compositing_node_group = bpy.data.node_groups.new(..., "CompositorNodeTree")` |
| Group node in compositor | `CompositorNodeGroup` (not `CompositorNodeNodeGroup`) |
| Pass extraction | `CompositorNodeRLayers` sockets appear only for enabled passes; one socket → `NodeGroupOutput` + PNG render works for combined/depth/normal/shadow |
| `NodeGroupOutput` types | a VALUE-typed socket silently renders the combined image in 5.2.3; the CLI always types it **Color** |
| `CompositorNodeOutputFile` | `file_format` only accepts `OPEN_EXR_MULTILAYER` on that node; multi-slot = `file_output_items.new(name)` |
| AOV | `view_layer.aovs.add()`; `aov.type="COLOR"`; socket appears in RLayers with `aov_socket=True` |
| 5.2 Action F-Curves | `action.layers[0].strips[0].channelbags[0].fcurves` (no `action.fcurves`) |
| GP v3 | `bpy.data.grease_pencils`, `GREASEPENCIL` object type, `stroke.cyclic` backed by a CURVE `BOOLEAN` attribute |
| Shader node groups | `tree.interface.new_socket(...)`; `Interface` is functional even though `hasattr(NodeTree, "interface")` is False |
| Object Info | has a `Location` output on 5.2 (used by Light Info fallback) |
| `bl_info` parsing | Blender parses it with literal-only AST rules; `"version": ADDON_VERSION` makes the module disappear from `addon_utils.modules()` |
| `use_fake_user` | zero-user node groups are purged on save unless `use_fake_user = True` |

## 2. Light Groups / 灯光组

Scene: cubes A (x=-1.8) and B (x=+1.8), red Sun "key" linked to A's material,
blue Sun "fill" linked to B's material, orthographic camera, 64×64 EEVEE, 16 samples.

| Check | Result |
| --- | --- |
| Receiver restriction | A mean RGB `[0.2977, 0.2750, 0.2750]` (red), B `[0.2751, 0.2777, 0.2978]` (blue) |
| Empty group | does not light the scene (green light stayed out after the `hide_render` fix) |
| Unlinked lights | still light both objects (matches GOO's "default group") |
| `collect_passes` | one pass per non-empty group plus the default pass for ungrouped lights |
| `sync_scene` return | `groups 2, lights_linked 2, receivers {Key:3, Rim:3}` on the CLI fixture |
| CLI pass render | `key` mean `[0.129, 0.060, 0.060]` (red), `fill` `[0.060, 0.064, 0.129]` (blue), `ambient` `[0.0478, 0.0478, 0.0478]` |
| Additivity of passes | composite of `key+fill+ambient` vs one combined render: **mean abs diff 1e-6, max abs diff 1.6e-5** |
| Composite image | `_work/lead/synced_groups/composite.png` — left cube red, right cube blue, neutral background |

The additivity result is the important one: it proves the CLI pipeline (sync → render
each group → linear sum) reproduces the single combined GOO-style render to floating
point precision, without relying on renderer-internal bitmasks.

## 3. Add-on headless test / 插件无头测试

`tests/test_addon.py`, run with `--factory-startup --background`:

```
82 passed, 0 failed
```

Covers: add-on register, literal `bl_info` version, operator presence, disable/re-enable lifecycle (classes unregistered and restored), all **22** node
groups built (17 shader + 2 geometry + 3 compositor, including the four SDF groups, both
OKLab groups, Threshold Mask, both Light Info nodes, Scene Time and Active Camera), the
exact tree-type split 17/2/3 and the absence of extra groups, rebuild keeps the
node-tree datablock, `use_fake_user` protection, every shader group instantiates in a
material, `sync_scene` receiver counts (1/1), empty-group semantics, `collect_passes`, a real
64×64 render with per-group tint (mean `[0.3412, 0.2922, 0.3409]`), `goo.setup_compositor`,
all `overrides.py` operators/properties (clear, force-enable, topology mirror, scene camera,
film transparent), the **Light Info New** operator (11 drivers: color ×3, energy,
position ×3, rotation ×3, camera-distance expression with 6 variables), the
**GOO Active Camera** group (OBJECT switch + group output link), the **bilingual i18n
table** (every registered key has `en_US` and `zh_CN`, including all five Active Camera
keys), **GP smoothing**
(spike flattened, endpoints preserved), the **pose tools** (merge constraints with
target re-linking, Copy Rotation Invert All, isolate-bones hide/restore), and the
**Outliner flag** (`PoseBone.hide_outliner` re-created on an ID property, set/cleared
by `goo.pose_outliner_flag`; 2 checks).

An isolated install (27 files, 335,624 B) was also exercised through Blender's real
add-on lifecycle: `addon_utils.enable('goo_npr')` → `disable` → `enable`. The pose and
scene properties disappear on disable and return on re-enable, and
`bpy.ops.goo.build_library` remains available.

UI/tools self-test (menus, panels, GP v3 auto-close/randomize, F-Curve isolation, line
width, register cycle): **252/252 checks passed**.

### 3.1 CLI smoke tests / CLI 冒烟测试

`tests/test_cli.py` runs with any Python ≥ 3.10 (the CLI itself has no `bpy`
dependency), from a **foreign working directory** with only `PYTHONPATH` pointing at the
checkout:

```
python tests/test_cli.py
17 tests, 31.8 s, OK
```

Portable path and discovery tests run on the host Python without Blender:

```
python tests/test_paths.py
13 tests, 0.02 s, OK
```

They cover the Windows `%APPDATA%` / `%LOCALAPPDATA%` branches, macOS
`~/Library/Application Support`, Linux `$XDG_CONFIG_HOME` and `~/.config` fallback,
discovery precedence (explicit path → `GOO_BLENDER` → config → install paths → `PATH`)
with case-insensitive deduplication, per-platform install-location scans
(`Blender Foundation/Blender 5.x` under `%PROGRAMFILES%`, `/Applications/Blender*.app`,
system bins plus `/opt/blender*/`), the hard error for a missing explicit executable,
and the Blender 5.2 version gate.

Covers: `goo version --json` resolves the checkout from another cwd; `goo bundle` emits
exactly **27** entries with `goo_npr/nodes/camera.py` and `goo_npr/tools/pose_tools.py`
and no `__pycache__`, and two builds are **byte-identical** (same SHA-256);
`goo bundle --source` likewise builds a reproducible **66-file** source archive with
`README.md`, `docs/feature-matrix.json`, the add-on, docs and tests (including the demo
`.blend`) and rejects `--source --addon`; `goo verify --json` re-hashes every
`dist/SHA256SUMS` entry, opens the plugin/wheel/source archives and checks their
required members (including `goo/verify.py` in the wheel and source archive), and
exits 2 when the sums file is missing or a listed hash is
tampered with (the error names the mismatching artifact and both digests); `goo doctor --json` reports Blender **5.2.3** and an existing add-on
path; `goo nodes ... --json` reports `spec_count: 22` including `GOO Active Camera` and
`GOO Light Info New`; the 49-row `docs/features.md` inventory and its machine-readable
`docs/feature-matrix.json` export agree on exactly
**Ported 9 / Native 1 / Approx 25 / N/A 14 / Not shipped 0**, every JSON row
carries its Markdown section (all eight sections, no `null` entries), its
`feature` / `implementation` fields are non-empty, and its `status_class`
matches the leading token of `status`; the delivered
`docs/goo44-source-manifest.txt` parses to exactly **178 source-only + 159 source-changed**
paths; the primary pipeline `goo apply` → `goo render --light-groups --camera DemoCam
--view-layer ViewLayer` (2 samples, 25%, 64×64) → `goo composite --png` produces key/fill/ambient
passes, `composite.png` + `composite.exr`, and records the selected camera and view
layer in `rendered["settings"]`; a second temporary .blend with an `Alt` view layer
renders through `--view-layer Alt` and reports `settings.view_layer: "Alt"`, while an
unknown layer fails with exit 4 and a message listing the available layers; native
burn-in renders `beauty.png` with
`settings.burn_in = true` and rejects `--format exr --burn-in` and
`--format png --burn-in --light-groups` with exit code 2; every literal
`t()` / `tr()` key in `goo_npr/**/*.py` has a matching bilingual table entry
(136 defined keys, 121 used, 0 missing), so no node label or description falls
back to an untranslated key; every relative Markdown
link in `README.md` and `docs/*.md` resolves to an existing file (over 50 links checked);
`goo gui --json` under `GOO_GUI_SELFTEST=1` builds the Tk widgets (including the
camera-name and view-layer entries, burn-in toggle and Verify button), reports the
field list in `fields`, keeps stdout as valid JSON even when the Windows pipe uses
a legacy code page (JSON is ASCII-escaped), and exits with
`{"ok": true, "opened": true}`;
and `goo install --target`
followed by Blender `--factory-startup --python tests/smoke_installed.py` imports the
**installed** copy (not the checkout), builds 22 groups, renders a 64×64 EEVEE frame
(mean 0.30312) and exits with `SMOKE_OK`; and the **shipped bundle** is installed by
Blender's own `bpy.ops.preferences.addon_install` through `tests/smoke_zip.py`, builds
22 groups (including `GOO Active Camera`), renders a 64×64 frame and survives
disable/re-enable, exiting with `ZIP_SMOKE_OK`.
The shipped **source archive** is self-contained as well: extracted into a clean temporary
directory and imported with only `PYTHONPATH`, `goo version` resolves the extracted add-on
and Blender 5.2.3, `goo bundle` and `goo bundle --source` reproduce the published add-on
and source zips byte-for-byte, and all three suites pass from that directory
(`tests/test_addon.py` 82 checks, `tests/test_cli.py` 17 tests, `tests/test_paths.py`
13 tests).
Doctor/nodes are skipped when Blender is not installed (`GOO_BLENDER` overrides the path).

### 3.2 Wheel portability / wheel 可移植性

Built with Blender's bundled Python **3.13.13** and pip 26.0.1:

```
goo_port-0.1.0-py3-none-any.whl   50 entries   171,718 B
sha256 373bec1fa2f128b53198cad0e59d70341d00ba9976c9abb4613c72cb2ec85145
```

Wheel archives are not byte-reproducible because setuptools stores timestamps, so the
size/hash above is a reference build only. The CI Linux build of the same tree, for
example, is **184,324 B** (sha256
`c13e81e33e7e2b7d01830ffaede832e90b6cae0a3ffc4235e5b1ba8f09d5ff65`); always use the
`SHA256SUMS` attached to the release you downloaded.

The wheel was then installed into an empty `--target` directory with the host Python
**3.12.14** and exercised from `%TEMP%` with only `PYTHONPATH` set: `python -m goo
version --json` reports `ok: true`, the installed `goo_npr` path and Blender 5.2.3;
`python -m goo install --dry-run --json` finds the add-on **inside the wheel**, plans
27 files / **335,624 B** to the Blender 5.2 user add-ons directory, and exits 0. This is
the portable path for users without a source checkout.

The wheel was also exercised end-to-end from a clean `%TEMP%` directory with only
`PYTHONPATH` pointing at the installed target: `goo apply` wrote the demo scene with
**22** GOO groups and 2 light groups; `goo doctor applied.blend` reported 22 groups,
key/fill groups and 2 passes; `goo render --light-groups --samples 2
--resolution-percentage 25 --camera DemoCam --view-layer ViewLayer` produced
key/fill/ambient **64×64** PNG passes and recorded `settings.camera` / 
`settings.view_layer`; `goo composite --png` wrote `composite.png` +
`composite.exr` + `composite_manifest.json` (mean luminance **0.093982**).
`GOO_GUI_SELFTEST=1 goo gui --json` opened the GUI, listed the **Verify** button
and the `Camera / 相机` + `View layer / 视图层` fields, then exited 0. No source
checkout was on `sys.path`. The installed wheel also contains `goo/verify.py`:
from `%TEMP%` it re-hashed the release `dist/` (3 artifacts, 27/50/66 entries)
with `goo verify --dist <repo>/dist --json` and exited 0.

### 3.3 GitHub Actions / 三平台 CI

Workflow: `.github/workflows/build.yml` (**Build and test / 构建与测试**). Every push to
`main` runs three jobs:

| Job | Runner | Blender | Result |
| --- | --- | --- | --- |
| `windows-x64` | `windows-latest` | 5.2.3 zip + Mesa llvmpipe | success, 1m48s |
| `linux-x64` | `ubuntu-latest` + `xvfb-run` | 5.2.3 tar.xz | success, 1m22s |
| `macos-arm64` | `macos-26` arm64 | 5.2.3 dmg | success, ~1m26s |

Each job builds the add-on zip, wheel and source zip, runs `goo verify`, the 13 path
tests and 17 CLI tests (one GUI self-test skipped with `GOO_SKIP_GUI=1`), then the
Blender add-on suite (**82 checks**), and uploads `goo-port-<platform>` with all
three artifacts plus `SHA256SUMS`.

The Windows runner has no GPU, so the workflow installs Mesa3D
(`ssciwr/setup-mesa-dist-win@v3`, version `26.3.0`, system-wide deployment) and sets
`GALLIUM_DRIVER=llvmpipe`, `LIBGL_ALWAYS_SOFTWARE=1`,
`MESA_GL_VERSION_OVERRIDE=4.5`, `MESA_GLSL_VERSION_OVERRIDE=450`. Before Mesa, the five
EEVEE render cases died with `3221225477` (0xC0000005); with Mesa, all pass.

Run `45039972528` (head `0d8adb5`) is green on all three platforms. Linux artifacts:

```
goo_npr-0.1.0.zip               96,131 B  87a6194d811b351a7c5f8146d177025d80ea6cc9f65f4497c52ec34bdfe86234
goo_port-0.1.0-py3-none-any.whl 184,324 B c13e81e33e7e2b7d01830ffaede832e90b6cae0a3ffc4235e5b1ba8f09d5ff65
goo-port-0.1.0-src.zip          367,793 B 1333fb3749eb00a2f499fc99dc0eefb4f914d5250379c675b91baa26bf3cc450
```

ZIP bytes differ slightly across operating systems because file-mode metadata differs;
the contents are identical. Use the `SHA256SUMS` from the same platform archive.

## 4. Compositor / 合成器

| Check | Result |
| --- | --- |
| Groups built | `GOO Line Composite`, `GOO Kuwahara NPR`, `GOO Depth Fog` |
| Interfaces | `Line Composite: Image, Line Mask, Line Color, Strength → Composite`; `Kuwahara: Image, Size, Uniformity, Sharpness, Eccentricity → Filtered`; `Depth Fog: Image, Depth, Fog Color, Start, End, Strength → Composite` |
| `goo.setup_compositor` | sets `scene.compositing_node_group`; tree contains RLayers + `CompositorNodeGroup` + `NodeGroupOutput` |
| Render | 64×64, 8 samples: mean RGB `[0.358, 0.358, 0.358]`, max `0.498`, non-zero |

## 5. CLI end-to-end / CLI 端到端

Fixture: 320×240, 16 EEVEE samples, red `Key` sun + blue `Rim` sun, two cubes, linear EXR.

| Command | Result |
| --- | --- |
| `goo version` | `goo 0.1.0 · Python 3.12.14 · Windows`, add-on 0.1.0, Blender 5.2.3, exit 0 |
| `goo doctor` | exit 0: Blender found, add-on present with `light_groups.py: yes`, add-on dir printed |
| `goo doctor applied_fix.blend` | engine `BLENDER_EEVEE`, `node groups 22 GOO / 22`, `light groups 2: Key(1), Rim(1)`, `passes Key, Rim` |
| `goo nodes` | exit 0: 22 registry groups listed |
| `goo apply test_scene.blend -o applied_fix.blend` | 22 groups created (fake users set at creation); the saved file reloads with **22 groups** |
| `goo render applied_fix.blend -o final --light-groups --passes beauty depth shadow --samples 16` | exit 0, 4.20 s |
| `goo composite final --png --shadow` | exit 0, EXR 1.2 MiB + PNG 10.5 KiB + `composite_manifest.json` |
| `goo render ... --no-light-groups` | reference beauty, mean lum `0.335616` |
| `goo render ... --format png --burn-in --resolution-percentage 50` | exit 0, 128×128, mean lum `0.1555`, 1.30 s; native stamp visible with `demo_lightgroups.blend \| frame 1`; `manifest.json` records `settings.burn_in: true` |
| `goo render ... --format exr --burn-in` / `--light-groups --burn-in` | exit **2**, `--burn-in needs --format png` / `--burn-in is not supported with --light-groups` with hints |
| `goo render ... --camera DemoCam --format png --resolution-percentage 50` | exit 0, 128×128, mean lum `0.1421`, 1.17 s; `manifest.json` records `settings.camera: "DemoCam"` |
| `goo render ... --camera Camera` | exit **1**, `--camera: no camera object named 'Camera' (available: ['DemoCam'])` |
| `goo render ... --view-layer ViewLayer` | exit 0; `manifest.json` records `settings.view_layer: "ViewLayer"`; `--view-layer Missing` exits **4** with `view layer 'Missing' not found` and the available list |
| `goo install --dry-run` | exit 0, 27 files / 335,624 B, nothing copied |
| `goo install` (isolated `BLENDER_USER_SCRIPTS`/`BLENDER_USER_CONFIG`) | exit 0: 27 files (335,624 B), `enabled_after: true`, `saved_preferences: true` |
| `goo bundle` | `dist/goo_npr-0.1.0.zip`, 27 files, 93.9 KiB, sha256 `21d5cf10fa23c395296660ca110409adb03e7b766a5b94443c2e73dbda0a9820` |
| `goo verify` | exit 0: 3 files checked, add-on 27 entries, wheel 50 entries, source 66 entries, all SHA-256 match |
| `goo verify --dist <empty>` | exit **2**, `SHA256SUMS not found` |
| `goo verify --dist <copy with tampered hash>` | exit **2**, `hash mismatch: goo_npr-0.1.0.zip (expected 000…, got 21d5cf…)` |
| `goo config` / `goo config --samples 32 --format png --scope local` | exit 0, writes `./.goo/config.json` |
| `goo gui --json` (`GOO_GUI_SELFTEST=1`) | exit 0, `{"ok": true, "opened": true, "buttons": [... "Verify" ...], "fields": [... "View layer / 视图层" ...]}`, widgets created |
| Error paths | missing `.blend` → **2**, bad `--blender` → **3**, failing job → **4** with Blender log tail |

Per-pass measurements (`tools/analyze_passes.py`, linear EXR):

| Pass | mean lum | max lum | R−B |
| --- | --- | --- | --- |
| Key | 0.270294 | 0.366273 | +0.2288 |
| Rim | 0.095370 | 0.134818 | −0.0790 |
| ambient | 0.029862 | 0.051429 | — |

* Key/Rim ratio **2.834**; reference beauty mean lum **0.335616**.
* `Key + Rim − ambient` vs reference: **mean abs 0.00040627, max 0.00195312 (0.1213 %)**.
* Naive sum without ambient removal: mean abs **0.03299526 — 81.2× worse**.
* Depth pass: mean **10.3964 m**, `valid_fraction 0.798281` (EEVEE's `1e10` background is
  masked, not averaged).
* Shadow pass: mean 0.9930; the matte affects **2.79 %** of pixels, masked-region
  luminance 0.364631 → 0.274506 (**−24.7 %**), max darkening 0.128349.
* Composite with `--shadow`: output luminance **0.3333**; plain composite **0.335798**
  vs all-lights **0.335616** (mean |Δ| 4.0e-4).
* PNG composite path luminance 0.335811; PNG↔EXR roundtrip mean |Δ| 1.3e-3, max 3.7e-3.
* `manifest.json` / `composite_manifest.json` are deterministic and include settings,
  groups, lights, per-pass stats and timings.

## 6. Add-on installation & discovery / 安装与发现

| Check | Result |
| --- | --- |
| Copy + enable | `goo install` wrote 27 files (335,624 B) into a fresh isolated profile, `enabled_after: true` |
| Fresh Blender discovery | `addon_utils.modules()` **FOUND** the installed copy; `enable()` returned success; the add-on list contains `goo_npr` |
| Installed PoseBone API | after enabling the installed copy: `hasattr(bpy.types.PoseBone, "hide_outliner")` **True**, `hasattr(bpy.ops.goo, "pose_outliner_flag")` **True** |
| Library after build | 22 GOO node groups, all with fake users (`registry.ensure_all()` / UI startup build) |
| Save → reload | 22 groups still present (fake-user protection, `goo nodes _work/lead/final22.blend`: `spec_count 22`) |
| `goo doctor` after install | reports `installed in Blender: yes` and the installed copy path |

This closes the two integration bugs found late: the non-literal `bl_info` version (fixed
with a literal `(0, 1, 0)`) and the zero-user purge (fixed with `use_fake_user = True` in
`utils.ensure_group`).

## 7. Numeric node tests / 节点数值测试

Both new node families were rendered and compared to independent Python references, not
just eyeballed.

**SDF** (64×64 EEVEE, Standard view, emission-mapped distance, sRGB decoded back to
linear, 2-pixel border excluded; `goo-port/_work/sdf/numeric_log.json`,
`"all_pass": true`):

| Group | Checks | Result |
| --- | --- | --- |
| `GOO SDF Primitive` | 8 shapes vs analytic formulas | all pass; centre error ≤ 0.0046, interior MAE ≤ 0.0027 |
| `GOO SDF Op` | 8 operations (union/intersect/subtract/smooth ×3/dilate/onion) | all pass; max error 0.0039 |
| `GOO SDF Vector Op` | 8 domain ops + Repeat periodicity | all pass; interior max ≤ 0.0099; periodicity error 0.0 |
| `GOO SDF Noise` | spatial variance + determinism | min/max 0.3515/0.6867, two renders differ by 0.0 |

Saved/reloaded in a fresh process: node counts 63/55/35/12, interfaces intact.

**OKLab** (Cycles, 32×32, 32-bit linear EXR, float64 reference; exact Ottosson matrices;
`goo-port/_work/oklab/oklab_test.log`, `RESULT: PASS`):

| Group | Cases | Max channel error | Result |
| --- | --- | --- | --- |
| `GOO OKLab Mix` | 4 | 3.15e-07 | pass (expected red→blue f=0.5 = `0.263734, 0.086572, 0.362824`) |
| `GOO OKLab Color Ramp` | 5 + 2 out-of-order + 2 duplicate stops | 6.19e-07 | pass (all segments, endpoints and degenerate stops finite) |

EXR linearity was checked separately: emission (0.25, 0.5, 0.75) read back exactly, so the
comparison is not hiding an sRGB decode. Alpha is not mixed (shader trees expose RGB only)
and no gamut clamp is applied — both deviations are documented in the node descriptions
and in `docs/compatibility.md`.

## 8. Known unverified items / 未验证项

* macOS and Linux: the code contains no Windows-only APIs, but only Windows 11 was tested.
* Viewport line width and language switching need a GUI session; the draw handler and
  registration are headless-tested, the visible effect is not.
* GP texture randomize depends on a material reading the `goo_tg_random` CURVE attribute
  with an Attribute node; the attribute itself is verified, the final look is left to the
  user's node graph (as in GOO, where it is a modifier option).
* Not shipped in this version: nothing is left unimplemented. The Outliner
  hidden-bones row filter itself cannot be added from Python, but GOO's
  `PoseBone.hide_outliner` flag is re-created on an ID property and exposed in the GOO
  Pose panel; the GOO 4.4 public branch stores the same flag and has no renderer/UI
  consumer for it either (`PCHAN_DRAW_SHOW_OUTLINER` appears only in DNA, RNA and the
  bone-properties UI in the downloaded source), so the port matches the available
  behaviour. Camera override, Light Info New, GP sculpt smoothing and pose-bone tools are
  shipped as approximations (see `docs/features.md`).
* Exact renderer parity is impossible from Python for the C++ internals (per-light
  Shader Info, bitmask light groups, shader depth writes); the C++ patch design is in
  `docs/native-extension.md`. The GOO 4.4 source tree **was** analysed
  (`docs/goo44-source-diff.md`, zip sha256
  `56BEDC3A9946A40DD38EC199953CA34030E0ABF8D350AD5E30BB2D9B35E91778`), so no
  source-access gap remains — only the renderer-API gap.


