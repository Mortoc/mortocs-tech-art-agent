from fastmcp import FastMCP
from pydantic_ai import Agent, PreparedToolset
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel

from tech_art_core.hosts import BLENDER, HOUDINI
from tech_art_core.providers import defer_all_but, mcp_env
from tech_art_core.settings import Settings
from tech_art_core.wire import ClientInfo


async def test_only_direct_mcp_tools_are_shown_up_front_the_rest_are_searchable():
    server = FastMCP("fake")

    @server.tool
    def get_node_info() -> str:
        return "n"

    @server.tool
    def rare_tool() -> str:
        return "r"

    model = TestModel(call_tools=[])
    toolset = PreparedToolset(MCPToolset(server), defer_all_but(frozenset({"get_node_info"})))
    agent = Agent(model, toolsets=[toolset])
    await agent.run("hi")
    tools = {t.name: t for t in model.last_model_request_parameters.function_tools}
    assert not tools["get_node_info"].defer_loading
    assert tools["rare_tool"].defer_loading
    assert "search_tools" in tools and not tools["search_tools"].defer_loading


def test_mcp_env_points_each_server_at_its_apps_bridge():
    houdini = ClientInfo(client_id="1", host="houdini", name="a.hip", meta={"bridgePort": 8101})
    blender = ClientInfo(client_id="2", host="blender", name="a.blend", meta={"bridgePort": 9877})
    assert mcp_env(HOUDINI, houdini, Settings())["HOUDINI_PORT"] == "8101"
    env = mcp_env(BLENDER, blender, Settings())
    assert (env["BLENDER_HOST"], env["BLENDER_PORT"]) == ("localhost", "9877")
    assert mcp_env(HOUDINI, None, Settings())["HOUDINI_PORT"] == "8100"


def test_the_blender_tool_server_is_never_allowed_to_send_telemetry_off_the_machine():
    env = mcp_env(BLENDER, ClientInfo(client_id="2", host="blender", name="a.blend"), Settings())
    for switch in ("DISABLE_TELEMETRY", "BLENDER_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"):
        assert env[switch] == "true"
    assert not any("TELEMETRY" in key for key in mcp_env(HOUDINI, None, Settings()))
