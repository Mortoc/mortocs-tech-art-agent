import type { ClientMessage, SessionRef } from "@houdini-pi/protocol";

interface Props {
  sessions: SessionRef[];
  current: string | undefined;
  send: (message: ClientMessage) => void;
  onOpen: () => void;
}

/** This scene's conversations, newest first. The list lives in the scene file, so a Save As keeps it. */
export function SessionsTab({ sessions, current, send, onOpen }: Props) {
  return (
    <div className="sessions" data-testid="sessions">
      <ul>
        {[...sessions]
          .sort((a, b) => b.modified - a.modified)
          .map((s) => (
            <li key={s.path}>
              <button
                className={s.path === current ? "current" : ""}
                title="Open this conversation"
                onClick={() => {
                  send({ type: "switch_session", sessionPath: s.path });
                  onOpen();
                }}
              >
                <span className="session-title">{s.name ?? (s.firstMessage || "New Conversation")}</span>
                <span className="session-date">{new Date(s.modified).toLocaleString()}</span>
              </button>
            </li>
          ))}
      </ul>
      {sessions.length === 0 && <p className="muted">No conversations in this scene yet.</p>}
    </div>
  );
}
