---
title: Pi for Houdini
---

# Pi for Houdini

A chat pane whose agent edits your live scene. Every turn is one Ctrl-Z.

## The pane

- **Conversation name · cost**: the name the conversation got after your
  first message, and what it has cost so far (USD).
- **chat / memory / sessions**: the conversation, the hip memory stored
  in this .hip (plus the project memory shared across $JOB), and earlier
  conversations this hip has had (stored in the .hip, so Save As keeps them).
  Opening the pane always starts a new conversation.
- **Link Pane Tab**: put the Pi tab and a network editor or parameter view in
  the same link group (1–9) and they follow the node the agent works on.
- **Model**: shows "Default" until you pick one; type to search. Only models that can see images are listed, so
  the agent can check its own renders. A new conversation starts on the first
  available entry of `defaultModel` in the settings file (DeepSeek V4 Flash
  Vision by default); a resumed one keeps the model it had.
- **thinking** (under the send button): how much the model reasons before acting.
- **+**: a new conversation, fresh context for the same hip.

## What a turn does

1. Writes a Houdini backup before the first change (`$HOUDINI_BACKUP_DIR`).
2. Opens one undo group, so **one Ctrl-Z reverts the whole turn**.
3. Selects and shows each node it touches, at least half a second apart.
4. In a Copernicus network, renders the output and looks at it before
   finishing. It can screenshot the viewport the same way. You see the
   thumbnail; click to expand. Images are kept with the conversation
   (`~/.houdini-pi/assets/<session>/`), so they are still there when you reopen it.
5. Ends with a green **Done**, or the count of tool errors. No summaries.
   **■ stop** (on the activity line) halts the turn; **↶ Undo** then reverts
   everything the turn did in Houdini and puts your message back in the box.
   **steer** sends a message into the running turn.

## Questions and tutorials

Ask "how do I…" or "why…" and the agent answers in a highlighted block instead
of editing the scene. Steps come with buttons: **◎ select** a node, **⚙** show
a parameter, **⌂** go to a network, **▣** open a pane, **?** open a help page.
A struck-through button points at something that does not exist in this
scene; hover it for the reason. Anything the model wrote outside the answer is
tucked under "unformatted reply".

## Tool cards

- Click a card to open it. Scripts (`execute_python`, `codemode`) show the
  code, four lines tall, scrollable, with a copy button.
- A successful `execute_python` shows nothing more. A failure shows the
  exception; warnings on stderr show in yellow.
- Dragging nodes from a network editor onto the pane adds them as references
  to your next message.

## Memory

Two scopes, both shown to the agent every turn:

- **Hip memory** — stored inside the .hip file. Per-scene decisions, naming
  conventions for these nodes, what a node is for. The agent appends to it;
  you can edit it in the **memory** tab. It compacts itself past ~16k tokens,
  with one-step rollback.
- **Project memory** — stored at `$JOB/.tech-art-agent/memory.md`, shared across
  every hip in the project. Team conventions, pipeline rules, "do not do X"
  that applies everywhere. Only really important things belong here. Edit it
  in the **memory** tab, below the hip memory. Unavailable when `$JOB` is not
  set.

## Settings

`~/.tech-art-agent/settings.json` (an existing `~/.houdini-pi/` keeps being used), shared by Houdini and Blender:

```json
{
  "naming": { "model": "cheapest", "renameAtUserMessages": [1, 5] },
  "followNodes": true,
  "defaultModel": ["gateway/anthropic/claude-haiku-5-5", "gateway/openai/gpt-6-luna"],
  "gateway": { "models": ["anthropic/claude-haiku-5-5", "openai/gpt-6-luna"] },
  "review": { "model": null },
  "providers": { "ollama": { "baseUrl": "http://localhost:11434/v1" } },
  "layout": { "autoLayout": false }
}
```

`gateway.models` lists what the model menu offers from the Pydantic AI Gateway (it has no model-list endpoint of its own).
`providers` adds an OpenAI-compatible model server (Ollama, LM Studio, llama.cpp). Its models appear in the model menu and
need no internet.

`layout.autoLayout` sets `FXHOUDINIMCP_AUTO_LAYOUT` for the bridge. **Off** (default): freshly-created nodes are placed beside their inputs, but existing node positions never move. **On**: `create_node` re-lays-out the entire network (`layoutChildren()` on all children), and the `layout_children` tool becomes callable. Also settable with `HOUDINI_PI_AUTO_LAYOUT=1` in the environment.

## Blender's tools

Blender's tools come from the `mcp-for-blender` add-on (installed and enabled by `./install --blender`). The agent gets
`execute_blender_code`, `get_scene_info`, `get_addon_status` and `look`. The add-on's other tools are excluded by default
because they send your prompts, images or trajectories to third-party services (its telemetry backend, 3D-generation and
asset libraries): `record_trajectory_feedback`, `disable_telemetry`, `generate_3d`, `search_assets`, `import_asset`. To allow
one, remove it from `mcp.blender.exclude` in settings; to allow only certain tools, use `mcp.blender.include`.

## Setting up the gateway (once)

The agent talks to models only through the [Pydantic AI Gateway](https://pydantic.dev/docs/ai/overview/gateway/).

1. In Logfire, open the organization's **Gateway settings** and create an API key, with the spend limits you want (daily,
   weekly or monthly, per key). That key is `PYDANTIC_AI_GATEWAY_API_KEY` in the core's environment.
2. For DeepSeek (the default agent), add **OpenRouter as a custom provider** (BYOK): an OpenAI-compatible provider at
   `https://openrouter.ai/api/v1` with an OpenRouter API key (give that key a credit limit at OpenRouter too), then create a
   **gateway endpoint** with the slug `openrouter` that routes to it. The menu entries written
   `chat@openrouter/deepseek/...` mean "the OpenAI-compatible API format, through the endpoint with that slug"; use another
   slug and the entries in `gateway.models` follow it.
3. Switch **"Require pricing data" off** for that provider. The gateway refuses models it has no prices for (a `400` from a
   custom provider), and the newest DeepSeek snapshot is unlikely to be in its price list. The cost of such calls is then not
   counted toward the gateway's own spend limits, so the credit limit on the OpenRouter key is the cap that matters; the pane
   still shows the cost, because OpenRouter reports it in each response.
4. Run **`tech-art-core probe`** with the key in the environment. It tries every model in your menu with a tiny request (text,
   an image, a tool call), prints what works and, for what does not, why in plain words.

**Custom or other models through Bedrock.** Bedrock is a built-in gateway provider, so Bedrock-hosted models are reachable as
`gateway/bedrock/<model id>` (ids look like `us.<maker>.<model>-v1:0`). Two things to check first: the core needs the `bedrock`
extra for pydantic-ai (it adds `boto3`), and AWS credentials must be set up for the gateway in Logfire. Models you imported or
fine-tuned on Bedrock are called by ARN and have no entry in the gateway's price list, which built-in providers refuse; they
would need Bedrock added as a *custom* provider behind an endpoint with "Require pricing data" off, like OpenRouter above.
`tech-art-core probe --model gateway/bedrock/<id>` shows whether a given model works.

The exact labels in Logfire's UI may differ from the ones above; the model strings are
`gateway/<api format>[@<endpoint slug>]/<model>`, for example `gateway/openai/gpt-6.1-sol` or
`gateway/chat@openrouter/deepseek/deepseek-v4.1-flash-1017`.

## Check understanding

The **check understanding** button (top bar) asks a model from a different family than the one working with you to read the
conversation and say where you and the agent may have meant different things. It looks at what you asked, what the agent said,
and which tools it called, never at screenshots. It reports ambiguities the agent resolved silently, assumptions nobody
confirmed, scope drift, words read in another app's sense (artists move between Houdini and Blender), and actions that
contradict the agent's own summary. Each finding quotes you and gives the single question that would settle it: press
*answer this* to put that question in the message box. The card says when no other family was available (a weaker check).

`review` in settings: `{"enabled": true, "model": null, "candidates": ["gateway/openai/gpt-6-luna", "gateway/anthropic/claude-haiku-5-5"], "auto": false, "every": 1}`.
With `model` unset, the first available candidate from a different family than the agent is the reviewer: Luna reviews Haiku and
Haiku reviews Luna. Pin `model` to use one reviewer always.

## When something is wrong

- **"Can't reach your local model server…"**: the server in `providers` isn't running. Start it (Ollama: `ollama serve`),
  then press **try again**.
- **"Can't reach the model provider…"**: no route to the hosted provider. Check your connection, or pick a local model.
  Your scene was not changed by the failure.
- **"Could not reach the agent core"** (the pane stays on its placeholder): the core isn't running. On NixOS it starts
  when something connects to `127.0.0.1:9100`; elsewhere run `tech-art-core` (see the README).
- **No Houdini bridge**: the fxhoudinimcp plugin is not loaded. Check `~/houdini22.0/packages/fxhoudinimcp.json`.
- **Key rejected / missing**: the message names the variable (`PYDANTIC_AI_GATEWAY_API_KEY` for the gateway, `OPENROUTER_API_KEY` for OpenRouter); set it in the core's environment, then try again.
- **"reached a spend limit"** (gateway): raise or wait out the key's limit in Logfire under the organization's Gateway settings.
- **After editing the agent's code**: Windows ▸ Squatch Studios ▸ Reload Agent (no Houdini restart).
- Black pane: `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu houdini`.

Source and issues: <https://github.com/Mortoc/mortocs-tech-art-agent>.
