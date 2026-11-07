import type { ClientMessage } from "@houdini-pi/protocol";
import type { Tab } from "../App.js";
import type { PaneState } from "../state.js";
import type { Status } from "../ws.js";
import { ModelPicker } from "./ModelPicker.js";

interface Props {
  state: PaneState;
  status: Status;
  send: (message: ClientMessage) => void;
  tab: Tab;
  onTab: (tab: Tab) => void;
  onModelPicked: () => void;
  /** Ask for a second opinion on whether the artist and the agent understood each other. */
  onReview: () => void;
}

export function Toolbar({ state, status, send, tab, onTab, onModelPicked, onReview }: Props) {
  const spoke = state.entries.some((entry) => entry.kind === "user");
  return (
    <header className="toolbar">
      <div className="toolbar-row">
        <span className="session-name" data-testid="session-name" title="Named automatically from the conversation">
          {state.sessionName}
        </span>
        <span className="session-cost" data-testid="session-cost" title="What this conversation has cost so far">
          ${state.cost.toFixed(3)}
        </span>
        <nav className="tabs" role="tablist">
          {(["chat", "memory", "sessions"] as const).map((name) => (
            <button key={name} role="tab" aria-selected={tab === name} onClick={() => onTab(name)}>
              {name}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        {status !== "open" && (
          <span className={`status status-${status}`} data-testid="status" title="Connection to the agent core">
            {status === "connecting" ? "connecting to the agent core…" : "agent core disconnected, reconnecting…"}
          </span>
        )}
        <button
          className="review-button"
          data-testid="review-button"
          disabled={!spoke || state.isStreaming || state.reviewing || status !== "open"}
          onClick={onReview}
          title="Ask a model from a different family whether you and the agent understood each other"
        >
          check understanding
        </button>
        <ModelPicker
          models={state.models}
          current={state.model}
          recommended={state.recommendedModels}
          defaultModel={state.defaultModel}
          picked={state.modelPicked}
          onPick={(m) => {
            send({ type: "set_model", provider: m.provider, modelId: m.id });
            onModelPicked();
          }}
        />
        <button className="new-session" data-testid="new-session" onClick={() => send({ type: "new_session" })} title="New conversation for this scene" aria-label="New conversation">
          +
        </button>
      </div>
    </header>
  );
}
