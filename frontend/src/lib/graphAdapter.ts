import type { Assessment, EvidenceResponse, Paper, Session } from "./contracts.ts";

export type NodeKind = "idea" | "topic" | "constraint" | "group";
export interface GraphData extends Record<string, unknown> {
  label: string; kind: NodeKind; description: string; source: string;
}
export interface GraphNode { id: string; position: { x: number; y: number }; data: GraphData; }
export interface GraphEdge { id: string; source: string; target: string; label: string; }
export interface PaperEntry { paper: Paper; assessments: Assessment[]; proposalId: string; round: string; id: string; }
export interface WorkspaceTask { id: string; title: string; description: string; nodeId: string; dependencies: string[]; estimateHours: number | null; priority: string; source: string; }
export interface WorkspaceGraph {
  nodes: GraphNode[]; edges: GraphEdge[]; papers: PaperEntry[]; warnings: string[];
  tasks: WorkspaceTask[]; researchReviewed: boolean; researchNeeded: boolean;
}
const key = (...parts: (string | number)[]) => JSON.stringify(parts);
export function safeUrl(value: string): string | undefined {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
}
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.every(v => typeof v === "string"); }
/** Evidence is an opaque dict in the shared ledger. Fail closed on malformed provenance. */
export function readEvidence(value: unknown, sessionId: string): EvidenceResponse | undefined {
  if (!record(value) || value.session_id !== sessionId || typeof value.round_id !== "string" || !record(value.review) || !record(value.research_plan)) return;
  const r = value.review, p = value.research_plan;
  if (r.agent_id !== "evidence" || typeof r.proposal_id !== "string" || !r.proposal_id.trim() || !["accept", "reject", "abstain"].includes(String(r.vote)) || typeof r.reasoning !== "string" || !strings(r.requested_changes) || !strings(r.research_paper_ids) || !Array.isArray(r.objections) || !Array.isArray(r.evidence_assessments) || !Array.isArray(value.papers) || !strings(value.warnings) || !strings(value.risk_ids) || typeof p.research_needed !== "boolean") return;
  if (!r.objections.every(o => record(o) && typeof o.description === "string" && typeof o.severity === "string" && typeof o.category === "string" && (o.affected_feature === null || typeof o.affected_feature === "string"))) return;
  if (!value.papers.every(o => record(o) && typeof o.paper_id === "string" && !!o.paper_id.trim() && typeof o.title === "string" && typeof o.url === "string" && !!safeUrl(o.url) && strings(o.authors) && o.source === "semantic_scholar" && (o.abstract === null || typeof o.abstract === "string") && (o.year === null || typeof o.year === "number"))) return;
  const ids = new Set(value.papers.map(o => (o as Record<string, unknown>).paper_id));
  if (ids.size !== value.papers.length || !r.research_paper_ids.every(id => ids.has(id))) return;
  if (!r.evidence_assessments.every(a => record(a) && typeof a.paper_id === "string" && (r.research_paper_ids as string[]).includes(a.paper_id) && typeof a.assumption === "string" && typeof a.summary === "string" && ["supports", "challenges", "mixed", "inconclusive"].includes(String(a.stance)) && a.abstract_only === true)) return;
  return value as unknown as EvidenceResponse;
}
export function approved(session: Session): boolean {
  return session.status === "idle" && !session.error && session.plan.outcome.status === "consensus" && session.plan.outcome.hard_constraint_violations.length === 0;
}
export function extractTasks(session: Session): WorkspaceTask[] {
  if (!approved(session)) return [];
  const nodes = new Map(session.plan.nodes.map(n => [n.id, n]));
  // The current orchestrator does not generate tasks. Use transparent, deterministic
  // feature-level work items only when it supplies no explicit task breakdown.
  if (!session.plan.tasks.length) return session.plan.nodes.filter(n => n.type === "feature" && n.status === "agreed").map(n => ({
    id: key("implement", n.id), title: `Implement ${n.label}`, description: n.rationale || "No description provided.",
    nodeId: key("node", n.id), dependencies: [], estimateHours: n.estimate_hours,
    priority: "Unspecified", source: "Derived from accepted feature; no task breakdown supplied",
  }));
  return session.plan.tasks.filter(t => nodes.get(t.node_id)?.status === "agreed").map(t => ({
    id: t.id, title: t.title, description: nodes.get(t.node_id)?.rationale || "No description provided.",
    nodeId: key("node", t.node_id), dependencies: t.depends_on, estimateHours: t.estimate_hours, priority: "Unspecified", source: "Plan task",
  }));
}
/** User-authored idea hierarchy only; negotiation remains outside the canvas. */
export function toWorkspaceGraph(session: Session): WorkspaceGraph {
  const plan = session.plan;
  const topics = plan.nodes.filter(n => n.proposed_by === "user");
  const idea = topics.find(n => n.id === "root" && n.type === "idea")
    ?? topics.find(n => n.type === "idea" && !n.parent);
  const root = idea ? key("node", idea.id) : key("project", plan.session_id);
  const nodes: GraphNode[] = [{ id: root, position: { x: 0, y: 0 }, data: {
    label: idea?.label || plan.idea, kind: "idea", description: plan.idea, source: "Your idea",
  } }];
  const edges: GraphEdge[] = [];
  const children = topics.filter(n => n !== idea);
  const byId = new Map(children.map(n => [n.id, n]));
  const constraintNodes = children.filter(n => n.type === "constraint");
  const assumed = new Set(plan.constraints.assumed ?? []);
  const scalarConstraints = [
    ...(Array.isArray(plan.constraints.assumed) && !assumed.has("team_size") ? [{ id: "team_size", label: `Team size: ${plan.constraints.team_size} ${plan.constraints.team_size === 1 ? "person" : "people"}`, description: "Team capacity for the project." }] : []),
    ...(Array.isArray(plan.constraints.assumed) && !assumed.has("timeline_weeks") ? [{ id: "timeline_weeks", label: `Timeline: ${plan.constraints.timeline_weeks} weeks`, description: "Target project timeline." }] : []),
    ...(Array.isArray(plan.constraints.assumed) && !assumed.has("weekly_hours") ? [{ id: "weekly_hours", label: `Availability: ${plan.constraints.weekly_hours} team hours/week`, description: "Total time the team can spend each week." }] : []),
    ...(Array.isArray(plan.constraints.assumed) && !assumed.has("budget_usd") && plan.constraints.budget_usd !== null ? [{ id: "budget_usd", label: `Budget: $${plan.constraints.budget_usd}`, description: "Available project budget." }] : []),
  ];
  const constraintsGroup = constraintNodes.length > 0 || scalarConstraints.length > 0
    ? key("group", "constraints")
    : undefined;
  if (constraintsGroup) {
    nodes.push({ id: constraintsGroup, position: { x: 0, y: 0 }, data: {
      label: "Constraints", kind: "group",
      description: "Project limits and requirements collected from the user's input.",
      source: "Grouped project constraints",
    } });
    edges.push({ id: key(root, constraintsGroup), source: root, target: constraintsGroup, label: "" });
    for (const constraint of scalarConstraints) {
      const id = key("constraint", constraint.id);
      const node: GraphNode = { id, position: { x: 0, y: 0 }, data: {
        label: constraint.label, kind: "constraint", description: constraint.description,
        source: "Project constraints",
      } };
      nodes.push(node);
      edges.push({ id: key(constraintsGroup, id), source: constraintsGroup, target: id, label: "" });
    }
  }
  // Broken, self-referencing or cyclic parent chains attach to the main idea.
  function parentOf(id: string): string | undefined {
    const parent = byId.get(id)?.parent;
    if (!parent || !byId.has(parent)) return;
    const seen = new Set([id]);
    let cursor: string | null | undefined = parent;
    while (cursor && byId.has(cursor)) {
      if (seen.has(cursor)) return;
      seen.add(cursor); cursor = byId.get(cursor)?.parent;
    }
    return parent;
  }
  const childrenByParent = new Map<string, string[]>();
  childrenByParent.set(root, constraintsGroup ? [constraintsGroup] : []);
  if (constraintsGroup) childrenByParent.set(constraintsGroup, scalarConstraints.map(c => key("constraint", c.id)));
  const nodeById = new Map(nodes.filter(n => n.id !== root).map(n => [n.id, n]));
  for (const n of children) {
    const id = key("node", n.id), parent = parentOf(n.id);
    const parentId = n.type === "constraint" && constraintsGroup
      ? constraintsGroup
      : parent ? key("node", parent) : root;
    const siblings = childrenByParent.get(parentId) ?? [];
    siblings.push(id);
    childrenByParent.set(parentId, siblings);
    childrenByParent.set(id, []);
    nodeById.set(id, { id, position: { x: 0, y: 0 }, data: {
      label: n.label, kind: n.type === "constraint" ? "constraint" : "topic",
      description: n.rationale, source: "Your input",
    } });
    edges.push({ id: key(parentId, id), source: parentId, target: id, label: "" });
  }
  if (constraintsGroup) {
    const grouped = childrenByParent.get(constraintsGroup) ?? [];
    for (const node of constraintNodes) grouped.push(key("node", node.id));
    childrenByParent.set(constraintsGroup, grouped);
  }
  let row = 0;
  function placeSubtree(id: string, depth: number): number {
    const childIds = childrenByParent.get(id) ?? [];
    let y: number;
    if (!childIds.length) {
      y = row++ * 170;
    } else {
      const childCenters = childIds.map(childId => placeSubtree(childId, depth + 1));
      y = (childCenters[0] + childCenters[childCenters.length - 1]) / 2;
    }
    if (id === root) {
      nodes[0].position = { x: 0, y };
    } else {
      const node = nodeById.get(id);
      if (node) node.position = { x: depth * 330, y };
    }
    return y;
  }
  placeSubtree(root, 0);
  for (const n of children) {
    const node = nodeById.get(key("node", n.id));
    if (node) nodes.push(node);
  }
  const papers: PaperEntry[] = [], warnings: string[] = [];
  let researchReviewed = false, researchNeeded = false;
  const seen = new Set<string>();
  for (const m of plan.negotiation) {
    if (m.evidence == null) continue;
    const evidence = readEvidence(m.evidence, session.id);
    if (!evidence || m.agent !== "evidence" || evidence.round_id !== String(m.round) || evidence.review.vote !== m.vote) {
      warnings.push(`Invalid or incomplete Evidence metadata in round ${m.round}; sources were not displayed.`);
      continue;
    }
    const review = evidence.review, scope = key(evidence.round_id, review.proposal_id);
    if (seen.has(scope)) continue;
    seen.add(scope);
    researchReviewed = true; researchNeeded ||= evidence.research_plan.research_needed;
    warnings.push(...evidence.warnings.map(w => `${review.proposal_id}: ${w}`));
    for (const paper of evidence.papers) papers.push({
      paper, assessments: review.evidence_assessments.filter(a => a.paper_id === paper.paper_id),
      proposalId: review.proposal_id, round: evidence.round_id, id: key("paper", scope, paper.paper_id),
    });
  }
  return { nodes, edges, papers, warnings: [...new Set(warnings)], tasks: extractTasks(session), researchReviewed, researchNeeded };
}
