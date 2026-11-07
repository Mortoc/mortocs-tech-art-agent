# Core server and clients (2026-11-03)

Supersedes the single-host sidecar design in `2026-10-12-houdini-pi-design.md`.

## Shape

```
 Houdini ─┐                      ┌── pane (React UI, /ws)
 Blender ─┼── /clients (ws) ──► core (FastAPI + pydantic-ai) ──► LLM (OpenRouter, ...)
 game    ─┘     ▲   pub/sub ◄─┘      │
                                      └── MCP servers (stdio, uvx): fxhoudinimcp, mcp-for-blender
```

- **One core per user**, Python: FastAPI, pydantic-ai 2.x, Logfire. It replaces the Node sidecar and pi.
- **Apps connect outbound** to `/clients`, say `hello` (host kind, name, helper tools, `meta`), and from then
  on can pub/sub topics through the core and be called by it. A Houdini, a Blender and a game talk to each other
  through the bus; the agent in any session can see the other connected apps.
- **Model tools come from MCP servers** the core runs: `fxhoudinimcp` (Houdini), `mcp-for-blender` (Blender).
  Both float on latest while we develop (`uvx --refresh`). Pinning is one setting per server
  (`mcp.<host>.ref`), switched on when deploys and evals begin.
- **Lifecycle helpers** (scene context, memory in the document, turn undo grouping, backup, follow, link
  validation, screenshots) are *internal* tools each app's connector registers. The model never sees them.
- **One conversation per connected app**; sessions list lives in the document, as before.

## Wire

`core/src/tech_art_core/wire.py` is the single source of truth (pydantic). `/clients` carries
`hello / result / sub / unsub / pub` in and `welcome / call / msg / error` out; `/ws` carries the pane
messages (the old `protocol/` set, with `houdini_action` generalised to `host_action`). TypeScript types for
the UI are generated from these models.

## Model access

**Gateway only.** There is no direct provider path in the defaults (OpenRouter and local servers remain supported in code,
unused unless configured). OpenRouter is reached *through* the gateway as a custom BYOK provider behind an endpoint slug, which
is what makes DeepSeek available: `gateway/chat@openrouter/deepseek/...`. Models the gateway has no prices for are refused by
default; for the OpenRouter provider the pricing requirement is switched off, so the cap that matters there is the credit limit
on the OpenRouter key. `tech-art-core probe` is how a key's reach is established, since the gateway has no model-list endpoint.


Default: the **Pydantic AI Gateway** (`gateway/<upstream>:<model>` in pydantic-ai; `gateway/<upstream>/<model>` in our
settings and pane). One key from Logfire, spend limits per key, every call traced, and the real cost of each call
comes back in the response (shown in the pane). The gateway has no model-list endpoint, so `gateway.models` in
settings is the menu. OpenRouter and any OpenAI-compatible local server (`providers`) still work; the menu lists only
what has a key or a running server. The one machine-wide secret is `PYDANTIC_AI_GATEWAY_API_KEY` in the sops env file.

## Second opinion

The working agent defaults to DeepSeek's newest Flash (`deepseek-v4.1-flash-1017`, through the gateway's OpenRouter endpoint, pinned to the dated
snapshot rather than the floating alias so that tuning docs and techniques compares like with like). A modest model is
the point: where it stumbles, our documentation or technique was vague. The gateway's GPT-6.1 Sol and Claude stay as
fallbacks, and the project sees how a different architecture reads the same prompts. On request (or every N messages), the
core sends a plain-text transcript of the conversation to a reviewer from yet another family (`review.candidates`,
first one that differs from the agent's, else a flagged same-family fallback). The reviewer returns a validated
`Review` (verdict, summary, up to six findings with the artist's quote and the question that would settle it) via
`PromptedOutput`, so any model that can emit JSON works, including a local one. Reviews are not persisted with the
conversation yet, and the working agent cannot call the reviewer itself (a natural next step: let it ask before a big edit).

## Lifetime

Socket-activated systemd user unit: nothing runs until an app or pane connects to `127.0.0.1:9100`; the
core exits after `idleTimeout` seconds with no apps, panes or running turns, and heavy imports (pydantic-ai,
logfire exporters) happen on first use. The NixOS module takes `environmentFile` for the sops-nix secret
(`OPENROUTER_API_KEY`). The repo's only GitHub secret is the sops age key.

## State

`~/.tech-art-agent/` (an existing `~/.houdini-pi/` keeps being used): `settings.json` (shared by every host),
`core.token`, logs, session files, tool images.

## Later: agents that run inside the app

The system build also installs pydantic-ai into Houdini's Python. The core's agent can therefore *author* agents
(a pydantic-ai `AgentSpec`: model, instructions, tools) and have Houdini run them in-process, stored on a node or
HDA parameter. Seams that keep this open: the shared `tech_art_agent` library stays standard-library only (Blender
has no pydantic-ai); any in-app agent runner lives in the host package (`houdini_pi`), where pydantic-ai may be
imported; and a runner would register as one more connector tool, so the core and other apps can call it.
