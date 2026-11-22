# Installing GOO Port

GOO Port has two parts that are installed separately:

* **the add-on `goo_npr`** — provides the GOO node library, the light-group
  system, panels and operators inside Blender 5.2;
* **the `goo` CLI** — the external, dependency-free command line tool that
  drives Blender headlessly (render passes, compositing, node library setup).

You can install either one on its own. Most users want both: the add-on for
authoring, the CLI for batch rendering.

---

## English

### 1. Requirements

| Requirement | Notes |
| --- | --- |
| Blender **5.2.x** (5.2.0+) | Tested with 5.2.3 LTS. The add-on refuses older versions. |
| Python **3.10+** | Only needed for the CLI. Standard library only — nothing to pip install. |
| Repository checkout | `goo_npr/` (add-on) and `goo/` (CLI) from the same checkout. |

### 2. Install the add-on

#### Option A — zip via the Blender preferences (recommended)

```console
cd <repo>
python -m goo bundle
```

This writes `dist/goo_npr-<version>.zip` (e.g. `dist/goo_npr-0.1.0.zip`) and
prints the file count, size and SHA-256. In Blender:

1. **Edit ▸ Preferences ▸ Add-ons**;
2. **Install from Disk…** and pick the zip;
3. enable the checkbox next to **GOO Port**;
4. optional: **Save Preferences**.

The zip contains the `goo_npr` package at its root and excludes
`__pycache__`, byte-code and `.blend` backups. The archive is deterministic
(sorted entries, fixed timestamps), so rebuilding the same sources gives the
same bytes.

#### Option B — `goo install` (fully headless)

```console
goo install --dry-run     # report what would happen, change nothing
goo install               # copy + enable + save user preferences
```

`goo install` starts Blender once in the background and, inside that session:

1. resolves the user add-on directory with
   `bpy.utils.user_resource("SCRIPTS", path="addons", create=True)`;
2. copies `goo_npr/` there (replacing an older copy);
3. calls `bpy.utils.refresh_script_paths()` and
   `addon_utils.modules_refresh()`;
4. enables the add-on with
   `bpy.ops.preferences.addon_enable(module="goo_npr")`;
5. saves the preferences with `bpy.ops.wm.save_userpref()`.

Useful flags: `--blender PATH` (choose the Blender build), `--target DIR`
(install somewhere else, e.g. a portable/staging directory),
`--no-enable` (only copy files), `--dry-run` (report only).

Exit codes: `0` success, `2` bad arguments, `3` Blender not found,
`4` the Blender job failed (the log tail is printed).

#### Option C — manual copy

Copy the whole `goo_npr/` folder into the user add-on directory:

| OS | User add-on directory |
| --- | --- |
| Windows | `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\goo_npr\` |
| macOS | `~/Library/Application Support/Blender/5.2/scripts/addons/goo_npr/` |
| Linux | `~/.config/blender/5.2/scripts/addons/goo_npr/` |

(Replace `5.2` with the minor version of the Blender you run.) Then enable
**GOO Port** in **Preferences ▸ Add-ons**. A manual copy is equivalent to
Option B without the preference save; enable the add-on once in the GUI.

### 3. Install the CLI

The CLI is a plain Python package in `goo/`; it never imports `bpy`.

**Run from the checkout (no installation):**

```console
cd <repo>
python -m goo --help          # Windows: py -3 -m goo --help, or any Python 3.10+
```

**Install the entry point:**

```console
cd <repo>
python -m pip install .
goo --help
```

**Install from the prebuilt wheel (no source tree needed):**

```console
python -m pip install goo_port-0.1.0-py3-none-any.whl
goo version
goo install --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
```

The wheel is pure Python (built once with Blender's Python 3.13, runs on any
Python ≥ 3.10) and contains both `goo` and `goo_npr`; `goo install` therefore
works without a checkout.

`pyproject.toml` declares the console script `goo = "goo.__main__:main"` and
packages `goo`, `goo.scripts`, `goo_npr` (the add-on is shipped inside the same
distribution, so `goo install` and `goo bundle` work from the installed copy as
well). A virtual environment is fine — the CLI only needs the Python standard
library. If you do not want to touch site-packages, keep using
`python -m goo` with `PYTHONPATH` pointing at the repository.

The CLI finds the add-on sources in this order: `--addon` argument,
`$GOO_ADDON_PATH`, `<repo>/goo_npr` next to the package, then the installed
Blender user add-on directory.

### 4. Verify the installation

```console
goo version      # CLI version, add-on path/version, Blender path/version
goo doctor       # full check: Blender discovery, add-on state, config
goo bundle       # smoke-test: builds dist/goo_npr-<version>.zip
```

`goo doctor` output should show the Blender 5.2 executable, the add-on version,
`light_groups.py: yes`, and (after Option A/B) `installed in Blender: yes`.
Inside Blender, the **GOO** panels appear in the 3D Viewport sidebar (N-panel)
and the light-group panel appears under Scene properties.

Run `goo doctor scene.blend` to confirm a file: it lists engine, resolution,
objects, materials with GOO nodes, GOO node groups stored in the file, light
groups and their pass order.

### 5. Upgrading and uninstalling

* **Upgrade**: pull the new sources and repeat the install step you used
  (`goo install` overwrites the old copy; the zip route replaces it in
  Preferences). Node groups already saved in `.blend` files keep their stored
  version until you run `goo apply` again (`reload_node_groups(force=True)`).
* **Uninstall the add-on**: disable it in **Preferences ▸ Add-ons**, then delete
  the `goo_npr` folder from the user add-on directory listed above.
* **Uninstall the CLI**: `python -m pip uninstall goo-port` (if installed with
  pip), or simply delete the checkout.
* **Remove the zip**: `dist/goo_npr-<version>.zip` can be deleted at any time.

### 6. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Blender says the add-on is incompatible | Blender must be 5.2.0 or newer; `goo_npr` declares a minimum version. |
| Add-on not listed after a manual copy | Copy the folder itself (`...\addons\goo_npr\__init__.py` must exist), then restart Blender. |
| Add-on files exist but Blender does not show the add-on | Blender's `bl_info` parser only accepts literals; a non-literal entry (e.g. `"version": ADDON_VERSION`) makes it skip the module silently. `goo doctor` reports this. |
| `goo install` exits 3 | Blender 5.2 was not found — pass `--blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"` or set `GOO_BLENDER`. |
| `goo install` exits 4 | The Blender log tail is printed; a common cause is writing to a directory that needs administrator rights (use `--target` or Blender's own user directory). |
| `light_groups.py: no` in `goo doctor` | The add-on source is incomplete (only some modules present). Use a full checkout or a complete installed copy. |
| Two copies of the add-on | An older copy is enabled elsewhere; remove duplicate `goo_npr` folders from other Blender script paths. |
| `goo` not found after `pip install .` | The Python `Scripts`/`bin` directory is not on `PATH`; use `python -m goo` instead. |

---

## 中文

GOO Port 分为两个可独立安装的部分：

* **插件 `goo_npr`** —— 在 Blender 5.2 内提供 GOO 节点库、灯光组系统、面板与操作符；
* **`goo` 命令行工具** —— 无第三方依赖的外部工具，用于无界面驱动 Blender
  （渲染通道、合成、节点库应用等）。

两者可以只装其一；通常建议都装：插件用于创作，CLI 用于批量渲染。

### 1. 环境要求

| 项目 | 说明 |
| --- | --- |
| Blender **5.2.x**（5.2.0 及以上） | 已在 5.2.3 LTS 上测试，版本过低插件会拒绝加载。 |
| Python **3.10+** | 仅 CLI 需要，只用标准库，无需 pip 安装任何依赖。 |
| 仓库源码 | 同一仓库中的 `goo_npr/`（插件）与 `goo/`（CLI）。 |

### 2. 安装插件

#### 方式 A —— 通过 Blender 偏好设置安装 zip（推荐）

```console
cd <仓库目录>
python -m goo bundle
```

生成 `dist/goo_npr-<版本>.zip`（例如 `dist/goo_npr-0.1.0.zip`），并打印文件数、大小与 SHA-256。
然后在 Blender 中：

1. **编辑 ▸ 偏好设置 ▸ 插件**；
2. 点击 **从磁盘安装…** 选择该 zip；
3. 勾选 **GOO Port**；
4. 可选：**保存偏好设置**。

zip 根目录即 `goo_npr` 包，已排除 `__pycache__`、字节码与 `.blend` 备份；打包是确定性的
（排序、固定时间戳），相同源码重复打包得到相同字节。

#### 方式 B —— `goo install`（全无界面）

```console
goo install --dry-run     # 只报告，不做任何修改
goo install               # 复制 + 启用 + 保存用户偏好
```

`goo install` 会启动一次后台 Blender，并在该会话中：

1. 用 `bpy.utils.user_resource("SCRIPTS", path="addons", create=True)` 取得用户插件目录；
2. 把 `goo_npr/` 复制过去（覆盖旧版本）；
3. 调用 `bpy.utils.refresh_script_paths()` 与 `addon_utils.modules_refresh()`；
4. 用 `bpy.ops.preferences.addon_enable(module="goo_npr")` 启用插件；
5. 用 `bpy.ops.wm.save_userpref()` 保存偏好。

常用参数：`--blender PATH`（选择 Blender 版本）、`--target DIR`（安装到自定义目录，
如便携版/临时目录）、`--no-enable`（只复制不启用）、`--dry-run`（只报告）。
退出码：`0` 成功、`2` 参数错误、`3` 找不到 Blender、`4` Blender 任务失败（会打印日志尾部）。

#### 方式 C —— 手动复制

把整个 `goo_npr/` 文件夹复制到用户插件目录：

| 系统 | 用户插件目录 |
| --- | --- |
| Windows | `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\goo_npr\` |
| macOS | `~/Library/Application Support/Blender/5.2/scripts/addons/goo_npr/` |
| Linux | `~/.config/blender/5.2/scripts/addons/goo_npr/` |

（把 `5.2` 换成你使用的 Blender 次版本号。）然后在 **偏好设置 ▸ 插件** 中勾选 **GOO Port**。
手动复制相当于方式 B 但不保存偏好，需要在界面上启用一次。

### 3. 安装 CLI

CLI 是 `goo/` 下的纯 Python 包，从不导入 `bpy`。

**直接从仓库运行（无需安装）：**

```console
cd <仓库目录>
python -m goo --help          # Windows 也可用 py -3 -m goo --help，或任意 Python 3.10+
```

**安装入口命令：**

```console
cd <仓库目录>
python -m pip install .
goo --help
```

**从预构建 wheel 安装（无需源码目录）：**

```console
python -m pip install goo_port-0.1.0-py3-none-any.whl
goo version
goo install --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
```

wheel 为纯 Python（用 Blender 自带 Python 3.13 构建一次，可在任意 Python ≥ 3.10 上运行），
同时包含 `goo` 与 `goo_npr`，因此没有源码检出也能执行 `goo install`。

`pyproject.toml` 声明了控制台脚本 `goo = "goo.__main__:main"`，并打包
`goo`、`goo.scripts`、`goo_npr`（插件随同一发行版一起安装，因此安装后的副本也能用
`goo install` 与 `goo bundle`）。使用虚拟环境也可以——CLI 只需要标准库。
如果不希望改动 site-packages，可继续用 `python -m goo`，并把 `PYTHONPATH` 指向仓库。

CLI 查找插件源码的顺序：`--addon` 参数 → `$GOO_ADDON_PATH` → 包旁边的
`<仓库>/goo_npr` → Blender 用户插件目录中已安装的副本。

### 4. 验证安装

```console
goo version      # CLI 版本、插件路径/版本、Blender 路径/版本
goo doctor       # 完整检查：Blender 发现、插件状态、配置
goo bundle       # 冒烟测试：生成 dist/goo_npr-<版本>.zip
```

`goo doctor` 应显示 Blender 5.2 可执行文件、插件版本、`light_groups.py: yes`，
并在方式 A/B 安装后显示 `installed in Blender: yes`。
在 Blender 内，**GOO** 面板出现在 3D 视图侧边栏（N 面板），灯光组面板位于场景属性。

对具体文件可用 `goo doctor scene.blend`：会列出引擎、分辨率、对象、带 GOO 节点的材质、
文件中的 GOO 节点组、灯光组及其通道顺序。

### 5. 升级与卸载

* **升级**：更新源码后重复原来的安装步骤（`goo install` 覆盖旧副本，zip 方式在偏好设置中替换）。
  已保存进 `.blend` 的节点组会保留旧版本，直到再次执行 `goo apply`
  （其内部调用 `reload_node_groups(force=True)`）。
* **卸载插件**：在 **偏好设置 ▸ 插件** 中禁用，然后删除上面列出的用户插件目录中的
  `goo_npr` 文件夹。
* **卸载 CLI**：`python -m pip uninstall goo-port`（若用 pip 安装），或直接删除仓库副本。
* 随时可以删除 `dist/goo_npr-<版本>.zip`。

### 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| Blender 提示插件不兼容 | 需要 Blender 5.2.0 及以上；`goo_npr` 声明了最低版本。 |
| 手动复制后插件列表中没有 | 必须是文件夹本身（`...\addons\goo_npr\__init__.py` 存在），然后重启 Blender。 |
| 插件文件存在但 Blender 不显示该插件 | Blender 的 `bl_info` 解析器只接受字面量；出现 `"version": ADDON_VERSION` 之类的表达式会静默跳过该模块。`goo doctor` 会报告该问题。 |
| `goo install` 退出码 3 | 找不到 Blender 5.2——用 `--blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"` 或设置 `GOO_BLENDER`。 |
| `goo install` 退出码 4 | 会打印 Blender 日志尾部；常见原因是目标目录需要管理员权限（改用 `--target` 或 Blender 用户目录）。 |
| `goo doctor` 显示 `light_groups.py: no` | 插件源码不完整（只有部分模块），请使用完整仓库或重新安装完整副本。 |
| 出现两份插件 | 其他脚本路径中有旧副本且被启用，删除多余的 `goo_npr` 文件夹。 |
| `pip install .` 后找不到 `goo` 命令 | 该 Python 的 `Scripts`/`bin` 目录不在 `PATH`，改用 `python -m goo`。 |
