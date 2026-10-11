"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "@/lib/contracts";
import { exampleSession } from "@/lib/exampleSession";
import { exampleTasks } from "@/lib/exampleTasks";
import { createSession, getSession, sendMessage, subscribeSession } from "@/lib/api";
import { toWorkspaceGraph } from "@/lib/graphAdapter";
import ConceptMap from "./ConceptMap";
import NodeDetails from "./NodeDetails";
import ResearchPanel from "./ResearchPanel";
import TaskBoard from "./TaskBoard";
import AgentDecisions from "./AgentDecisions";

export default function Workspace() {
  const [session, setSession] = useState<Session>(exampleSession);
  const [isExample, setIsExample] = useState(true);
  const [idea, setIdea] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("example");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const generation = useRef(0);
  const id = session?.id;
  useEffect(() => {
    if (!id || isExample) return;
    let active = true;
    const close = subscribeSession(id, next => { if (active) setSession(next); }, state => { if (active) setConnection(state); });
    return () => { active = false; close(); };
  }, [id, isExample]);
  const graph = useMemo(() => toWorkspaceGraph(session), [session]);
  async function run(action: () => Promise<Session>, newSession = false) {
    const current = ++generation.current; setBusy(true); setError("");
    try {
      const next = await action();
      if (current !== generation.current) return;
      setSession(next); setSessionId(next.id); setMessage(""); setIsExample(false);
      if (newSession) { setSelectedId(null); setConnection("connecting"); }
    } catch (err) { setError(err instanceof Error ? err.message : "Unable to reach backend."); }
    finally { if (current === generation.current) setBusy(false); }
  }
  return <main className="workspace">
    <header className="workspace-header"><div><span className="eyebrow">Collaborative project intelligence</span><h1>Untangle<span className="brand-dot">.</span></h1></div><span className="workspace-label">Evidence & planning workspace</span></header>
    <section className="intake panel"><div><h2>Give your idea a shape.</h2><p className="muted">Turn your idea into a mind map of topics, details and connections.</p></div>
      <form onSubmit={e => { e.preventDefault(); void run(() => createSession(idea), true); }}>
        <label htmlFor="idea">Project idea and constraints</label><textarea id="idea" value={idea} onChange={e => setIdea(e.target.value)} placeholder="What would you like to plan? Include team size, deadline, weekly hours and budget." required />
        <button className="primary" disabled={busy || !idea.trim()}>Start project</button>
      </form>
      <form className="load-session" onSubmit={e => { e.preventDefault(); void run(() => getSession(sessionId.trim()), true); }}><label htmlFor="session-id">Existing session ID</label><div className="inline"><input id="session-id" value={sessionId} onChange={e => setSessionId(e.target.value)} /><button disabled={busy || !sessionId.trim()}>Load session</button></div></form>
    </section>
    {busy && <p role="status">Contacting the backend…</p>}{error && <p role="alert" className="error">{error}</p>}
    {session && graph ? <>
      {isExample && <p className="example-banner" role="note"><strong>Example session · illustrative data.</strong> This sample is shown locally and is not connected to the backend. Start a project or load a session to work with live data.</p>}
      <section className="session-strip"><div><span className="eyebrow">{isExample ? "Example session · illustrative data" : `${session.transport || "Transport not reported"} · ${connection}`}</span><h2>{session.plan.idea}</h2>{!isExample && <p className="provenance">Session: {session.id}</p>}</div><div><span className="muted">{session.status === "negotiating" ? "Updating your plan…" : session.status === "needs_answers" ? "More details needed" : "Ready"}</span>{!isExample && <button disabled={busy} onClick={() => void run(() => getSession(session.id))}>Refresh</button>}</div></section>
      <p className="muted">{session.plan.constraints.team_size} people · {session.plan.constraints.timeline_weeks} weeks · {session.plan.constraints.weekly_hours} team hours/week · Budget {session.plan.constraints.budget_usd == null ? "unspecified" : `$${session.plan.constraints.budget_usd}`}</p>
      {session.error && <p role="alert" className="error">The session reports a negotiation failure. Check the backend terminal, then retry with a message.</p>}
      {!isExample && connection !== "connected" && <p className="notice">{connection === "invalid" ? "An incompatible live update was ignored." : "Live updates are connecting or reconnecting."} Use Refresh to retrieve the latest snapshot.</p>}
      <section className="map-section"><div className="section-heading"><h2>Your idea mind map</h2><span className="muted">Drag to arrange · select to inspect · scroll to zoom</span></div>

        <div className="workspace-grid"><ConceptMap key={session.id} graph={graph} selectedId={selectedId} onSelect={setSelectedId} /><aside className="inspector"><NodeDetails graph={graph} selectedId={selectedId} onSelect={setSelectedId} /></aside></div>
        <details className="node-directory"><summary>Browse all {graph.nodes.length} nodes</summary><div className="connection-list">{graph.nodes.map(n => <button key={n.id} onClick={() => setSelectedId(n.id)}>{n.data.kind}: {n.data.label}</button>)}</div></details>
      </section>
      <AgentDecisions key={`decisions-${session.id}`} session={session} />
      <div className="lower-grid"><ResearchPanel graph={graph} loading={session.status === "negotiating"} /><section className="panel"><h2>Project updates</h2><p className="muted">{isExample ? "These sample questions are illustrative and are not sent to the backend." : "Answers and constraint changes go to the existing session orchestrator."}</p>
        {session.plan.questions.filter(q => q.answer === null).map(q => <div className="question" key={q.id}><h3>{q.question}</h3><p className="muted">{q.why}</p></div>)}
        {isExample ? <><div className="notice"><strong>Example follow-up</strong><p>Connect the workspace to our issue tracker and keep the decision history private to invited teammates.</p></div><p className="empty-state">Start a project or load a session above to send updates.</p></> : <form onSubmit={e => { e.preventDefault(); void run(() => sendMessage(session.id, message)); }}><label htmlFor="reply">Answer or revise constraints</label><textarea id="reply" value={message} onChange={e => setMessage(e.target.value)} /><button className="primary" disabled={busy || session.status === "negotiating" || !message.trim()}>Send update</button></form>}
      </section></div>
      <TaskBoard key={`tasks-${session.id}`} tasks={isExample ? exampleTasks : graph.tasks} topicIds={graph.nodes.map(n => n.id)} onSelect={setSelectedId} illustrative={isExample} initialStatuses={isExample ? { "example-task-context-schema": "In Progress", "example-task-workspace-access": "To Do" } : undefined} />
    </> : <section className="welcome"><span className="eyebrow">From possibility to a plan</span><h2>A shared view of what to build,<br />why it matters, and what remains uncertain.</h2><p>No project loaded. Start an idea or connect an existing session.</p></section>}
  </main>;
}
