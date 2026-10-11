import type { Session } from "@/lib/contracts";
import { readEvidence, safeUrl } from "@/lib/graphAdapter";

export default function AgentDecisions({ session }: { session: Session }) {
  const { plan } = session;
  const labelFor = (id: string) => plan.nodes.find(node => node.id === id)?.label || id;
  return (
    <details className="panel agent-decisions">
      <summary>Agent decisions + details <span className="count">{plan.negotiation.length}</span></summary>
      <div className="decision-content" role="region" aria-label="Agent decisions and details">
        <h3>Plan: {plan.outcome.status.replaceAll("_", " ")}</h3>
        {session.status === "negotiating" && <p role="status">Evaluation in progress. The recorded outcome may belong to the previous evaluation.</p>}
        <p>{plan.outcome.summary || "No overall decision recorded yet."}</p>
        <p className="muted">Individual votes are recorded below; overall agreement comes from the protocol.</p>
        {session.error && <p className="notice">The latest evaluation failed. Earlier decisions remain visible.</p>}
        {plan.outcome.hard_constraint_violations.length > 0 && <>
          <h3>Constraint issues</h3>
          <ul>{plan.outcome.hard_constraint_violations.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
        </>}
        {plan.outcome.open_disputes.length > 0 && <p>Open questions: {plan.outcome.open_disputes.map(labelFor).join(", ")}</p>}
        {!plan.negotiation.length && <p className="empty-state">No agent decisions yet. They will appear here as the plan is evaluated.</p>}
        {plan.negotiation.map((entry, index) => {
          const parsed = readEvidence(entry.evidence, session.id);
          const evidence = entry.agent === "evidence" && parsed?.round_id === String(entry.round) && parsed.review.vote === entry.vote ? parsed : undefined;
          return (
            <details className="decision-entry" key={`${entry.round}-${index}`}>
              <summary>Round {entry.round} · {entry.agent} · {entry.phase}{entry.vote ? ` · ${entry.vote}` : ""}</summary>
              <p className="preserve-lines">{entry.message}</p>
              {!!entry.target_node_ids.length && <p className="muted">Related topics: {entry.target_node_ids.map(labelFor).join(", ")}</p>}
              {entry.counteroffer && <><h3>Proposed alternative</h3><p>{entry.counteroffer}</p></>}
              {entry.evidence != null && !evidence && <p className="notice">Evidence details could not be validated. Unverified sources are not shown.</p>}
              {evidence && <>
                <p className="provenance">Proposal: {evidence.review.proposal_id}</p>
                <p>{evidence.review.reasoning}</p>
                {evidence.review.objections.map((risk, i) => <div className="notice" key={i}>
                  <strong>{risk.category} · {risk.severity}</strong>
                  <p>{risk.description}</p>
                  {risk.affected_feature && <p>Affected topic: {labelFor(risk.affected_feature)}</p>}
                  <small>Recorded objection; no explicit resolution is supplied.</small>
                </div>)}
                {!!evidence.review.requested_changes.length && <>
                  <h3>Requested changes</h3>
                  <ul>{evidence.review.requested_changes.map((change, i) => <li key={i}>{change}</li>)}</ul>
                </>}
                {!!evidence.papers.length && <><h3>Retrieved sources</h3><ul>{evidence.papers.map(paper => <li key={paper.paper_id}><a href={safeUrl(paper.url)} target="_blank" rel="noreferrer">{paper.title}</a></li>)}</ul></>}
                {evidence.warnings.map((warning, i) => <p className="muted" key={i}>{warning}</p>)}
              </>}
            </details>
          );
        })}
      </div>
    </details>
  );
}
