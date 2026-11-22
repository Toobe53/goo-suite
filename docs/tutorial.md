# GOO Suite 使用教程 / User Tutorial

本文面向第一次使用 **GOO Suite** 的用户：它把 GOO Engine 4.4 的 NPR 节点库、Blender 5.2 插件、
本地/远程渲染和兼容性报告合并成一个可移植工具。English follows each section.

## 1. 安装 / Install

需要 Blender **5.2.x** 和 Python **3.11+**。

```bash
# 从源码目录安装（或安装 Release 中的 wheel）
python -m pip install .
# 或
python -m pip install goo_suite-1.0.0-py3-none-any.whl

# 检查 Blender、插件和节点库
goo doctor
goo nodes
```

安装 Blender 插件：

```bash
goo install --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
```

也可以把 Release 中的 `goo_npr-1.0.0.zip` 拖进 Blender 的
**Preferences ▸ Add-ons ▸ Install from Disk**，然后启用 **GOO NPR**。

## 2. 在 Blender 中使用 / Inside Blender

- 节点：`Node Editor ▸ Shift+A ▸ GOO Engine`，22 个节点组（17 shader、2 geometry、3 compositor）。
- 灯光组：`Properties ▸ Scene ▸ GOO Light Groups`，创建 `key` / `fill` / `ambient` 并分配灯光。
- 视图层：`Properties ▸ View Layer ▸ GOO View Layer Overrides`。
- 工具：`3D Viewport ▸ Sidebar ▸ GOO`（相机、film transparent、collection、姿态/GP/F-Curve 工具）。

## 3. 本地 CLI / Local CLI

```bash
# 应用节点库并保存副本
goo apply scene.blend -o scene_goo.blend

# 每个灯光组渲染一张 EXR，再合成
goo render scene_goo.blend --light-groups -o out/passes --format exr
goo composite out/passes --png

# 单张 beauty、指定相机/视图层、PNG burn-in
goo render scene_goo.blend -o out/still --format png
goo render scene_goo.blend --camera "Camera" --view-layer "ViewLayer" -o out/key
goo render scene_goo.blend -o out/frame --format png --burn-in
```

`--burn-in` 只支持 PNG，不能与 `--light-groups` 或 EXR 同用。`--view-layer` 不存在时退出 4。

## 4. 分析与兼容性 / Analysis and Compatibility

```bash
goo analyze scene.blend -o analysis.json
goo report analysis.json -o report.html
goo compat --analysis analysis.json -o compat.html
```

`analyze` 会扫描外部贴图、链接库、字体等依赖，并生成可缓存的场景报告（`--no-cache` 强制重跑）。
`compat` 输出 39 项外置兼容审计：13 L2、15 L1、11 L0。

## 5. 远程渲染 / Remote Rendering

GOO Suite 通过 SSH 或本地 worker 把任务发到另一台机器；本机不需要安装 Blender 渲染农场。

```bash
# 登记主机（密钥登录，不保存密码）
goo remote add studio --host render.example.com --user artist --key ~/.ssh/id_ed25519

# 测试连接与远端 Blender
goo remote test --profile studio

# 提交任务；资产会按 SHA-256 增量上传，结果回传后校验
goo remote render scene.blend -o out/remote --profile studio

# 查看状态 / 下载 / 清理
goo remote status --profile studio
goo remote fetch <job-id> --profile studio
goo remote clean --profile studio

# 没有 SSH 时用本地目录模拟
goo remote render scene.blend -o out/local --local
```

远程目录默认 `~/.goo-suite/jobs/<id>`；缺失资产默认阻止任务，可用 `--allow-missing` 显式放行。

## 6. GUI

```bash
goo gui
```

GUI 与 CLI 共用配置：项目、分析、远程、队列、报告。无显示器时使用 CLI。

## 7. 配置与迁移 / Configuration

配置文件位于：

- Windows: `%APPDATA%\GOO Suite\config.json`
- macOS: `~/Library/Application Support/GOO Suite/config.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/goo-suite/config.json`

旧的 `goo-bridge` 配置会在首次保存时迁移；旧命令 `goo-bridge` 仍可用一个版本。
设置 `GOO_SUITE_HOME` 可覆盖状态目录，设置 `GOO_SUITE_CACHE` 可覆盖缓存文件。

## 8. 测试与构建 / Tests and Build

```bash
python -m unittest discover -s tests -v
blender --factory-startup --background --python tests/test_addon.py
python tools/build_release.py
goo verify
```

`tools/build_release.py` 生成插件 zip、wheel 和源码包，写入 `dist/SHA256SUMS`。
`goo verify` 校验哈希和归档必需成员，失败退出 2。

## 9. 常见问题 / Troubleshooting

**Blender 里没有 GOO 菜单**：确认 5.2.x、插件已启用；重跑 `goo install` 并看 Blender 控制台。

**`goo doctor` 找不到 Blender**：用 `--blender` 指到可执行文件，或设置 `GOO_BLENDER`。

**渲染崩溃/EEVEE 错误**：只支持 EEVEE；无 GPU 的 Linux 需要软件 OpenGL/EGL，Windows CI 使用 Mesa llvmpipe。

**远程任务缺资产**：`goo analyze scene.blend` 查看缺失列表；补齐后重跑，或明确使用 `--allow-missing`。

**中文乱码**：机器 JSON 始终 ASCII 安全；人类可读输出可 `chcp 65001`。

## 10. 许可 / License

GPL-3.0-or-later。GOO Engine 的原始设计归 DillonGoo Studios；本项目是独立重实现。
