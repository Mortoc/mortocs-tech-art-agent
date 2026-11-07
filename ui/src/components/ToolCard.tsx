import { useState } from "react";
import type { ToolCall } from "../state.js";
import { CodeBlock } from "./CodeBlock.js";
import { CopyButton } from "./CopyButton.js";

/** Tools that run a script in the app (Houdini, Blender). */
const CODE_TOOLS = ["execute_python", "execute_blender_code"];

function houdiniSummary(tool: ToolCall): string | undefined {
  const is_own = ["inspect_cop", "inspect_viewport", "present_answer", "search_tools"].includes(tool.toolName) || tool.toolName.startsWith("memory_");
  if (is_own) return undefined;
  const args = (tool.args ?? {}) as Record<string, unknown>;
  const parts = [args["node_path"], args["parm_name"], args["node_type"], args["object_name"], args["name"]].filter((v) => typeof v === "string") as string[];
  return parts.join(" · ");
}

/** The script a code-running tool was given, with its language, or undefined for every other tool. */
export function scriptOf(tool: ToolCall): { code: string; language: "python" | "javascript" } | undefined {
  const args = (tool.args ?? {}) as Record<string, unknown>;
  const is_python = CODE_TOOLS.includes(tool.toolName) && typeof args["code"] === "string";
  if (is_python) return { code: args["code"] as string, language: "python" };
  const script = args["code"] ?? args["script"];
  const is_codemode = tool.toolName === "codemode" && typeof script === "string";
  if (is_codemode) return { code: script as string, language: "javascript" };
  return undefined;
}

export interface ShownResult {
  text: string;
  level: "ok" | "warning" | "error";
}

function lastLine(text: string): string {
  const lines = text.split("\n").map((l) => l.trimEnd()).filter((l) => l.trim().length > 0);
  return lines.at(-1) ?? "";
}

/**
 * What of a tool result is worth a human's eyes. A successful execute_python
 * shows nothing; a failed one shows the exception line; stderr is a warning.
 * Other tools show their text only when they failed or returned something short.
 */
export function shownResult(tool: ToolCall): ShownResult | undefined {
  if (tool.result === undefined) return undefined;
  const is_code_tool = CODE_TOOLS.includes(tool.toolName);
  if (is_code_tool) {
    let parsed: Record<string, unknown> | undefined;
    try {
      parsed = JSON.parse(tool.result) as Record<string, unknown>;
    } catch {
      parsed = undefined;
    }
    if (parsed === undefined) return tool.isError ? { text: tool.result, level: "error" } : undefined;
    const error = (parsed["error"] ?? parsed["eval_error"]) as string | undefined;
    if (error) return { text: `${lastLine(error)}\n\n${error}`.trim(), level: "error" };
    const stderr = parsed["stderr"] as string | undefined;
    if (stderr && stderr.trim().length > 0) return { text: stderr.trim(), level: "warning" };
    return undefined;
  }
  if (tool.isError) return { text: tool.result, level: "error" };
  return tool.result.length <= 400 ? { text: tool.result, level: "ok" } : undefined;
}

export function ToolCard({ tool }: { tool: ToolCall }) {
  const label = tool.toolName === "inspect_cop" ? "inspect COP" : tool.toolName === "inspect_viewport" ? "inspect viewport" : tool.toolName;
  const summary = houdiniSummary(tool);
  const pending = tool.result === undefined;
  const script = scriptOf(tool);
  const result = shownResult(tool);
  const [expanded, setExpanded] = useState(false);
  const open_by_default = script !== undefined || tool.imageUrl !== undefined || result?.level === "error";
  return (
    <details className={`tool${tool.isError ? " error" : ""}${pending ? " pending" : ""}`} data-testid="tool-card" open={open_by_default}>
      <summary>
        <span className="tool-name">{label}</span>
        {summary && <span className="tool-summary">{summary}</span>}
        <span className="tool-status">{pending ? "…" : tool.isError ? "error" : `${tool.durationMs} ms`}</span>
      </summary>
      {tool.imageUrl !== undefined && (
        <img
          className={`tool-image${expanded ? " expanded" : " thumb"}`}
          data-testid="tool-image"
          alt={`${label} result`}
          title={expanded ? "click to shrink" : "click to expand"}
          src={withToken(tool.imageUrl)}
          onClick={() => setExpanded(!expanded)}
        />
      )}
      {script !== undefined && <CodeBlock code={script.code} language={script.language} />}
      {script === undefined && (
        <div className="text-wrap">
          <CopyButton text={JSON.stringify(tool.args, null, 2)} />
          <pre className="tool-args">{JSON.stringify(tool.args, null, 2)}</pre>
        </div>
      )}
      {result !== undefined && (
        <div className="text-wrap">
          <CopyButton text={result.text} />
          <pre className={`tool-result ${result.level}`} data-testid="tool-result">{result.text}</pre>
        </div>
      )}
    </details>
  );
}

/** Conversation-asset URLs on the sidecar need the launch token; data: URLs are self-contained. */
export function withToken(url: string, search = window.location.search): string {
  if (!url.startsWith("/session-assets/")) return url;
  const token = new URLSearchParams(search).get("token") ?? "";
  return `${url}?token=${encodeURIComponent(token)}`;
}
