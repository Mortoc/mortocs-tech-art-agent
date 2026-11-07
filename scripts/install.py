#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Install the tech-art agent into this user's apps: build the pane UI, record how to start the core, set up Houdini / Blender.

Run it through ./install, which checks the requirements first.

    ./install                # everything that is present on this machine
    ./install --houdini      # only Houdini
    ./install --blender      # only Blender
    ./install --uninstall    # remove what this script wrote

Never overwrites a Houdini package file it did not write itself. On NixOS use the flake's module instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from tech_art_agent.paths import state_dir  # noqa: E402

MARKER = "_houdini_pi_installer"
PACKAGE_NAME = "houdini_pi.json"
# The MCP servers float on latest while we develop; flip to a tag/ref here (and in settings.json) when deploys begin.
BRIDGE_SPEC = "git+https://github.com/healkeiser/fxhoudinimcp"
BRIDGE_PACKAGE_NAME = "fxhoudinimcp.json"
BLENDER_REPO_URL = "https://mortoc.github.io/mortocs-tech-art-agent/blender/index.json"

Runner = Callable[[list[str]], str | None]


def run_output(argv: list[str]) -> str | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def houdini_pref_dir(run: Runner = run_output) -> Path:
    out = run(["hython", "-c", "import hou; print(hou.homeHoudiniDirectory())"])
    if not out:
        raise SystemExit("install: hython could not report hou.homeHoudiniDirectory(); is Houdini licensed?")
    return Path(out.splitlines()[-1].strip())


def package_json(root: Path) -> dict:
    """Houdini's package file: the repo's houdini/ directory on the path. The shared client library (python/)
    is found by houdini_pi itself, as a sibling of houdini/."""
    return {MARKER: True, "env": [{"TECH_ART_AGENT_ROOT": str(root)}], "path": "$TECH_ART_AGENT_ROOT/houdini"}


def write_package(packages_dir: Path, root: Path) -> Path:
    target = packages_dir / PACKAGE_NAME
    if target.exists():
        try:
            existing = json.loads(target.read_text())
        except ValueError:
            existing = {}
        if existing.get(MARKER) is not True:
            raise SystemExit(f"install: {target} exists and was not written by this installer; move it aside first.")
    packages_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(package_json(root), indent=2) + "\n")
    return target


def remove_package(packages_dir: Path) -> bool:
    target = packages_dir / PACKAGE_NAME
    if not target.exists():
        return False
    if json.loads(target.read_text()).get(MARKER) is not True:
        raise SystemExit(f"install: {target} was not written by this installer; not removing it.")
    target.unlink()
    return True


def write_install_record(directory: Path, root: Path, uv: str, uvx: str) -> Path:
    """`install.json` in the state directory: how an app can start the core when no systemd socket does it."""
    file = directory / "install.json"
    try:
        record = json.loads(file.read_text())
    except (OSError, ValueError):
        record = {}
    record.update({"root": str(root), "uvx": uvx, "core": [uv, "run", "--project", str(root / "core"), "tech-art-core"]})
    directory.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(record, indent=2) + "\n")
    return file


def remove_install_record(directory: Path) -> bool:
    file = directory / "install.json"
    if not file.exists():
        return False
    file.unlink()
    return True


def bridge_root(package: dict) -> Path | None:
    """The plugin directory a fxhoudinimcp package file points Houdini at."""
    for entry in package.get("env", []):
        if "FXHOUDINIMCP" in entry:
            return Path(entry["FXHOUDINIMCP"])
    return None


def is_inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def install_bridge(packages_dir: Path, run: Runner = run_output) -> Path:
    """Install the fxhoudinimcp bridge as a uv TOOL and write its Houdini package from there.

    Not `uvx --from … houdini-package`: uvx runs from uv's cache, so the package file it writes points into
    ~/.cache/uv/archive-v0/…, and `uv cache clean` deletes the bridge out from under Houdini. A uv tool lives at a
    stable path until it is uninstalled. `--upgrade` keeps it on latest.
    """
    print(f"[install] uv tool install --upgrade {BRIDGE_SPEC}")
    if subprocess.run(["uv", "tool", "install", "--upgrade", BRIDGE_SPEC]).returncode != 0:
        raise SystemExit("install: could not install the fxhoudinimcp bridge with uv tool install")
    tool_bin, cache = run(["uv", "tool", "dir", "--bin"]), run(["uv", "cache", "dir"])
    if not tool_bin or not cache:
        raise SystemExit("install: uv could not report its tool or cache directory")
    exe = Path(tool_bin) / "fxhoudinimcp"
    if subprocess.run([str(exe), "houdini-package", "--write", str(packages_dir)]).returncode != 0:
        raise SystemExit(f"install: {exe} houdini-package failed")
    target = packages_dir / BRIDGE_PACKAGE_NAME
    plugin = bridge_root(json.loads(target.read_text()))
    if plugin is None or is_inside(plugin, Path(cache)):
        raise SystemExit(f"install: {target} points Houdini at {plugin}, inside uv's cache; it would not survive a cache clean")
    return target


def enable_git_hooks(root: Path) -> bool:
    """Point this checkout's git at .githooks, whose pre-commit refuses secrets and company data (the repo is public)."""
    if not (root / ".git").exists() or not (root / ".githooks" / "pre-commit").exists():
        return False
    return subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=root).returncode == 0


def build_ui(root: Path) -> bool:
    """Build the pane UI if Node is here. False: skipped (the core still runs; only the pane page is missing)."""
    if not (shutil.which("node") and shutil.which("npm")):
        print("[install] node/npm not found: skipping the pane UI build")
        return False
    for argv in (["npm", "ci"], ["npm", "run", "build"]):
        print(f"[install] {' '.join(argv)}")
        if subprocess.run(argv, cwd=root).returncode != 0:
            raise SystemExit(f"install: {' '.join(argv)} failed")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--houdini", action="store_true")
    parser.add_argument("--blender", action="store_true")
    args = parser.parse_args(argv)
    chosen = args.houdini or args.blender
    want_houdini = args.houdini or (not chosen and shutil.which("hython") is not None)
    want_blender = args.blender or (not chosen and shutil.which("blender") is not None)

    if args.uninstall:
        if want_houdini and shutil.which("hython"):
            print("[install] removed the Houdini package" if remove_package(houdini_pref_dir() / "packages") else "[install] no Houdini package to remove")
        print("[install] removed the install record" if remove_install_record(state_dir()) else "[install] no install record to remove")
        print("[install] Blender: remove the extension in Preferences > Get Extensions (and the repository, if you added it).")
        return 0

    if enable_git_hooks(ROOT):
        print("[install] enabled the pre-commit secrets check (git config core.hooksPath .githooks)")
    build_ui(ROOT)
    uv, uvx = shutil.which("uv") or "uv", shutil.which("uvx") or "uvx"
    print(f"[install] wrote {write_install_record(state_dir(), ROOT, uv, uvx)}")
    if want_houdini:
        packages = houdini_pref_dir() / "packages"
        print(f"[install] wrote {install_bridge(packages)}")
        print(f"[install] wrote {write_package(packages, ROOT)}")
    if want_blender:
        print("[install] uvx mcp-for-blender install-addon")
        if subprocess.run([uvx, "mcp-for-blender", "install-addon"]).returncode != 0:
            print("[install] could not install the mcp-for-blender add-on; run `uvx mcp-for-blender setup` yourself.")
        print(f"[install] Blender: Preferences > Get Extensions > Repositories > + > Add Remote Repository:\n[install]   {BLENDER_REPO_URL}")
    print("[install] done. Start the core with `cd core && uv run tech-art-core`, or just open Houdini/Blender: they start it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
