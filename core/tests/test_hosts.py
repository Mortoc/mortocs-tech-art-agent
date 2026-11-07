from tech_art_core.hosts import BLENDER, HOUDINI
from tech_art_core.settings import McpServerSettings


def test_mcp_servers_float_on_latest_with_refresh():
    assert HOUDINI.mcp.argv(McpServerSettings()) == ["uvx", "--refresh", "--from", "git+https://github.com/healkeiser/fxhoudinimcp", "fxhoudinimcp"]
    assert BLENDER.mcp.argv(McpServerSettings()) == ["uvx", "--refresh", "--from", "mcp-for-blender", "mcp-for-blender"]


def test_pinning_attaches_per_source_and_stops_refreshing():
    assert HOUDINI.mcp.argv(McpServerSettings(ref="v2.25.0"))[1:3] == ["--from", "git+https://github.com/healkeiser/fxhoudinimcp@v2.25.0"]
    assert BLENDER.mcp.argv(McpServerSettings(ref="2.2.6")) == ["uvx", "--from", "mcp-for-blender==2.2.6", "mcp-for-blender"]


def test_reads_do_not_modify_and_a_declared_flag_wins():
    assert not HOUDINI.can_modify("get_node_info") and HOUDINI.can_modify("create_node")
    assert HOUDINI.can_modify("get_x", declared=True) and not HOUDINI.can_modify("create_node", declared=False)


def test_paths_come_from_keyed_strings_only():
    args = {"node_path": "/obj/geo1/a", "label": "/not/keyed", "nodes": ["/obj/geo1/b", "x"]}
    assert HOUDINI.paths_from(args, {"created": "/obj/geo1/c"}) == ["/obj/geo1/a", "/obj/geo1/b", "/obj/geo1/c"]
    assert BLENDER.paths_from({"object_name": "Cube", "color": "red"}, {"objects": ["Sphere"]}) == ["Cube", "Sphere"]


def test_context_block_has_the_document_memory_and_other_apps():
    block = HOUDINI.context_block({"document": "/s/a.hip", "current": "/obj/geo1", "selected": [{"path": "/obj/geo1/a", "type": "xform"}], "extra": {"frame": 1}}, "- metres", None, ["blender: car.blend"])
    assert "/obj/geo1/a (xform)" in block and "Hip memory" in block and "blender: car.blend" in block and "House rules" in block
