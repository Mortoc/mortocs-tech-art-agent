import json
from pathlib import Path

from houdini_pi import settings as sidecar
from tech_art_agent.paths import read_settings


def test_software_render_is_off_by_default(tmp_path: Path):
    assert sidecar.software_render_wanted(env={}, settings={}) is False


def test_software_render_from_env_or_settings(tmp_path: Path):
    assert sidecar.software_render_wanted(env={"HOUDINI_PI_SOFTWARE_RENDER": "1"}, settings={}) is True
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"pane": {"softwareRender": True}}))
    assert sidecar.software_render_wanted(env={}, settings=read_settings(f)) is True
    f.write_text("{bad")
    assert sidecar.software_render_wanted(env={}, settings=read_settings(f)) is False


def test_auto_layout_is_none_by_default(tmp_path: Path):
    assert sidecar.auto_layout_wanted(env={}, settings={}) is None


def test_auto_layout_from_env(tmp_path: Path):
    assert sidecar.auto_layout_wanted(env={"HOUDINI_PI_AUTO_LAYOUT": "1"}, settings={}) is True
    assert sidecar.auto_layout_wanted(env={"HOUDINI_PI_AUTO_LAYOUT": "0"}, settings={}) is False


def test_auto_layout_from_settings(tmp_path: Path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"layout": {"autoLayout": True}}))
    assert sidecar.auto_layout_wanted(env={}, settings=read_settings(f)) is True
    f.write_text(json.dumps({"layout": {"autoLayout": False}}))
    assert sidecar.auto_layout_wanted(env={}, settings=read_settings(f)) is False
    # Settings without the layout key: leave the upstream default alone.
    f.write_text(json.dumps({"pane": {"softwareRender": True}}))
    assert sidecar.auto_layout_wanted(env={}, settings=read_settings(f)) is None
    f.write_text("{bad")
    assert sidecar.auto_layout_wanted(env={}, settings=read_settings(f)) is None


def test_auto_layout_env_wins_over_settings(tmp_path: Path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"layout": {"autoLayout": True}}))
    assert sidecar.auto_layout_wanted(env={"HOUDINI_PI_AUTO_LAYOUT": "0"}, settings=read_settings(f)) is False


def test_pane_zoom_follows_houdini_ui_scale():
    assert sidecar.pane_zoom(1.0) == 1.0
    assert sidecar.pane_zoom(1.5) == 1.5
    assert sidecar.pane_zoom(10) == sidecar.ZOOM_MAX
    assert sidecar.pane_zoom(0.1) == sidecar.ZOOM_MIN


def test_pane_zoom_falls_back_to_1x():
    for junk in (None, 0, -2, "abc", float("nan")):
        assert sidecar.pane_zoom(junk) == 1.0


class FakeStartup:
    """fxhoudinimcp_server.startup as the pane sees it inside Houdini."""

    def __init__(self, running: bool, port: int = 8100, starting: bool = False):
        self._running, self._port, self._starting = running, port, starting

    def is_running(self) -> bool:
        return self._running

    def is_starting(self) -> bool:
        return self._starting

    def get_port(self) -> int:
        return self._port


def test_bridge_port_is_this_houdinis_bridge_not_the_default():
    # A second Houdini finds 8100 taken and serves its bridge on 8101. The pane
    # used to hand the sidecar 8100 anyway, so the agent drove the other Houdini.
    assert sidecar.bridge_port(env={}, startup=FakeStartup(running=True, port=8101)) == 8101


def test_bridge_port_waits_while_the_bridge_is_starting():
    assert sidecar.bridge_port(env={}, startup=FakeStartup(running=False, starting=True)) is None


def test_bridge_port_env_override_wins():
    assert sidecar.bridge_port(env={"HOUDINI_PI_BRIDGE_PORT": "8105"}, startup=FakeStartup(running=True, port=8101)) == 8105


def test_bridge_port_falls_back_to_the_configured_port_without_the_plugin():
    assert sidecar.bridge_port(env={}, startup=None) == 8100
    assert sidecar.bridge_port(env={"FXHOUDINIMCP_PORT": "8110"}, startup=None) == 8110
    assert sidecar.bridge_port(env={}, startup=FakeStartup(running=False)) == 8100
