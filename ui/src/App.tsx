import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { ClientMessage } from "@houdini-pi/protocol";
import { ActivityBar } from "./components/ActivityBar.js";
import { ChatLog } from "./components/ChatLog.js";
import { MemoryTab } from "./components/MemoryTab.js";
import { PromptBox } from "./components/PromptBox.js";
import { SessionsTab } from "./components/SessionsTab.js";
import { Toolbar } from "./components/Toolbar.js";
import { initialState, reduce } from "./state.js";
import { PaneSocket, type Status } from "./ws.js";

export type Tab = "chat" | "memory" | "sessions";

export function App() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const [status, setStatus] = useState<Status>("connecting");
  const [tab, setTab] = useState<Tab>("chat");
  const [droppedNodes, setDroppedNodes] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const socketRef = useRef<PaneSocket | undefined>(undefined);

  useEffect(() => {
    const socket = new PaneSocket(PaneSocket.urlFromPage(window.location));
    socket.onMessage = dispatch;
    socket.onStatus = (next) => {
      setStatus(next);
      if (next === "open") socket.send({ type: "list_models" });
    };
    socket.connect();
    socketRef.current = socket;
    return () => socket.close();
  }, []);

  const send = useMemo(
    () => (message: ClientMessage) => {
      try {
        socketRef.current?.send(message);
      } catch (error) {
        dispatch({ type: "error", message: (error as Error).message, action: "reconnect" });
      }
    },
    [],
  );

  // Houdini's own drags (parms, channels) never reach the DOM; the pane's Qt
  // side decodes them and calls this with the paths.
  useEffect(() => {
    const w = window as Window & { houdiniPiDrop?: (paths: string[]) => void };
    w.houdiniPiDrop = (paths) => {
      if (!Array.isArray(paths) || paths.length === 0) return;
      setTab("chat");
      setDroppedNodes((current) => [...current, ...paths.filter((p) => typeof p === "string" && !current.includes(p))]);
    };
    return () => {
      delete w.houdiniPiDrop;
    };
  }, []);

  const openTab = (next: Tab) => {
    setTab(next);
    if (next === "sessions") send({ type: "list_sessions" });
    if (next === "memory") {
      send({ type: "memory_read" });
      send({ type: "project_memory_read" });
    }
  };

  // Houdini drags node and parameter paths as text, in whichever mime types
  // the source pane uses. Every type is scanned; anything that looks like an
  // absolute Houdini path becomes a reference in the prompt.
  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    const types = [...event.dataTransfer.types];
    const payloads = types.map((type) => event.dataTransfer.getData(type)).filter((t) => typeof t === "string" && t.length > 0);
    const paths = nodePathsIn(payloads.join("\n"));
    if (paths.length === 0) {
      dispatch({ type: "error", message: `Dropped something I could not read as a Houdini path. Types: ${types.join(", ") || "(none)"}. Data: ${payloads.map((p) => p.slice(0, 120)).join(" | ") || "(empty)"}` });
      return;
    }
    setTab("chat");
    setDroppedNodes((current) => [...current, ...paths.filter((p) => !current.includes(p))]);
  };

  return (
    <div className="pane" data-status={status} onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <Toolbar
        state={state}
        status={status}
        send={send}
        tab={tab}
        onTab={openTab}
        onModelPicked={() => dispatch({ type: "model_picked" })}
        onReview={() => {
          dispatch({ type: "review_requested" });
          send({ type: "review" });
        }}
      />
      {tab === "chat" && (
        <>
          <ChatLog
            title={state.hello?.title}
            onDraft={setDraft}
            reviewing={state.reviewing}
            onRetry={() => {
              const lastUser = [...state.entries].reverse().find((e) => e.kind === "user");
              if (lastUser?.kind === "user") send({ type: "prompt", text: lastUser.text });
            }}
            entries={state.entries}
            lastTurn={state.lastTurn}
            send={send}
            onUndoTurn={() => {
              // Revert the whole turn in the app (it is one undo group), drop it
              // from the log, and hand the message back to the artist to edit.
              send({ type: "undo_turn" });
              const lastUser = [...state.entries].reverse().find((e) => e.kind === "user");
              if (lastUser?.kind === "user") setDraft(lastUser.text);
              dispatch({ type: "drop_last_turn" });
            }}
          />
          <ActivityBar activity={state.activity} streaming={state.isStreaming} onStop={() => send({ type: "abort" })} />
          <PromptBox streaming={state.isStreaming} connected={status === "open"} thinkingLevel={state.thinkingLevel} send={send} nodes={droppedNodes} onNodesChange={setDroppedNodes} draft={draft} onDraftChange={setDraft} />
        </>
      )}
      {tab === "memory" && <MemoryTab memory={state.memory} projectMemory={state.projectMemory} compaction={state.compaction} send={send} />}
      {tab === "sessions" && <SessionsTab sessions={state.sessions} current={state.hello?.sessionPath} send={send} onOpen={() => setTab("chat")} />}
    </div>
  );
}

export function nodePathsIn(text: string): string[] {
  return text
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => /^\/[A-Za-z0-9_\/.:-]+$/.test(token) && token.length > 1);
}
