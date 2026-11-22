# SPDX-License-Identifier: GPL-3.0-or-later
"""Tkinter desktop application for GOO Bridge.

The GUI is deliberately a thin shell around the callable API in
:mod:`goo_bridge` (analyze, render, report, config) and around
``python -m goo_bridge`` for the optional remote transport.

Importing this module must not import tkinter: the CLI runs on servers and in
Blender's bundled Python, neither of which normally ships Tk.  ``run_gui``
imports tkinter lazily and raises ``RuntimeError`` when it is unavailable.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .analyze import analyze
from .blender import find_blender
from .config import (
    Config,
    RemoteProfile,
    config_path,
    load_config,
    save_config,
)
from .render import render
from .report import summarize, write_html, write_json

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

TABS = ("Project", "Analyze", "Remote", "Queue", "Report")

#: Every ttk.Button the window creates.  The headless selftest reports this
#: list so CI can prove the window was fully built.
BUTTONS = (
    "Browse Scene...",
    "Browse Blender...",
    "Detect Blender",
    "Save Settings",
    "Doctor",
    "Run Analysis",
    "Save Profile",
    "Test Connection",
    "Browse Output...",
    "Add Job",
    "Run Queue",
    "Stop Queue",
    "Clear Queue",
    "Refresh Compatibility",
    "Save Report...",
    "Close",
)

DEFAULT_FORMAT = "PNG"
FORMATS = ("PNG", "JPEG", "OPEN_EXR", "TIFF")
LEVELS = ("L2", "L1", "L0")


# ---------------------------------------------------------------------------
# Pure helpers (used by the Tk widgets and unit-tested without tkinter)
# ---------------------------------------------------------------------------
def build_remote_profile(
    *,
    name: str,
    host: str,
    port: int | str = 22,
    user: str = "",
    key: str = "",
    blender: str = "blender",
    workspace: str = "~/.goo-bridge",
    python: str = "python3",
) -> RemoteProfile:
    """Validate GUI fields and return a :class:`RemoteProfile`."""
    cleaned_name = str(name).strip()
    cleaned_host = str(host).strip()
    if not cleaned_name:
        raise ValueError("profile name is required")
    if not cleaned_host:
        raise ValueError("host is required")
    try:
        port_value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("port must be an integer between 1 and 65535") from exc
    if not 1 <= port_value <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return RemoteProfile(
        name=cleaned_name,
        host=cleaned_host,
        port=port_value,
        user=str(user).strip(),
        key=str(key).strip(),
        blender=str(blender).strip() or "blender",
        workspace=str(workspace).strip() or "~/.goo-bridge",
        python=str(python).strip() or "python3",
    )


def local_render_kwargs(
    scene: str | Path,
    output: str | Path,
    *,
    camera: str = "",
    view_layer: str = "",
    samples: int | str = 32,
    resolution: int | str = 100,
    frame: str = "1",
    format: str = DEFAULT_FORMAT,
) -> dict[str, Any]:
    """Validate queue fields and return keyword arguments for ``render``."""
    if not str(scene).strip():
        raise ValueError("scene is required")
    if not str(output).strip():
        raise ValueError("output folder is required")
    try:
        samples_value = int(samples)
    except (TypeError, ValueError) as exc:
        raise ValueError("samples must be an integer") from exc
    if samples_value < 1:
        raise ValueError("samples must be at least 1")
    try:
        resolution_value = int(resolution)
    except (TypeError, ValueError) as exc:
        raise ValueError("resolution must be an integer percentage") from exc
    if not 1 <= resolution_value <= 100:
        raise ValueError("resolution must be between 1 and 100")
    return {
        "camera": str(camera).strip(),
        "view_layer": str(view_layer).strip(),
        "samples": samples_value,
        "resolution": resolution_value,
        "frame": str(frame).strip() or "1",
        "format": str(format).strip().upper() or DEFAULT_FORMAT,
    }


def _python_executable() -> str:
    return sys.executable or "python"


def remote_test_command(profile_name: str, *, python: Optional[str] = None) -> list[str]:
    """Command line for ``goo-bridge remote test --profile <profile>``."""
    name = str(profile_name).strip()
    if not name:
        raise ValueError("a remote profile name is required")
    return [
        python or _python_executable(),
        "-m",
        "goo_bridge",
        "remote",
        "test",
        "--profile",
        name,
    ]


def remote_render_command(
    profile_name: str,
    scene: str | Path,
    output: str | Path,
    *,
    python: Optional[str] = None,
    camera: str = "",
    view_layer: str = "",
    samples: int | str = 32,
    resolution: int | str = 100,
    frame: str = "1",
    format: str = DEFAULT_FORMAT,
) -> list[str]:
    """Command line for ``goo-bridge remote render``.

    The remote command takes the scene as a positional argument and the
    profile through ``--profile``; every render option exposed by the GUI is
    forwarded so a queued remote job renders exactly like a local one.
    """
    name = str(profile_name).strip()
    if not name:
        raise ValueError("a remote profile name is required")
    options = local_render_kwargs(
        scene,
        output,
        camera=camera,
        view_layer=view_layer,
        samples=samples,
        resolution=resolution,
        frame=frame,
        format=format,
    )
    command = [
        python or _python_executable(),
        "-m",
        "goo_bridge",
        "remote",
        "render",
        str(scene).strip(),
        "-o",
        str(output).strip(),
        "--profile",
        name,
        "--samples",
        str(options["samples"]),
        "--resolution",
        str(options["resolution"]),
        "--frame",
        options["frame"],
        "--format",
        options["format"],
    ]
    if options["camera"]:
        command.extend(["--camera", options["camera"]])
    if options["view_layer"]:
        command.extend(["--view-layer", options["view_layer"]])
    return command


def compat_items(payload: Any) -> list[Mapping[str, Any]]:
    """Return the item rows from a compatibility report payload."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("items", "rows", "checks", "matrix", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        values = [value for value in payload.values() if isinstance(value, Mapping)]
        if values:
            return values
    return []


def compat_counts(payload: Any) -> dict[str, int]:
    """Count L2/L1/L0 rows in a compatibility report payload."""
    counts = {level: 0 for level in LEVELS}
    for item in compat_items(payload):
        level = str(item.get("level") or item.get("tier") or "").strip().upper()
        if level in counts:
            counts[level] += 1
    if not any(counts.values()) and isinstance(payload, Mapping):
        raw = payload.get("counts")
        if isinstance(raw, Mapping):
            for level in LEVELS:
                try:
                    counts[level] = int(raw.get(level, 0))
                except (TypeError, ValueError):
                    counts[level] = 0
    return counts


def format_analysis_summary(report: Mapping[str, Any]) -> str:
    """Human readable multi-line analysis summary."""
    summary = summarize(dict(report))
    lines = [
        f"File: {summary['file'] or '(unnamed)'}",
        f"Blender: {summary['blender'] or '(unknown)'}",
        f"GOO groups: {summary['goo_groups']}",
        f"Objects: {summary['objects']}",
        f"Materials: {summary['materials']}",
        f"External files: {summary['external_files']}",
        f"Missing files: {summary['missing_files']}",
    ]
    if summary["group_names"]:
        lines.append("")
        lines.append("GOO groups found:")
        lines.extend(f"  - {name}" for name in summary["group_names"])
    if summary["missing"]:
        lines.append("")
        lines.append("Missing external files:")
        lines.extend(f"  - {name}" for name in summary["missing"])
    return "\n".join(lines)


def collect_button_labels(root: Any) -> list[str]:
    """Collect the text of every ``ttk.Button``/``tk.Button`` below *root*."""
    labels: list[str] = []

    def walk(widget: Any) -> None:
        try:
            kind = widget.winfo_class()
        except Exception:  # pragma: no cover - destroyed widget during selftest
            return
        if kind in {"TButton", "Button"}:
            try:
                text = str(widget.cget("text")).strip()
            except Exception:
                text = ""
            if text:
                labels.append(text)
        try:
            children = widget.winfo_children()
        except Exception:
            return
        for child in children:
            walk(child)

    walk(root)
    return labels


def _child_env() -> dict[str, str]:
    """Environment for child ``python -m goo_bridge`` calls."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PACKAGE_ROOT) + (os.pathsep + existing if existing else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _selftest_payload(
    *,
    opened: bool,
    buttons: Optional[list[str]] = None,
    skipped: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "opened": opened,
        "buttons": list(buttons if buttons else BUTTONS),
        "tabs": list(TABS),
        "version": __version__,
    }
    if skipped:
        payload["skipped"] = skipped
    return payload


# ---------------------------------------------------------------------------
# Tk application
# ---------------------------------------------------------------------------
class _App:
    """The tabbed GOO Bridge window.  Only instantiated when Tk exists."""

    def __init__(
        self,
        root: Any,
        tk: Any,
        ttk: Any,
        filedialog: Any,
        messagebox: Any,
        config: Optional[Config] = None,
    ) -> None:
        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.config = config or load_config()
        self.events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.closing = False
        self.after_id: Optional[str] = None
        self.analysis: Optional[dict[str, Any]] = None
        self.jobs: list[dict[str, Any]] = []
        self.queue_thread: Optional[threading.Thread] = None
        self.queue_active = False
        self.stop_requested = False
        self.current_process: Optional[subprocess.Popen[str]] = None
        self._build_variables()
        self._build_window()
        self._poll_events()

    # -- setup ---------------------------------------------------------
    def _build_variables(self) -> None:
        tk = self.tk
        self.status_var = tk.StringVar(value="Ready.")
        self.scene_var = tk.StringVar(value="")
        self.blender_var = tk.StringVar(value=self.config.blender or "")
        self.project_info_var = tk.StringVar(value=f"Config: {config_path()}")

        self.analysis_vars = {
            key: tk.StringVar(value="-")
            for key in ("file", "blender", "groups", "objects", "materials", "external", "missing")
        }

        self.remote_name_var = tk.StringVar(value="")
        self.remote_host_var = tk.StringVar(value="")
        self.remote_port_var = tk.StringVar(value="22")
        self.remote_user_var = tk.StringVar(value="")
        self.remote_key_var = tk.StringVar(value="")
        self.remote_blender_var = tk.StringVar(value="blender")
        self.remote_workspace_var = tk.StringVar(value="~/.goo-bridge")

        self.queue_scene_var = tk.StringVar(value="")
        self.queue_output_var = tk.StringVar(value="")
        self.queue_camera_var = tk.StringVar(value="")
        self.queue_samples_var = tk.StringVar(value="32")
        self.queue_frame_var = tk.StringVar(value="1")
        self.queue_format_var = tk.StringVar(value=DEFAULT_FORMAT)
        self.queue_mode_var = tk.StringVar(value="local")
        self.queue_profile_var = tk.StringVar(value="")

        self.compat_vars = {level: tk.StringVar(value="-") for level in LEVELS}
        self.compat_status_var = tk.StringVar(value="Run an analysis to see the L2/L1/L0 summary.")
        self.report_file_var = tk.StringVar(value="File: -")
        self.report_blender_var = tk.StringVar(value="Blender: -")
        self.report_groups_var = tk.StringVar(value="GOO groups: -")
        self.report_objects_var = tk.StringVar(value="Objects: -")
        self.report_materials_var = tk.StringVar(value="Materials: -")
        self.report_missing_var = tk.StringVar(value="Missing files: -")

    def _build_window(self) -> None:
        root = self.root
        ttk = self.ttk
        style = ttk.Style(root)
        for theme in ("vista", "clam", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        family = "Segoe UI" if sys.platform == "win32" else "TkDefaultFont"
        style.configure("Title.TLabel", font=(family, 17, "bold"))
        style.configure("Hint.TLabel", foreground="#555555")
        style.configure("L2.TLabel", foreground="#146c2e", font=(family, 11, "bold"))
        style.configure("L1.TLabel", foreground="#9a5b00", font=(family, 11, "bold"))
        style.configure("L0.TLabel", foreground="#a40000", font=(family, 11, "bold"))

        root.title(f"GOO Bridge {__version__}")
        root.geometry("1040x720")
        root.minsize(860, 580)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, padding=(12, 10, 12, 4))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="GOO Bridge", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="GOO NPR rendering for stock Blender 5.2",
            style="Hint.TLabel",
        ).pack(side="left", padx=(12, 0))
        ttk.Button(header, text="Close", command=self.close).pack(side="right")

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 8))

        self._build_project_tab()
        self._build_analyze_tab()
        self._build_remote_tab()
        self._build_queue_tab()
        self._build_report_tab()

        status = ttk.Label(
            root,
            textvariable=self.status_var,
            anchor="w",
            relief="sunken",
            padding=(6, 3),
        )
        status.grid(row=2, column=0, sticky="ew")

        self.button_labels = collect_button_labels(root)
        self._refresh_profile_choices()

    # -- small widget helpers -----------------------------------------
    def _entry_row(
        self,
        parent: Any,
        row: int,
        label: str,
        variable: Any,
        *,
        browse_text: str = "",
        browse_command: Any = None,
    ) -> Any:
        ttk = self.ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if browse_text and browse_command is not None:
            ttk.Button(parent, text=browse_text, command=browse_command).grid(
                row=row, column=2, sticky="w", padx=(6, 0), pady=3
            )
        return entry

    def _make_log(self, parent: Any, *, height: int = 10) -> Any:
        tk = self.tk
        frame = tk.Frame(parent)
        text = tk.Text(
            frame,
            height=height,
            wrap="none",
            state="disabled",
            font="TkFixedFont",
            background="#fdfdfd",
        )
        y_scroll = tk.Scrollbar(frame, orient="vertical", command=text.yview)
        x_scroll = tk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        return frame, text

    def _log(self, text_widget: Any, message: str) -> None:
        if text_widget is None:
            return
        text_widget.configure(state="normal")
        text_widget.insert("end", message.rstrip() + "\n")
        line_count = int(text_widget.index("end-1c").split(".")[0])
        if line_count > 4000:
            text_widget.delete("1.0", "500.0")
        text_widget.see("end")
        text_widget.configure(state="disabled")

    def _set_text(self, text_widget: Any, message: str) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", message)
        text_widget.configure(state="disabled")

    # -- tabs ----------------------------------------------------------
    def _build_project_tab(self) -> None:
        ttk = self.ttk
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=TABS[0])
        frame.columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="Choose the scene to inspect and the local Blender 5.2 executable.",
            style="Hint.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self._entry_row(
            frame,
            1,
            "Scene (.blend)",
            self.scene_var,
            browse_text="Browse Scene...",
            browse_command=self._browse_scene,
        )
        self._entry_row(
            frame,
            2,
            "Blender executable",
            self.blender_var,
            browse_text="Browse Blender...",
            browse_command=self._browse_blender,
        )

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(actions, text="Detect Blender", command=self._detect_blender).pack(side="left")
        ttk.Button(actions, text="Save Settings", command=self._save_settings).pack(side="left", padx=6)
        ttk.Button(actions, text="Doctor", command=self._doctor).pack(side="left")

        ttk.Label(
            frame,
            textvariable=self.project_info_var,
            style="Hint.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(16, 0))

        ttk.Label(
            frame,
            text=(
                "Tip: `Detect Blender` searches GOO_BRIDGE_BLENDER, PATH and the "
                "standard Blender Foundation install folders."
            ),
            style="Hint.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_analyze_tab(self) -> None:
        ttk = self.ttk
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=TABS[1])
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)

        ttk.Label(frame, text="Scene:").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.scene_var, style="Hint.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        self.analysis_button = ttk.Button(frame, text="Run Analysis", command=self._run_analysis)
        self.analysis_button.grid(row=1, column=0, sticky="w", pady=(8, 8))

        counts = ttk.LabelFrame(frame, text="Counts", padding=8)
        counts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        pairs = (
            ("GOO groups", "groups"),
            ("Objects", "objects"),
            ("Materials", "materials"),
            ("External files", "external"),
            ("Missing files", "missing"),
            ("Blender", "blender"),
        )
        for index, (label, key) in enumerate(pairs):
            column = index % 3
            row = index // 3
            ttk.Label(counts, text=f"{label}:").grid(
                row=row, column=column * 2, sticky="e", padx=(0, 6), pady=2
            )
            ttk.Label(counts, textvariable=self.analysis_vars[key]).grid(
                row=row, column=column * 2 + 1, sticky="w", padx=(0, 18), pady=2
            )

        log_frame, self.analyze_text = self._make_log(frame, height=14)
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self._log(self.analyze_text, "Analysis output appears here.")

    def _build_remote_tab(self) -> None:
        ttk = self.ttk
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=TABS[2])
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(9, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Saved profile:").pack(side="left")
        self.profile_combo = ttk.Combobox(top, textvariable=self.remote_name_var, state="readonly", width=28)
        self.profile_combo.pack(side="left", padx=(6, 0))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        self._entry_row(frame, 1, "Profile name", self.remote_name_var)
        self._entry_row(frame, 2, "Host", self.remote_host_var)
        self._entry_row(frame, 3, "SSH port", self.remote_port_var)
        self._entry_row(frame, 4, "User", self.remote_user_var)
        self._entry_row(frame, 5, "SSH key", self.remote_key_var)
        self._entry_row(frame, 6, "Remote Blender", self.remote_blender_var)
        self._entry_row(frame, 7, "Remote workspace", self.remote_workspace_var)

        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(actions, text="Save Profile", command=self._save_profile).pack(side="left")
        ttk.Button(actions, text="Test Connection", command=self._test_connection).pack(
            side="left", padx=6
        )

        log_frame, self.remote_text = self._make_log(frame, height=8)
        log_frame.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        self._log(
            self.remote_text,
            "Remote transport is provided by goo_bridge.remote; Test Connection "
            "runs `python -m goo_bridge remote test <profile>`.",
        )

    def _build_queue_tab(self) -> None:
        ttk = self.ttk
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=TABS[3])
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        frame.rowconfigure(3, weight=1)

        form = ttk.LabelFrame(frame, text="Job", padding=8)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        self._entry_row(form, 0, "Scene (.blend)", self.queue_scene_var)
        self._entry_row(
            form,
            1,
            "Output folder",
            self.queue_output_var,
            browse_text="Browse Output...",
            browse_command=self._browse_output,
        )

        row = ttk.Frame(form)
        row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(row, text="Camera").pack(side="left")
        ttk.Entry(row, textvariable=self.queue_camera_var, width=18).pack(side="left", padx=(4, 14))
        ttk.Label(row, text="Samples").pack(side="left")
        ttk.Spinbox(row, from_=1, to=4096, textvariable=self.queue_samples_var, width=7).pack(
            side="left", padx=(4, 14)
        )
        ttk.Label(row, text="Frame(s)").pack(side="left")
        ttk.Entry(row, textvariable=self.queue_frame_var, width=10).pack(side="left", padx=(4, 14))
        ttk.Label(row, text="Format").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.queue_format_var,
            values=FORMATS,
            state="readonly",
            width=10,
        ).pack(side="left", padx=(4, 0))

        row = ttk.Frame(form)
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(row, text="Mode").pack(side="left")
        ttk.Radiobutton(row, text="Local", variable=self.queue_mode_var, value="local").pack(
            side="left", padx=(4, 8)
        )
        ttk.Radiobutton(row, text="Remote", variable=self.queue_mode_var, value="remote").pack(
            side="left", padx=(0, 8)
        )
        ttk.Label(row, text="Remote profile").pack(side="left", padx=(8, 4))
        self.queue_profile_combo = ttk.Combobox(
            row, textvariable=self.queue_profile_var, state="readonly", width=22
        )
        self.queue_profile_combo.pack(side="left")

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.add_job_button = ttk.Button(actions, text="Add Job", command=self._add_job)
        self.add_job_button.pack(side="left")
        self.run_queue_button = ttk.Button(actions, text="Run Queue", command=self._run_queue)
        self.run_queue_button.pack(side="left", padx=6)
        self.stop_queue_button = ttk.Button(actions, text="Stop Queue", command=self._stop_queue)
        self.stop_queue_button.pack(side="left")
        ttk.Button(actions, text="Clear Queue", command=self._clear_queue).pack(side="left", padx=6)

        self.queue_progress = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.queue_progress.pack(side="right")

        self.job_tree = ttk.Treeview(
            frame,
            columns=("scene", "mode", "output", "status"),
            show="headings",
            height=6,
        )
        for column, title, width in (
            ("scene", "Scene", 360),
            ("mode", "Mode", 80),
            ("output", "Output", 300),
            ("status", "Status", 120),
        ):
            self.job_tree.heading(column, text=title)
            self.job_tree.column(column, width=width, stretch=True)
        self.job_tree.grid(row=2, column=0, sticky="nsew")
        self.job_tree.tag_configure("pending", foreground="#555555")
        self.job_tree.tag_configure("running", foreground="#005a9e")
        self.job_tree.tag_configure("done", foreground="#146c2e")
        self.job_tree.tag_configure("failed", foreground="#a40000")

        log_frame, self.queue_text = self._make_log(frame, height=10)
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self._log(self.queue_text, "Queued renders and their output appear here.")

    def _build_report_tab(self) -> None:
        ttk = self.ttk
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text=TABS[4])
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        summary = ttk.LabelFrame(frame, text="Analysis summary", padding=8)
        summary.grid(row=0, column=0, sticky="ew")
        for index, variable in enumerate(
            (
                self.report_file_var,
                self.report_blender_var,
                self.report_groups_var,
                self.report_objects_var,
                self.report_materials_var,
                self.report_missing_var,
            )
        ):
            ttk.Label(summary, textvariable=variable).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 24), pady=2
            )

        compat = ttk.LabelFrame(frame, text="Compatibility (L2 / L1 / L0)", padding=8)
        compat.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for index, level in enumerate(LEVELS):
            ttk.Label(
                compat,
                text=f"{level}:",
                style=f"{level}.TLabel",
            ).grid(row=0, column=index * 2, sticky="e", padx=(0, 6))
            ttk.Label(compat, textvariable=self.compat_vars[level]).grid(
                row=0, column=index * 2 + 1, sticky="w", padx=(0, 24)
            )
        ttk.Button(
            compat, text="Refresh Compatibility", command=self._refresh_compat
        ).grid(row=0, column=6, sticky="w", padx=(12, 0))
        ttk.Label(
            compat,
            textvariable=self.compat_status_var,
            style="Hint.TLabel",
            wraplength=820,
            justify="left",
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))

        self.compat_tree = ttk.Treeview(
            frame,
            columns=("id", "level", "detail"),
            show="headings",
            height=9,
        )
        for column, title, width in (
            ("id", "Item", 160),
            ("level", "Level", 70),
            ("detail", "Detail", 620),
        ):
            self.compat_tree.heading(column, text=title)
            self.compat_tree.column(column, width=width, stretch=True)
        self.compat_tree.grid(row=3, column=0, sticky="nsew", pady=(8, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="Save Report...", command=self._save_report).pack(side="left")
        ttk.Label(
            actions,
            text="Writes the analysis as self-contained HTML or JSON.",
            style="Hint.TLabel",
        ).pack(side="left", padx=10)

    # -- project actions ----------------------------------------------
    def _browse_scene(self) -> None:
        path = self.filedialog.askopenfilename(
            title="Choose a Blender scene",
            filetypes=(("Blender files", "*.blend"), ("All files", "*.*")),
        )
        if path:
            self.scene_var.set(path)
            if not self.queue_scene_var.get().strip():
                self.queue_scene_var.set(path)
            self._set_status(f"Scene: {path}")

    def _browse_blender(self) -> None:
        path = self.filedialog.askopenfilename(
            title="Choose the Blender executable",
            filetypes=(("Blender", "blender.exe"), ("All files", "*.*"))
            if sys.platform == "win32"
            else (("All files", "*"),),
        )
        if path:
            self.blender_var.set(path)
            self._set_status(f"Blender: {path}")

    def _browse_output(self) -> None:
        path = self.filedialog.askdirectory(title="Choose the render output folder")
        if path:
            self.queue_output_var.set(path)
            self._set_status(f"Output: {path}")

    def _save_settings(self) -> None:
        self.config.blender = self.blender_var.get().strip()
        try:
            path = save_config(self.config)
        except OSError as exc:
            self.messagebox.showerror("GOO Bridge", f"Could not save settings: {exc}")
            self._set_status("Could not save settings.")
            return
        self.project_info_var.set(f"Saved config: {path}")
        self._set_status("Settings saved.")

    def _detect_blender(self) -> None:
        self._set_status("Looking for Blender...")
        self._thread("doctor", lambda: self._doctor_payload())

    def _doctor(self) -> None:
        self._set_status("Running doctor...")
        self._thread("doctor", lambda: self._doctor_payload())

    def _doctor_payload(self) -> dict[str, Any]:
        explicit = self.blender_var.get().strip()
        info = find_blender(explicit or None)
        return {
            "path": str(info.path),
            "version": info.version,
            "config": str(config_path()),
            "profiles": [profile.name for profile in self.config.remote],
        }

    def _on_doctor_done(self, payload: Mapping[str, Any]) -> None:
        self.blender_var.set(str(payload.get("path", "")))
        self.project_info_var.set(
            f"Blender {payload.get('version', '?')} · config {payload.get('config', '?')}"
        )
        self._set_status("Blender detected.")
        profiles = payload.get("profiles") or []
        self.messagebox.showinfo(
            "GOO Bridge doctor",
            "Blender: {path}\nVersion: {version}\nConfig: {config}\nRemote profiles: {profiles}".format(
                path=payload.get("path", ""),
                version=payload.get("version", ""),
                config=payload.get("config", ""),
                profiles=", ".join(profiles) if profiles else "none",
            ),
        )

    def _on_doctor_error(self, message: str) -> None:
        self._set_status("Blender not found.")
        self.messagebox.showerror("GOO Bridge", f"Could not find Blender:\n{message}")

    # -- analysis ------------------------------------------------------
    def _run_analysis(self) -> None:
        scene_text = self.scene_var.get().strip()
        if not scene_text:
            self.messagebox.showwarning("GOO Bridge", "Choose a .blend scene first.")
            return
        scene = Path(scene_text).expanduser()
        if not scene.is_file():
            self.messagebox.showerror("GOO Bridge", f"Scene not found:\n{scene}")
            return
        blender_text = self.blender_var.get().strip()
        self.analysis_button.configure(state="disabled")
        self._set_status("Analyzing scene...")
        self._log(self.analyze_text, f"Analyzing {scene} ...")

        def work() -> dict[str, Any]:
            info = find_blender(blender_text or None)
            return analyze(scene, blender=info)

        self._thread("analysis", work)

    def _on_analysis_done(self, report: Mapping[str, Any]) -> None:
        self.analysis = dict(report)
        summary = summarize(self.analysis)
        values = self.analysis_vars
        values["file"].set(str(summary["file"] or "-"))
        values["blender"].set(str(summary["blender"] or "-"))
        values["groups"].set(str(summary["goo_groups"]))
        values["objects"].set(str(summary["objects"]))
        values["materials"].set(str(summary["materials"]))
        values["external"].set(str(summary["external_files"]))
        values["missing"].set(str(summary["missing_files"]))
        self._set_text(self.analyze_text, format_analysis_summary(self.analysis))
        self._update_report_summary(summary)
        self.analysis_button.configure(state="normal")
        self._set_status("Analysis complete.")
        self._refresh_compat(silent=True)

    def _on_analysis_error(self, message: str) -> None:
        self.analysis_button.configure(state="normal")
        self._set_status("Analysis failed.")
        self._log(self.analyze_text, f"ERROR: {message}")
        self.messagebox.showerror("GOO Bridge", f"Analysis failed:\n{message}")

    def _update_report_summary(self, summary: Mapping[str, Any]) -> None:
        self.report_file_var.set(f"File: {summary.get('file') or '-'}")
        self.report_blender_var.set(f"Blender: {summary.get('blender') or '-'}")
        self.report_groups_var.set(f"GOO groups: {summary.get('goo_groups', 0)}")
        self.report_objects_var.set(f"Objects: {summary.get('objects', 0)}")
        self.report_materials_var.set(f"Materials: {summary.get('materials', 0)}")
        self.report_missing_var.set(f"Missing files: {summary.get('missing_files', 0)}")

    # -- remote profiles ----------------------------------------------
    def _refresh_profile_choices(self) -> None:
        names = [profile.name for profile in self.config.remote]
        if hasattr(self, "profile_combo"):
            self.profile_combo.configure(values=names)
        if hasattr(self, "queue_profile_combo"):
            self.queue_profile_combo.configure(values=names)
        if names and not self.queue_profile_var.get().strip():
            self.queue_profile_var.set(names[0])
        if names and not self.remote_name_var.get().strip():
            self.remote_name_var.set(names[0])
            self._load_profile(names[0])

    def _on_profile_selected(self, _event: Any = None) -> None:
        name = self.remote_name_var.get().strip()
        if name:
            self._load_profile(name)

    def _load_profile(self, name: str) -> None:
        try:
            profile = self.config.profile(name)
        except KeyError:
            return
        self.remote_name_var.set(profile.name)
        self.remote_host_var.set(profile.host)
        self.remote_port_var.set(str(profile.port))
        self.remote_user_var.set(profile.user)
        self.remote_key_var.set(profile.key)
        self.remote_blender_var.set(profile.blender)
        self.remote_workspace_var.set(profile.workspace)

    def _profile_from_fields(self) -> RemoteProfile:
        return build_remote_profile(
            name=self.remote_name_var.get(),
            host=self.remote_host_var.get(),
            port=self.remote_port_var.get(),
            user=self.remote_user_var.get(),
            key=self.remote_key_var.get(),
            blender=self.remote_blender_var.get(),
            workspace=self.remote_workspace_var.get(),
        )

    def _save_profile(self, *, quiet: bool = False) -> Optional[RemoteProfile]:
        try:
            profile = self._profile_from_fields()
        except ValueError as exc:
            self.messagebox.showerror("GOO Bridge", str(exc))
            return None
        self.config.set_profile(profile)
        try:
            path = save_config(self.config)
        except OSError as exc:
            self.messagebox.showerror("GOO Bridge", f"Could not save profile: {exc}")
            return None
        self._refresh_profile_choices()
        self._set_status(f"Profile '{profile.name}' saved to {path}")
        if not quiet:
            self.messagebox.showinfo("GOO Bridge", f"Saved remote profile '{profile.name}'.")
        return profile

    def _test_connection(self) -> None:
        profile = self._save_profile(quiet=True)
        if profile is None:
            return
        command = remote_test_command(profile.name)
        self._set_status(f"Testing {profile.name}...")
        self._log(self.remote_text, "Command: " + " ".join(command))

        def work() -> dict[str, Any]:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                cwd=str(PACKAGE_ROOT),
                env=_child_env(),
            )
            return {
                "code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }

        self._thread("remote_test", work)

    def _on_remote_test_done(self, payload: Mapping[str, Any]) -> None:
        output = (payload.get("stdout") or "").strip()
        error = (payload.get("stderr") or "").strip()
        code = int(payload.get("code", 0))
        if output:
            self._log(self.remote_text, output)
        if error:
            self._log(self.remote_text, error)
        if code == 0:
            self._set_status("Remote connection OK.")
            self.messagebox.showinfo("GOO Bridge", "Remote connection test succeeded.")
        else:
            self._set_status(f"Remote connection failed (exit {code}).")
            self.messagebox.showwarning(
                "GOO Bridge",
                "Remote connection test failed (exit {code}).\n\n{detail}".format(
                    code=code, detail=(error or output or "no output")[-1500:]
                ),
            )

    def _on_remote_test_error(self, message: str) -> None:
        self._set_status("Remote connection test failed.")
        self._log(self.remote_text, f"ERROR: {message}")
        self.messagebox.showerror("GOO Bridge", message)

    # -- queue ---------------------------------------------------------
    def _add_job(self) -> None:
        if self.queue_active:
            self.messagebox.showwarning("GOO Bridge", "Wait for the current queue to finish.")
            return
        try:
            kwargs = local_render_kwargs(
                self.queue_scene_var.get(),
                self.queue_output_var.get(),
                camera=self.queue_camera_var.get(),
                samples=self.queue_samples_var.get(),
                resolution=100,
                frame=self.queue_frame_var.get(),
                format=self.queue_format_var.get(),
            )
        except ValueError as exc:
            self.messagebox.showerror("GOO Bridge", str(exc))
            return
        scene = Path(self.queue_scene_var.get().strip()).expanduser()
        if not scene.is_file():
            self.messagebox.showerror("GOO Bridge", f"Scene not found:\n{scene}")
            return
        mode = self.queue_mode_var.get()
        profile = self.queue_profile_var.get().strip()
        if mode == "remote" and not profile:
            self.messagebox.showerror("GOO Bridge", "Choose a remote profile for remote jobs.")
            return
        job = {
            "scene": str(scene),
            "output": str(Path(self.queue_output_var.get().strip()).expanduser()),
            "mode": mode,
            "profile": profile,
            "kwargs": kwargs,
            "status": "pending",
        }
        self.jobs.append(job)
        index = len(self.jobs) - 1
        self.job_tree.insert(
            "",
            "end",
            iid=str(index),
            values=(scene.name, mode, job["output"], "pending"),
            tags=("pending",),
        )
        self._log(
            self.queue_text,
            f"Added job {index + 1}: {scene} [{mode}] samples={kwargs['samples']}",
        )
        self._set_status(f"{len(self.jobs)} queued job(s).")

    def _run_queue(self) -> None:
        if self.queue_active:
            return
        pending = [index for index, job in enumerate(self.jobs) if job["status"] == "pending"]
        if not pending:
            self.messagebox.showinfo("GOO Bridge", "Add a pending job first.")
            return
        self.queue_active = True
        self.stop_requested = False
        self.queue_progress.start(12)
        self.run_queue_button.configure(state="disabled")
        self._set_status(f"Rendering {len(pending)} job(s)...")
        self.queue_thread = threading.Thread(
            target=self._queue_worker, args=(pending,), daemon=True
        )
        self.queue_thread.start()

    def _stop_queue(self) -> None:
        if not self.queue_active:
            self._set_status("Queue is not running.")
            return
        self.stop_requested = True
        process = self.current_process
        if process is not None and process.poll() is None:
            process.terminate()
        self._log(self.queue_text, "Stop requested; finishing the current job cleanly.")
        self._set_status("Stopping after the current job...")

    def _clear_queue(self) -> None:
        if self.queue_active:
            self.messagebox.showwarning("GOO Bridge", "Wait for the queue to finish.")
            return
        self.jobs.clear()
        for item in self.job_tree.get_children():
            self.job_tree.delete(item)
        self._log(self.queue_text, "Queue cleared.")
        self._set_status("Queue cleared.")

    def _queue_worker(self, indices: list[int]) -> None:
        for index in indices:
            if self.stop_requested:
                break
            job = self.jobs[index]
            self.events.put(("queue_status", (index, "running")))
            try:
                if job["mode"] == "local":
                    self.events.put(("queue_log", f"Local render: {job['scene']}"))
                    result = render(job["scene"], job["output"], **job["kwargs"])
                    if result.log:
                        self.events.put(("queue_log", result.log[-2000:]))
                    if result.ok:
                        detail = f"{len(result.files)} file(s) in {result.duration:.2f}s"
                    else:
                        detail = result.error or "render failed"
                    self.events.put(("queue_result", (index, result.ok, detail)))
                else:
                    command = remote_render_command(
                        job["profile"],
                        job["scene"],
                        job["output"],
                        camera=job["kwargs"].get("camera", ""),
                        view_layer=job["kwargs"].get("view_layer", ""),
                        samples=job["kwargs"].get("samples", 32),
                        resolution=job["kwargs"].get("resolution", 100),
                        frame=job["kwargs"].get("frame", "1"),
                        format=job["kwargs"].get("format", DEFAULT_FORMAT),
                    )
                    self.events.put(("queue_log", "Command: " + " ".join(command)))
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        cwd=str(PACKAGE_ROOT),
                        env=_child_env(),
                    )
                    self.current_process = process
                    if process.stdout is not None:
                        for line in process.stdout:
                            self.events.put(("queue_log", line.rstrip()))
                            if self.stop_requested:
                                process.terminate()
                                break
                    code = process.wait()
                    self.current_process = None
                    self.events.put(
                        ("queue_result", (index, code == 0, f"remote exit code {code}"))
                    )
            except Exception as exc:  # keep the queue alive after one bad job
                self.events.put(("queue_log", f"Job {index + 1} failed: {exc}"))
                self.events.put(("queue_result", (index, False, str(exc))))
        self.events.put(("queue_finished", None))

    def _on_queue_log(self, message: str) -> None:
        self._log(self.queue_text, str(message))

    def _on_queue_status(self, payload: tuple[int, str]) -> None:
        index, status = payload
        if not 0 <= index < len(self.jobs):
            return
        self.jobs[index]["status"] = status
        self._update_job_row(index)

    def _on_queue_result(self, payload: tuple[int, bool, str]) -> None:
        index, ok, detail = payload
        if not 0 <= index < len(self.jobs):
            return
        status = "done" if ok else "failed"
        self.jobs[index]["status"] = status
        self._update_job_row(index)
        self._log(self.queue_text, f"Job {index + 1} {status}: {detail}")

    def _update_job_row(self, index: int) -> None:
        job = self.jobs[index]
        values = (
            Path(job["scene"]).name,
            job["mode"],
            job["output"],
            job["status"],
        )
        try:
            self.job_tree.item(str(index), values=values, tags=(job["status"],))
        except Exception:
            pass

    def _on_queue_finished(self, _payload: Any) -> None:
        self.queue_active = False
        self.queue_thread = None
        self.current_process = None
        self.queue_progress.stop()
        self.run_queue_button.configure(state="normal")
        done = sum(1 for job in self.jobs if job["status"] == "done")
        failed = sum(1 for job in self.jobs if job["status"] == "failed")
        self._set_status(f"Queue finished: {done} done, {failed} failed.")
        self._log(self.queue_text, f"Queue finished: {done} done, {failed} failed.")

    # -- report --------------------------------------------------------
    def _refresh_compat(self, silent: bool = False) -> None:
        if self.analysis is None:
            if not silent:
                self.messagebox.showinfo("GOO Bridge", "Run an analysis first.")
            return
        module = None
        reason = ""
        try:
            from . import compat as module  # type: ignore[no-redef]
        except Exception as exc:
            reason = f"goo_bridge.compat is not available: {exc}"
            module = None
        if module is None:
            for level in LEVELS:
                self.compat_vars[level].set("-")
            self.compat_status_var.set(reason)
            if not silent:
                self._log(self.analyze_text, reason)
            return
        scanner = getattr(module, "scan_blend", None) or getattr(module, "scan", None)
        if scanner is None:
            self.compat_status_var.set(
                "goo_bridge.compat has no scan_blend()/scan() function yet."
            )
            return
        try:
            payload = scanner(self.analysis)
        except Exception as exc:
            self.compat_status_var.set(f"Compatibility scan failed: {exc}")
            if not silent:
                self.messagebox.showerror("GOO Bridge", f"Compatibility scan failed:\n{exc}")
            return
        items = compat_items(payload)
        counts = compat_counts(payload)
        for level in LEVELS:
            self.compat_vars[level].set(str(counts[level]))
        self.compat_status_var.set(
            f"{len(items)} compatibility item(s) scanned; "
            f"L2 {counts['L2']} · L1 {counts['L1']} · L0 {counts['L0']}."
        )
        for item in self.compat_tree.get_children():
            self.compat_tree.delete(item)
        for item in items:
            item_id = str(
                item.get("id") or item.get("item") or item.get("name") or item.get("key") or ""
            )
            level = str(item.get("level") or item.get("tier") or "")
            detail = str(
                item.get("rationale")
                or item.get("reason")
                or item.get("title")
                or item.get("summary")
                or item.get("description")
                or ""
            )
            self.compat_tree.insert("", "end", values=(item_id, level, detail))
        if not silent:
            self._set_status("Compatibility summary refreshed.")

    def _save_report(self) -> None:
        if self.analysis is None:
            self.messagebox.showinfo("GOO Bridge", "Run an analysis first.")
            return
        path = self.filedialog.asksaveasfilename(
            title="Save GOO Bridge report",
            defaultextension=".html",
            filetypes=(("HTML report", "*.html"), ("JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        target = Path(path)
        try:
            if target.suffix.lower() in {".json"}:
                written = write_json(self.analysis, target)
            else:
                written = write_html(self.analysis, target)
        except OSError as exc:
            self.messagebox.showerror("GOO Bridge", f"Could not write report: {exc}")
            return
        self._set_status(f"Report written to {written}")
        self._log(self.analyze_text, f"Report written to {written}")

    # -- threading / status -------------------------------------------
    def _thread(self, kind: str, work: Any) -> None:
        def runner() -> None:
            try:
                self.events.put((kind + "_done", work()))
            except Exception as exc:
                self.events.put((kind + "_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=runner, daemon=True).start()

    def _dispatch(self, kind: str, payload: Any) -> None:
        handler = getattr(self, "_on_" + kind, None)
        if handler is None:
            return
        try:
            handler(payload)
        except Exception as exc:  # a broken handler must not kill the event loop
            self._set_status(f"UI error: {exc}")

    def _poll_events(self) -> None:
        if self.closing:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._dispatch(kind, payload)
        except queue.Empty:
            pass
        if not self.closing:
            self.after_id = self.root.after(120, self._poll_events)

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def close(self) -> None:
        self.closing = True
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        try:
            self.root.destroy()
        except Exception:
            pass


def _create_app(root: Any, config: Optional[Config] = None) -> _App:
    """Build the window for an existing Tk root (used by tests and run_gui)."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    return _App(root, tk, ttk, filedialog, messagebox, config)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_gui(argv: Optional[list[str]] = None) -> int:
    """Open the GOO Bridge desktop application.

    When ``GOO_BRIDGE_SELFTEST=1`` the window is built and immediately closed,
    then exactly one JSON line describing the buttons is printed.  Missing
    tkinter or a missing display turns that mode into a skip (exit 0).
    """
    selftest = os.environ.get("GOO_BRIDGE_SELFTEST") == "1"
    try:
        import tkinter as tk
    except Exception as exc:
        message = f"Tkinter is not available: {exc}"
        if selftest:
            print(json.dumps(_selftest_payload(opened=False, skipped=message), ensure_ascii=True))
            return 0
        raise RuntimeError(message) from exc

    try:
        root = tk.Tk()
    except Exception as exc:
        if selftest:
            print(
                json.dumps(
                    _selftest_payload(opened=False, skipped=f"no display: {exc}"),
                    ensure_ascii=True,
                )
            )
            return 0
        raise RuntimeError(f"Could not open a Tk window: {exc}") from exc

    app: Optional[_App] = None
    try:
        app = _create_app(root)
        if selftest:
            root.update_idletasks()
            buttons = collect_button_labels(root) or list(BUTTONS)
            print(json.dumps(_selftest_payload(opened=True, buttons=buttons), ensure_ascii=True))
            return 0
        root.mainloop()
        return 0
    finally:
        if selftest:
            if app is not None:
                app.close()
            else:
                try:
                    root.destroy()
                except Exception:
                    pass
        else:
            try:
                root.destroy()
            except Exception:
                pass


__all__ = [
    "BUTTONS",
    "DEFAULT_FORMAT",
    "FORMATS",
    "LEVELS",
    "PACKAGE_ROOT",
    "TABS",
    "build_remote_profile",
    "collect_button_labels",
    "compat_counts",
    "compat_items",
    "format_analysis_summary",
    "local_render_kwargs",
    "remote_render_command",
    "remote_test_command",
    "run_gui",
]


if __name__ == "__main__":  # pragma: no cover - GUI entry point
    raise SystemExit(run_gui())
