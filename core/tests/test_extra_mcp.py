import json

from fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from tech_art_core.providers import extra_toolsets, oauth_server_dir, signed_in
from tech_art_core.settings import ExtraMcpServer, Settings, load_settings


def test_notion_is_configured_by_default_but_stays_off_until_signed_in(tmp_path):
    settings = Settings()
    assert settings.mcp_servers["notion"].url == "https://mcp.notion.com/mcp" and settings.mcp_servers["notion"].auth == "oauth"
    notes = []
    assert extra_toolsets("blender", settings, tmp_path, log=notes.append) == []
    assert "tech-art-core auth notion" in notes[0]
    token = oauth_server_dir(tmp_path, "notion") / "mcp-oauth-token" / "x.json"
    token.parent.mkdir(parents=True)
    token.write_text("{}")
    assert signed_in(tmp_path, "notion")
    assert len(extra_toolsets("blender", settings, tmp_path)) == 1


def test_servers_can_be_limited_to_hosts_and_disabled(tmp_path):
    settings = Settings(mcp_servers={"x": ExtraMcpServer(command=["true"], hosts=["houdini"]), "y": ExtraMcpServer(command=["true"], enabled=False)})
    assert extra_toolsets("blender", settings, tmp_path) == []
    assert len(extra_toolsets("houdini", settings, tmp_path)) == 1


def test_settings_file_can_replace_the_server_list(tmp_path):
    file = tmp_path / "settings.json"
    file.write_text(json.dumps({"mcpServers": {"notion": {"url": "https://mcp.notion.com/mcp", "auth": "oauth", "exclude": ["notion-delete-pages"]}}}))
    assert load_settings(file).mcp_servers["notion"].exclude == ["notion-delete-pages"]


async def test_include_and_exclude_filter_what_the_model_sees():
    from pydantic_ai import FilteredToolset
    from pydantic_ai.mcp import MCPToolset

    from tech_art_core.providers import tool_filter

    server = FastMCP("notion-like")

    @server.tool
    def search() -> str:
        return "s"

    @server.tool
    def delete_pages() -> str:
        return "d"

    model = TestModel(call_tools=[])
    toolset = FilteredToolset(MCPToolset(server), tool_filter(ExtraMcpServer(url="x", exclude=["delete_pages"])))
    await Agent(model, toolsets=[toolset]).run("hi")
    assert {t.name for t in model.last_model_request_parameters.function_tools} == {"search"}
