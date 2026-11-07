# houdini-pi — Implementation Plan

Spec: `docs/specs/2026-10-12-houdini-pi-design.md`. Read it first; this plan
does not repeat the why.

Work one task at a time, TDD: failing test → implementation → green → commit.
Each task ends in one commit with a plain message (no emoji, no signature).
Checkboxes track progress. Tasks 1 and 2 gate everything after them.

## Global constraints

- Node 22.19+. TypeScript strict everywhere. `npm` workspaces at the root:
  `protocol`, `sidecar`, `ui`, `pi`. One `package-lock.json`.
- pi dependency `@earendil-works/pi-coding-agent` pinned **exactly** (no
  caret). `npm run check-pi` (Task 8) fails when the API surface we use moves.
- `houdini/` imports only stdlib, `hou`, `hdefereval`, `PySide6`. No
  third-party packages ever enter Houdini's Python.
- Python scripts outside `houdini/` use `uv` (`#!/usr/bin/env -S uv run
  --script`, PEP 723 block, `requires-python = ">=3.12"`).
- Everything binds `127.0.0.1`. The sidecar exits non-zero on any other bind.
- No silent fallbacks. A missing dependency, a dead bridge, a hook that throws,
  a failed compaction: raise and show. Never degrade quietly.
- Multipart conditions become named booleans before the `if`. Comments state
  WHY only, one line.
- Nothing tests against a real Houdini except the manual checklist and the
  `houdini`-marked integration test, which **skips with a reason** when
  `:8100` does not answer.

## File structure (target)

```
package.json                      # workspaces, root scripts (test, build, check-pi)
tsconfig.base.json
vitest.workspace.ts
playwright.config.ts
.github/workflows/ci.yml
install                           # uv script: checks deps, builds, writes package JSON
flake.nix                         # NixOS consumers
AGENTS.md                         # pi project contract (what the agent is, how it works in a scene)
.pi/mcp.json                      # fxhoudini via uvx, pinned
protocol/
  package.json  src/index.ts  src/index.test.ts
sidecar/
  package.json
  src/index.ts                    # argv → start(); --port --houdini-port --parent-pid --token --fake-agent --host
  src/server.ts                   # http + ws, static ui/dist, /health, token gate
  src/watchdog.ts                 # parent-pid poll → process.exit
  src/houdini-bridge.ts           # HTTP client for :8100 (execute_python, health)
  src/session.ts                  # AgentSession wrapper → WS events / WS commands
  src/fake-agent.ts               # canned event stream
  src/session-index.ts            # hip hash → pi session id (~/.houdini-pi/sessions.json)
  src/pi-surface.ts               # the pi exports we rely on, for check-pi
  test/*.test.ts
pi/
  package.json                    # pi package manifest (extensions, skills, prompts)
  extensions/houdini.ts           # hooks, memory tools, commands
  extensions/memory.ts            # estimate, store, compactor (bridge-backed)
  skills/houdini/SKILL.md
  test/*.test.ts
ui/
  package.json  index.html  vite.config.ts
  src/main.tsx  src/App.tsx  src/ws.ts  src/components/*.tsx
  e2e/*.spec.ts
houdini/
  houdini_pi.json                 # package JSON template; installer fills paths
  MainMenuCommon.xml
  python_panels/houdini_pi.pypanel
  python3.13libs/houdini_pi/__init__.py
  python3.13libs/houdini_pi/panel.py
  python3.13libs/houdini_pi/sidecar.py
  python3.13libs/houdini_pi/hooks_target.py
  tests/test_sidecar.py  tests/test_hooks_target.py  tests/conftest.py (fake hou)
docs/specs/  docs/plans/  docs/manual-e2e.md
```

---

## Task 0: Workspace tooling

**Files:** `package.json`, `tsconfig.base.json`, `vitest.workspace.ts`,
`playwright.config.ts`, `protocol/package.json`, `sidecar/package.json`,
`ui/package.json`, `pi/package.json`, `.github/workflows/ci.yml`,
`pyproject.toml` (pytest for `houdini/tests`).

- [ ] Root `package.json` with `"workspaces": ["protocol","sidecar","ui","pi"]`,
  scripts `test` (vitest run), `test:e2e` (playwright), `test:py`
  (`uv run pytest houdini/tests`), `build` (ui then sidecar), `check-pi`.
- [ ] `engines.node >= 22.19.0`, `.nvmrc` = `22`.
- [ ] Each workspace: `tsconfig.json` extending base, `vitest` configured,
  one placeholder test that passes, so CI is green from commit one.
- [ ] CI: `ubuntu-latest`, Node 22, `npm ci`, `npm test`, `npm run test:py`,
  `npm run build`, Playwright with browsers cached, `npm run test:e2e`.
- [ ] Commit: `Workspace tooling: npm workspaces, vitest, playwright, pytest, CI`

---

## Task 1: Spike gate — QWebEngineView inside a Houdini Python Panel

Manual, on a real Houdini. Must pass before Tasks 9 and 10. If it fails, the
UI fallback (PySide widgets over the same WS protocol) replaces Task 9's
scope; the sidecar and protocol are unaffected.

**Files:** `houdini/python_panels/houdini_pi.pypanel`,
`houdini/python3.13libs/houdini_pi/panel.py` (spike version),
`houdini/houdini_pi.json`, `docs/manual-e2e.md`.

- [ ] `panel.py`: `createInterface()` returns a `QWebEngineView` loading
  `https://example.com`. Nothing else.
- [ ] `houdini_pi.json`: `{"env":[{"HOUDINI_PI_ROOT":"<repo>"}],
  "path":"$HOUDINI_PI_ROOT/houdini"}`. Hand-copy to
  `$HOUDINI_USER_PREF_DIR/packages/` for the spike (the installer does this in
  Task 11).
- [ ] Launch Houdini, open the pane from the Python Panel pane type. Record in
  `docs/manual-e2e.md`: host, Houdini build, Wayland/XWayland, GPU, result.
- [ ] If the widget is black: try `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`
  and record. If still black, STOP and switch Task 9 to the PySide fallback.
- [ ] Commit: `Spike: QWebEngineView renders in a Houdini Python Panel` (or
  the negative result, which is just as important to record).

---

## Task 2: `protocol` — WebSocket message types

Shared by sidecar and ui so they cannot drift.

**Files:** `protocol/src/index.ts`, `protocol/src/index.test.ts`.

- [ ] Client → server (discriminated union on `type`):
  `prompt {text, images?}`, `steer {text}`, `follow_up {text}`, `abort`,
  `new_session`, `switch_session {sessionId}`, `fork {entryId}`,
  `set_model {provider, modelId}`, `set_thinking {level}`, `compact`,
  `list_models`, `list_sessions`, `memory_edit {text}`,
  `set_file_tools {enabled}`, `undo_compaction`.
- [ ] Server → client: `hello {sessionId, model, thinkingLevel, hip, fileTools}`,
  `text_delta {delta}`, `thinking_delta {delta}`, `message_end {message}`,
  `tool_start {toolCallId, toolName, args}`, `tool_end {toolCallId, result,
  isError, durationMs}`, `turn_end {cost, durationMs}`, `settled {aborted}`,
  `models {models}`, `sessions {sessions}`, `memory {text, prevAvailable}`,
  `memory_compacted {before, after}`, `error {message, action?}`,
  `state {model, thinkingLevel, isStreaming}`.
- [ ] `encode(msg): string`, `decodeClient(raw): ClientMessage` (throws on
  unknown `type` or missing fields), `decodeServer`.
- [ ] Tests: round-trip every variant; unknown type throws; missing field throws.
- [ ] Commit: `protocol: WebSocket message types with strict decoders`

---

## Task 3: `sidecar` — server, token gate, watchdog

**Files:** `sidecar/src/server.ts`, `sidecar/src/watchdog.ts`,
`sidecar/src/index.ts`, `sidecar/test/server.test.ts`,
`sidecar/test/watchdog.test.ts`.

- [ ] `startServer({host, port, token, staticDir, onConnection})` → `{close,
  port}`. Throws `Error("refusing to bind <host>")` when `host !==
  "127.0.0.1"`.
- [ ] `GET /health` → `{ok: true, pid, port}`. Static files from `staticDir`
  (built ui). Anything else 404.
- [ ] WS upgrade requires `?token=<token>`; wrong or missing → 401 and no
  socket. Each accepted socket gets `onConnection(ws)`.
- [ ] `watchParent(pid, intervalMs, exit)` polls `process.kill(pid, 0)`; on
  ESRCH calls `exit(0)`. Injected `exit` for tests.
- [ ] `index.ts`: argv parsing; `--host` defaults to `127.0.0.1`; `--parent-pid`
  required unless `--fake-agent`. Wire server + watchdog.
- [ ] Tests: bind refusal; health; 401 on bad token; connection on good token;
  watchdog exits when pid gone (fake `kill` throwing ESRCH).
- [ ] Commit: `sidecar: loopback-only HTTP+WS server, launch token, parent watchdog`

---

## Task 4: `sidecar` — Houdini bridge client

**Files:** `sidecar/src/houdini-bridge.ts`, `sidecar/test/houdini-bridge.test.ts`.

- [ ] Read the pinned fxhoudinimcp source (`houdini/scripts/python/
  fxhoudinimcp_server/`) for the exact HTTP wire format of the bridge:
  endpoint path, request body shape for `execute_python`, response envelope,
  error status. Write those into the test as fixtures with the pinned sha in a
  comment.
- [ ] `class HoudiniBridge { constructor(port); health(); executePython(code);
  runHelper(name, kwargs) }`. `runHelper` builds
  `import houdini_pi.hooks_target as t; print(json.dumps(t.<name>(**kw)))`
  and parses the JSON. Non-2xx or error envelope → throws `BridgeError` with
  the bridge's message.
- [ ] Tests against a fake HTTP server: health ok; executePython round-trip;
  error envelope throws; connection refused throws `BridgeUnreachable`.
- [ ] Commit: `sidecar: Houdini bridge client matching fxhoudinimcp's wire format`

---

## Task 5: `sidecar` — session wrapper and fake agent

**Files:** `sidecar/src/session.ts`, `sidecar/src/fake-agent.ts`,
`sidecar/test/session.test.ts`.

- [ ] Define `AgentLike` — the subset of pi's `AgentSession` we use:
  `subscribe(cb)`, `prompt(text, opts)`, `steer`, `followUp`, `abort`,
  `setModel`, `getAvailableModels`, `setThinkingLevel`, `newSession`,
  `switchSession`, `fork`, `compact`, `sessionId`, `model`, `thinkingLevel`.
- [ ] `class SessionHub { constructor(agent: AgentLike, bridge, index);
  attach(ws); }`. Forwards pi events → protocol server messages (mapping
  table in the spec's data-flow section); protocol client messages → agent
  calls. Broadcasts to every attached socket.
- [ ] `FakeAgent implements AgentLike`: on `prompt` emits a canned sequence
  (`message_start`, text deltas, a `tool_execution_start/end` for
  `mcp__fxhoudini__set_parameter`, `turn_end`, `agent_settled`) with small
  delays. `abort` cuts it short with `settled {aborted: true}`.
- [ ] Tests: prompt → ordered server messages on two attached sockets; abort
  mid-stream; `set_model` forwarded; decode error → `error` message, socket
  stays open.
- [ ] Commit: `sidecar: session hub forwarding pi events over the protocol; fake agent`

---

## Task 6: `pi` extension — hooks, undo grouping, memory tools, commands

**Files:** `pi/extensions/houdini.ts`, `pi/extensions/memory.ts`,
`pi/package.json`, `pi/test/houdini.test.ts`, `pi/test/memory.test.ts`.

- [ ] `pi/package.json` declares the pi package (`"pi": {"extensions":
  ["extensions/houdini.ts"], "skills": ["skills"]}` per pi's package format;
  verify the exact keys against pi's `packages.md` and write them in a
  comment).
- [ ] Extension factory `houdiniExtension(opts: {bridge, fileToolsEnabled: ()
  => boolean})`:
  - `before_agent_start`: `scene_context()` + `memory_read()` via bridge →
    prepend as two blocks to the system prompt sections; `begin_turn(label)`.
  - `turn_end` and `agent_settled`: `end_turn()`; also in `catch`/`finally`
    when any hook throws.
  - `tool_call`: `bash`/`edit`/`write` → `{block: true, reason}` unless
    `fileToolsEnabled()`.
  - Tools `memory_read`, `memory_append(section, text)`, `memory_replace(text)`.
  - Commands `/scene`, `/memory`, `/undo-turn` (runs `hou.undos.performUndo()`
    through the bridge).
- [ ] `memory.ts`: `estimateTokens(text) = ceil(len/4)`;
  `MemoryStore(bridge)` with `read`, `append`, `replace`, `rollback`.
- [ ] Tests with a fake `ExtensionAPI` (records `on`/`registerTool`/
  `registerCommand`) and a fake bridge: hook order; undo group closed when
  the agent throws; tools blocked then allowed; memory append creates the
  section when absent.
- [ ] Commit: `pi: Houdini extension — scene context, undo-grouped turns, memory tools`

---

## Task 7: Memory compaction

**Files:** `pi/extensions/memory.ts` (Compactor), `pi/test/compaction.test.ts`.

- [ ] `Compactor({threshold: 16384, target: 8192, runCondense: (prompt) =>
  Promise<string>})`. `maybeCompact(store)`: below threshold → no-op; above →
  condense, write result, rotate old to `.prev`, return `{before, after}`.
  Condense throwing → store untouched, error rethrown.
- [ ] `runCondense` in production is a one-shot pi session with no tools
  (`createAgentSession({ noTools: true, sessionManager:
  SessionManager.inMemory() })`), wired in Task 8. Here it is injected.
- [ ] Tests: no-op below; rotation above; failure leaves both keys intact;
  `undo_compaction` restores `.prev`.
- [ ] Commit: `pi: memory compaction with one-step rollback`

---

## Task 8: Real pi wiring

**Files:** `sidecar/src/agent.ts`, `sidecar/src/session-index.ts`,
`sidecar/src/pi-surface.ts`, `.pi/mcp.json`, `AGENTS.md`,
`sidecar/test/session-index.test.ts`, `sidecar/test/pi-surface.test.ts`.

- [ ] `.pi/mcp.json`: `fxhoudini` via `uvx --from git+https://github.com/
  healkeiser/fxhoudinimcp@3ab5cf27879ade7adbaf949e6423a943da39c0c0
  fxhoudinimcp`, env `HOUDINI_PORT`. Sidecar overrides the env per launch
  when `--houdini-port` is not 8100 (write a per-launch override the way pi
  supports project overrides; verify in `mcp.md` and record the mechanism).
- [ ] `createAgent({repoRoot, bridge, fileTools})`:
  `createAgentSession({cwd: repoRoot, extensionFactories:
  [createMcpExtension(), houdiniExtension(...)], ...})` then
  `session.bindExtensions()`. Returns `AgentLike`.
- [ ] `SessionIndex` at `~/.houdini-pi/sessions.json`: `get(hipPath)`,
  `set(hipPath, sessionId)`. Untitled hip → no entry (ephemeral).
- [ ] `pi-surface.ts`: imports every pi symbol we use and asserts their
  types/arity in a test. `npm run check-pi` runs just this test. A pi bump
  that breaks it fails CI with the symbol named.
- [ ] `AGENTS.md`: who the agent is, that it works on the live scene through
  `mcp__fxhoudini__*`, prefers `set_parameter` to `execute_python`, reads the
  node card before guessing, what belongs in project memory.
- [ ] Manual check (recorded in `docs/manual-e2e.md`): `node sidecar --port
  9123 --houdini-port 8100 --parent-pid $$ --token t` with Houdini + bridge
  up; `wscat` a `prompt`; see `tool_start` for a Houdini tool and an undo
  entry in Houdini.
- [ ] Commit: `sidecar: real pi session with builtin MCP and the Houdini extension`

---

## Task 9: Web UI

**Files:** `ui/*`, `ui/e2e/pane.spec.ts`.

- [ ] Vite + React + TypeScript. `vite.config.ts` proxies `/ws` and `/health`
  to `http://127.0.0.1:9123` for dev against `sidecar --fake-agent`.
- [ ] Components: `MessageList` (markdown via `marked`, streamed text,
  thinking blocks collapsed), `ToolCard` (collapsible; `mcp__fxhoudini__*`
  cards show node path and parm name from args), `PromptBox` (Enter sends,
  Shift+Enter newline; while streaming the button reads Steer), `Abort`,
  `ModelPicker` + `ThinkingPicker` (from `models`), `SessionList` (per hip),
  `MemoryTab` (text + Save + Undo compaction), `TurnFooter` (cost, duration),
  `FileToolsToggle`, `ErrorBanner` with action button (reconnect).
- [ ] `ws.ts`: connects with the token from `location.search`, reconnects with
  backoff, decodes via `protocol`.
- [ ] Playwright against `sidecar --fake-agent --port 9123 --token t`:
  hello renders model; prompt streams text; tool card appears and expands;
  abort shows aborted; memory tab round-trips; model picker lists models.
- [ ] Commit: `ui: chat pane — streaming, tool cards, model picker, memory tab`

---

## Task 10: Houdini side

**Files:** `houdini/python3.13libs/houdini_pi/{sidecar,hooks_target,panel}.py`,
`houdini/MainMenuCommon.xml`, `houdini/tests/*`.

- [ ] `hooks_target.py` (hou injected as a module attribute for tests):
  `scene_context() -> dict` (hip, current network path, selected node paths
  and types, display node), `begin_turn(label)` stores
  `hou.undos.group(label)` on a module global and enters it; `end_turn()`
  exits and clears it, no-op when none open; `memory_read() -> str`,
  `memory_write(text, prev)` on `hou.node("/")` user data keys
  `houdini_pi.memory` / `houdini_pi.memory.prev`.
- [ ] `sidecar.py`: `derive_port(pid) = 9100 + (pid % 400)`;
  `find_free_port(start, is_free)`; `make_token()` (`secrets.token_urlsafe`);
  `probe_health(port, http_get) -> bool`; `spawn(node, entry, port,
  houdini_port, parent_pid, token, popen)` detached (`start_new_session=True`,
  stdio to `~/.houdini-pi/sidecar-<port>.log`); `ensure_sidecar(...) -> url`.
  `node` resolved from PATH; absent → raise with the README's requirement
  line.
- [ ] `panel.py`: `createInterface()` → `ensure_sidecar()` → `QWebEngineView`
  loading the URL. Exceptions → `hou.ui.displayMessage` with the text, and
  the pane shows a plain `QLabel` with the same text. Nothing else.
- [ ] `MainMenuCommon.xml`: **houdini-pi ▸ Open Agent Pane** →
  `hou.ui.curDesktop().createFloatingPanel(hou.paneTabType.PythonPanel)` set
  to our interface.
- [ ] Tests (pytest, fake `hou` in `conftest.py`): context shape; undo group
  enter/exit and double-end no-op; memory keys; port derivation range and
  collision walk; spawn argv; probe true/false.
- [ ] Commit: `houdini: panel, sidecar lifecycle, and hou-side helpers`

---

## Task 11: Installer, flake, docs

**Files:** `install`, `tests/test_install.py`, `flake.nix`, `README.md`,
`docs/manual-e2e.md`.

- [ ] `install` (uv script): check `node --version` ≥ 22.19, `pi --version`
  ≥ first `builtin:mcp` release (record the exact number once pi's changelog
  names it), `uv --version`, `hython` on PATH or `$HFS`. Any missing →
  exit 1 naming it and the install line. Then `npm ci`, `npm run build`,
  then write `$HOUDINI_USER_PREF_DIR/packages/houdini_pi.json` (pref dir from
  `hython -c 'import hou; print(hou.homeHoudiniDirectory())'`), refusing to
  overwrite a file it did not write (marker key `"_houdini_pi_installer":
  true`). `--uninstall` removes only that file.
- [ ] Tests with fakes: each missing dependency named; package JSON content;
  refuses foreign file.
- [ ] `flake.nix`: `packages.sidecar` (`buildNpmPackage`, builds ui + sidecar),
  `packages.houdini-package-json` (the JSON pointing at the store path),
  `apps.install`. Note for the company NixOS config: home-manager writes
  `~/houdini22.0/packages/houdini_pi.json` from this.
- [ ] README: requirements, install, pane usage, model selection and auth
  (pi's), file-tools toggle, memory, troubleshooting (black widget, bridge
  down, pi not logged in). `docs/manual-e2e.md`: the checklist from the spec.
- [ ] Commit: `install script, nix flake, README`

---

## Task 12: CI release artifacts

**Files:** `.github/workflows/release.yml`.

- [ ] On tag `v*`: build, attach `ui/dist` + `sidecar/dist` tarball so users
  without a toolchain can install from a release.
- [ ] Commit: `ci: release artifacts`

---

## After this plan (other repos)

- **the company NixOS config:** bump `numtide/llm-agents.nix` (pi ≥ 1.x); install
  houdini-pi via its flake; write the package JSON from home-manager.
- **Squatch:** `art/launch-houdini`, `.config/ensure-houdini-mcp`,
  `.config/houdini-mcp`, devctl's Houdini desktop entry → discover Houdini via
  `hython` on PATH / `$HFS`; MCP registration points at the same pinned
  fxhoudinimcp. Notion task "AI integration into Houdini" tracks it.
