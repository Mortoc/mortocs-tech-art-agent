"""Machine-wide settings, shared by every host: `settings.json` in the state directory."""

from __future__ import annotations

import json
import os
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HOME_ENV = "TECH_ART_AGENT_HOME"


def state_dir(env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """`TECH_ART_AGENT_HOME`, else `~/.tech-art-agent`, else an existing `~/.houdini-pi` (this project's old name)."""
    env = os.environ if env is None else env
    if env.get(HOME_ENV):
        return Path(env[HOME_ENV])
    home = home or Path.home()
    current, legacy = home / ".tech-art-agent", home / ".houdini-pi"
    return legacy if not current.exists() and legacy.exists() else current


class Naming(BaseModel):
    # "cheapest" picks the lowest-priced model the catalog lists; or "provider/model-id".
    model: str = "cheapest"
    rename_at_user_messages: list[int] = Field(default_factory=lambda: [1, 5], alias="renameAtUserMessages")
    model_config = ConfigDict(populate_by_name=True)


class McpServerSettings(BaseModel):
    # None: latest. A git ref / version here pins the server (flip on when deploys and evals begin).
    ref: str | None = None
    # Re-resolve latest on every start. Off once a ref is pinned.
    refresh: bool = True


class McpSettings(BaseModel):
    houdini: McpServerSettings = McpServerSettings()
    blender: McpServerSettings = McpServerSettings()


class ExtraMcpServer(BaseModel):
    """An MCP server the model may use alongside the host's own (Notion, ...). Remote (url) or local (command)."""

    model_config = ConfigDict(populate_by_name=True)

    url: str | None = None
    command: list[str] | None = None
    # Environment for a local server: NAME -> literal value, or "$NAME" to pass the core's own env var through
    # (secrets come in through the service's environmentFile, never this file).
    env: dict[str, str] = Field(default_factory=dict)
    # "oauth": sign in once with `tech-art-core auth <name>`; the token is cached in the state directory.
    auth: Literal["oauth"] | None = None
    enabled: bool = True
    # Only these tools / not these tools (names as the server lists them). Empty include: all.
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    # Which hosts get it. Empty: all.
    hosts: list[str] = Field(default_factory=list)


class Provider(BaseModel):
    """An OpenAI-compatible model server (Ollama, LM Studio, llama.cpp, vLLM, a gateway). Works with no internet."""

    model_config = ConfigDict(populate_by_name=True)

    base_url: str = Field(alias="baseUrl")
    # Name of the environment variable holding its API key, if it needs one. Local servers usually don't.
    api_key_env: str | None = Field(None, alias="apiKeyEnv")
    # Do its models accept images? The pane only offers models that do, because the agent has to look at renders.
    vision: bool = True
    # Models to offer. Empty: ask the server (GET {baseUrl}/models).
    models: list[str] = Field(default_factory=list)


class GatewaySettings(BaseModel):
    """Pydantic AI Gateway: one key (PYDANTIC_AI_GATEWAY_API_KEY, made in Logfire) for many providers, with spend limits.

    The gateway has no model-list endpoint, so the menu offers the models named here, written `<upstream>/<model>`
    and used as `gateway/<upstream>/<model>` (pydantic-ai spells it `gateway/<upstream>:<model>`).
    """

    models: list[str] = Field(
        default_factory=lambda: [
            # Behind a custom gateway endpoint: OpenRouter added as a BYOK provider in Logfire's Gateway settings, with the
            # slug `openrouter` (see docs/help.md). `chat` is the OpenAI-compatible API format those endpoints speak.
            "chat@openrouter/deepseek/deepseek-v4.1-flash-1017",
            "chat@openrouter/deepseek/deepseek-v4.1-flash",
            "chat@openrouter/google/gemini-3.8-flash",
            # The gateway's built-in providers.
            "openai/gpt-6.1-sol",
            "openai/gpt-6.1-terra",
            "google/gemini-3.8-flash",
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-haiku-5-5",
        ]
    )
    # Do these models accept images? The pane only offers models that do, because the agent has to look at renders.
    vision: bool = True


class ReviewSettings(BaseModel):
    """The second opinion: a model, preferably from a different family than the one working, reads the conversation and
    looks for places where the agent and the artist may have understood each other differently."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    # Pin the reviewer, "provider/model". None: the first available candidate from a different family than the agent's.
    model: str | None = None
    candidates: list[str] = Field(
        default_factory=lambda: [
            "gateway/google/gemini-3.8-flash",
            "gateway/chat@openrouter/google/gemini-3.8-flash",
            "gateway/openai/gpt-6.1-terra",
            "gateway/anthropic/claude-haiku-5-5",
        ]
    )
    # Review by itself after every N-th message from the artist. Off: only when asked ("check understanding").
    auto: bool = False
    every: int = 1


class PaneSettings(BaseModel):
    software_render: bool = Field(False, alias="softwareRender")
    model_config = ConfigDict(populate_by_name=True)


class Layout(BaseModel):
    auto_layout: bool = Field(False, alias="autoLayout")
    model_config = ConfigDict(populate_by_name=True)


class Settings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    default_model: list[str] = Field(
        default_factory=lambda: [
            # The working agent: DeepSeek's newest Flash (images, 1M context, a fraction of a frontier model's price), reached
            # through the gateway's OpenRouter endpoint. A dated snapshot, not a floating alias, so that tuning docs and
            # techniques against it compares like with like.
            "gateway/chat@openrouter/deepseek/deepseek-v4.1-flash-1017",
            "gateway/chat@openrouter/deepseek/deepseek-v4.1-flash",
            # Fallbacks on the gateway's built-in providers, used when that endpoint is not set up.
            "gateway/openai/gpt-6.1-sol",
            "gateway/openai/gpt-6.1-terra",
            "gateway/anthropic/claude-sonnet-5-5",
            "gateway/anthropic/claude-haiku-5-5",
        ],
        alias="defaultModel",
    )
    naming: Naming = Naming()
    follow_nodes: bool = Field(True, alias="followNodes")
    layout: Layout = Layout()
    pane: PaneSettings = PaneSettings()
    mcp: McpSettings = McpSettings()
    providers: dict[str, Provider] = Field(default_factory=dict)
    gateway: GatewaySettings = GatewaySettings()
    review: ReviewSettings = ReviewSettings()
    mcp_servers: dict[str, ExtraMcpServer] = Field(
        default_factory=lambda: {"notion": ExtraMcpServer(url="https://mcp.notion.com/mcp", auth="oauth")},
        alias="mcpServers",
    )
    # Seconds with no app, pane or running turn before the daemon exits. 0: never.
    idle_timeout: int = Field(600, alias="idleTimeout")


def load_settings(file: Path | None = None) -> Settings:
    """The settings file over the defaults. Missing is fine; a corrupt file is an error, not a silent default."""
    file = file or state_dir() / "settings.json"
    try:
        raw = file.read_text()
    except FileNotFoundError:
        return Settings()
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{file} is not valid JSON: {error}") from error
    if isinstance(data.get("defaultModel"), str):
        data["defaultModel"] = [data["defaultModel"]]
    return Settings.model_validate(data)
