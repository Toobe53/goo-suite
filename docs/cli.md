# goo CLI — GOO Suite for Blender 5.2

`goo` is the external command line tool that ships with **GOO Suite**, a portable
re-implementation of DillonGoo GOO Engine's NPR features for Blender 5.2 plus a
local/remote render manager. It is a thin, dependency-free front end: every
operation that needs Blender is executed by `blender.exe --factory-startup
--background <file.blend> --python <script>` and the JSON result is read back by
the CLI. The CLI itself never imports `bpy` and runs on any Python 3.11+
(Windows, macOS, Linux).

The add-on package `goo_npr/` and the CLI live in the same repository; the CLI
finds the add-on in the checkout next to it, or in Blender's user add-on
directory when the add-on is installed there. The `goo_bridge` package is
reachable through the same CLI (`goo analyze|report|compat|remote`) and through
the legacy `goo-bridge` entry point.

---

## English

### 1. Requirements

| Requirement | Notes |
| --- | --- |
| Blender **5.2.x** | 5.2.0 or newer; tested with 5.2.3 LTS. Older versions are rejected with a clear error. |
| Python **3.10+** for the CLI | Standard library only — no pip dependencies. |
| `goo_npr` add-on package | Either the checkout next to the CLI or an installed copy (`goo install`). |

### 2. Quick start

```console
# 1. Is Blender + the add-on visible?
goo doctor

# 2. Apply the node library and sync light groups into a copy of the scene
goo apply scene.blend -o scene_goo.blend

# 3. One beauty pass per light group (+ ambient, optional utility passes)
goo render scene_goo.blend -o renders --light-groups --passes beauty depth shadow

# 4. Add the per-group beauties back together, write EXR + PNG
goo composite renders --png --shadow

# 5. Optional: install the add-on into Blender without opening the GUI
goo install --dry-run
goo install
```

Run `python -m goo …` if the `goo` entry point is not on `PATH`.

### 3. Global options and exit codes

```
goo [--json] [-V/--version] COMMAND …
```

* `--json` may be placed before **or** after the subcommand and prints one JSON
  object on stdout instead of the human-readable report.
* `-V/--version` prints `goo <version>`.

| Exit code | Meaning |
| --- | --- |
| `0` | success |
| `1` | unexpected internal error (set `GOO_DEBUG=1` for a traceback) |
| `2` | usage/configuration error (bad path, unknown pass, bad config file) |
| `3` | Blender 5.2 could not be found or the given `--blender` is unusable |
| `4` | the Blender job failed (log tail is printed, job files are kept) |

Every error prints `goo: error: …` plus a short hint (English/Chinese) and, for
job failures, the last lines of Blender's stderr.

### 4. Commands

#### `goo doctor [FILE.blend]`

Checks the CLI installation, the discovered Blender (path, version, how it was
found), the add-on (path, version, whether `light_groups.py` is present, whether
it is installed/enabled in Blender) and, when a `.blend` is given, a scene
summary: engine, resolution, objects, materials with GOO nodes, GOO node groups
stored in the file, light groups and pass order. Exit code `4` if the scene
inspection itself fails.

#### `goo nodes [FILE.blend]`

Lists the node library registered by `goo_npr` (name, category, tree type,
version, EN/ZH description). With a `.blend`, additionally lists the GOO node
groups actually stored in that file, with node counts and builder version.

#### `goo apply FILE.blend [-o OUT.blend | --in-place] [--no-build]`

1. loads the file in a headless Blender session,
2. registers `goo_npr` and calls `reload_node_groups(force=True)`,
3. marks the created standalone node groups with a fake user so they survive
   saving (Blender otherwise purges zero-user datablocks),
4. calls `goo_npr.light_groups.sync_scene(scene)` for every scene,
5. saves to `OUT.blend` (or in place; `--in-place` first writes a timestamped
   `FILE.blend.YYYYMMDD-HHMMSS.bak` backup).

`-o` and `--in-place` are mutually exclusive; without either the command refuses
to run. `--no-build` only syncs light groups (useful for re-syncing receivers
after editing collections).

#### `goo render FILE.blend -o OUT [options]`

| Option | Meaning |
| --- | --- |
| `--passes PASS …` | any of `beauty`, `combined` (alias of beauty), `image`, `depth`, `normal`, `shadow`, `mist`, `alpha` (default `beauty`) |
| `--light-groups` | render one beauty pass per light group |
| `--no-light-groups` | single combined beauty pass (default) |
| `--samples N` | EEVEE `taa_render_samples` |
| `--resolution-percentage N` | 1–1000 |
| `--format exr\|png` | beauty format (data passes are always EXR) |
| `--camera NAME` | use this camera object for the render (fails with the available list if missing) |
| `--view-layer NAME` | render this view layer (default: the first layer; fails with the available list if missing) |
| `--film-transparent` | transparent film |
| `--burn-in` | burn the blend file name + current frame into the PNG beauty (native render stamp; requires `--format png`, rejected with `--light-groups`) |
| `--no-ambient` | skip the ambient (all lights hidden) pass |
| `--keep-temp` | keep the generated job JSON under the system temp dir |
| `--blender PATH` | Blender 5.2 executable |

Pass filenames are deterministic:

* `<group>_beauty.<ext>` — one per light group (group names are sanitised;
  empty/unassigned group becomes `default`);
* `ambient_beauty.<ext>` — world/ambient-only render, used by `goo composite`
  to remove ambient light that would otherwise be counted once per group;
* `beauty.<ext>` — when light groups are off;
* `depth.exr`, `normal.exr`, `shadow.exr`, `mist.exr`, `alpha.exr` — generic
  passes, always EXR for numeric fidelity;
* `manifest.json` — machine-readable description of the run.

The scene's engine must be EEVEE (`BLENDER_EEVEE` on Blender 5.2); materials and
camera are used as saved. Settings that the CLI overrides (samples, resolution
percentage, film, format, output path, view transform) are listed under
`settings` in the manifest.

**How the passes are produced**

* Light groups: every light that is not part of the current pass gets
  `hide_render = True` for that render and is restored afterwards, so the pass
  is a physically correct EEVEE render of just that group (light linking set up
  by `sync_scene` is preserved).
* Utility passes: a temporary compositor group is built with
  `CompositorNodeRLayers` and a single group output, the render layer socket
  (`Depth`, `Normal`, `Shadow`, `Mist`, `Alpha`, `Image`) is wired to the output,
  `scene.compositing_node_group` is assigned for that render and the previous
  compositing setup is restored afterwards. The output socket is always typed
  *Color*: on Blender 5.2.3 a VALUE-typed group output silently falls back to
  the combined image (verified), while VALUE/VECTOR sockets convert correctly
  into Color.
* EEVEE writes `1e10` into the Z pass for background pixels; the manifest's
  depth statistics mask that sentinel and report `valid_fraction` /
  `ignored_fraction` instead of an absurd mean.

#### `goo composite DIR [--png] [--no-exr] [--exposure E] [--contrast C] [--shadow] [--no-ambient] [--basename NAME]`

Reads `manifest.json` from a render directory (falling back to file-name
patterns), converts PNG inputs from sRGB to linear, then:

1. adds every light-group beauty in linear space,
2. subtracts `(groups − 1) × ambient` when the ambient pass exists and
   `--no-ambient` was not passed (world light is present in every group's
   render, so a plain sum counts it `groups` times),
3. optionally multiplies by the shadow matte (`--shadow`); black or fully white
   shadow passes are detected and skipped with a warning,
4. optionally applies exposure (`×2^E` stops) and contrast (pivot 0.18).

Outputs `composite.exr` (linear, 32-bit) and/or `composite.png` (manual sRGB
encode, 8-bit RGBA — Blender's own generated-image PNG save was verified to
produce black frames, so the CLI writes PNG bytes itself), plus
`composite_manifest.json` with the input list, their mean luminance, the
combination settings and output statistics.

**Accuracy (320×240 test scene):** `Key + Rim − Ambient` differs from a
single all-lights beauty by mean |Δ| = 4.1e-4, max |Δ| = 2.0e-3 (0.12 %); the
CLI's `composite` output matches the all-lights render by mean |Δ| = 2.3e-4.
A naive sum without ambient correction is ~80× worse (mean |Δ| = 3.3e-2).

To reproduce those numbers on your own render, run the bundled helper inside
Blender (`tools/` is a repository extra, not part of the CLI package):

```console
blender --factory-startup --background --python tools/analyze_passes.py -- \
    renders renders_ref/beauty.exr
```

It prints mean/max luminance and R−B per pass, the ratio between the two first
light groups, and — when an all-lights reference is given — the reconstruction
error with and without ambient removal.

#### `goo install [--dry-run] [--target DIR] [--no-enable]`

Copies `goo_npr/` into Blender's user add-on directory, refreshes the script
paths and enables the add-on in the user preferences (`addon_enable` +
`save_userpref`), all through a headless Blender session. `--dry-run` reports
source, target and file count without touching anything; `--target` installs to
a custom directory; `--no-enable` only copies. See
[install.md](install.md) for the full guide.

#### `goo bundle [-o ZIP] [--source]`

Creates `dist/goo_npr-<version>.zip` (deterministic: sorted entries, fixed
timestamps, `__pycache__`/byte-code/backup files excluded) for
**Preferences ▸ Add-ons ▸ Install from Disk…**. Prints path, file count, size
and SHA-256. With `--source`, creates `dist/goo-port-<version>-src.zip`
instead: README, `pyproject.toml`, `goo/`, `goo_npr/`, `docs/` and `tests/`
(including the demo `.blend`) in the same deterministic zip format.

#### `goo verify [--dist DIR]`

Checks a release directory (default `dist/`): every file listed in
`SHA256SUMS` must exist and match its SHA-256, and each `.zip`/`.whl` is opened
to confirm the core members a user needs are present (add-on package,
`goo` package, README/docs/tests for the source archive). Prints one line per
artifact and exits 2 on any problem, so it can gate a release or CI job.
`--json` returns `ok`, `files`, `checks` and `errors`.

#### `goo config [--blender PATH] [--output-dir DIR] [--samples N] [--format exr|png] [--resolution-percentage N] [--addon DIR] [--scope user|local]`

Shows the effective configuration (defaults merged with the user and local
files) or stores defaults. `--scope user` writes the per-user file,
`--scope local` writes `./.goo/config.json` (wins when both exist).

#### `goo gui [--blender PATH]`

Opens the dependency-free Tkinter GUI (fields for .blend, output directory,
Blender executable, samples, resolution %, format, optional camera name and view
layer, checkboxes for applying the node library / light-group passes / compositing /
burn-in note, buttons Doctor, Apply, Render, Composite, Verify, Install, Bundle, and a live log). The
GUI runs the CLI as a child process and streams its output, so behaviour is
identical to the commands above (including the `--camera NAME`, `--view-layer NAME`
and `--burn-in` constraints). If Tkinter is unavailable, a clear message points at the CLI instead.
`GOO_GUI_SELFTEST=1` builds the window and closes it after 1.5 s (used for
headless verification).

#### `goo version`

Prints CLI version, platform/Python, add-on path and version, and Blender path
and version (Blender failures are reported instead of aborting).

### 5. Light-group workflow

Light groups are the core of GOO's pass workflow: every light belongs to a
group, each group renders its own beauty pass, and the passes are composited
back together (or used as masks).

1. **Create groups** — either in Blender:
   * Scene properties ▸ *GOO Light Groups* panel: add groups (`Key`, `Rim`,
     `Fill`…), optionally enable *Use Shadow* per group and pick a receiver
     collection for light linking;
   * or programmatically: `scene.goo_light_groups.add().name = "Key"` and set
     `light_object.goo_light_group = "Key"` for each light;
   * `goo_npr.light_groups.sync_scene(scene)` assigns receivers and groups, and
     `bpy.ops.goo.light_group_sync()` does the same from the UI.
2. **Save** the .blend. Light group data is stored in the scene/materials/objects,
   so it survives `--factory-startup` once the add-on is registered (the CLI
   registers it automatically when the add-on package is present).
3. **Render**: `goo render scene.blend -o renders --light-groups`. Verify the
   per-group brightness in the printed table — the groups normally differ (in
   the test scene the warm Key pass has mean luminance 0.2703 and the cool Rim
   pass 0.0954, ratio 2.83; the pass colours differ by R−B = +0.229 vs −0.079).
4. **Composite**: `goo composite renders --png [--shadow]`.
5. Iterate: render only the passes you need (`--passes beauty shadow depth`),
   keep the group names stable when possible so downstream scripts can rely on
   the filenames.

### 6. Portability notes

**Blender discovery order** (first valid 5.2+ executable wins):

1. `--blender PATH` argument,
2. `$GOO_BLENDER` environment variable,
3. `$GOO_BLENDER_52` environment variable,
4. config files: `%APPDATA%\goo-port\config.json` (Windows),
   `~/Library/Application Support/goo-port/config.json` (macOS),
   `~/.config/goo-port/config.json` (Linux), then `./.goo/config.json`,
5. common install locations:
   * Windows: `C:\Program Files\Blender Foundation\Blender 5*\blender.exe`,
     `%LOCALAPPDATA%\Programs\Blender Foundation\Blender 5*\blender.exe`,
     Steam `steamapps\common\Blender\blender.exe`, WindowsApps;
   * macOS: `/Applications/Blender.app/Contents/MacOS/Blender` and variants;
   * Linux: `/usr/bin/blender`, `/usr/local/bin/blender`, `/snap/bin/blender`,
     `/opt/blender*/blender`;
6. `PATH`.

The version is validated by running `blender --background --python-expr` and
parsing `bpy.app.version_string`; a found-but-too-old Blender is skipped (and
reported by `goo doctor`), while an explicit `--blender`/env value that is wrong
fails hard with exit code 3.

**Environment variables**

| Variable | Purpose |
| --- | --- |
| `GOO_BLENDER`, `GOO_BLENDER_52` | Blender executable override |
| `GOO_ADDON_PATH` | directory (or `goo_npr` folder) used as the add-on source |
| `GOO_DEBUG=1` | traceback + extra detail on unexpected errors |
| `GOO_GUI_SELFTEST=1` | build the GUI, then close it (headless smoke test) |
| `PYTHONIOENCODING`/`PYTHONUTF8` | set to UTF-8 for the Blender child process by the CLI |

**Config files** are JSON with the keys `blender`, `addon_path`, `output_dir`,
`samples`, `format`, `resolution_percentage`; `goo config` validates paths
before writing. Paths with spaces are fine everywhere (argument vectors are
passed without a shell); on Windows quoting matters only in your own shell.

**Encoding:** the CLI sets UTF-8 I/O for Blender and prints UTF-8. Older Windows
consoles may need `chcp 65001`; redirected output is plain UTF-8.

### 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `goo: error: Blender 5.2 not found` (exit 3) | Install Blender 5.2 or pass `--blender PATH` / set `GOO_BLENDER`. `goo doctor` lists every candidate it tried. |
| exit 4 with a log tail | Read the tail; rerun `goo doctor <file>` to check the scene. Job files are kept in a `goo-cli-*` temp dir, `--keep-temp` keeps them on success too. |
| `light-group passes need goo_npr.light_groups` | The add-on package is incomplete (module missing). Use a full checkout or `goo install` a complete zip. |
| All group passes look identical | The lights were not assigned: run `goo apply` (syncs) and check `goo doctor <file>` for `light groups 2: Key(1), Rim(1)`; verify each sun actually lights the visible side of the objects. |
| Composite looks brighter than the viewport render | Ambient/world light is counted once per group; keep the ambient pass and `goo composite` (subtracts `(n−1)×ambient`). Do not use `--no-ambient` unless you add images yourself. |
| `goo composite` warns the shadow pass is black/white | EEVEE wrote no shadows (nothing casts them or shadows are disabled) — the matte is skipped rather than darkening the whole image. |
| `goo install` copied the files but Blender does not list the add-on | Blender's `bl_info` parser only accepts literals; a computed version such as `"version": ADDON_VERSION` makes it silently skip the module. `goo doctor` prints this as a `bl_info` warning. |
| `goo gui` says Tkinter is unavailable | Use a python.org Python build or the CLI subcommands. |
| Chinese output garbled in a piped Windows console | Set the console to UTF-8 (`chcp 65001`). |

---

## 中文

### 1. 功能概述

`goo` 是 **GOO Port**（面向 Blender 5.2 的 GOO Engine NPR 特性可移植重实现）自带的外部命令行工具。
所有需要 Blender 的操作都通过
`blender.exe --factory-startup --background <file.blend> --python <script>`
执行，CLI 自身**不导入 bpy**，只使用 Python 标准库，可在 Windows/macOS/Linux 的 Python 3.10+ 上运行。

### 2. 环境要求

| 项目 | 说明 |
| --- | --- |
| Blender **5.2.x** | 需要 5.2.0 及以上；已在 5.2.3 LTS 测试。版本过低会被明确拒绝。 |
| CLI 用 Python **3.10+** | 仅标准库，无需安装任何第三方包。 |
| `goo_npr` 插件包 | 与 CLI 同仓库的 `goo_npr/`，或通过 `goo install` 安装到 Blender。 |

### 3. 快速开始

```console
goo doctor
goo apply scene.blend -o scene_goo.blend
goo render scene_goo.blend -o renders --light-groups --passes beauty depth shadow
goo composite renders --png --shadow
goo install --dry-run
goo install
```

没有把 `goo` 加入 `PATH` 时，用 `python -m goo …` 代替。

### 4. 全局参数与退出码

```
goo [--json] [-V/--version] 命令 …
```

* `--json` 可放在子命令前或后，输出单个 JSON 对象，便于脚本处理。
* 退出码：`0` 成功；`1` 内部错误（`GOO_DEBUG=1` 查看堆栈）；`2` 用法/配置错误；
  `3` 找不到可用的 Blender 5.2；`4` Blender 任务失败（会打印日志尾部并保留临时任务文件）。
* 所有错误都会打印 `goo: error: …` 和简短中英文提示。

### 5. 命令说明

#### `goo doctor [FILE.blend]`
检查 CLI 安装、Blender 路径与版本（以及来源）、插件路径/版本、`light_groups.py` 是否存在、
插件是否已安装并启用；给出 `.blend` 时还会输出场景摘要：渲染引擎、分辨率、对象、带 GOO 节点的材质、
文件内的 GOO 节点组、灯光组与通道顺序。场景检查失败时退出码为 `4`。

#### `goo nodes [FILE.blend]`
列出 `goo_npr` 注册的节点库（名称、类别、节点树类型、版本、中英文描述）；
带 `.blend` 时再列出该文件内实际存在的 GOO 节点组及节点数量。

#### `goo apply FILE.blend [-o 输出.blend | --in-place] [--no-build]`
在无界面 Blender 中：注册插件 → `reload_node_groups(force=True)` 重建节点库 →
给独立节点组加 fake user（否则 Blender 保存时会清除零引用数据块）→
对每个场景执行 `sync_scene` 同步灯光组 → 保存。
输出到 `-o`，或用 `--in-place` 原地保存（会先写
`FILE.blend.YYYYMMDD-HHMMSS.bak` 备份）。二者必须指定其一。

#### `goo render FILE.blend -o 输出目录 [参数]`

| 参数 | 说明 |
| --- | --- |
| `--passes 通道…` | `beauty`、`combined`（beauty 别名）、`image`、`depth`、`normal`、`shadow`、`mist`、`alpha`，默认 `beauty` |
| `--light-groups` | 每个灯光组渲染一张 beauty |
| `--no-light-groups` | 渲染一张合并 beauty（默认） |
| `--samples N` | EEVEE 采样数 |
| `--resolution-percentage N` | 分辨率百分比 1–1000 |
| `--format exr\|png` | beauty 格式（数值通道始终为 EXR） |
| `--camera NAME` | 使用指定相机渲染；不存在时报错并列出可用相机 |
| `--view-layer NAME` | 渲染指定视图层（默认第一层；不存在时报错并列出可用层） |
| `--film-transparent` | 透明背景 |
| `--burn-in` | 在 PNG beauty 上烧录文件名与当前帧（原生 render stamp；要求 `--format png`，不能与 `--light-groups` 同用） |
| `--no-ambient` | 不渲染环境光（全部灯光隐藏）通道 |
| `--keep-temp` | 保留临时任务 JSON |
| `--blender PATH` | 指定 Blender 5.2 |

输出文件名固定：`<组名>_beauty.<ext>`（空组名映射为 `default`，组名会做安全化处理）、
`ambient_beauty.<ext>`（仅世界光，用于合成时扣除重复环境光）、`beauty.<ext>`（不分灯光组时）、
`depth.exr`/`normal.exr`/`shadow.exr`/`mist.exr`/`alpha.exr`（数值通道一律 EXR）、
以及 `manifest.json`（记录所有设置、灯光组、每张图的统计与耗时）。

**实现要点**：灯光组通道通过临时把其它灯光设为 `hide_render = True` 实现，渲染后恢复；
`sync_scene` 设置的灯光链接保持不变。数值通道通过临时创建
`CompositorNodeRLayers → NodeGroupOutput` 合成组并把对应输出接到组输出实现，
渲染后恢复原合成设置。Blender 5.2.3 上组输出必须是 **Color** 类型，
VALUE 类型会静默回退成合成图（已实测），VALUE/VECTOR 输出会自动转换。
Z 通道背景值为 `1e10`，清单统计会屏蔽该哨兵值并给出 `valid_fraction`。

#### `goo composite 目录 [--png] [--no-exr] [--exposure E] [--contrast C] [--shadow] [--no-ambient] [--basename 名称]`
读取渲染目录的 `manifest.json`（缺失时按文件名回退），把 PNG 输入从 sRGB 转回线性后：
1) 线性相加所有灯光组 beauty；
2) 若存在环境光通道且未加 `--no-ambient`，扣除 `(组数−1)×环境光`
   （每张组图都含世界光，直接相加会重复计算）；
3) 可选乘以阴影遮罩 `--shadow`（全黑或全白遮罩会跳过并警告）；
4) 可选曝光（`×2^E` 档）与对比度（以 0.18 为支点）。
输出 `composite.exr`（线性 32 位）和/或 `composite.png`（手动 sRGB 编码；
实测 Blender 自带的生成图 PNG 保存会得到全黑，故由 CLI 自行写 PNG 字节），
以及 `composite_manifest.json`（输入列表、各输入平均亮度、合成参数、输出统计）。

**精度实测**（320×240 测试场景）：`Key + Rim − 环境光` 与单张全灯光 beauty 的平均绝对差
4.1e-4、最大 2.0e-3（0.12 %）；`goo composite` 与全灯光渲染平均绝对差 2.3e-4；
不做环境光扣除的直接相加误差约 3.3e-2（约 80 倍）。

如需在自己的渲染结果上复现这些数字，可在 Blender 内运行仓库附带的辅助脚本
（`tools/` 是仓库附加工具，不属于 CLI 包）：

```console
blender --factory-startup --background --python tools/analyze_passes.py -- \
    renders renders_ref/beauty.exr
```

脚本会打印每个通道的平均/最大亮度与 R−B、前两个灯光组的亮度比，
并在给出全灯光参考图时给出扣除环境光前后的重建误差。

#### `goo install [--dry-run] [--target 目录] [--no-enable]`
把 `goo_npr/` 复制到 Blender 用户插件目录，刷新脚本路径并在用户偏好中启用
（`addon_enable` + `save_userpref`），全程无界面。`--dry-run` 只报告不修改；
`--target` 指定自定义目录；`--no-enable` 只复制不启用。详见 [install.md](install.md)。

#### `goo bundle [-o ZIP] [--source]`
生成 `dist/goo_npr-<版本>.zip`（确定性打包：排序、固定时间戳、排除 `__pycache__`/字节码/备份），
可在 **偏好设置 ▸ 插件 ▸ 从磁盘安装** 中直接安装。输出路径、文件数、大小与 SHA-256。
加 `--source` 则生成 `dist/goo-port-<版本>-src.zip`：README、`pyproject.toml`、
`goo/`、`goo_npr/`、`docs/` 与 `tests/`（含演示 `.blend`），同样确定性打包。

#### `goo verify [--dist 目录]`
校验发布目录（默认 `dist/`）：`SHA256SUMS` 中每个文件必须存在且 SHA-256 匹配，
并打开每个 `.zip`/`.whl` 确认用户所需的核心成员存在（插件包、`goo` 包、源码包的
README/docs/tests）。每个产物打印一行，任何问题退出码 2，可用于发布或 CI 门禁；
`--json` 返回 `ok`、`files`、`checks`、`errors`。

#### `goo config [...]`
查看当前生效配置（默认值 + 用户配置 + 本地配置），或写入默认值。
`--scope user` 写用户配置，`--scope local` 写 `./.goo/config.json`（两者同时存在时本地优先）。

#### `goo gui [--blender PATH]`
打开无第三方依赖的 Tkinter 图形界面：`.blend` 路径、输出目录、Blender 可执行文件、采样数、
分辨率百分比、格式、可选相机名与视图层，以及“应用节点库 / 渲染灯光组 / 合成 / 烧录信息”勾选项，按钮
Doctor、Apply、Render、Composite、Verify、Install、Bundle，并有实时日志窗口。
GUI 只是以子进程运行上面的 CLI 并转发输出，行为与命令行一致（包括 `--camera NAME`、
`--view-layer NAME` 与 `--burn-in` 的限制）；没有 tkinter 时会给出明确提示。
`GOO_GUI_SELFTEST=1` 可构建窗口后 1.5 秒自动关闭（用于无头验证）。

### 6. 灯光组工作流

1. **建组**：在 Blender 的 *GOO Light Groups* 场景面板中添加组（如 Key、Rim、Fill），
   可设置每组是否投影（Use Shadow）和接收者集合；
   或在脚本中 `scene.goo_light_groups.add().name = "Key"`，并给灯光设置
   `light_object.goo_light_group = "Key"`；
   `sync_scene(scene)`/`bpy.ops.goo.light_group_sync()` 负责分配接收者与组。
2. **保存** `.blend`（灯光组数据保存在场景/材质/对象上）。
3. **渲染**：`goo render scene.blend -o renders --light-groups`，
   用表格里的平均亮度确认各组确实不同（测试场景中暖色 Key 组 0.2703、冷色 Rim 组 0.0954，
   比值 2.83；两组的 R−B 分别为 +0.229 与 −0.079）。
4. **合成**：`goo composite renders --png [--shadow]`。
5. 迭代时只渲染需要的通道，例如 `--passes beauty shadow depth`。

### 7. 可移植性与路径

Blender 查找顺序：`--blender` → `$GOO_BLENDER` → `$GOO_BLENDER_52` →
配置文件（Windows `%APPDATA%\goo-port\config.json`、macOS
`~/Library/Application Support/goo-port/config.json`、Linux
`~/.config/goo-port/config.json`，之后 `./.goo/config.json`）→
常见安装路径（Windows 的 `C:\Program Files\Blender Foundation\Blender 5*\blender.exe`、
`%LOCALAPPDATA%`、Steam、WindowsApps；macOS 的 `/Applications/Blender.app/...`；
Linux 的 `/usr/bin/blender`、`/snap/bin/blender` 等）→ `PATH`。
找到的版本会用 `--background --python-expr` 读取 `bpy.app.version_string` 校验；
过旧的非显式候选会被跳过并由 `goo doctor` 报告，显式指定但不可用的路径直接以退出码 3 报错。

环境变量：`GOO_BLENDER`/`GOO_BLENDER_52`（Blender 路径）、`GOO_ADDON_PATH`（插件源目录）、
`GOO_DEBUG=1`（详细堆栈）、`GOO_GUI_SELFTEST=1`（GUI 自检）。
含空格的路径无需特殊处理（CLI 不经过 shell）；配置文件是 JSON，键为
`blender`、`addon_path`、`output_dir`、`samples`、`format`、`resolution_percentage`。

### 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| 退出码 3：找不到 Blender 5.2 | 安装 Blender 5.2，或用 `--blender`/`GOO_BLENDER` 指定；`goo doctor` 会列出所有候选。 |
| 退出码 4 且打印日志尾部 | 查看日志；先用 `goo doctor <文件>` 检查场景。任务文件保存在 `goo-cli-*` 临时目录，`--keep-temp` 可在成功时也保留。 |
| 提示缺少 `goo_npr.light_groups` | 插件包不完整，请用完整仓库或重新 `goo install`。 |
| 各组通道看起来一样 | 灯光没有分组：执行 `goo apply`（会同步），并用 `goo doctor` 确认 `light groups 2: Key(1), Rim(1)`；同时确认光源确实照亮可见面。 |
| 合成结果比视口更亮 | 世界光被每组各算一次；保留环境光通道并使用 `goo composite`（扣除 `(n−1)×环境光`），不要随意加 `--no-ambient`。 |
| 合成分支提示阴影通道全黑/全白 | EEVEE 没有产生阴影；此时跳过遮罩而不是把整张图压黑。 |
| `goo install` 已复制文件但 Blender 插件列表中没有 | Blender 的 `bl_info` 解析器只接受字面量；写成 `"version": ADDON_VERSION` 之类的表达式会让它静默跳过该模块。`goo doctor` 会把这个问题作为 `bl_info` 警告打印。 |
| `goo gui` 提示没有 tkinter | 使用 python.org 的 Python，或改用命令行子命令。 |
| Windows 管道输出中文乱码 | 控制台设为 UTF-8（`chcp 65001`）。 |
