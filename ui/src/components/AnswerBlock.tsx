import { marked } from "marked";
import { useMemo } from "react";
import type { Answer, ClientMessage, ActionKind } from "@houdini-pi/protocol";
import { CopyButton } from "./CopyButton.js";

const KINDS: ActionKind[] = ["select", "parm", "network", "pane", "help"];
const PARAM: Record<ActionKind, string> = { select: "path", parm: "path", network: "path", pane: "type", help: "page" };
const ICON: Record<ActionKind, string> = { select: "◎", parm: "⚙", network: "⌂", pane: "▣", help: "?" };

export function parseAction(href: string): { kind: ActionKind; value: string } | undefined {
  const match = /^(?:houdini|blender):\/\/([a-z]+)\?([a-z]+)=(.+)$/.exec(href);
  if (match === null) return undefined;
  const [, kind, param, raw] = match as unknown as [string, ActionKind, string, string];
  const is_known = KINDS.includes(kind) && PARAM[kind] === param;
  return is_known ? { kind, value: decodeURIComponent(raw) } : undefined;
}

/**
 * An answer the artist reads. `houdini://` and `blender://` links become buttons
 * that act in the app; links the core could not validate render disabled with the reason.
 */
export function AnswerBlock({ answer, send }: { answer: Answer; send: (message: ClientMessage) => void }) {
  const html = useMemo(() => {
    const renderer = new marked.Renderer();
    renderer.link = ({ href, text }) => {
      const action = parseAction(href);
      // Not an app action: render as inert text. This pane is an app,
      // not a browser; a navigable link would tear the agent out of the window.
      if (action === undefined) return `<span class="link" title="${href.replace(/"/g, "&quot;")}">${text}</span>`;
      const reason = answer.invalid[href];
      const disabled = reason === undefined ? "" : ` disabled title="${reason.replace(/"/g, "&quot;")}"`;
      return `<button type="button" class="action action-${action.kind}" data-href="${href}"${disabled}><span class="action-icon">${ICON[action.kind]}</span>${text}</button>`;
    };
    return marked.parse(answer.markdown, { async: false, renderer }) as string;
  }, [answer]);

  const onClick = (event: React.MouseEvent) => {
    const button = (event.target as HTMLElement).closest("button.action") as HTMLButtonElement | null;
    if (button === null || button.disabled) return;
    const action = parseAction(button.dataset["href"] ?? "");
    if (action !== undefined) send({ type: "host_action", kind: action.kind, value: action.value });
  };

  return (
    <div className="answer" data-testid="answer" onClick={onClick}>
      <CopyButton text={answer.markdown} />
      <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
