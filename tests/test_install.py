import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")


def load():
    path = ROOT / "scripts" / "install.py"
    spec = importlib.util.spec_from_loader("tech_art_install", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


def check(tmp_path: Path, tools: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    """Run `./install --check` with only `tools` on PATH, each printing its version."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, version in tools.items():
        stub = bin_dir / name
        stub.write_text(f"#!{SH}\necho '{version}'\n")
        stub.chmod(0o755)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path / "home")}
    return subprocess.run([SH, str(ROOT / "install"), "--check", *args], env=env, capture_output=True, text=True)


ALL = {"uv": "uv 0.11.2", "uvx": "uv 0.11.2", "node": "v22.23.3", "npm": "10.9.0"}


def problems(result: subprocess.CompletedProcess) -> list[str]:
    return [line.split()[1] for line in result.stdout.splitlines() if line.lstrip().startswith("✗")]


def warnings(result: subprocess.CompletedProcess) -> list[str]:
    return [line.split()[1] for line in result.stdout.splitlines() if line.lstrip().startswith("!")]


def test_all_present_passes_silently(tmp_path: Path):
    result = check(tmp_path, ALL)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_only_uv_is_required_and_it_is_named_with_how_to_get_it(tmp_path: Path):
    result = check(tmp_path, {})
    assert result.returncode == 1
    assert problems(result) == ["uv"]
    assert "astral.sh/uv/install.sh" in result.stdout
    assert "1 requirement is missing" in result.stderr


def test_missing_or_old_node_is_a_warning_not_a_failure(tmp_path: Path):
    missing = check(tmp_path, {k: v for k, v in ALL.items() if k not in ("node", "npm")})
    assert missing.returncode == 0 and warnings(missing) == ["node"] and "UI build will be skipped" in missing.stdout
    old = check(tmp_path, {**ALL, "node": "v22.18.9"})
    assert old.returncode == 0 and "found 22.18.9, need 22.19.0 or newer" in old.stdout


def test_uv_installed_but_off_path_says_so(tmp_path: Path):
    tools = {k: v for k, v in ALL.items() if k != "uv"}
    local_bin = tmp_path / "home" / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "uv").write_text(f"#!{SH}\n")
    (local_bin / "uv").chmod(0o755)
    result = check(tmp_path, tools)
    assert problems(result) == ["uv"]
    assert f"installed at {local_bin / 'uv'} but not on PATH" in result.stdout


def test_unknown_option_is_refused(tmp_path: Path):
    result = check(tmp_path, ALL, "--bogus")
    assert result.returncode == 2 and "unknown option '--bogus'" in result.stderr


def test_package_json_puts_the_repo_on_houdinis_path(tmp_path: Path):
    m = load()
    target = m.write_package(tmp_path / "packages", ROOT)
    data = json.loads(target.read_text())
    assert data[m.MARKER] is True
    assert data["env"] == [{"TECH_ART_AGENT_ROOT": str(ROOT)}]
    assert data["path"] == "$TECH_ART_AGENT_ROOT/houdini"
    assert m.remove_package(tmp_path / "packages") is True
    assert m.remove_package(tmp_path / "packages") is False


def test_refuses_to_touch_a_foreign_package_file(tmp_path: Path):
    m = load()
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / m.PACKAGE_NAME).write_text('{"path": "/someone/elses"}')
    with pytest.raises(SystemExit, match="not written by this installer"):
        m.write_package(packages, ROOT)
    with pytest.raises(SystemExit, match="not written by this installer"):
        m.remove_package(packages)


def test_the_install_record_tells_an_app_how_to_start_the_core_and_keeps_other_keys(tmp_path: Path):
    m = load()
    (tmp_path / "install.json").write_text(json.dumps({"keep": "me"}))
    file = m.write_install_record(tmp_path, ROOT, "/bin/uv", "/bin/uvx")
    record = json.loads(file.read_text())
    assert record["core"] == ["/bin/uv", "run", "--project", str(ROOT / "core"), "tech-art-core"]
    assert record["keep"] == "me" and record["uvx"] == "/bin/uvx"
    # ...and the connector reads exactly this.
    import sys

    sys.path.insert(0, str(ROOT / "python"))
    from tech_art_agent.core_launch import core_command

    assert core_command({"TECH_ART_AGENT_HOME": str(tmp_path)}) == record["core"]
    assert m.remove_install_record(tmp_path) is True and m.remove_install_record(tmp_path) is False


def test_the_bridge_is_unpinned_so_it_floats_on_latest():
    m = load()
    assert "@" not in m.BRIDGE_SPEC.split("//", 1)[1]


def test_bridge_root_reads_the_fxhoudinimcp_package_file():
    m = load()
    assert m.bridge_root({"env": [{"FXHOUDINIMCP_PORT": "8100"}, {"FXHOUDINIMCP": "/x/houdini"}]}) == Path("/x/houdini")
    assert m.bridge_root({"env": []}) is None


def test_a_bridge_inside_uvs_cache_is_detected(tmp_path: Path):
    m = load()
    cache, tools = tmp_path / "cache", tmp_path / "tools"
    assert m.is_inside(cache / "archive-v0" / "abc" / "fxhoudinimcp" / "houdini", cache)
    assert not m.is_inside(tools / "fxhoudinimcp" / "houdini", cache)


def test_the_installer_turns_on_the_public_repo_commit_hook(tmp_path: Path):
    m = load()
    (tmp_path / ".githooks").mkdir()
    (tmp_path / ".githooks" / "pre-commit").write_text("#!/bin/sh\n")
    assert m.enable_git_hooks(tmp_path) is False  # not a git checkout: nothing to configure
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert m.enable_git_hooks(tmp_path) is True
    out = subprocess.run(["git", "config", "core.hooksPath"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    assert out == ".githooks"


def test_the_blender_extension_is_installed_from_the_checkouts_zip_so_a_branch_can_be_tested_before_it_is_merged(tmp_path: Path):
    m = load()
    (tmp_path / "docs" / "blender").mkdir(parents=True)
    (tmp_path / "docs" / "blender" / "tech_art_agent-0.1.0.zip").write_bytes(b"zip")
    (tmp_path / "docs" / "blender" / "tech_art_agent-0.2.0.zip").write_bytes(b"zip")
    seen = {}

    def runner(argv):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0)

    installed = m.install_blender_extension(tmp_path, blender="/usr/bin/blender", runner=runner)
    assert installed.name == "tech_art_agent-0.2.0.zip"  # the newest build
    argv = seen["argv"]
    assert argv[:2] == ["/usr/bin/blender", "--background"] and argv[2:5] == ["--command", "extension", "install-file"]
    assert "--enable" in argv and argv[argv.index("--repo") + 1] == "user_default" and argv[-1].endswith("tech_art_agent-0.2.0.zip")
    assert m.install_blender_extension(tmp_path, blender="/usr/bin/blender", runner=lambda a: subprocess.CompletedProcess(a, 1)) is None  # a failed install is reported, not hidden
    assert m.install_blender_extension(tmp_path / "empty", blender="/usr/bin/blender", runner=runner) is None  # no zip: skipped


def test_the_mcp_addon_is_enabled_by_name_through_blenders_own_api():
    m = load()
    seen = {}

    def runner(argv):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0)

    assert m.enable_blender_addon("blender_mcp", blender="/usr/bin/blender", runner=runner) is True
    expr = seen["argv"][seen["argv"].index("--python-expr") + 1]
    assert "addon_enable(module='blender_mcp')" in expr and "save_userpref" in expr  # enabled, and remembered across restarts
    assert m.enable_blender_addon("blender_mcp", blender="/usr/bin/blender", runner=lambda a: subprocess.CompletedProcess(a, 1)) is False
