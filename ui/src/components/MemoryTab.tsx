import { useEffect, useState } from "react";
import type { ClientMessage } from "@houdini-pi/protocol";
import type { PaneState } from "../state.js";

interface Props {
  memory: PaneState["memory"];
  projectMemory: PaneState["projectMemory"];
  compaction: PaneState["compaction"];
  send: (message: ClientMessage) => void;
}

export function MemoryTab({ memory, projectMemory, compaction, send }: Props) {
  const [draft, setDraft] = useState(memory?.text ?? "");
  const [projectDraft, setProjectDraft] = useState(projectMemory?.text ?? "");
  useEffect(() => {
    setDraft(memory?.text ?? "");
  }, [memory?.text]);
  useEffect(() => {
    setProjectDraft(projectMemory?.text ?? "");
  }, [projectMemory?.text]);
  const dirty = draft !== (memory?.text ?? "");
  const projectDirty = projectDraft !== (projectMemory?.text ?? "");
  return (
    <section className="memory" data-testid="memory">
      <p className="muted">Scene memory lives inside the scene file and is shown to the agent every turn. Decisions and conventions for this scene belong here, narration does not.</p>
      {compaction && (
        <p className="notice info" data-testid="compaction-notice">
          Hip memory was compacted from ~{compaction.before} to ~{compaction.after} tokens.
        </p>
      )}
      <textarea data-testid="memory-text" value={draft} onChange={(e) => setDraft(e.target.value)} rows={10} />
      <div className="memory-actions">
        <button data-testid="memory-save" disabled={!dirty} onClick={() => send({ type: "memory_edit", text: draft })}>
          save to scene
        </button>
        <button data-testid="memory-undo" disabled={!memory?.prevAvailable} onClick={() => send({ type: "undo_compaction" })}>
          undo compaction
        </button>
      </div>

      <hr className="memory-divider" />

      {projectMemory === undefined ? (
        <p className="muted">Loading project memory…</p>
      ) : !projectMemory.available ? (
        <p className="muted">Project memory is unavailable: $JOB is not set. Set $JOB to your project root to share memory across all hips.</p>
      ) : (
        <>
          <p className="muted">Project memory lives at <code>$JOB/.tech-art-agent/memory.md</code> and is shown to the agent in every scene of this project. Team conventions and pipeline rules belong here, per-scene decisions do not.</p>
          <textarea data-testid="project-memory-text" value={projectDraft} onChange={(e) => setProjectDraft(e.target.value)} rows={10} />
          <div className="memory-actions">
            <button data-testid="project-memory-save" disabled={!projectDirty} onClick={() => send({ type: "project_memory_edit", text: projectDraft })}>
              save to $JOB
            </button>
          </div>
        </>
      )}
    </section>
  );
}
