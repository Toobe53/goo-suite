# GOO Bridge 使用教程 / User tutorial

GOO Bridge 是一个跨平台的桌面和命令行工具：它不修改 Blender，也不把 Blender
打包进安装包，而是调用你本机（或远程服务器）上的 Blender 5.2 来完成 GOO 风格
NPR 场景的分析和渲染。

GOO Bridge is a cross-platform desktop and command-line tool. It does not modify
Blender and does not bundle Blender. It calls Blender 5.2 on your machine (or on
a remote server) to analyze and render GOO-style NPR scenes.

---

## 1. 安装 / Install

### 从源码 / From source

```bash
git clone https://github.com/Toobe53/goo-bridge.git
cd goo-bridge
python -m pip install .
```

### 从 wheel / From a wheel

从 [Releases](https://github.com/Toobe53/goo-bridge/releases) 下载 `goo_bridge-0.1.0-py3-none-any.whl`
后安装；也可以使用本次 CI 构建的 Actions Artifact：

```bash
python -m pip install goo_bridge-0.1.0-py3-none-any.whl
```

Download the wheel from [Releases](https://github.com/Toobe53/goo-bridge/releases),
or from the `goo-bridge-wheel-*` Actions artifact for this commit, then run:

```bash
python -m pip install goo_bridge-0.1.0-py3-none-any.whl
```

### 检查 / Check

```bash
goo-bridge --version
goo-bridge doctor
```

`doctor` 会报告 Python、配置文件位置、Blender 路径、可选的 GOO 引擎路径和已保存
的远程服务器。没有找到 Blender 时，用环境变量指定：

```bash
# Windows PowerShell
$env:GOO_BRIDGE_BLENDER = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"

# macOS / Linux
export GOO_BRIDGE_BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
```

`doctor` reports Python, the config file, the Blender path, the optional GOO
engine and saved remote profiles. If Blender is not found, set
`GOO_BRIDGE_BLENDER`.

### 系统要求 / Requirements

- Python 3.11 或更新版本 / Python 3.11+
- Blender 5.2.x（本机或远程 / local or remote）
- 图形界面需要 Tkinter；Linux 上安装 `python3-tk`
- 远程渲染需要 OpenSSH 客户端（Windows 10/11 自带 `ssh.exe`）

---

## 2. 第一次打开场景 / First scene

```bash
goo-bridge analyze my_scene.blend -o analysis.json
goo-bridge report analysis.json -o analysis.html
```

分析报告包含：

- Blender 版本、渲染引擎、分辨率
- 场景中所有 `GOO ...` 节点组
- 使用 GOO 节点的材质
- 外部图片、链接库、字体、声音、Movie Clip 等依赖
- 缺失文件列表
- 相机、视图层、GOO 功能标记

The analysis report lists the Blender version, render engine, resolution, all
`GOO ...` node groups, materials using them, external files, missing files,
cameras, view layers and feature flags.

**缺失资产默认不会被隐藏。** 在本地渲染前先看 `missing`；远程渲染默认会阻止
缺失资产的作业，避免服务器静默使用粉色占位图。

Missing assets are never hidden. Check `missing` before rendering; remote jobs
block missing assets by default instead of silently using placeholder images.

---

## 3. 本地渲染 / Local render

```bash
goo-bridge render my_scene.blend -o renders/ \
  --camera Camera \
  --view-layer ViewLayer \
  --samples 32 \
  --resolution 100 \
  --frame 1
```

| 选项 / Option | 说明 / Meaning |
|---|---|
| `--camera NAME` | 使用指定相机 / use a named camera |
| `--view-layer NAME` | 使用指定视图层 / use a named view layer |
| `--samples N` | EEVEE/Cycles 采样数 / samples |
| `--resolution N` | 分辨率百分比 / resolution percentage |
| `--frame 1` / `1-10` / `1,3,5` | 单帧、范围或列表 / frame, range or list |
| `--format PNG` | PNG / JPEG / OPEN_EXR |
| `--json` | 机器可读输出 / machine-readable output |

输出目录里会有 `render_0001.png` 和 `render_manifest.json`。`--json` 输出可直接
交给脚本或 CI。

The output directory contains `render_0001.png` and `render_manifest.json`.
`--json` output is suitable for scripts and CI.

---

## 4. 图形界面 / Desktop GUI

```bash
goo-bridge gui
```

界面分为：

1. **Project**：选择 `.blend` 和 Blender 路径
2. **Analyze**：运行分析并显示 GOO 组、缺失文件数量
3. **Remote**：保存服务器配置、测试 SSH 连接
4. **Queue**：本地或远程渲染，显示进度和日志
5. **Report**：L2/L1/L0 兼容性汇总

如果系统没有显示器，使用 `GOO_BRIDGE_SELFTEST=1 goo-bridge gui` 做无头自检。

Tabs: Project, Analyze, Remote, Queue, Report. On a headless machine use
`GOO_BRIDGE_SELFTEST=1 goo-bridge gui` for a self-test.

---

## 5. 远程渲染 / Remote render

远程机器只需要：

- 能通过 SSH 登录
- 安装 Blender 5.2.x
- 有可写的 `~/.goo-bridge` 目录

Remote host requirements: SSH access, Blender 5.2.x, a writable
`~/.goo-bridge` directory.

### 5.1 配置 SSH 密钥 / Set up a key

```bash
ssh-keygen -t ed25519 -C "goo-bridge"
ssh-copy-id render@10.0.0.5
ssh render@10.0.0.5 blender --version
```

Windows 没有 `ssh-copy-id` 时，把 `~/.ssh/id_ed25519.pub` 内容追加到服务器的
`~/.ssh/authorized_keys`。

On Windows without `ssh-copy-id`, append the public key to the server's
`~/.ssh/authorized_keys`.

### 5.2 添加服务器 / Add a profile

```bash
goo-bridge remote add studio \
  --host 10.0.0.5 \
  --user render \
  --port 22 \
  --key ~/.ssh/id_ed25519 \
  --blender blender \
  --workspace ~/.goo-bridge
```

```bash
goo-bridge remote list
goo-bridge remote test --profile studio
```

密码不会写入配置或任务文件；只使用 SSH 密钥和系统 `known_hosts`。

Passwords are never written to config or job files; only SSH keys and the
system `known_hosts` are used.

### 5.3 提交渲染 / Submit a job

```bash
goo-bridge remote render --profile studio my_scene.blend -o renders/ \
  --camera Camera --samples 32 --resolution 100
```

桥接器会：

1. 分析场景并列出外部资产
2. 计算 SHA-256，拒绝缺失资产（可用显式选项覆盖）
3. 打包 `.blend`、资产、`job.json` 和 worker 脚本
4. 通过 `scp` 上传到 `~/.goo-bridge/jobs/<job-id>`
5. 在服务器上以无头模式运行 Blender
6. 下载结果并校验 SHA-256

The bridge analyzes the scene, hashes assets, refuses missing assets by
default, bundles the scene and worker, uploads with `scp`, runs headless
Blender, downloads outputs and verifies SHA-256.

### 5.4 状态、下载、清理 / Status, fetch, clean

```bash
goo-bridge remote status --profile studio <job-id>
goo-bridge remote fetch --profile studio <job-id> -o renders/
goo-bridge remote clean --profile studio <job-id>
```

任务中断后可以重新提交；已经上传的资产会尽量复用。

---

## 6. 兼容性报告 / Compatibility report

```bash
goo-bridge analyze my_scene.blend -o analysis.json
goo-bridge compat --analysis analysis.json -o compat.html
```

报告把 39 项 GOO 功能分成：

- **L2**：在 1%–2% 视觉误差内可用
- **L1**：意图相同，但有可见差异
- **L0**：明确不支持

L0 不会被伪装成“已支持”；报告会给出原因和替代做法。

The report classifies 39 GOO features as L2 (within 1–2% visual tolerance), L1
(same intent, visible difference) or L0 (explicitly unsupported). L0 items are
never presented as supported.

---

## 7. 常见问题 / Troubleshooting

### `Blender 5.2 was not found`

设置 `GOO_BRIDGE_BLENDER`，或把 Blender 安装到默认目录。`goo-bridge doctor --json`
会列出尝试过的路径。

Set `GOO_BRIDGE_BLENDER` or install Blender in a default location.
`goo-bridge doctor --json` lists attempted paths.

### GUI 打不开 / GUI does not open

Linux 安装 `python3-tk`；远程服务器没有显示器时不要运行 GUI，使用 CLI。

Install `python3-tk` on Linux; use the CLI on headless servers.

### 远程渲染 0xC0000005 / access violation

Windows 无 GPU 的服务器需要 Mesa llvmpipe。GOO Bridge 的 GitHub Actions 示例
使用 `ssciwr/setup-mesa-dist-win`，并设置 `GALLIUM_DRIVER=llvmpipe` 和
`LIBGL_ALWAYS_SOFTWARE=1`。

Windows servers without a GPU need Mesa llvmpipe. The GitHub Actions example uses
`ssciwr/setup-mesa-dist-win`, `GALLIUM_DRIVER=llvmpipe` and
`LIBGL_ALWAYS_SOFTWARE=1`.

### 渲染很慢 / Render is slow

降低 `--samples` 和 `--resolution` 做预览；最终帧再提高。远程渲染受网络上传
速度影响，首次上传资产最慢。

Lower `--samples` and `--resolution` for previews; raise them for final frames.
The first remote upload is dominated by asset transfer.

---

## 8. 安全与许可 / Security and license

- 不保存密码，不执行远程任意 shell / no passwords, no arbitrary remote shell
- 不修改 Blender 安装 / does not modify Blender
- 缺失资产默认阻断 / missing assets block by default
- GPL-3.0-or-later；Blender 不属于本项目的发布物 / GPL-3.0-or-later; Blender is
  not part of this project's distribution

更多细节见 [architecture.md](architecture.md)、[remote.md](remote.md) 和
[compatibility.md](compatibility.md)。
