"""What differs between host apps: how the model gets its tools, how scene context reads, what to follow.

Everything else (sessions, memory, answers, the bus) is shared. A host is a `HostProfile` plus the
connector inside the app that registers the internal helper tools named in `HELPERS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from .settings import McpServerSettings, Settings

# The lifecycle helpers every connector registers (as `internal` tools). Names are the contract.
HELPERS = (
    "scene_context",  # -> {document, current, selected:[{path,type}], extra:{...}}
    "begin_turn", "end_turn",  # undo grouping
    "backup_scene",  # -> {path} | {skipped}
    "memory_read", "memory_write",  # document memory
    "project_memory_read", "project_memory_write",
    "sessions_read", "session_tag",  # conversations recorded in the document
    "follow", "validate_paths", "do_action", "undo_turn",  # make the app follow the agent / answer buttons
)


def prompt(name: str) -> str:
    return resources.files("tech_art_core").joinpath("prompts", name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class McpSpec:
    """An MCP server the core runs for the model: `uvx [--refresh] --from <package><ref> <executable>`."""

    package: str
    executable: str
    # How a pin attaches to the package: "@ref" for a git URL, "==ver" for PyPI.
    pin_style: str = "=="
    env: dict[str, str] = field(default_factory=dict)

    def argv(self, pin: McpServerSettings, uvx: str = "uvx", offline: bool = False) -> list[str]:
        """Offline: no refresh, and uv may only use what it already has cached."""
        spec = self.package + (f"{self.pin_style}{pin.ref}" if pin.ref else "")
        if offline:
            return [uvx, "--offline", "--from", spec, self.executable]
        refresh = ["--refresh"] if pin.refresh and not pin.ref else []
        return [uvx, *refresh, "--from", spec, self.executable]


@dataclass(frozen=True)
class Inspector:
    """A tool that returns an image of the app: the connector helper produces a PNG, the core shows it to the model."""

    name: str
    helper: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    # Seeing this host's output is required after changing it (the settle nudge): after any change...
    required_after_modify: bool = False
    # ...or only after a change to nodes of this category (Houdini: COPs, whose wiring only the image proves).
    required_category: str | None = None


@dataclass(frozen=True)
class HostProfile:
    kind: str
    title: str
    link_scheme: str
    document_noun: str
    contract: str
    mcp: McpSpec
    # Tools shown to the model up front; the rest are found through tool search.
    direct_tools: frozenset[str]
    # Tool-name prefixes that only read.
    read_prefixes: tuple[str, ...]
    inspectors: tuple[Inspector, ...]
    # Argument/result keys whose string values name things in the scene, for follow.
    path_keys: frozenset[str]
    path_pattern: re.Pattern[str]
    action_kinds: tuple[str, ...]
    house_rules: str = ""

    def mcp_pin(self, settings: Settings) -> McpServerSettings:
        return getattr(settings.mcp, self.kind)

    def can_modify(self, tool_name: str, declared: bool | None = None) -> bool:
        """Whether a tool can change the scene. A declared flag wins; otherwise reads are named for what they return."""
        if declared is not None:
            return declared
        return not any(tool_name.startswith(p) for p in self.read_prefixes)

    def paths_from(self, args: Any, result: Any) -> list[str]:
        """Scene paths a tool call touched, from its arguments and result, in order, at most 20."""
        found: list[str] = []

        def visit(value: Any, keyed: bool, depth: int) -> None:
            if depth > 6:
                return
            if isinstance(value, str):
                if keyed and self.path_pattern.match(value) and value not in found:
                    found.append(value)
            elif isinstance(value, list):
                for item in value:
                    visit(item, keyed, depth + 1)
            elif isinstance(value, dict):
                for key, item in value.items():
                    visit(item, key in self.path_keys, depth + 1)

        visit(args, False, 0)
        visit(result, False, 0)
        return found[:20]

    def context_block(self, scene: dict[str, Any], doc_memory: str, project_memory: str | None, others: list[str], user_memory: str = "") -> str:
        selected = ", ".join(f"{s['path']} ({s['type']})" for s in scene.get("selected", [])) or "(nothing selected)"
        lines = [
            f"## {self.title} scene right now",
            f"- {self.document_noun}: {scene.get('document') or '(untitled)'}",
            f"- current: {scene.get('current') or '(none)'}",
            f"- selected: {selected}",
            *(f"- {k}: {v}" for k, v in scene.get("extra", {}).items()),
        ]
        if others:
            lines += ["", "## Other connected apps", *(f"- {o}" for o in others)]
        if doc_memory.strip():
            lines += ["", f"## {self.document_noun.capitalize()} memory", "", doc_memory.strip()]
        if project_memory and project_memory.strip():
            lines += ["", "## Project memory (shared across the project)", "", project_memory.strip()]
        if user_memory.strip():
            lines += ["", "## User memory (this artist, across all apps and projects)", "", user_memory.strip()]
        if self.house_rules.strip():
            lines += ["", "## House rules (defaults; project memory overrides)", "", self.house_rules.strip()]
        return "\n".join(lines)

    def system_prompt(self) -> str:
        return prompt("base.md") + "\n" + self.contract


HOUDINI_DIRECT = frozenset(
    "get_scene_info get_scene_summary get_selection set_selection explain_node get_node_card get_node_info find_nodes "
    "list_children create_node connect_nodes delete_node rename_node build_network layout_children get_parameters "
    "set_parameter set_parameters set_expression create_wrangle get_wrangle_code set_wrangle_code validate_vex "
    "set_current_network get_node_errors_detailed execute_python search_help".split()
)
READ_PREFIXES = ("get_", "list_", "find_", "search_", "explain_", "capture_", "inspect_", "compare_", "sample_", "validate_", "evaluate_", "describe_", "verify_", "render_", "memory_read")

HOUDINI = HostProfile(
    kind="houdini",
    title="Houdini",
    link_scheme="houdini",
    document_noun="hip",
    contract=prompt("houdini.md"),
    house_rules=prompt("houdini-house-rules.md"),
    mcp=McpSpec(package="git+https://github.com/healkeiser/fxhoudinimcp", executable="fxhoudinimcp", pin_style="@"),
    direct_tools=HOUDINI_DIRECT,
    read_prefixes=READ_PREFIXES,
    inspectors=(
        Inspector("inspect_cop", "render_cop", "Render a Copernicus (COP) node to an image and look at it. Call after every change to a COP network, on its output node, before you finish.", {"node_path": {"type": "string", "description": "Absolute path of the COP node, usually the network's output"}, "max_size": {"type": "integer", "description": "Longest edge in pixels, default 256"}}, required_category="Cop"),
        Inspector("inspect_viewport", "capture_viewport", "Screenshot the scene viewer (or a named pane tab) and look at it.", {"pane_name": {"type": "string", "description": "Pane tab name; default: the first scene viewer"}}),
    ),
    path_keys=frozenset({"node_path", "path", "parent_path", "parent", "node", "new_path", "target_path", "node_paths", "paths", "nodes", "created", "selected"}),
    path_pattern=re.compile(r"^/[A-Za-z0-9_][A-Za-z0-9_/.:-]*$"),
    action_kinds=("select", "parm", "network", "pane", "help"),
)

BLENDER = HostProfile(
    kind="blender",
    title="Blender",
    link_scheme="blender",
    document_noun="blend file",
    contract=prompt("blender.md"),
    mcp=McpSpec(package="mcp-for-blender", executable="mcp-for-blender", pin_style="=="),
    direct_tools=frozenset(),  # the server's tool list is short; everything is direct
    read_prefixes=READ_PREFIXES + ("search",),
    inspectors=(Inspector("inspect_viewport", "capture_viewport", "Screenshot the 3D viewport and look at it. Use after changing geometry, materials or lights.", {}, required_after_modify=True),),
    path_keys=frozenset({"name", "names", "object", "object_name", "objects", "target", "created", "selected"}),
    path_pattern=re.compile(r"^[^\s/][^/]{0,62}$"),
    action_kinds=("select", "network", "pane", "help"),
)

PROFILES: dict[str, HostProfile] = {p.kind: p for p in (HOUDINI, BLENDER)}
