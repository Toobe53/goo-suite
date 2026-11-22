# SPDX-License-Identifier: GPL-3.0-or-later
"""Transports used to run GOO Bridge render jobs.

Two implementations share one small interface:

``SshTransport``
    Drives the system ``ssh`` and ``scp`` executables.  It needs no Python
    package on either side and works with the usual key-based authentication.
``LocalTransport``
    Executes the same job in a directory on this machine.  The test suite uses
    it to exercise the complete upload/render/download pipeline without a
    server.

Both transports expose the same file operations, so the worker never needs to
know where a job actually runs.  The subprocess runner is injectable; tests
assert the exact command lines without starting a process.
"""

from __future__ import annotations

import posixpath
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .config import RemoteProfile

__all__ = [
    "RemoteProfile",
    "ProcResult",
    "TransportError",
    "Transport",
    "SshTransport",
    "LocalTransport",
    "default_runner",
]


class TransportError(RuntimeError):
    """Raised when a transport command fails or cannot be started."""


@dataclass
class ProcResult:
    """Outcome of one transport command."""

    argv: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, limit: int = 4000) -> str:
        text = (self.stderr or "").strip() or (self.stdout or "").strip()
        return text[-limit:]

    def to_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-4000:],
            "duration": round(float(self.duration), 3),
        }


#: A runner receives ``(command, timeout=..., stdin=..., cwd=...)`` and returns
#: a :class:`ProcResult`, a ``subprocess.CompletedProcess``, a
#: ``(returncode, stdout, stderr)`` tuple, or any object with those attributes.
Runner = Callable[..., object]


def _coerce_result(raw: object, argv: Sequence[str]) -> ProcResult:
    """Normalise the values a runner may return into a :class:`ProcResult`."""
    if isinstance(raw, ProcResult):
        return raw
    if isinstance(raw, subprocess.CompletedProcess):
        return ProcResult(
            argv=list(argv),
            returncode=int(raw.returncode or 0),
            stdout=raw.stdout if isinstance(raw.stdout, str) else "",
            stderr=raw.stderr if isinstance(raw.stderr, str) else "",
        )
    if isinstance(raw, tuple) and len(raw) >= 2:
        return ProcResult(
            argv=list(argv),
            returncode=int(raw[0] or 0),
            stdout=str(raw[1] or ""),
            stderr=str(raw[2] or "") if len(raw) > 2 else "",
        )
    return ProcResult(
        argv=list(argv),
        returncode=int(getattr(raw, "returncode", 0) or 0),
        stdout=str(getattr(raw, "stdout", "") or ""),
        stderr=str(getattr(raw, "stderr", "") or ""),
    )


def default_runner(
    command: Sequence[str],
    *,
    timeout: float = 3600,
    stdin: Optional[str] = None,
    cwd: Optional[str] = None,
) -> ProcResult:
    """Run a command with the standard library and capture its output."""
    argv = [str(part) for part in command]
    try:
        completed = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError(
            f"command timed out after {timeout:g}s: {' '.join(argv[:4])}"
        ) from exc
    except FileNotFoundError as exc:
        raise TransportError(
            f"command not found: {argv[0]}. Install an SSH client "
            f"(Windows: Settings > Apps > Optional Features > OpenSSH Client) "
            f"and make sure it is on PATH."
        ) from exc
    except OSError as exc:
        raise TransportError(f"could not start {argv[0]}: {exc}") from exc
    return ProcResult(
        argv=argv,
        returncode=int(completed.returncode or 0),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _clean_rel(rel: str) -> str:
    """Validate a workspace-relative path and return it with ``/`` separators."""
    text = str(rel).replace("\\", "/").strip()
    if not text or text in {".", "./"}:
        return ""
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise TransportError(f"path escapes the workspace: {rel!r}")
    return "/".join(parts)


class Transport:
    """Common interface for local and SSH execution."""

    name = "transport"

    # -- execution ---------------------------------------------------------
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 3600,
        stdin: Optional[str] = None,
    ) -> ProcResult:
        raise NotImplementedError

    # -- files -------------------------------------------------------------
    def path(self, rel: str) -> str:
        """Return the location of ``rel`` in a form usable by commands."""
        raise NotImplementedError

    def mkdir(self, rel: str) -> str:
        raise NotImplementedError

    def upload(self, local: str | Path, rel: str) -> str:
        raise NotImplementedError

    def download(self, rel: str, local: str | Path) -> Path:
        raise NotImplementedError

    def listdir(self, rel: str) -> list[str]:
        raise NotImplementedError

    def exists(self, rel: str) -> bool:
        raise NotImplementedError

    def remove_tree(self, rel: str) -> bool:
        raise NotImplementedError

    def read_text(self, rel: str) -> str:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------
    def upload_text(self, text: str, rel: str) -> str:
        """Write ``text`` to a temporary file and upload it."""
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8", errors="replace"
        )
        try:
            with handle:
                handle.write(text)
            return self.upload(Path(handle.name), rel)
        finally:
            try:
                Path(handle.name).unlink()
            except OSError:
                pass

    def _invoke(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        stdin: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> ProcResult:
        started = time.monotonic()
        try:
            raw = self._runner(command, timeout=timeout, stdin=stdin, cwd=cwd)
        except TransportError:
            raise
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            raise TransportError(str(exc)) from exc
        result = _coerce_result(raw, command)
        result.duration = round(time.monotonic() - started, 3)
        return result

    def _require(self, result: ProcResult, action: str) -> ProcResult:
        if result.ok:
            return result
        detail = result.tail(1200)
        raise TransportError(
            f"{action} failed (exit {result.returncode})"
            + (f": {detail}" if detail else "")
        )


class SshTransport(Transport):
    """Run commands and copy files through the system OpenSSH client."""

    name = "ssh"

    def __init__(
        self,
        profile: RemoteProfile,
        *,
        runner: Optional[Runner] = None,
        ssh: str = "ssh",
        scp: str = "scp",
        connect_timeout: int = 20,
        timeout: float = 120,
    ) -> None:
        if not profile.host:
            raise TransportError("remote profile has no host")
        self.profile = profile
        self.ssh = ssh
        self.scp = scp
        self.connect_timeout = int(connect_timeout)
        self.timeout = float(timeout)
        self._runner: Runner = runner or default_runner
        self._workspace: Optional[str] = None
        self._workspace_source = str(profile.workspace or "~/.goo-bridge").replace(
            "\\", "/"
        )

    # -- ssh/scp command lines --------------------------------------------
    @property
    def target(self) -> str:
        host = self.profile.host
        user = self.profile.user
        return f"{user}@{host}" if user else host

    def _common_options(self) -> list[str]:
        options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        if self.connect_timeout > 0:
            options += ["-o", f"ConnectTimeout={self.connect_timeout}"]
        if self.profile.key:
            options += ["-i", str(Path(self.profile.key).expanduser())]
        return options

    def ssh_arguments(self) -> list[str]:
        args = [self.ssh, "-p", str(int(self.profile.port or 22))]
        args += self._common_options()
        args.append(self.target)
        return args

    def scp_arguments(self) -> list[str]:
        args = [self.scp, "-P", str(int(self.profile.port or 22))]
        args += self._common_options()
        return args

    # -- workspace ---------------------------------------------------------
    @property
    def workspace(self) -> str:
        if self._workspace is None:
            workspace = self._workspace_source
            if workspace == "~" or workspace.startswith("~/"):
                home = self._remote_home()
                tail = workspace[2:].lstrip("/")
                workspace = posixpath.join(home, tail) if tail else home
            self._workspace = posixpath.normpath(workspace) or "."
        return self._workspace

    def _remote_home(self) -> str:
        result = self._invoke(
            self.ssh_arguments() + ["printenv HOME"], timeout=self.timeout
        )
        home = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if not result.ok or not home:
            raise TransportError(
                "cannot determine the remote HOME directory: "
                + (result.tail(400) or "empty response")
            )
        return home

    def path(self, rel: str) -> str:
        clean = _clean_rel(rel)
        return posixpath.join(self.workspace, clean) if clean else self.workspace

    # -- execution ---------------------------------------------------------
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 3600,
        stdin: Optional[str] = None,
    ) -> ProcResult:
        remote_command = " ".join(shlex.quote(str(part)) for part in argv)
        command = self.ssh_arguments() + [remote_command]
        return self._invoke(command, timeout=timeout, stdin=stdin)

    # -- files -------------------------------------------------------------
    def mkdir(self, rel: str) -> str:
        target = self.path(rel)
        self._require(self.run(["mkdir", "-p", target]), f"mkdir {rel}")
        return target

    def exists(self, rel: str) -> bool:
        return self.run(["test", "-e", self.path(rel)]).ok

    def listdir(self, rel: str) -> list[str]:
        result = self.run(["ls", "-1", self.path(rel)])
        if not result.ok:
            if "No such file" in result.stderr or result.returncode == 2:
                return []
            self._require(result, f"list {rel}")
        return [line for line in result.stdout.splitlines() if line and line not in {".", ".."}]

    def read_text(self, rel: str) -> str:
        result = self._require(self.run(["cat", self.path(rel)]), f"read {rel}")
        return result.stdout

    def upload(self, local: str | Path, rel: str) -> str:
        source = Path(local).expanduser()
        if not source.is_file():
            raise TransportError(f"upload source not found: {source}")
        clean = _clean_rel(rel)
        parent = posixpath.dirname(clean)
        if parent:
            self.mkdir(parent)
        target = self.path(clean)
        command = self.scp_arguments() + [str(source.resolve()), f"{self.target}:{target}"]
        self._require(self._invoke(command, timeout=self.timeout), f"scp upload {rel}")
        return target

    def download(self, rel: str, local: str | Path) -> Path:
        target = Path(local).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        command = self.scp_arguments() + [f"{self.target}:{self.path(rel)}", str(target)]
        self._require(self._invoke(command, timeout=self.timeout), f"scp download {rel}")
        if not target.is_file():
            raise TransportError(f"scp reported success but {target} is missing")
        return target

    def remove_tree(self, rel: str) -> bool:
        return self.run(["rm", "-rf", self.path(rel)]).ok


class LocalTransport(Transport):
    """Execute a job in a local directory without SSH.

    File operations use :mod:`shutil`; only the actual renderer/Blender command
    starts a process.  This keeps the CI class of tests independent from a
    working POSIX shell (the test host may be Windows).
    """

    name = "local"

    def __init__(
        self,
        root: str | Path,
        *,
        runner: Optional[Runner] = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._runner: Runner = runner or default_runner

    @property
    def workspace(self) -> str:
        return str(self.root)

    def path(self, rel: str) -> str:
        clean = _clean_rel(rel)
        target = self.root / clean if clean else self.root
        return str(target)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 3600,
        stdin: Optional[str] = None,
    ) -> ProcResult:
        command = [str(part) for part in argv]
        if self._runner is default_runner:
            return self._invoke(command, timeout=timeout, stdin=stdin, cwd=str(self.root))
        return self._invoke(command, timeout=timeout, stdin=stdin)

    def mkdir(self, rel: str) -> str:
        target = Path(self.path(rel))
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

    def exists(self, rel: str) -> bool:
        return Path(self.path(rel)).exists()

    def listdir(self, rel: str) -> list[str]:
        target = Path(self.path(rel))
        if not target.is_dir():
            return []
        return sorted(entry.name for entry in target.iterdir())

    def read_text(self, rel: str) -> str:
        target = Path(self.path(rel))
        if not target.is_file():
            raise TransportError(f"remote file not found: {rel}")
        return target.read_text(encoding="utf-8", errors="replace")

    def upload(self, local: str | Path, rel: str) -> str:
        source = Path(local).expanduser()
        if not source.is_file():
            raise TransportError(f"upload source not found: {source}")
        target = Path(self.path(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return str(target)

    def download(self, rel: str, local: str | Path) -> Path:
        source = Path(self.path(rel))
        if not source.is_file():
            raise TransportError(f"remote file not found: {rel}")
        target = Path(local).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def remove_tree(self, rel: str) -> bool:
        target = Path(self.path(rel))
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError:
            return False
        return True
