import json

import pytest

from tech_art_core.settings import Settings, load_settings, state_dir


def test_missing_file_is_the_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.json") == Settings()


def test_the_houdini_pi_era_file_still_parses(tmp_path):
    file = tmp_path / "settings.json"
    file.write_text(json.dumps({"defaultModel": "openrouter/x/y", "naming": {"model": "cheapest", "renameAtUserMessages": [2]}, "followNodes": False, "pane": {"softwareRender": True}}))
    s = load_settings(file)
    assert s.default_model == ["openrouter/x/y"] and s.naming.rename_at_user_messages == [2]
    assert s.follow_nodes is False and s.pane.software_render is True


def test_corrupt_file_is_loud(tmp_path):
    file = tmp_path / "settings.json"
    file.write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_settings(file)


def test_mcp_servers_float_on_latest_until_pinned(tmp_path):
    file = tmp_path / "settings.json"
    assert load_settings(file).mcp.blender.ref is None
    file.write_text(json.dumps({"mcp": {"blender": {"ref": "2.2.6", "refresh": False}}}))
    assert load_settings(file).mcp.blender.ref == "2.2.6"


def test_state_dir_prefers_env_then_new_then_legacy(tmp_path):
    assert state_dir({"TECH_ART_AGENT_HOME": "/x"}, tmp_path) == __import__("pathlib").Path("/x")
    assert state_dir({}, tmp_path) == tmp_path / ".tech-art-agent"
    (tmp_path / ".houdini-pi").mkdir()
    assert state_dir({}, tmp_path) == tmp_path / ".houdini-pi"
    (tmp_path / ".tech-art-agent").mkdir()
    assert state_dir({}, tmp_path) == tmp_path / ".tech-art-agent"
