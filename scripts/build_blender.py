#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build the Blender extension and the extension-repository index.

    scripts/build_blender.py                  # -> docs/blender/{tech_art_agent-<version>.zip, index.json}
    scripts/build_blender.py --out build/repo # somewhere else

The shared client library (python/tech_art_agent) is copied into the extension at build time, so the source
of truth stays in one place and the extension zip is self-contained. Blender's own tooling does the
packaging and index generation; it is found on PATH or via --blender.

Add the result to Blender: Preferences > Get Extensions > Repositories > + > Add Remote Repository, with
https://mortoc.github.io/mortocs-tech-art-agent/blender/index.json
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_SRC = ROOT / "blender"
SHARED_SRC = ROOT / "python" / "tech_art_agent"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "tests")


def stage(into: Path) -> Path:
    """The extension tree as Blender will see it: blender/ plus the vendored shared library."""
    target = into / "tech_art_agent"
    shutil.copytree(EXTENSION_SRC, target, ignore=IGNORE)
    shutil.copytree(SHARED_SRC, target / "tech_art_agent", ignore=IGNORE)
    return target


def run(argv: list[str]) -> None:
    print("+", " ".join(argv))
    if subprocess.run(argv).returncode != 0:
        raise SystemExit(f"build_blender: {argv[0]} failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "blender")
    parser.add_argument("--blender", default=shutil.which("blender") or "blender")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("tech_art_agent-*.zip"):
        old.unlink()
    with tempfile.TemporaryDirectory() as tmp:
        source = stage(Path(tmp))
        run([args.blender, "--command", "extension", "build", "--source-dir", str(source), "--output-dir", str(args.out)])
    run([args.blender, "--command", "extension", "server-generate", "--repo-dir", str(args.out)])
    print(f"built {', '.join(p.name for p in sorted(args.out.glob('*.zip')))} and index.json in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
