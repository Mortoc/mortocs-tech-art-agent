# houdini-pi: an in-Houdini chat agent built on pi

Date: 2026-10-12
Status: approved design, not yet implemented

> Supersedes the Squatch-internal "Houdini Agent Panel" spec (Notion, 2026-08-16),
> which built the same pane on the Claude Agent SDK inside the Squatch monorepo.
> Two decisions changed on 2026-10-11: the harness is **pi**, and the plugin
> lives in **its own public repo**, installed into Houdini like any other
> package. Squatch is a consumer.

## What it is

A dockable Houdini pane that chats with an LLM which can edit the live scene.
The pane is an HTML page in a `QWebEngineView`. The agent is
[pi](https://github.com/badlogic/pi-mono) (`@earendil-works/pi-coding-agent`),
run as a library inside a local Node sidecar. The scene is reached through the
[fxhoudinimcp](https://github.com/healkeiser/fxhoudinimcp) MCP server, which
rides Houdini's own `hwebserver`. Each agent turn is one Houdini undo step. The
agent sees where the artist is in the scene, resumes across sessions, and keeps
a curated per-`.hip` memory.

Artists never leave Houdini and never open a terminal. The same pi package also
works from the pi CLI in a terminal, for people who want that.

## Goals

- Install like a normal Houdini plugin: clone, run the installer, a package
  JSON lands in `$HOUDINI_USER_PREF_DIR/packages/`, Houdini shows the pane.
- Any model pi supports. Model and thinking level are picked in the pane.
- Auth is pi's: `~/.pi/agent/auth.json`, provider API keys, or `/login`. The
  sidecar never holds a key of its own.
- Houdini's Python stays untouched: nothing installed into `hython`, nothing
  but a thin panel on Houdini's event loop.
- Everything except "open the pane" is testable without Houdini.
- A real web development environment for the UI (Vite, TypeScript, hot reload,
  Playwright), because the pane will grow dynamic features.

## Non-goals

- Shipping pi, Node or uv. They are host dependencies, documented in the README.
- Remote or multi-user access. Everything binds `127.0.0.1`.
- Replacing fxhoudinimcp's tool surface. We add Houdini *workflow* (context,
  undo, memory, skills), not another node API.
- Compile-time gating. Nothing here is a shipped game artifact; the trust
  boundary is "the artist's own machine, loopback only".

## Host dependencies

| Dependency | Why | Minimum |
|---|---|---|
| Houdini | the host | 22.0 (Python 3.13, PySide6 6.8 with QtWebEngine) |
| Node | runs the sidecar and the pi library | 22.19, pi's own floor |
| pi | the agent engine and its auth | the first release with `builtin:mcp` (1.x; 0.87.1 lacks it) |
| uv | runs the fxhoudinimcp MCP server with `uvx` | any current |
| fxhoudinimcp Houdini plugin | the `hwebserver` bridge on `:8100` | v2.10.0 (pinned commit) |

Houdini is found through `hython` on `PATH`, falling back to `$HFS`. Never a
hard-coded install root: on NixOS `$HFS` is a store path and `/opt` does not
exist.

## Architecture

Three local processes.

```
┌─────────────────────── Houdini ───────────────────────┐
│ network editor │ viewport │ [houdini-pi pane]          │
│                │          │  QWebEngineView            │
│                │          │  http://127.0.0.1:<port>/  │
│                │          │     ?token=<launch token>  │
└──────────────────────────────┬────────────────────────┘
                 HTTP + WebSocket, 127.0.0.1
                              │
       ┌──────────────────────▼──────────────────────┐
       │ sidecar (Node): serves ui/dist, one WS per   │
       │ open pane, owns pi AgentSession(s) in-process │
       │ loads pi/ extension + builtin:mcp             │
       └──────────────────────┬──────────────────────┘
          MCP tools (fxhoudini)  │  direct HTTP for undo/context/memory
                              ▼
                 Houdini :8100 hwebserver bridge → hou.*
```

1. **Houdini** loads the package JSON, which puts `<repo>/houdini` on
   `HOUDINI_PATH`. That registers the Python Panel and the menu entry. The
   fxhoudinimcp Houdini plugin (installed separately, its usual way) starts the
   bridge on `127.0.0.1:8100`.
2. **The sidecar** is a Node process started by the panel. It uses pi's SDK
   (`createAgentSession`) rather than spawning the `pi` CLI, so the session,
   model list, extension hooks and MCP connection are all in-process objects
   we can drive from the WebSocket. One sidecar per Houdini process.
3. **pi** runs inside the sidecar with `cwd = <repo>`, so the repo is the pi
   project: `.pi/mcp.json`, `AGENTS.md`, skills and prompt templates apply.
   The same `pi/` package is installable into the CLI with
   `pi install git:github.com/Mortoc/houdini-pi`.

### Why the SDK and not `pi --mode rpc`

RPC mode would also work, and the panel could have spoken to it over
`QWebChannel` with no server at all. We chose the sidecar because the pane is
going to grow: multiple sessions, scene inspectors, live tool-call cards with
Houdini-specific rendering, maybe viewport thumbnails. Those want a normal web
stack with hot reload and browser tests, and a Node process that already holds
the pi session is the natural place to put the server half of them.

### Lifecycle

- Port: `9100 + (houdini_pid % 400)`, retried upward on collision, so two
  Houdinis never share a sidecar. The bridge port (default 8100) is passed
  through so the sidecar talks to *its* Houdini.
- The panel probes `GET /health` on open. If nothing answers it spawns
  `node <repo>/sidecar/dist/index.js --port <N> --houdini-port <B>
  --parent-pid <houdini pid> --token <random>` detached. The sidecar exits
  when its parent pid dies. Closing the pane does not kill it; reopening
  re-attaches. Two panes in one Houdini share the sidecar and get separate
  WebSockets onto the same session.
- Launch token: a random string the panel generates and passes to the sidecar
  on spawn, then appends to the page URL. Every WebSocket upgrade must carry
  it. Cheap, and it means a stray local process cannot drive the scene through
  our port without first reading Houdini's memory. The fxhoudinimcp bridge has
  no such check; that is upstream's choice and documented as such.
- The sidecar refuses any bind other than `127.0.0.1` and exits non-zero.

## Components

### `houdini/` — Houdini side (pure Python 3.13: stdlib, `hou`, `hdefereval`, `PySide6`)

- `python_panels/houdini_pi.pypanel`, `python3.13libs/houdini_pi/panel.py`:
  the pane. Ensures the sidecar (health probe or spawn), loads the URL. About
  100 lines, no other logic. `QWebEngineView` is created with GPU compositing
  disabled if a black widget is seen on first run; this is a known DCC failure
  mode and is checked in the first implementation task.
- `python3.13libs/houdini_pi/sidecar.py`: port derivation, spawn, health probe,
  token generation. Plain Python, injected `subprocess`/`urllib`, unit tested
  with fakes.
- `python3.13libs/houdini_pi/hooks_target.py`: `hou`-side helpers the sidecar
  runs through the bridge's `execute_python`:
  `begin_turn(label)` / `end_turn()` (open and close `hou.undos.group`),
  `scene_context()` (hip path, current network, selected nodes, display node,
  as JSON), `memory_read()` / `memory_write(text, prev)` on root-node user
  data. Tested natively against a fake `hou`.
- `MainMenuCommon.xml`: **houdini-pi ▸ Open Agent Pane**.
- `houdini_pi.json`: the package JSON template. The installer writes it to
  `$HOUDINI_USER_PREF_DIR/packages/` with `HOUDINI_PATH += <repo>/houdini` and
  `HOUDINI_PI_ROOT = <repo>`.

### `sidecar/` — Node, TypeScript

- `src/server.ts`: HTTP (static `ui/dist`, `/health`) + WebSocket on
  `127.0.0.1`. Validates the launch token. Parent-pid watchdog.
- `src/session.ts`: wraps pi's `AgentSession`. `createAgentSession({ cwd:
  <repo>, extensionFactories: [createMcpExtension(), houdiniExtension],
  ... })`, then `bindExtensions()`. Forwards session events to WebSockets;
  translates WS commands to `prompt` / `steer` / `followUp` / `abort` /
  `setModel` / `newSession` / `switchSession` / `fork` / `compact`.
- `src/protocol.ts`: the WS message types, shared with `ui/` through a
  workspace package so the two cannot drift.
- `src/houdini-bridge.ts`: thin HTTP client for the direct `:8100` calls
  (undo, context, memory), same wire format fxhoudinimcp uses.
- `src/fake-agent.ts`: canned event stream behind `--fake-agent`, for UI tests
  and for developing the UI without a model or a Houdini.
- Sessions persist where pi puts them (`~/.pi/agent/sessions/`, keyed by cwd).
  A per-hip session index lives in `~/.houdini-pi/sessions.json`: hip path
  hash → pi session id, so opening a hip resumes its conversation.

### `pi/` — the pi package (works in the sidecar and the CLI)

- `extensions/houdini.ts`: the workflow.
  - `before_agent_start`: prepend `scene_context()` and the project memory as
    two compact blocks; open the undo group with the prompt as its label.
  - `turn_end` / `agent_settled`: close the undo group (always, including on
    error and abort). One Ctrl-Z reverts a whole turn.
  - `tool_call`: refuse `bash`, `edit`, `write` unless the pane enabled file
    tools for this session. The agent works on the scene, not the repo.
  - Tools `memory_read`, `memory_append(section, text)`,
    `memory_replace(text)` implemented against the bridge.
  - Commands `/scene` (print context), `/memory` (print memory), `/undo-turn`.
- `skills/houdini/SKILL.md`: how to work in this scene. Read the node card
  before guessing. Units are the hip's. Prefer `set_parameter` over
  `execute_python`. What belongs in project memory and what does not.
- `AGENTS.md`: the system-prompt-level contract pi reads from the project.
- `.pi/mcp.json` (repo root, so it applies with `cwd = <repo>`):

  ```json
  {
    "fxhoudini": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/healkeiser/fxhoudinimcp@<pinned sha>", "fxhoudinimcp"],
      "env": { "HOUDINI_PORT": "8100" }
    }
  }
  ```

  The sidecar rewrites `HOUDINI_PORT` per launch when the bridge is not on
  8100.

### `ui/` — Vite + TypeScript + React

- Message list with streamed text, collapsible tool-call cards (Houdini tools
  render their node path and parameter), thinking blocks, prompt box with
  steer-while-streaming, abort, model and thinking-level pickers, session list
  per hip, **Memory** tab with edit, undo-turn indicator, cost and duration per
  turn.
- `vite dev` proxies `/ws` to a running sidecar (`--fake-agent` or real), so
  UI work is browser hot reload with no Houdini.
- Built output is committed? **No.** The installer runs `npm ci && npm run
  build`; CI builds and attaches `dist` to releases for people who do not
  want a toolchain.

### Per-hip project memory

Unchanged from the earlier spec, now implemented as pi extension tools.

- Storage: markdown in `hou.node("/").userData("houdini_pi.memory")`, with
  `houdini_pi.memory.prev` for one-step rollback. It dirties the scene, rides
  along through save-as and version control, and is shared by everyone who
  opens the file.
- Loaded every turn as a `## Project memory for <hip>` block.
- Compaction: when the estimate (chars / 4) exceeds 16 384 tokens, a one-shot
  no-tools pi session condenses it to under 8 192, preserving decisions,
  conventions and node names verbatim. Old text goes to `.prev`; the pane
  shows before/after sizes and an "undo compaction" action. Failure leaves
  memory untouched and is shown, not swallowed.

## Data flow of one turn

1. Artist types "make the wires thinner" → WS `{type: "prompt", text}`.
2. `before_agent_start`: bridge → `scene_context()`, `memory_read()`;
   both prepended. `begin_turn("agent: make the wires thinner")`.
3. pi streams. `message_update` text deltas → assistant bubble.
   `tool_execution_start/end` for `mcp__fxhoudini__set_parameter` → a card.
4. `agent_settled`: `end_turn()` closes the undo group; the pane gets
   `{type: "turn_end", cost, durationMs}`; the hip → session index is written;
   memory size is checked.
5. Errors surface, never degrade: bridge unreachable → an error card with a
   "reconnect" action; provider auth error → pi's own message verbatim; an
   extension hook throwing aborts the turn and shows the trace. The undo group
   is closed in `finally`.

## Testing

- `sidecar/`: vitest. Protocol encode/decode, session wrapper against a fake
  `AgentSession`, token check, parent-pid watchdog, bind refusal.
- `pi/`: vitest with pi's extension test harness where it exists, else a fake
  `ExtensionAPI`: hooks fire in order, undo group closes on error, file tools
  blocked by default.
- `ui/`: Playwright against `sidecar --fake-agent`: bubbles, tool cards, model
  picker, memory tab, abort.
- `houdini/`: pytest against a fake `hou` for `hooks_target`, injected fakes
  for `sidecar.py`. No Houdini needed.
- Manual e2e checklist in the README (launch, open pane, run a turn, Ctrl-Z
  reverts it, memory appears). A real-Houdini test marked `houdini` runs only
  when `:8100` answers and is reported **skipped with reason** otherwise.
- GitHub Actions on ubuntu-latest: everything above except the manual list.

## Installation (the "usual way")

```
git clone https://github.com/Mortoc/houdini-pi ~/houdini-pi
cd ~/houdini-pi && ./install     # or: uv run install.py
```

The installer checks Node, pi and uv versions, runs `npm ci && npm run build`,
and writes `$HOUDINI_USER_PREF_DIR/packages/houdini_pi.json`. It refuses to
proceed when a dependency is missing and says which one. It does not install
fxhoudinimcp's Houdini plugin; the README points at upstream's one-liner.

A `flake.nix` exposes the built sidecar and a `houdini-pi-package-json`
derivation for NixOS users, so a home-manager config can declare the package
file instead of running the installer. This is how the Squatch fleet consumes
it.

## Security

- Sidecar and bridge bind `127.0.0.1` only; the sidecar exits on any other
  bind.
- WebSocket upgrades require the per-launch token.
- `execute_python` through the bridge is arbitrary code in Houdini. Same class
  as every DCC MCP; dev machine, loopback, never a shipped artifact.
- Sessions and the hip index live under the user's home, never in the repo.

## Risks, in the order to retire them

1. `QWebEngineView` inside a Houdini Python Panel on an NVIDIA/XWayland host.
   Imports pass; rendering is unverified. If it fails, the fallback is a
   PySide widget UI over the same WebSocket protocol: a UI swap, not a
   redesign.
2. pi's SDK moves fast (0.87 → 1.1 in two months). Pin the dependency exactly;
   `npm run check-pi` fails CI when the pinned API surface changes.
3. fxhoudinimcp's bridge wire format. Pinned by commit; the direct HTTP client
   has a contract test against the pinned source.

## Squatch consumer steps (tracked in the Squatch repo, not here)

- the company NixOS config: bump `numtide/llm-agents.nix` so `pi` is ≥ the built-in-MCP
  release; install `houdini-pi` and write its package JSON from the flake.
- Squatch: fix the four `/opt/hfs22.0` defaults (`art/launch-houdini`,
  `.config/ensure-houdini-mcp`, `.config/houdini-mcp`, devctl's desktop
  entry) to discover Houdini via `hython` on `PATH`; point its MCP registration
  at the same pinned fxhoudinimcp.
