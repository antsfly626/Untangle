import { test } from "node:test";
import assert from "node:assert/strict";
import { approved, extractTasks, readEvidence, safeUrl, toWorkspaceGraph } from "../src/lib/graphAdapter.ts";
import { consensus, evidence, session, withRisk } from "./fixtures.ts";

test("mind map contains only user topics, with stable positions and valid edges", () => {
  const s = withRisk();
  s.plan.nodes.push({ ...s.plan.nodes[0], id: "agent-node", label: "Agent suggestion", proposed_by: "architect" });
  const g = toWorkspaceGraph(s);
  assert.deepEqual(g, toWorkspaceGraph(s));
  assert.equal(g.nodes.length, 2);
  assert.equal(new Set(g.nodes.map(n => JSON.stringify(n.position))).size, g.nodes.length);
  assert(g.edges.every(e => g.nodes.some(n => n.id === e.source) && g.nodes.some(n => n.id === e.target)));
  assert(!JSON.stringify(g.nodes).includes("Agent suggestion"));
  assert(g.nodes.every(n => ["idea", "topic", "constraint", "group"].includes(n.data.kind)));
});
test("negotiation results never become mind-map nodes or details", () => {
  const s = withRisk();
  s.plan.negotiation.push({ round: 1, phase: "critique", agent: "architect", message: "Reject this plan", target_node_ids: [], vote: "reject" });
  const before = toWorkspaceGraph(session()), after = toWorkspaceGraph(s);
  assert.deepEqual(after.nodes, before.nodes);
  assert.deepEqual(after.edges, before.edges);
  assert.equal(after.papers.length, 1);
});
test("user hierarchy survives rejection and does not duplicate the idea root", () => {
  const s = session();
  s.plan.nodes.unshift({ ...s.plan.nodes[0], id: "root", label: "Workshop", type: "idea", parent: null });
  s.plan.nodes[1].parent = "root";
  s.plan.nodes[1].status = "rejected";
  s.plan.nodes.push({ ...s.plan.nodes[1], id: "child", label: "Registration", parent: "uploads" });
  const g = toWorkspaceGraph(s);
  assert.equal(g.nodes.length, 3);
  assert.equal(g.nodes.filter(n => n.data.kind === "idea").length, 1);
  assert(g.edges.some(e => e.source === JSON.stringify(["node", "uploads"]) && e.target === JSON.stringify(["node", "child"])));
});
test("related topic subtrees are kept together in the mind map layout", () => {
  const s = session();
  s.plan.nodes = [
    { ...s.plan.nodes[0], id: "root", label: "Project", type: "idea", parent: null },
    { ...s.plan.nodes[0], id: "collaboration", label: "Team collaboration", parent: "root" },
    { ...s.plan.nodes[0], id: "comments", label: "Comments", parent: "collaboration" },
    { ...s.plan.nodes[0], id: "mentions", label: "Mentions", parent: "collaboration" },
    { ...s.plan.nodes[0], id: "privacy", label: "Private workspaces", parent: "root" },
  ];
  const graph = toWorkspaceGraph(s);
  const position = (id: string) => graph.nodes.find(node => node.id === JSON.stringify(["node", id]))!.position;
  assert(graph.edges.some(edge => edge.source === JSON.stringify(["node", "collaboration"]) && edge.target === JSON.stringify(["node", "comments"])));
  assert.equal(position("collaboration").y, (position("comments").y + position("mentions").y) / 2);
  assert(position("comments").y < position("privacy").y);
  assert(position("mentions").y < position("privacy").y);
});
test("constraint topics and explicitly stated limits are grouped under Constraints", () => {
  const s = session();
  s.plan.constraints.assumed = ["timeline_weeks"];
  s.plan.nodes = [
    { ...s.plan.nodes[0], id: "root", label: "Project", type: "idea", parent: null },
    { ...s.plan.nodes[0], id: "feature", label: "Shared workspace", parent: "root" },
    { ...s.plan.nodes[0], id: "privacy", label: "Private by default", type: "constraint", parent: "feature" },
    { ...s.plan.nodes[0], id: "budget-limit", label: "No paid APIs", type: "constraint", parent: "root" },
  ];
  const graph = toWorkspaceGraph(s);
  const group = graph.nodes.find(node => node.data.label === "Constraints")!;
  assert.equal(group.data.kind, "group");
  const groupChildren = graph.edges.filter(edge => edge.source === group.id).map(edge => graph.nodes.find(node => node.id === edge.target)!.data.label);
  assert(graph.edges.some(edge => edge.source === JSON.stringify(["node", "root"]) && edge.target === group.id));
  assert(groupChildren.includes("Private by default"));
  assert(groupChildren.includes("No paid APIs"));
  assert(groupChildren.includes("Team size: 2 people"));
  assert(groupChildren.includes("Availability: 20 team hours/week"));
  assert(groupChildren.includes("Budget: $50"));
  assert(!groupChildren.some(label => label.startsWith("Timeline:")));
  assert(graph.nodes.find(node => node.data.label === "Constraints")!.position.y >= 0);
});
test("invalid or cyclic topic parents fall back to the idea root", () => {
  const s = session();
  s.plan.nodes[0].parent = "child";
  s.plan.nodes.push({ ...s.plan.nodes[0], id: "child", parent: "uploads" });
  const g = toWorkspaceGraph(s);
  assert(g.edges.every(e => e.source === g.nodes[0].id));
});
test("accepted tasks require idle protocol consensus and agreed features", () => {
  assert.equal(extractTasks(withRisk()).length, 0);
  const s = consensus(); assert.equal(extractTasks(s).length, 1);
  s.status = "negotiating"; assert.equal(extractTasks(s).length, 0);
  s.status = "idle"; s.plan.nodes[0].status = "rejected"; assert.equal(extractTasks(s).length, 0);
  s.plan.nodes[0].status = "agreed"; s.plan.outcome.hard_constraint_violations.push("budget"); assert(!approved(s));
});
test("consensus does not decorate user ideas with voting statuses", () => {
  assert.deepEqual(toWorkspaceGraph(consensus()).nodes, toWorkspaceGraph(session()).nodes);
});
test("plans without explicit tasks get labeled work items only for accepted features", () => {
  const s = consensus(); s.plan.tasks = [];
  assert.equal(extractTasks(s)[0].title, "Implement Document uploads");
  assert.match(extractTasks(s)[0].source, /Derived from accepted feature/);
  s.plan.outcome.status = "unresolved";
  assert.deepEqual(extractTasks(s), []);
});
test("missing and hallucinated paper metadata is never displayed", () => {
  const s = withRisk(), e = evidence(); e.papers = []; s.plan.negotiation[0].evidence = e;
  const g = toWorkspaceGraph(s); assert.equal(g.papers.length, 0); assert.match(g.warnings[0], /Invalid/);
  assert.equal(readEvidence(evidence(), "wrong-session"), undefined);
  assert.equal(readEvidence({}, s.id), undefined);
});
test("research outages and no-research decisions remain explicit", () => {
  const s = withRisk(), e = evidence(); e.papers = []; e.review.research_paper_ids = []; e.review.evidence_assessments = []; e.warnings = ["Research API timed out"];
  s.plan.negotiation[0].evidence = e;
  const g = toWorkspaceGraph(s); assert.equal(g.papers.length, 0); assert(g.warnings[0].includes("timed out"));
  e.research_plan.research_needed = false; e.warnings = [];
  assert.equal(toWorkspaceGraph(s).researchNeeded, false);
});
test("paper assessments and task dependencies stay outside the mind map", () => {
  const s = consensus(); s.plan.tasks.push({ id: "followup", title: "Test uploads", node_id: "uploads", estimate_hours: 1, depends_on: ["task-upload"] });
  const g = toWorkspaceGraph(s);
  assert.deepEqual(g.tasks[1].dependencies, ["task-upload"]);
  assert.equal(g.nodes.length, 2);
  assert.equal(g.papers[0].assessments[0].stance, "mixed");
  assert.equal(g.papers[0].paper.url, "https://example.org/test-paper");
});
test("malformed URLs and empty snapshots cannot introduce fake research", () => {
  assert.equal(safeUrl("javascript:alert(1)"), undefined);
  assert.equal(toWorkspaceGraph(session()).papers.length, 0);
  const e = evidence(); e.papers[0].url = "javascript:alert(1)";
  assert.equal(readEvidence(e, "test-session"), undefined);
});
