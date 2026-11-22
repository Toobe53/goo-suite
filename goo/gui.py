# SPDX-License-Identifier: GPL-3.0-or-later
"""``goo gui`` — a small dependency free Tkinter front end.

The GUI never imports ``bpy`` and never talks to Blender directly: it shells out
to ``python -m goo <command>`` and streams that process' output into a log
widget, so the CLI stays the single source of truth.  If Tkinter is missing the
command fails with a clear pointer to the CLI instead of a traceback.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import REPO_ROOT
from . import blender as blender_module
from . import config as config_module
from .errors import UsageError

try:  # pragma: no cover - depends on the Python build
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    TK_AVAILABLE = True
    TK_ERROR: Optional[BaseException] = None
except Exception as exc:  # noqa: BLE001 - a missing tkinter is a normal situation
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    TK_AVAILABLE = False
    TK_ERROR = exc

APP_TITLE = "GOO Port — Blender 5.2 NPR toolkit"
MONO_FONT = ("Consolas", 9)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _suggest_cli() -> str:
    return (
        "Use the CLI instead / 请改用命令行：\n"
        "  goo doctor\n"
        "  goo apply scene.blend -o scene_goo.blend\n"
        "  goo render scene.blend -o renders --light-groups\n"
        "  goo composite renders --png"
    )


def run(blender: Optional[str] = None) -> dict:
    """Open the GUI window and block until the user closes it."""
    if not TK_AVAILABLE:
        raise UsageError(
            f"Tkinter is not available in this Python ({type(TK_ERROR).__name__}: {TK_ERROR})",
            hint=(
                "Install a Python build that includes tkinter (python.org builds do) or use the CLI "
                "/ 请安装带 tkinter 的 Python（python.org 版本自带），或直接使用命令行\n" + _suggest_cli()
            ),
        )
    try:
        app = GooApp(blender)
    except Exception as exc:  # noqa: BLE001 - e.g. TclError without a display
        raise UsageError(
            f"cannot open the GUI window: {type(exc).__name__}: {exc}",
            hint="Set DISPLAY (Linux) or run the CLI instead / 请检查图形环境，或使用命令行\n" + _suggest_cli(),
        )
    if os.environ.get("GOO_GUI_SELFTEST"):
        # Headless-friendly smoke test: build the widgets, pump the event loop
        # briefly, then close. Used by the CLI test suite.
        app.root.after(1500, app.root.destroy)
        app.root.after(100, lambda: print("GOO_GUI_SELFTEST: widgets ready", file=sys.stderr, flush=True))
    app.mainloop()
    return {
        "ok": True,
        "command": "gui",
        "opened": True,
        "blender": app.var_blender.get() or None,
        "buttons": list(app.button_labels),
        "fields": list(app.field_labels),
        "operations": app.history,
    }


class GooApp:  # pragma: no cover - interactive UI
    """Main window."""

    def __init__(self, blender: Optional[str] = None) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("980x720")
        self.root.minsize(820, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.process: Optional[subprocess.Popen] = None
        self.worker: Optional[threading.Thread] = None
        self.running = False
        self.history: list[dict] = []
        self.buttons: list = []
        self.button_labels: list[str] = []
        self.field_labels: list[str] = []

        cfg = config_module.load_config()
        detected = ""
        try:
            detected = blender_module.discover(blender).path
        except Exception:  # noqa: BLE001 - the GUI must still open without Blender
            detected = str(cfg.get("blender") or "") if cfg.get("blender") else ""

        self.var_blend = tk.StringVar(value="")
        self.var_out = tk.StringVar(value=str(cfg.get("output_dir") or ""))
        self.var_blender = tk.StringVar(value=detected)
        self.var_samples = tk.StringVar(value=str(cfg.get("samples") or 32))
        self.var_percent = tk.StringVar(value=str(cfg.get("resolution_percentage") or 100))
        self.var_format = tk.StringVar(value=str(cfg.get("format") or "exr"))
        self.var_camera = tk.StringVar(value="")
        self.var_view_layer = tk.StringVar(value="")
        self.var_apply = tk.BooleanVar(value=True)
        self.var_groups = tk.BooleanVar(value=True)
        self.var_composite = tk.BooleanVar(value=True)
        self.var_burn_in = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="Ready / 就绪")

        self._build()
        self._log(f"goo {__import__('goo').__version__}  ·  Python {sys.version.split()[0]}  ·  {sys.platform}")
        if detected:
            self._log(f"Blender: {detected}")
        else:
            self._log("Blender not detected — set the executable field or GOO_BLENDER / 未检测到 Blender")
        self.root.after(120, self._drain)

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        inputs = ttk.LabelFrame(outer, text="Inputs / 输入", padding=8)
        inputs.grid(row=0, column=0, sticky="ew")
        inputs.columnconfigure(1, weight=1)

        self._file_row(inputs, 0, "Scene (.blend) / 场景", self.var_blend, self._pick_blend)
        self._file_row(inputs, 1, "Output directory / 输出目录", self.var_out, self._pick_out, directory=True)
        self._file_row(inputs, 2, "Blender executable / 可执行文件", self.var_blender, self._pick_blender)

        options = ttk.LabelFrame(outer, text="Options / 选项", padding=8)
        options.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column in range(6):
            options.columnconfigure(column, weight=1)

        ttk.Label(options, text="Samples / 采样").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=1, to=4096, increment=8, textvariable=self.var_samples, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(options, text="Resolution % / 分辨率%").grid(row=0, column=2, sticky="e")
        ttk.Spinbox(options, from_=1, to=1000, increment=25, textvariable=self.var_percent, width=8).grid(row=0, column=3, sticky="w")
        ttk.Label(options, text="Format / 格式").grid(row=0, column=4, sticky="e")
        ttk.Combobox(options, textvariable=self.var_format, values=("exr", "png"), width=8, state="readonly").grid(row=0, column=5, sticky="w")

        ttk.Checkbutton(options, text="Apply node library before render / 渲染前应用节点库", variable=self.var_apply).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(options, text="Render light-group passes / 按灯光组渲染", variable=self.var_groups).grid(
            row=1, column=3, columnspan=3, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(options, text="Composite afterwards / 渲染后合成", variable=self.var_composite).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        ttk.Label(options, text="Camera / 相机").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(options, textvariable=self.var_camera).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Checkbutton(options, text="Burn-in note / 烧录文件名与帧", variable=self.var_burn_in).grid(
            row=3, column=3, columnspan=3, sticky="w", pady=(6, 0)
        )
        ttk.Label(options, text="View layer / 视图层").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(options, textvariable=self.var_view_layer).grid(
            row=4, column=1, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Label(options, text="blank = first layer / 留空为第一层", foreground="#666666").grid(
            row=4, column=3, columnspan=3, sticky="w", pady=(6, 0)
        )
        self.field_labels.extend(["Camera / 相机", "View layer / 视图层"])

        buttons = ttk.Frame(outer)
        buttons.grid(row=2, column=0, sticky="ew", pady=8)
        for column in range(10):
            buttons.columnconfigure(column, weight=1)
        specs = [
            ("Doctor", self.on_doctor, 0),
            ("Apply", self.on_apply, 1),
            ("Render", self.on_render, 2),
            ("Composite", self.on_composite, 3),
            ("Analyze", self.on_analyze, 4),
            ("Compat", self.on_compat, 5),
            ("Remote…", self.on_remote, 6),
            ("Verify", self.on_verify, 7),
            ("Install Add-on", self.on_install, 8),
            ("Bundle", self.on_bundle, 9),
        ]
        for label, callback, column in specs:
            button = ttk.Button(buttons, text=label, command=callback)
            button.grid(row=0, column=column, sticky="ew", padx=3)
            self.buttons.append(button)
            self.button_labels.append(label)

        log_frame = ttk.LabelFrame(outer, text="Log / 日志", padding=4)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        self.log = tk.Text(log_frame, height=18, wrap="none", font=MONO_FONT, state="disabled", background="#111418", foreground="#d6dde5")
        scroll_y = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll_x = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")

        status = ttk.Frame(outer)
        status.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.var_status, anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Button(status, text="Clear log / 清空", command=self._clear_log).grid(row=0, column=1, sticky="e")

    def _file_row(self, parent, row: int, label: str, variable, command, directory: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(parent, text="Browse…" if not directory else "Browse…", command=command, width=10).grid(row=row, column=2, sticky="e", pady=2)

    # -- pickers -----------------------------------------------------------
    def _pick_blend(self) -> None:
        path = filedialog.askopenfilename(title="Select a .blend", filetypes=[("Blender files", "*.blend"), ("All files", "*.*")])
        if path:
            self.var_blend.set(path)
            if not self.var_out.get().strip():
                self.var_out.set(str(Path(path).with_suffix("")) + "_renders")

    def _pick_out(self) -> None:
        path = filedialog.askdirectory(title="Select an output directory")
        if path:
            self.var_out.set(path)

    def _pick_blender(self) -> None:
        path = filedialog.askopenfilename(title="Select blender executable", filetypes=[("Blender", "blender*"), ("All files", "*.*")])
        if path:
            self.var_blender.set(path)

    # -- logging -----------------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _drain(self) -> None:
        try:
            while True:
                message = self.queue.get_nowait()
                kind = message[0]
                if kind == "line":
                    self._log(message[1])
                elif kind == "status":
                    self.var_status.set(message[1])
                elif kind == "done":
                    self._finish(message[1], message[2])
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    # -- job running -------------------------------------------------------
    def _finish(self, returncode: int, label: str) -> None:
        self.running = False
        self.process = None
        for button in self.buttons:
            button.configure(state="normal")
        if returncode == 0:
            self.var_status.set(f"{label}: done / 完成")
            self._log(f"[{label}] finished successfully / 完成")
        else:
            self.var_status.set(f"{label}: FAILED (exit {returncode}) / 失败")
            self._log(f"[{label}] FAILED with exit code {returncode} / 失败")

    def _run(self, label: str, commands: list[list[str]]) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "A job is already running / 已有任务在运行")
            return
        self.running = True
        for button in self.buttons:
            button.configure(state="disabled")
        self.var_status.set(f"{label}: running… / 运行中")
        self.worker = threading.Thread(target=self._worker, args=(label, commands), daemon=True)
        self.worker.start()

    def _worker(self, label: str, commands: list[list[str]]) -> None:
        returncode = 0
        for argv in commands:
            self.queue.put(("line", ""))
            self.queue.put(("line", "$ goo " + " ".join(argv)))
            code = self._spawn(argv)
            self.history.append({"command": argv, "returncode": code, "seconds": None})
            if code != 0:
                returncode = code
                break
        self.queue.put(("done", returncode, label))

    def _spawn(self, argv: list[str]) -> int:
        command = [sys.executable, "-u", "-m", "goo", *argv]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        started = time.monotonic()
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT) if REPO_ROOT.is_dir() else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except OSError as exc:
            self.queue.put(("line", f"cannot start goo: {exc}"))
            return 1
        process = self.process
        assert process.stdout is not None
        for line in process.stdout:
            self.queue.put(("line", line.rstrip("\n")))
        returncode = process.wait()
        elapsed = time.monotonic() - started
        self.history[-1]["seconds"] = round(elapsed, 3) if self.history else None
        return returncode

    # -- validation helpers ------------------------------------------------
    def _int_field(self, variable, name: str, minimum: int, maximum: int) -> Optional[int]:
        try:
            value = int(str(variable.get()).strip())
        except ValueError:
            messagebox.showerror(APP_TITLE, f"{name} must be a number / 必须是数字")
            return None
        if not (minimum <= value <= maximum):
            messagebox.showerror(APP_TITLE, f"{name} must be between {minimum} and {maximum}")
            return None
        return value

    def _blend(self) -> Optional[str]:
        path = self.var_blend.get().strip()
        if not path:
            messagebox.showerror(APP_TITLE, "Choose a .blend file first / 请先选择 .blend 文件")
            return None
        if not Path(path).is_file():
            messagebox.showerror(APP_TITLE, f"File not found / 文件不存在:\n{path}")
            return None
        return path

    def _out_dir(self, required: bool = True) -> Optional[str]:
        path = self.var_out.get().strip()
        if not path and required:
            messagebox.showerror(APP_TITLE, "Choose an output directory / 请选择输出目录")
            return None
        return path or None

    def _blender_args(self) -> list[str]:
        path = self.var_blender.get().strip()
        return ["--blender", path] if path else []

    # -- button handlers ---------------------------------------------------
    def on_doctor(self) -> None:
        argv = ["doctor"]
        if self.var_blend.get().strip():
            argv.append(self.var_blend.get().strip())
        self._run("doctor", [argv + self._blender_args()])

    def on_apply(self) -> None:
        blend = self._blend()
        if not blend:
            return
        out_dir = self._out_dir(required=False) or str(Path(blend).parent)
        out_path = str(Path(out_dir) / f"{Path(blend).stem}_goo.blend")
        self._run("apply", [["apply", blend, "-o", out_path, *self._blender_args()]])

    def on_render(self) -> None:
        blend = self._blend()
        if not blend:
            return
        out_dir = self._out_dir()
        if not out_dir:
            return
        samples = self._int_field(self.var_samples, "Samples", 1, 65536)
        if samples is None:
            return
        percent = self._int_field(self.var_percent, "Resolution percentage", 1, 1000)
        if percent is None:
            return
        if self.var_burn_in.get():
            if self.var_format.get() != "png":
                messagebox.showerror(
                    "Burn-in needs PNG / 烧录需要 PNG",
                    "Set Format to png, or disable Burn-in. / 请把格式设为 png，或关闭烧录。",
                    parent=self.root,
                )
                return
            if self.var_groups.get():
                messagebox.showerror(
                    "Burn-in and light groups / 烧录与灯光组",
                    "Burn-in is not supported together with light-group passes. / 烧录不能与按灯光组渲染同时使用。",
                    parent=self.root,
                )
                return
        tasks: list[list[str]] = []
        common = ["--samples", str(samples), "--resolution-percentage", str(percent), "--format", self.var_format.get()]
        if self.var_apply.get():
            applied = str(Path(out_dir) / f"{Path(blend).stem}_goo.blend")
            tasks.append(["apply", blend, "-o", applied, *self._blender_args()])
            render_blend = applied
        else:
            render_blend = blend
        render = ["render", render_blend, "-o", out_dir, *common]
        camera = self.var_camera.get().strip()
        if camera:
            render.extend(["--camera", camera])
        view_layer = self.var_view_layer.get().strip()
        if view_layer:
            render.extend(["--view-layer", view_layer])
        if self.var_groups.get():
            render.append("--light-groups")
        if self.var_burn_in.get():
            render.append("--burn-in")
        render.extend(self._blender_args())
        tasks.append(render)
        if self.var_composite.get():
            composite = ["composite", out_dir]
            if self.var_format.get() == "png":
                composite.append("--png")
            composite.extend(self._blender_args())
            tasks.append(composite)
        self._run("render", tasks)

    def on_analyze(self) -> None:
        blend = self._blend()
        if not blend:
            return
        out_dir = self._out_dir(required=False) or str(Path(blend).parent)
        out_path = str(Path(out_dir) / f"{Path(blend).stem}_analysis.json")
        self._run("analyze", [["analyze", blend, "-o", out_path, *self._blender_args()]])

    def on_compat(self) -> None:
        blend = self._blend()
        if not blend:
            return
        out_dir = self._out_dir(required=False) or str(Path(blend).parent)
        analysis = str(Path(out_dir) / f"{Path(blend).stem}_analysis.json")
        report = str(Path(out_dir) / f"{Path(blend).stem}_compat.html")
        self._run(
            "compat",
            [
                ["analyze", blend, "-o", analysis, *self._blender_args()],
                ["compat", "--analysis", analysis, "-o", report],
            ],
        )

    def on_remote(self) -> None:
        """Open the GOO Suite remote/queue window in its own process."""
        command = [sys.executable, "-m", "goo_bridge.gui"]
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            subprocess.Popen(
                command,
                cwd=str(REPO_ROOT) if REPO_ROOT.is_dir() else None,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                **kwargs,
            )
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Cannot open remote window / 无法打开远程窗口:\n{exc}")

    def on_composite(self) -> None:
        out_dir = self._out_dir()
        if not out_dir:
            return
        argv = ["composite", out_dir]
        if self.var_format.get() == "png":
            argv.append("--png")
        self._run("composite", [argv + self._blender_args()])

    def on_install(self) -> None:
        self._run("install add-on", [["install", *self._blender_args()]])

    def on_bundle(self) -> None:
        self._run("bundle", [["bundle"]])

    def on_verify(self) -> None:
        dist = REPO_ROOT / "dist"
        argv = ["verify"]
        if dist.is_dir():
            argv.extend(["--dist", str(dist)])
        self._run("verify", [argv])

    # -- window lifecycle --------------------------------------------------
    def _on_close(self) -> None:
        if self.running and self.process is not None:
            if not messagebox.askyesno(APP_TITLE, "A job is running. Stop it and quit? / 任务正在运行，是否停止并退出？"):
                return
            try:
                self.process.terminate()
            except OSError:
                pass
        self.root.destroy()

    def mainloop(self) -> None:
        self.root.mainloop()
