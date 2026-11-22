# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the three GOO Suite release artifacts and write SHA256SUMS."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def build(dist: Path) -> list[Path]:
    dist.mkdir(parents=True, exist_ok=True)
    for old in dist.glob("goo*"):
        if old.is_file():
            old.unlink()
    env_python = sys.executable
    run([env_python, "-m", "goo", "bundle"])
    run([env_python, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "--wheel-dir", str(dist)])
    run([env_python, "-m", "goo", "bundle", "--source"])
    artifacts = sorted(path for path in dist.iterdir() if path.is_file() and path.suffix in {".zip", ".whl"})
    if len(artifacts) != 3:
        raise SystemExit(f"expected 3 artifacts, found {len(artifacts)}: {[p.name for p in artifacts]}")
    lines = [f"{sha256(path)}  {path.name}" for path in artifacts]
    (dist / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dist / "REPRODUCIBILITY.txt").write_text(
        "GOO Suite release artifacts\n"
        "==========================\n\n"
        "The add-on zip and source zip use fixed timestamps and sorted entries; "
        "two builds on the same machine are byte-identical.\n"
        "The wheel is built by setuptools and may differ because of archive "
        "timestamps; verify its SHA-256 from SHA256SUMS instead.\n",
        encoding="utf-8",
    )
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default=str(ROOT / "dist"))
    args = parser.parse_args(argv)
    dist = Path(args.dist).resolve()
    artifacts = build(dist)
    print()
    for path in artifacts:
        print(f"{sha256(path)}  {path.name}  {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
