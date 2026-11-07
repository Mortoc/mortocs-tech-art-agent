# mortocs-tech-art-agent

One agent that works inside your DCC apps. **Houdini** and **Blender** (and later a game) connect to a small local
**core** service; you chat with it in a pane inside the app, and it edits the live scene through that app's own tools.
Every turn is one undo step, it looks at its own renders, and it keeps notes in the scene file.

```
 Houdini ─┐                       ┌── chat pane (React, in the app or a browser)
 Blender ─┼── connect ──►  core  ─┼── model: OpenRouter, or a local server (Ollama, LM Studio, llama.cpp…)
 game    ─┘   (+ pub/sub)         └── tools: fxhoudinimcp, mcp-for-blender, Notion
```

Design notes: [docs/specs/2026-11-03-core-and-clients.md](docs/specs/2026-11-03-core-and-clients.md).

## The core

`core/` is Python: FastAPI, pydantic-ai, Logfire. One per user; apps connect to it over a WebSocket, register their
tools, and can pub/sub messages through it, so the agent in one app can see and call the others.

```
cd core && uv run tech-art-core        # 127.0.0.1:9100, state in ~/.tech-art-agent
```

- **Models: the [Pydantic AI Gateway](https://pydantic.dev/docs/ai/overview/gateway/), and only that.** One key
  (`PYDANTIC_AI_GATEWAY_API_KEY`, made in Logfire under the organization's Gateway settings) with per-key spend limits,
  every call traced, and the real cost of each call shown in the pane. It reaches OpenAI, Anthropic and Google directly,
  and anything on OpenRouter once OpenRouter is added there as a custom (BYOK) provider; see *Setting up the gateway* in
  [docs/help.md](docs/help.md). The gateway has no model-list endpoint, so the model menu is `gateway.models` in settings;
  **`tech-art-core probe`** tries each one with a tiny request (text, an image, a tool call) and prints what works.
  Local OpenAI-compatible servers (Ollama and friends) work with no internet through `providers`.
  The working agent defaults to DeepSeek's newest Flash (`deepseek-v4.1-flash-1017`, a dated snapshot, through the
  gateway's OpenRouter endpoint: it sees images, has a 1M context and costs a fraction of a frontier model), deliberately
  not Claude and deliberately modest: a weaker model with a different architecture exposes where our documentation and
  Houdini techniques are vague, which is the point. GPT-6.1 and Claude on the gateway are the fallbacks.
- **Second opinion.** **check understanding** in the pane's toolbar sends the conversation (what you asked, what the agent
  said and did) to a model from a *different family* than the agent (Gemini by default) and shows where the two of you
  may have meant different things: an ambiguity resolved silently, an unconfirmed assumption, scope drift, a term read in
  the wrong app's sense. Each finding has the one question that would settle it, with an *answer this* button. Set
  `review.auto` to run it by itself every `review.every` messages; `review.model` pins the reviewer.
- **Tools.** The model's app tools come from MCP servers the core runs through `uvx`: `fxhoudinimcp` and
  `mcp-for-blender`. They float on latest while we develop; pin one with `mcp.<host>.ref` in settings. Notion is
  configured by default; sign in once with `tech-art-core auth notion`.
- **No file or shell tool.** The agent writes only through memory tools (scene, project, user) that write fixed places.
- **When something is unreachable** the pane says what and why in plain words and offers *try again*: a local model
  server that isn't running ("Is it running? Ollama: `ollama serve`"), a hosted provider that can't be reached, a bad
  API key, a rate limit. A tool server that can't be reached is skipped with a notice instead of ending the turn.
- **Traces.** Logfire (`LOGFIRE_TOKEN`; local-only without it).

### NixOS

```nix
inputs.tech-art-agent.url = "github:Mortoc/mortocs-tech-art-agent";
# ...
imports = [ inputs.tech-art-agent.nixosModules.default ];
services.tech-art-agent = {
  enable = true;
  environmentFile = config.sops.secrets."tech-art-agent.env".path;   # PYDANTIC_AI_GATEWAY_API_KEY=...
};
```

A socket-activated user service: nothing runs until an app connects, it exits after 10 idle minutes, and its disk
access is confined to its state directory. The only GitHub secret this repo needs is the sops age key.

### Elsewhere

`./install` builds the pane UI, records how to start the core in `~/.tech-art-agent/install.json` (so apps can start
it themselves), and sets up Houdini and/or Blender if they are on this machine. It needs `uv`; Node is optional.

## Houdini

Installed by `./install` (a package file pointing at this repo's `houdini/`; the fxhoudinimcp bridge goes in as a
`uv tool`). When Houdini starts it connects to the core. **Windows ▸ Squatch Studios ▸ Houdini Agent** opens the pane.

**Hot reload.** **Windows ▸ Squatch Studios ▸ Reload Agent** (or `import houdini_pi; houdini_pi.reload()`) reloads this
package and the shared client library without restarting Houdini, and re-creates open panes. The core sees the same
Houdini come back, so the conversation continues. To reload the core itself, restart it: apps and panes reconnect on
their own and pick up the conversation where it was.

## Blender

1. Preferences ▸ Get Extensions ▸ Repositories ▸ **+** ▸ Add Remote Repository:
   `https://mortoc.github.io/mortocs-tech-art-agent/blender/index.json`
2. Install **Tech Art Agent**, and the add-on it drives: `uvx mcp-for-blender install-addon`.
3. 3D Viewport sidebar (N) ▸ **Agent** ▸ **Open Agent**.

After changing `blender/` or `python/`: `scripts/build_blender.py` rebuilds the repository under `docs/blender/`.

Not yet in Blender: *Undo turn* (Blender gives no way to find where a turn began in its undo stack).

## Settings

`~/.tech-art-agent/settings.json` (an existing `~/.houdini-pi/` keeps being used), shared by every app, all optional:
`defaultModel`, `providers`, `gateway`, `review`, `naming`, `followNodes`, `pane.softwareRender`, `layout.autoLayout`, `mcp`, `mcpServers`,
`idleTimeout`. Schema: `core/src/tech_art_core/settings.py`.

## Development

```
uv run --project core pytest              # everything Python (real Blender/Houdini tests skip if absent)
npm ci && npm run typecheck && npm test   # the pane UI
npm run build && npm run test:e2e         # the pane in a browser, against the real core and a fake local model
```

On NixOS, `nix develop` gives Python, uv and Node. Real-app tests need `blender` / `hython` on PATH.

## License

MIT
