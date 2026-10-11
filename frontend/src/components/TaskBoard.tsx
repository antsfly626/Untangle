"use client";
import { useState } from "react";
import type { WorkspaceTask } from "@/lib/graphAdapter";
const columns = ["Backlog", "To Do", "In Progress", "Done"] as const;
type TaskStatus = typeof columns[number];
export default function TaskBoard({ tasks, topicIds, onSelect, initialStatuses = {}, illustrative = false }: { tasks: WorkspaceTask[]; topicIds: string[]; onSelect: (id: string) => void; initialStatuses?: Record<string, TaskStatus>; illustrative?: boolean }) {
  const [statuses, setStatuses] = useState<Record<string, TaskStatus>>(initialStatuses);
  return <section className="task-section" aria-label="Task board"><div className="section-heading"><h2>Implementation board</h2><span className="muted">Execution status is local to this session view</span></div>
    {illustrative && <p className="example-banner" role="note">Illustrative example tasks only — these have not been approved or returned by the backend.</p>}
    {!tasks.length ? <p className="empty-state">No approved tasks yet. Tasks appear after protocol consensus for agreed features. The backend may also return an accepted plan with no tasks.</p> : <div className="task-columns">{columns.map(column => <div className="task-column" key={column}><h3>{column}</h3>{tasks.filter(t => (statuses[t.id] ?? "Backlog") === column).map(t => <article className="task" key={t.id}>
      <h4>{t.title}</h4><p>{t.description}</p><p className="muted">{t.estimateHours == null ? "Effort unspecified" : `${t.estimateHours}h`} · Priority: {t.priority}</p>
      <p className="muted">{t.source}</p>
      <p className="provenance">Task {t.id}</p>
      {!!t.dependencies.length && <p>Depends on: {t.dependencies.join(", ")}</p>}
      {topicIds.includes(t.nodeId) && <button className="text-button" onClick={() => onSelect(t.nodeId)}>Related idea</button>}
      <label>Task status<select aria-label={`Status for ${t.title}`} value={statuses[t.id] ?? "Backlog"} onChange={e => setStatuses(s => ({ ...s, [t.id]: e.target.value as TaskStatus }))}>{columns.map(c => <option key={c}>{c}</option>)}</select></label>
    </article>)}</div>)}</div>}
  </section>;
}
