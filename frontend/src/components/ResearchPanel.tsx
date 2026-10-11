import { safeUrl, type WorkspaceGraph } from "@/lib/graphAdapter";

export default function ResearchPanel({ graph, loading }: { graph: WorkspaceGraph; loading: boolean }) {
  return <section className="panel" aria-label="Research evidence">
    <div className="section-heading"><h2>Research evidence</h2><span className="count">{graph.papers.length}</span></div>
    <p className="muted">Retrieved sources, with uncertainty intact.</p>
    {loading && <p role="status">Agents are evaluating the plan. Research appears when a review arrives.</p>}
    {graph.warnings.map(w => <p className="notice" key={w}>{w}</p>)}
    {!graph.papers.length && <p className="empty-state">{graph.warnings.length ? "No verified papers available. See research limitations above." : !graph.researchReviewed ? "No Evidence review received yet." : !graph.researchNeeded ? "Academic research was not needed for the reviewed proposals." : "No papers were retrieved for the research queries."}</p>}
    {graph.papers.map(entry => <article className="paper" key={entry.id}>
      <h3><a href={safeUrl(entry.paper.url)} target="_blank" rel="noreferrer">{entry.paper.title} ↗</a></h3>
      <p className="muted">{entry.paper.authors.join(", ") || "Authors unavailable"} · {entry.paper.year ?? "Year unavailable"}</p>
      <p className="eyebrow">Semantic Scholar · metadata / abstract only</p>
      {entry.paper.doi && <p>DOI: {entry.paper.doi}</p>}
      <details><summary>Available abstract</summary><p>{entry.paper.abstract || "No abstract available; only metadata was retrieved."}</p></details>
      {!entry.assessments.length && <p className="muted">Retrieved but not assessed for a project claim.</p>}
      {entry.assessments.map((a, i) => <div className="assessment" key={i}>
        <span className={`badge stance-${a.stance}`}>{({ supports: "Supporting", challenges: "Challenging", mixed: "Mixed", inconclusive: "Inconclusive" })[a.stance]}</span>
        <p><strong>Claim:</strong> {a.assumption}</p><p>{a.summary}</p>
      </div>)}
      <p className="muted">Full text was not reviewed. Abstracts do not establish a definitive conclusion.</p>
      <p className="provenance">{entry.proposalId} · round {entry.round}</p>
    </article>)}
  </section>;
}
