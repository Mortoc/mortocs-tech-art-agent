"""Run the core: `tech-art-core` (listens on 127.0.0.1:9100), or under systemd socket activation.

Under a `.socket` unit the listening socket arrives as fd 3 and nothing runs until the first connection;
the core then exits after `idleTimeout` seconds with no app, pane or running turn, and systemd wakes it again.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

DEFAULT_PORT = 9100
SD_LISTEN_FDS_START = 3


def inherited_fd(env: dict[str, str] | None = None, pid: int | None = None) -> int | None:
    """The socket systemd handed us (LISTEN_FDS/LISTEN_PID protocol), or None when started by hand."""
    env = os.environ if env is None else env
    if env.get("LISTEN_PID") != str(pid if pid is not None else os.getpid()):
        return None
    return SD_LISTEN_FDS_START if int(env.get("LISTEN_FDS", "0")) >= 1 else None


def default_ui_dir() -> Path | None:
    """The pane UI: $TECH_ART_AGENT_UI (the Nix wrapper sets it), else the built UI of a source checkout."""
    if os.environ.get("TECH_ART_AGENT_UI"):
        return Path(os.environ["TECH_ART_AGENT_UI"])
    checkout = Path(__file__).resolve().parents[3] / "ui" / "dist"
    return checkout if checkout.is_dir() else None


def configure_logfire(app) -> None:
    """Traces for every turn, model call and tool call. Local-only until LOGFIRE_TOKEN is set."""
    import logfire

    logfire.configure(service_name="tech-art-core", send_to_logfire="if-token-present", console=False)
    logfire.instrument_pydantic_ai()
    logfire.instrument_fastapi(app, excluded_urls="health")


async def watch_idle(core, server, timeout: int, poll: float = 5.0) -> None:
    while not server.should_exit:
        await asyncio.sleep(poll)
        if timeout and core.idle_for() > timeout:
            logging.getLogger("tech_art_core").info("idle for %ss; exiting until woken", timeout)
            server.should_exit = True


def sign_in(name: str, home: Path | None) -> None:
    """Interactive one-time OAuth sign-in for an extra MCP server (Notion): opens a browser, caches the token."""
    from fastmcp.client import Client

    from .providers import extra_client, oauth_server_dir
    from .settings import load_settings, state_dir

    state = home or state_dir()
    server = load_settings(state / "settings.json").mcp_servers.get(name)
    if server is None or server.auth != "oauth" or not server.url:
        raise SystemExit(f"{name!r} is not an OAuth MCP server in settings (mcpServers.{name}.auth = \"oauth\").")

    async def go() -> None:
        async with extra_client(name, server, state) as client:
            tools = await client.list_tools()
        print(f"signed in to {name}: {len(tools)} tools available. Token cached in {oauth_server_dir(state, name)}")

    asyncio.run(go())


def run_probe(argv: list[str]) -> int:
    """`tech-art-core probe`: try each configured model with a tiny request and print what works."""
    from .probe import probe_models, row, table
    from .providers import build_model
    from .settings import load_settings, state_dir

    parser = argparse.ArgumentParser(prog="tech-art-core probe", description="Try each configured model: text, an image, a tool call (a few cents at most).")
    parser.add_argument("--model", action="append", default=[], help="probe this model key instead of the configured list (repeatable), e.g. gateway/openai/gpt-6.1-sol")
    parser.add_argument("--quick", action="store_true", help="text only: is it reachable at all?")
    parser.add_argument("--home", type=Path, default=None)
    args = parser.parse_args(argv)
    settings = load_settings((args.home or state_dir()) / "settings.json")
    keys = args.model or list(dict.fromkeys([f"gateway/{m}" for m in settings.gateway.models] + settings.default_model))
    if not (os.environ.get("PYDANTIC_AI_GATEWAY_API_KEY") or os.environ.get("PAIG_API_KEY")) and any(k.startswith("gateway/") for k in keys):
        print("PYDANTIC_AI_GATEWAY_API_KEY is not set in this environment; gateway models will fail until it is.", file=sys.stderr)
    print(f"probing {len(keys)} models at once...", file=sys.stderr, flush=True)
    probes = asyncio.run(probe_models(keys, settings, lambda key: build_model(key, settings), quick=args.quick,
                                      on_done=lambda p: print(f"  done: {row(p)}", file=sys.stderr, flush=True)))
    print()
    print(table(probes))
    return 0 if any(p.usable or (args.quick and p.text.ok) for p in probes) else 1


def main(argv: list[str] | None = None) -> None:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["probe"]:
        raise SystemExit(run_probe(argv[1:]))
    if argv[:1] == ["auth"]:
        parser = argparse.ArgumentParser(prog="tech-art-core auth", description="Sign in to an OAuth MCP server (e.g. notion) once.")
        parser.add_argument("name")
        parser.add_argument("--home", type=Path, default=None)
        parsed = parser.parse_args(argv[1:])
        return sign_in(parsed.name, parsed.home)
    import uvicorn

    from .app import Core, create_app
    from .settings import state_dir

    parser = argparse.ArgumentParser(prog="tech-art-core")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--home", type=Path, default=None, help="state directory (default: ~/.tech-art-agent)")
    parser.add_argument("--static-dir", type=Path, default=default_ui_dir())
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        raise SystemExit(f"refusing to bind {args.host}: the core serves loopback only")

    core = Core(args.home or state_dir())
    app = create_app(core, args.static_dir)
    configure_logfire(app)
    fd = inherited_fd()
    config = uvicorn.Config(app, **({"fd": fd} if fd is not None else {"host": args.host, "port": args.port}), log_level="info", access_log=False, ws_ping_interval=20)
    server = uvicorn.Server(config)

    async def run() -> None:
        watchdog = asyncio.create_task(watch_idle(core, server, core.settings.idle_timeout))
        await server.serve()
        watchdog.cancel()

    asyncio.run(run())


if __name__ == "__main__":
    main()
