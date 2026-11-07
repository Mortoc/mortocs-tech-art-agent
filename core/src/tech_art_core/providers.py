"""The model's tool sources that need heavy imports: MCP servers run through `uvx`.

Imported on the first turn, not at startup, so an idle core never loads fastmcp or the MCP SDK.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
from pathlib import Path
from typing import Any

from .hosts import HostProfile
from .settings import Settings
from .wire import ClientInfo


def build_model(model_key: str, settings: Settings, env: dict[str, str] | None = None, http_client: Any = None) -> Any:
    """`provider/model-id` to a pydantic-ai model. A provider named in settings is an OpenAI-compatible server
    at its baseUrl (local or not); anything else is pydantic-ai's own `provider:model` resolution."""
    env = os.environ if env is None else env
    provider_name, _, model_id = model_key.partition("/")
    if provider_name == "gateway":
        upstream, _, name = model_id.partition("/")
        upstream, _, route = upstream.partition("@")
        if route:  # a gateway endpoint configured in Logfire (a custom provider, failover...): `gateway/chat@slug/model`
            return gateway_route_model(upstream, route, name)
        return f"gateway/{upstream}:{name}"  # the key comes from PYDANTIC_AI_GATEWAY_API_KEY, the URL from the key's region
    configured = settings.providers.get(provider_name)
    if configured is None:
        return f"{provider_name}:{model_id}" if model_id else model_key
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    key = env.get(configured.api_key_env, "") if configured.api_key_env else ""
    provider = OpenAIProvider(base_url=configured.base_url, api_key=key or "not-needed", **({"http_client": http_client} if http_client else {}))
    return OpenAIChatModel(model_id, provider=provider)


def gateway_route_model(upstream: str, route: str, name: str) -> Any:
    """A model behind a custom gateway endpoint (its slug from Logfire's Gateway settings), spoken to in `upstream`'s API format.

    `chat` is OpenAI's chat-completions format, which is what an OpenAI-compatible provider such as DeepSeek speaks."""
    from pydantic_ai.providers.gateway import gateway_provider

    known = {"chat", "openai-chat", "openai", "responses", "openai-responses", "anthropic", "google", "google-cloud"}
    if upstream not in known:
        raise ValueError(f"don't know how to speak {upstream!r} through a gateway route (try chat, openai, anthropic or google)")
    provider = gateway_provider(upstream, route=route)
    if upstream in ("chat", "openai-chat"):
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel(name, provider=provider)
    if upstream in ("openai", "responses", "openai-responses"):
        from pydantic_ai.models.openai import OpenAIResponsesModel

        return OpenAIResponsesModel(name, provider=provider)
    if upstream == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel(name, provider=provider)
    if upstream in ("google", "google-cloud"):
        from pydantic_ai.models.google import GoogleModel

        return GoogleModel(name, provider=provider)
    raise AssertionError(upstream)  # unreachable: `known` above lists exactly the branches


def uvx_path(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("TECH_ART_AGENT_UVX") or env.get("HOUDINI_PI_UVX") or shutil.which("uvx") or "uvx"


def mcp_env(profile: HostProfile, info: ClientInfo | None, settings: Settings) -> dict[str, str]:
    """The environment for the MCP server: where this app's bridge listens, from what its connector reported."""
    meta = (info.meta if info else {}) or {}
    env = dict(profile.mcp.env)
    if profile.kind == "houdini":
        env["HOUDINI_PORT"] = str(meta.get("bridgePort", 8100))
        env["FXHOUDINIMCP_AUTO_LAYOUT"] = "1" if settings.layout.auto_layout else "0"
    elif profile.kind == "blender":
        env["BLENDER_HOST"] = str(meta.get("host", "localhost"))
        env["BLENDER_PORT"] = str(meta.get("bridgePort", 9876))
        # mcp-for-blender can send prompts, scene screenshots and tool trajectories to a third-party Supabase project.
        # Studio scenes and prompts stay on the machine, whatever its consent prompt says: every switch it honours, forced on.
        env.update({"DISABLE_TELEMETRY": "true", "BLENDER_MCP_DISABLE_TELEMETRY": "true", "MCP_DISABLE_TELEMETRY": "true"})
    return env


class OptionalToolset:
    """Built lazily so importing this module never imports pydantic-ai."""

    @staticmethod
    def wrap(toolset: Any, name: str, notify: Any) -> Any:
        from pydantic_ai import WrapperToolset

        class Optional(WrapperToolset):
            """Use the toolset if it can be reached; if not, say so once and carry on without it."""

            available = True

            async def __aenter__(self) -> Any:
                try:
                    await super().__aenter__()
                except Exception as error:
                    from .errors import is_unreachable

                    self.available = False
                    why = "it can't be reached" if is_unreachable(error) else f"{type(error).__name__}"
                    notify(f"{name} isn't available right now ({why}); carrying on without it.")
                return self

            async def __aexit__(self, *exc: Any) -> Any:
                return await super().__aexit__(*exc) if self.available else None

            async def get_tools(self, ctx: Any) -> Any:
                return await super().get_tools(ctx) if self.available else {}

        return Optional(toolset)


def defer_all_but(direct: frozenset[str]) -> Any:
    """A `prepare` function hiding every tool not in `direct` behind tool search. Empty `direct`: nothing is hidden."""

    async def prepare(_ctx: Any, tool_defs: list[Any]) -> list[Any]:
        if not direct:
            return tool_defs
        return [td if td.name in direct else dataclasses.replace(td, defer_loading=True) for td in tool_defs]

    return prepare


def oauth_dir(state: Path) -> Path:
    return state / "oauth"


def oauth_server_dir(state: Path, name: str) -> Path:
    return oauth_dir(state) / name


def signed_in(state: Path, name: str) -> bool:
    """Whether `tech-art-core auth <name>` has stored a token (the daemon cannot open a browser itself)."""
    directory = oauth_server_dir(state, name)
    return directory.is_dir() and any(directory.rglob("*.json"))


def oauth_for(state: Path, name: str, url: str) -> Any:
    from fastmcp.client.auth import OAuth
    from key_value.aio.stores.filetree import FileTreeStore

    return OAuth(mcp_url=url, client_name="Tech Art Agent", token_storage=FileTreeStore(data_directory=oauth_server_dir(state, name)))


def extra_client(name: str, server: Any, state: Path, env: dict[str, str] | None = None) -> Any:
    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport

    env = os.environ if env is None else env
    if server.url:
        return Client(server.url, auth=oauth_for(state, name, server.url) if server.auth == "oauth" else None)
    resolved = {k: (env.get(v[1:], "") if v.startswith("$") else v) for k, v in server.env.items()}
    return Client(StdioTransport(command=server.command[0], args=server.command[1:], env={**env, **resolved}))


def tool_filter(server: Any) -> Any:
    async def allowed(_ctx: Any, tool_def: Any) -> bool:
        if server.include and tool_def.name not in server.include:
            return False
        return tool_def.name not in server.exclude

    return allowed


def extra_toolsets(host: str, settings: Settings, state: Path, log: Any = print, notify: Any = print) -> list[Any]:
    """The configured extra MCP servers for this host. One that needs a sign-in nobody has done is skipped, with a note."""
    from pydantic_ai import FilteredToolset
    from pydantic_ai.mcp import MCPToolset

    toolsets = []
    for name, server in settings.mcp_servers.items():
        if not server.enabled or (server.hosts and host not in server.hosts) or not (server.url or server.command):
            continue
        if server.auth == "oauth" and not signed_in(state, name):
            log(f"[tech-art-core] {name}: not signed in; run `tech-art-core auth {name}` once to enable it")
            continue
        toolset = MCPToolset(extra_client(name, server, state), id=f"{name}-mcp")
        toolset = FilteredToolset(toolset, tool_filter(server)) if server.include or server.exclude else toolset
        toolsets.append(OptionalToolset.wrap(toolset, name.capitalize(), notify))
    return toolsets


def mcp_toolsets(profile: HostProfile, info: ClientInfo | None, settings: Settings, state: Path | None = None, notify: Any = print, online: Any = None) -> list[Any]:
    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport
    from pydantic_ai import PreparedToolset
    from pydantic_ai.mcp import MCPToolset

    from .errors import network_up

    offline = not (online or network_up)()
    if offline:
        notify(f"Couldn't reach PyPI/GitHub to update the {profile.title} tool server; using its cached copy.")
    argv = profile.mcp.argv(profile.mcp_pin(settings), uvx_path(), offline=offline)
    transport = StdioTransport(command=argv[0], args=argv[1:], env={**os.environ, **mcp_env(profile, info, settings)})
    from .settings import state_dir

    host = PreparedToolset(MCPToolset(Client(transport), id=f"{profile.kind}-mcp"), defer_all_but(profile.direct_tools))
    return [OptionalToolset.wrap(host, f"The {profile.title} tool server", notify)] + extra_toolsets(profile.kind, settings, state or state_dir(), notify=notify)
