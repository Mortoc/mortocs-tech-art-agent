# Decisions taken autonomously, overnight 2026-10-12

Each entry: decision, options weighed, why. Review in the morning.

1. **Work on a branch + PR, not straight to main.** Options: push task commits
   to `main` (repo is brand new, nobody else on it) vs branch
   `feature/initial-implementation` + PR. Chose the PR: it gives a single place
   to review the overnight run and CI runs against it.
2. **Dev toolchain comes from the repo's own `flake.nix` devShell** (Node 22,
   uv, Python 3.12, playwright-driver browsers). Options: rely on the host's
   `~/.nix-profile` node (20.x, too old for pi) vs the flake. The flake is also
   what the company NixOS config will consume, so it was needed anyway.
3. **`@playwright/test` pinned to nixpkgs' `playwright-driver` version (1.61.1)**
   so the nix-provided browsers match. Downloading browsers via `npx playwright
   install` on NixOS needs nix-ld and is not reproducible.
4. **fxhoudinimcp pinned to v2.25.0 (`2278840713996159333df75b8202dc3fc1c35fe2`), not
   v2.10.0.** Squatch's pin `3ab5cf27…` no longer exists in upstream history
   (rewritten); the Notion plan's `079ed58…` neither. v2.25.0 is the current
   release (2026-10-05) and its bridge wire format is what the sidecar's client
   and its contract tests are written against.
5. **MCP server registered from the extension (`pi.registerMcpServer`), no
   `.pi/mcp.json`.** The Houdini port is a runtime value (second Houdini → 8101),
   which a static file cannot express. Exposure `codemode` with a curated set of
   ~28 `direct` tools (`DIRECT_TOOLS` in `pi/extensions/houdini.ts`); 210 direct
   tools would crowd the context. The sidecar loads pi's codemode and tool-search
   extensions so the rest stays reachable.
6. **Sidecar embeds pi via the SDK (`createAgentSessionRuntime`), not `pi --mode
   rpc`.** One process, direct access to the model list, session runtime and
   extension hooks; replacement of the session on new/switch/fork is handled by
   re-subscribing in the adapter. pi pinned exactly at 1.1.0; `npm run check-pi`
   guards the surface used.
7. **pi file tools (bash/edit/write) blocked by default**, toggle in the pane.
   Implemented as a `tool_call` gate in the extension rather than `noTools`, so
   the toggle works mid-session without re-creating the session.
8. **Per-turn undo relies on Houdini nesting undo blocks by depth.** `begin_turn`
   enters a `hou.undos.group` that outlives the request; fxhoudinimcp's own
   per-command groups then nest inside it. Unverified on a real Houdini: it is the
   first item of `docs/manual-e2e.md`. Fallback if wrong: `/undo-turn` performs
   as many `scene.undo` calls as the turn issued commands.
9. **Playwright pinned to the flake's `playwright-driver` version (1.63.0, not
   the 1.61.1 first assumed).** `nix eval nixpkgs#…` answered from the registry's
   channel, the flake's `nixos-unstable` pin is newer; the devShell exports
   `PLAYWRIGHT_DRIVER_VERSION` so the mismatch is visible.
10. **Compaction's condense call is a one-shot `completeSimple` on the current
    model** (no tools, in-memory), not a second AgentSession. Cheaper and it
    cannot touch the scene.
11. **The Squatch consumer work is NOT in this run.** the company NixOS config's pi bump
    landed independently (pi is 1.1.0 on the dev machines now); the four `/opt/hfs22.0`
    defaults in the Squatch repo and the home-manager package-file entry are
    left for a separate PR, since this run is the houdini-pi repo.
12. **Sidecar is spawned with Houdini's `LD_LIBRARY_PATH` / `PYTHONPATH` scrubbed.**
    First live run: Node died with `undefined symbol: sqlite3session_attach` because
    Houdini's `dsolib` shadowed the nix store's libsqlite3. Same class of problem
    the Squatch notes record for Blender and Houdini launchers.
13. **Panel startup is a QTimer poll, not a sleep loop.** The first run froze
    Houdini ~5 s while `ensure_sidecar` slept on the main thread; the pane now
    shows "Starting…" and swaps in the web view when `/health` answers.
14. **Menu moved to Windows ▸ Squatch Studios ▸ Pi** at Mortoc's request (was a
    top-level houdini-pi menu).
15. **Session naming uses the cheapest *priced* model pi lists** when
    `naming.model` is `"cheapest"` (default); free (0-cost) and unpriced
    entries are skipped because they are usually rate-limited or unknown.
16. **"Can modify the scene" is decided by tool-name prefix** (`get_`, `list_`,
    `find_`, `capture_`… are reads; everything else on fxhoudini, plus
    `codemode`, writes). fxhoudinimcp publishes no readOnly annotations to key
    off; a wrong guess costs one extra backup, never a missing one.
17. **Node drag-and-drop reads `text/plain`** from the drop and keeps tokens
    that look like absolute node paths. Houdini's drag payload format is
    unverified from here; the e2e test covers the page side only.
18. **COP previews render through a temporary `rop_image` COP** (`coppath` set to
    the target, executed for the current frame, destroyed), not through
    `hou.ImageLayer` pixel access: `ImageLayer` exposes no pixel export and the
    ROP applies the same colour management the viewer does.
19. **The "always inspect COPs" rule is enforced, not just prompted.** The
    extension marks the turn COP-dirty when a modifying tool touched a node whose
    category is `Cop`, and `agent_before_settle` returns `continue: true` with a
    nudge message (max twice per turn) until `inspect_cop` has run. AGENTS.md
    says the same in words.
20. **Follow calls are queued 500 ms apart, never dropped** (`FollowQueue`);
    `agent_before_settle` waits for the queue so the COP-dirty flag is final.
21. **Only vision models are offered in the pane**; the naming model is picked
    from all models since naming is text-only.
22. **Playwright runs serially** (`workers: 1`): one fake-agent sidecar holds one
    conversation, so parallel specs interleaved their prompts and failed at random.
23. **The agent-side follow logic reads the Pi tab's own link group**, set with
    Houdini's built-in Link Pane Tab control.
24. **Help (`?`) goes to GitHub Pages** (`docs/`, Jekyll minimal theme) at
    https://mortoc.github.io/houdini-pi/help; the `.pypanel` `help_url` is static
    and cannot point at the per-launch sidecar port.
25. **History replays tool cards**: `historyOf` pairs pi's `toolCall` parts with
    `toolResult` messages, so a reload or reconnect keeps every card, error and
    image. Fake agent records the same.
26. **A never-saved scene gets no backup and says so once** as a notice; a scene
    with edits still reports `untitled.hip` as its basename, which the old check
    read as "saved".
27. **Tool images are conversation assets**: `~/.houdini-pi/assets/<session id>/
    <tool call id>.png`, served by the sidecar at `/session-assets/…` with the
    launch token, referenced from the tool result's `details.asset` (which pi
    persists, so history replays them). The model still gets the image inline.
    `/assets/` could not be used: Vite's bundle lives there, and the first try
    blanked the page.
28. **Opening the pane always starts a fresh conversation.** Earlier ones are in
    the sessions tab, grouped by the hip they worked on (`sessions.json` now maps
    session path → hip). Opening one also opens its hip; a scene with unsaved
    changes refuses, rather than a modal prompt blocking the bridge.
29. **File ▸ Open does not restart Houdini**, but it rebuilds the desktop, so the
    pane reconnects on the new hip: the hub notices the hip change and starts a
    new conversation unless the current one is already tagged with that hip.
30. **The scene agent loads no MCP servers from `~/.pi/agent/mcp.json`.** A probe
    showed the sidecar pulling in the user's Notion MCP; only `fxhoudini`
    (registered by the extension) belongs in Houdini's tool set.
31. **"Still defaults to Kimi" was a stale sidecar**, not the picker: a fresh
    sidecar against the live bridge reports `deepseek/deepseek-v4-flash-vision-exp`
    on start and after New Conversation. The sidecar only restarts with Houdini.
32. **The pane renders in software** (`QQuickWindow.setGraphicsApi(Software)` +
    Chromium `--disable-gpu --disable-gpu-compositing`). Houdini aborted at
    17:15:49 in `QSGRenderLoop::handleContextCreationFailure` via PySide6's
    QtQuick, which only our QWebEngineView loads: with the GPU full, the web
    view's scene graph could not get a GL context and Qt treats that as fatal.
    A chat pane has no business on the GPU next to Copernicus.
33. **Software rendering of the pane is a setting, off by default** (Mortoc:
    everything should feel as good as possible). `pane.softwareRender` or
    `HOUDINI_PI_SOFTWARE_RENDER=1`. A future resource-pressure responder could
    turn it on before the pane opens; Qt fixes the scene-graph API at the first
    window, so it cannot change for a pane that is already up.
