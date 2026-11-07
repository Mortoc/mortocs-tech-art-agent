/**
 * WebSocket protocol between the houdini-pi sidecar and the pane UI.
 *
 * Both ends import this module, so a message shape cannot drift between them.
 * Decoders throw on anything they do not recognise: an unknown message is a
 * bug on the sending side, never something to ignore.
 */

export type ThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

export interface ModelRef {
  provider: string;
  id: string;
  name: string;
  reasoning: boolean;
  /** Accepts image input. The pane only offers these: the agent must be able to look at renders. */
  vision: boolean;
  /** USD per million tokens, when the provider publishes it. */
  cost?: { input: number; output: number };
}

/** Names a model without its metadata: what the sidecar says about a model it also listed. */
export interface ModelKey {
  provider: string;
  id: string;
}

export interface SessionRef {
  id: string;
  path: string;
  name: string | undefined;
  firstMessage: string;
  modified: number;
}

export type ActionKind = "select" | "parm" | "network" | "pane" | "help";

/** An answer the artist reads: Markdown whose links (houdini://, blender://) the pane renders as buttons; `invalid` maps a link href to why it is disabled. */
export interface Answer {
  markdown: string;
  invalid: Record<string, string>;
}

export interface ImageAttachment {
  mimeType: string;
  data: string;
}

export interface ReviewFinding {
  kind: string;
  severity: "low" | "medium" | "high";
  /** Which artist message, counting from 1. */
  turn: number;
  userSaid: string;
  agentDid: string;
  risk: string;
  askInstead: string;
}

/** A second opinion on whether the artist and the agent understood each other. */
export interface ReviewResult {
  verdict: "aligned" | "check" | "misaligned";
  summary: string;
  findings: ReviewFinding[];
  /** The reviewing model ("provider/id") and the lineages involved. */
  reviewer: string;
  reviewerFamily: string;
  agentFamily: string;
  /** False when no model from another family was available: a weaker check. */
  differentFamily: boolean;
  turns: number;
  cost: number;
}

export type ClientMessage =
  | { type: "prompt"; text: string; images?: ImageAttachment[] }
  | { type: "steer"; text: string }
  | { type: "follow_up"; text: string }
  | { type: "abort" }
  | { type: "new_session" }
  | { type: "switch_session"; sessionPath: string }
  | { type: "fork"; entryId: string }
  | { type: "set_model"; provider: string; modelId: string }
  | { type: "set_thinking"; level: ThinkingLevel }
  | { type: "compact" }
  | { type: "list_models" }
  | { type: "list_sessions" }
  | { type: "memory_read" }
  | { type: "memory_edit"; text: string }
  | { type: "undo_compaction" }
  | { type: "project_memory_read" }
  | { type: "project_memory_edit"; text: string }
  | { type: "rename_session"; name: string }
  | { type: "host_action"; kind: ActionKind; value: string }
  | { type: "open_external"; url: string }
  | { type: "undo_turn" }
  /** Ask a model from another family whether the artist and the agent understood each other. */
  | { type: "review" };

export interface HelloState {
  sessionId: string;
  sessionPath: string | undefined;
  model: ModelRef | undefined;
  thinkingLevel: ThinkingLevel;
  /** The app this pane is attached to ("houdini", "blender") and its open document. */
  host: string;
  document: string | undefined;
  /** The scheme answers use for buttons, and the title the pane shows. */
  linkScheme: string;
  title: string;
  sessionName: string | undefined;
  isStreaming: boolean;
  /** What the conversation has cost so far, USD. */
  cost: number;
}

export type ServerMessage =
  | ({ type: "hello" } & HelloState)
  | { type: "state"; model: ModelRef | undefined; thinkingLevel: ThinkingLevel; isStreaming: boolean }
  | { type: "session_name"; name: string | undefined }
  | { type: "user_message"; text: string }
  | { type: "text_delta"; delta: string }
  | { type: "thinking_delta"; delta: string }
  | { type: "message_end"; role: "assistant"; text: string }
  | { type: "tool_start"; toolCallId: string; toolName: string; args: unknown }
  | { type: "tool_end"; toolCallId: string; toolName: string; result: string; isError: boolean; durationMs: number; imageUrl?: string; answer?: Answer }
  | { type: "turn_end"; turnIndex: number }
  | { type: "settled"; aborted: boolean; cost: number; durationMs: number }
  | {
      type: "models";
      models: ModelRef[];
      /** The curated models among `models`, best first (settings.defaultModel, available ones only). */
      recommended: ModelKey[];
      /** What a new conversation starts on: the first recommended model, when there is one. */
      defaultModel?: ModelKey;
    }
  | { type: "sessions"; sessions: SessionRef[] }
  | { type: "memory"; text: string; prevAvailable: boolean }
  | { type: "memory_compacted"; before: number; after: number }
  | { type: "project_memory"; available: boolean; text: string; prevAvailable: boolean }
  | ({ type: "review" } & ReviewResult)
  | { type: "error"; message: string; action?: "reconnect" | "login" | "unreachable" | "retry" }
  | { type: "history"; messages: HistoryMessage[] };

export interface HistoryTool {
  toolCallId: string;
  toolName: string;
  args: unknown;
  result?: string;
  isError?: boolean;
  /** Where the pane loads the result image: a /session-assets/… path on the sidecar, or a data: URL. */
  imageUrl?: string;
  answer?: Answer;
}

export interface HistoryMessage {
  role: "user" | "assistant";
  text: string;
  tools?: HistoryTool[];
}

const CLIENT_TYPES: Record<ClientMessage["type"], readonly string[]> = {
  prompt: ["text"],
  steer: ["text"],
  follow_up: ["text"],
  abort: [],
  new_session: [],
  switch_session: ["sessionPath"],
  fork: ["entryId"],
  set_model: ["provider", "modelId"],
  set_thinking: ["level"],
  compact: [],
  list_models: [],
  list_sessions: [],
  memory_read: [],
  memory_edit: ["text"],
  undo_compaction: [],
  project_memory_read: [],
  project_memory_edit: ["text"],
  rename_session: ["name"],
  host_action: ["kind", "value"],
  open_external: ["url"],
  undo_turn: [],
  review: [],
};

const SERVER_TYPES: Record<ServerMessage["type"], readonly string[]> = {
  hello: ["sessionId", "thinkingLevel", "isStreaming", "host", "linkScheme", "title"],
  state: ["thinkingLevel", "isStreaming"],
  session_name: [],
  user_message: ["text"],
  text_delta: ["delta"],
  thinking_delta: ["delta"],
  message_end: ["role", "text"],
  tool_start: ["toolCallId", "toolName"],
  tool_end: ["toolCallId", "toolName", "result", "isError", "durationMs"],
  turn_end: ["turnIndex"],
  settled: ["aborted", "cost", "durationMs"],
  models: ["models", "recommended"],
  sessions: ["sessions"],
  memory: ["text", "prevAvailable"],
  memory_compacted: ["before", "after"],
  project_memory: ["available", "text", "prevAvailable"],
  error: ["message"],
  review: ["verdict", "summary", "findings"],
  history: ["messages"],
};

export class ProtocolError extends Error {}

export function encode(message: ClientMessage | ServerMessage): string {
  return JSON.stringify(message);
}

function decode<T extends { type: string }>(raw: string, table: Record<string, readonly string[]>, side: string): T {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new ProtocolError(`${side} message is not JSON: ${(error as Error).message}`);
  }
  const is_object = typeof parsed === "object" && parsed !== null && !Array.isArray(parsed);
  if (!is_object) throw new ProtocolError(`${side} message is not an object`);
  const record = parsed as Record<string, unknown>;
  const type = record["type"];
  if (typeof type !== "string" || !(type in table)) {
    throw new ProtocolError(`unknown ${side} message type: ${String(type)}`);
  }
  for (const field of table[type] ?? []) {
    if (!(field in record)) throw new ProtocolError(`${side} message "${type}" is missing "${field}"`);
  }
  return record as unknown as T;
}

export function decodeClient(raw: string): ClientMessage {
  return decode<ClientMessage>(raw, CLIENT_TYPES, "client");
}

export function decodeServer(raw: string): ServerMessage {
  return decode<ServerMessage>(raw, SERVER_TYPES, "server");
}
