# GOO Suite

[English](README.md) | 简体中文

[![build](https://github.com/Toobe53/goo-suite/actions/workflows/build.yml/badge.svg)](https://github.com/Toobe53/goo-suite/actions/workflows/build.yml)

GOO Suite 把 **GOO Engine 4.4 的 NPR 功能移植**与**外置渲染管理**合并为一个跨平台工具：

| 组件 | 作用 | 使用方式 |
| --- | --- | --- |
| `goo_npr` | Blender 5.2 插件：22 个 GOO 节点组、灯光组、相机/视图层工具 | 安装 `goo_npr-1.0.0.zip` |
| `goo` | 命令行与 Tk GUI：应用节点、分灯光组渲染、合成、安装、打包、分析、兼容性报告 | `goo ...` |
| `goo_bridge` | 远程渲染与队列：SSH 或本地 worker、资产校验、任务状态、HTML 报告 | `goo remote ...` / `goo-bridge ...` |

> 不修改 Blender，不打包 Blender。所有组件都是标准库 Python，随本仓库分发。

## 安装

需要 Blender **5.2.x** 与 Python **3.11+**。

```bash
# 从源码目录安装（推荐开发时使用）
python -m pip install .

# 或从 Release 下载 wheel
python -m pip install goo_suite-1.0.0-py3-none-any.whl

# 安装 Blender 插件
goo install --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"

# 检查环境
goo doctor
```

也可以把 `goo_npr-1.0.0.zip` 拖进 Blender 的 **Preferences ▸ Add-ons ▸ Install**。

## 快速开始

```bash
# 1. 在 .blend 上应用 GOO 节点组/灯光组
goo apply scene.blend -o scene_goo.blend

# 2. 每个灯光组输出 EXR，再合成 PNG
goo render scene.blend --light-groups -o out/passes
goo composite out/passes --png

# 3. 分析资产和兼容性（不启动渲染）
goo analyze scene.blend -o analysis.json
goo report analysis.json -o report.html
goo compat --analysis analysis.json -o compat.html

# 4. 远程渲染：先登记主机，再提交任务
goo remote add studio --host render.example.com --user artist --key ~/.ssh/id_ed25519
goo remote test --profile studio
goo remote render scene.blend -o out/remote --profile studio

# 5. 打开 GUI
goo gui
```

在 Blender 中：`Node Editor ▸ Add ▸ GOO Engine`，`Properties ▸ Scene ▸ GOO Light Groups`，
`Properties ▸ View Layer ▸ GOO View Layer Overrides`，`3D Viewport ▸ Sidebar ▸ GOO`。

## 状态与兼容性

- 49 项功能矩阵：9 项 Ported、1 项 Native、25 项 Approx、14 项 N/A；详见 [docs/compatibility.md](docs/compatibility.md)。
- 39 项外置兼容审计：13 项 L2、15 项 L1、11 项 L0；`goo compat` 生成报告。
- Blender 5.2 的 EEVEE 与 GOO 4.4 的 EEVEE Legacy 存在实现差异；Approx 项在文档中逐条说明。
- 已有 `goo-bridge` 配置会自动迁移到 `GOO Suite` 目录；旧命令保留一个版本。

## 文档

- [用户教程（中英双语）](docs/tutorial.md)
- [CLI 参考](docs/cli.md)
- [远程渲染](docs/remote.md)
- [架构](docs/architecture.md)
- [优化记录](docs/optimizations.md)
- [验证证据](docs/verification.md)

## 开发与测试

```bash
python -m unittest discover -s tests -v
blender --factory-startup --background --python tests/test_addon.py
python tests/test_paths.py
python tools/build_release.py
```

## 许可

GPL-3.0-or-later。GOO Engine 的原始设计归 DillonGoo Studios；本仓库是独立重实现。
