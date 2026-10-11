import type { WorkspaceGraph } from "@/lib/graphAdapter";

export default function NodeDetails({ graph, selectedId, onSelect }: { graph: WorkspaceGraph; selectedId: string | null; onSelect: (id: string) => void }) {
  const node = graph.nodes.find(n => n.id === selectedId);
  if (!node) return <section className="panel"><h2>Explore your idea</h2><p className="muted">Select a topic to see its description and connected ideas.</p></section>;
  const connections = graph.edges.filter(e => e.source === node.id || e.target === node.id);
  return <section className="panel details" aria-label="Node details">
    <span className="eyebrow">{node.data.kind}</span><h2>{node.data.label}</h2>
    <p className="preserve-lines">{node.data.description || "No additional description provided."}</p>
    <h3>Connected ideas</h3>
    {!connections.length && <p className="muted">No connected topics yet.</p>}
    <div className="connection-list">{connections.map(e => {
      const other = graph.nodes.find(n => n.id === (e.source === node.id ? e.target : e.source));
      return other && <button key={e.id} onClick={() => onSelect(other.id)}>{other.data.label}</button>;
    })}</div>
  </section>;
}
