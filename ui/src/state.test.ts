import type { ServerMessage } from "@houdini-pi/protocol";
import { describe, expect, it } from "vitest";
import { initialState, reduce, type PaneState } from "./state.js";

const play = (messages: ServerMessage[], from: PaneState = initialState) => messages.reduce(reduce, from);

const failingTurn = (text: string): ServerMessage[] => [
  { type: "user_message", text },
  { type: "tool_start", toolCallId: `${text}-a`, toolName: "inspect_viewport", args: {} },
  { type: "tool_end", toolCallId: `${text}-a`, toolName: "inspect_viewport", result: "boom", isError: true, durationMs: 1 },
  { type: "tool_start", toolCallId: `${text}-b`, toolName: "inspect_viewport", args: {} },
  { type: "tool_end", toolCallId: `${text}-b`, toolName: "inspect_viewport", result: "boom", isError: true, durationMs: 1 },
];

const workingTurn = (text: string): ServerMessage[] => [
  { type: "user_message", text },
  { type: "tool_start", toolCallId: `${text}-ok`, toolName: "get_node_info", args: {} },
  { type: "tool_end", toolCallId: `${text}-ok`, toolName: "get_node_info", result: "{}", isError: false, durationMs: 1 },
  { type: "settled", aborted: false, cost: 0, durationMs: 10 },
];

describe("the turn footer", () => {
  it("goes away as soon as the next message is sent", () => {
    const afterFirst = play([...failingTurn("one"), { type: "settled", aborted: false, cost: 0, durationMs: 10 }]);
    expect(afterFirst.lastTurn?.errors).toBe(2);
    expect(reduce(afterFirst, { type: "user_message", text: "two" }).lastTurn).toBeUndefined();
  });

  it("counts only this turn's tool errors when the previous turn never settled", () => {
    // The bridge died mid-turn, so no `settled` arrived and the first turn's entry stayed open.
    const state = play([...failingTurn("one"), ...workingTurn("two")]);
    expect(state.lastTurn?.errors).toBe(0);
    expect(state.entries.filter((e) => e.kind === "assistant" && e.streaming)).toEqual([]);
  });

  it("still reports a turn's own errors", () => {
    const state = play([...workingTurn("one"), ...failingTurn("two"), { type: "settled", aborted: false, cost: 0, durationMs: 10 }]);
    expect(state.lastTurn?.errors).toBe(2);
  });
});

describe("conversation cost", () => {
  const hello = (cost: number): ServerMessage => ({ type: "hello", sessionId: "s", sessionPath: "/s.jsonl", model: undefined, thinkingLevel: "off", host: "houdini", document: undefined, linkScheme: "houdini", title: "Houdini Agent", sessionName: undefined, isStreaming: false, cost });

  it("starts from what the conversation had already cost", () => {
    expect(play([hello(0.25)]).cost).toBe(0.25);
  });

  it("keeps the running total and gives the turn footer only that turn's share", () => {
    const state = play([hello(0.25), { type: "user_message", text: "a" }, { type: "settled", aborted: false, cost: 0.3, durationMs: 1 }]);
    expect(state.cost).toBe(0.3);
    expect(state.lastTurn?.cost).toBeCloseTo(0.05);
    const next = play([{ type: "user_message", text: "b" }, { type: "settled", aborted: false, cost: 0.31, durationMs: 1 }], state);
    expect(next.cost).toBe(0.31);
    expect(next.lastTurn?.cost).toBeCloseTo(0.01);
  });

  it("resets with the conversation", () => {
    const state = play([hello(0.25), { type: "settled", aborted: false, cost: 0.3, durationMs: 1 }, hello(0)]);
    expect(state.cost).toBe(0);
    expect(state.lastTurn).toBeUndefined();
  });
});

describe("an unreachable model server", () => {
  it("becomes a notice that offers to try again", () => {
    const next = reduce(initialState, { type: "error", message: "Can't reach your local model server at localhost:11434.", action: "unreachable" });
    const notice = next.entries.at(-1);
    expect(notice).toMatchObject({ kind: "notice", level: "error", action: "unreachable" });
  });
});

describe("a second opinion", () => {
  const review: ServerMessage = {
    type: "review", verdict: "check", summary: "One request was ambiguous.",
    findings: [{ kind: "ambiguity", severity: "high", turn: 1, userSaid: "make it bigger", agentDid: "scaled by 2", risk: "wrong object?", askInstead: "Which object?" }],
    reviewer: "gateway/google/gemini-3.8-flash", reviewerFamily: "google", agentFamily: "openai", differentFamily: true, turns: 1, cost: 0.002,
  };

  it("is pending from the click until the answer arrives, then becomes a card and adds its cost", () => {
    const asked = reduce(initialState, { type: "review_requested" });
    expect(asked.reviewing).toBe(true);
    const done = reduce(asked, review);
    expect(done.reviewing).toBe(false);
    expect(done.cost).toBeCloseTo(0.002);
    expect(done.entries.at(-1)).toMatchObject({ kind: "review", review: { verdict: "check", differentFamily: true } });
  });

  it("stops waiting if the review fails, and says why", () => {
    const failed = reduce(reduce(initialState, { type: "review_requested" }), { type: "error", message: "Can't reach the model provider", action: "unreachable" });
    expect(failed.reviewing).toBe(false);
    expect(failed.entries.at(-1)).toMatchObject({ kind: "notice", action: "unreachable" });
  });
});
