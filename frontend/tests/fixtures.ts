/** Explicit test-only snapshots. Never imported by production code. */
import type { EvidenceResponse, Session } from "../src/lib/contracts.ts";
export function evidence(): EvidenceResponse {
  return {
    session_id: "test-session", round_id: "1", risk_ids: ["retention-risk"],
    review: { proposal_id: "proposal-1", agent_id: "evidence", vote: "reject", score: 42,
      objections: [{ category: "privacy", severity: "high", description: "Uploads are retained indefinitely", affected_feature: "uploads" }],
      requested_changes: ["Delete uploads after processing"], reasoning: "A retention policy is needed.",
      research_paper_ids: ["mock-paper"], evidence_assessments: [{ paper_id: "mock-paper", assumption: "Practice supports learning", stance: "mixed", summary: "Test-only assessment with uncertainty.", abstract_only: true }] },
    papers: [{ paper_id: "mock-paper", title: "Mock retrieved paper — test fixture only", year: 2020, url: "https://example.org/test-paper", abstract: "Synthetic abstract used only in automated tests.", authors: ["Test Author"], source: "semantic_scholar" }],
    warnings: ["Only metadata and abstracts were reviewed."],
    research_plan: { research_needed: true, evidence_essential: false, assumptions: ["Practice supports learning"], queries: ["retrieval practice"], rationale: "Evaluate learning claims." },
  };
}
export function session(): Session {
  return {
    id: "test-session", status: "idle", summary: "Test session", error: null, transport: "local",
    plan: { session_id: "test-session", idea: "Study assistant test project", constraints: { team_size: 2, timeline_weeks: 1, weekly_hours: 20, budget_usd: 50 },
      nodes: [{ id: "uploads", label: "Document uploads", type: "feature", status: "proposed", parent: null, proposed_by: "user", rationale: "Private study documents", supported_by: [], objections: [], estimate_hours: 3 }],
      architecture: [{ id: "api", name: "API", tech: "FastAPI", purpose: "Process uploads", status: "proposed", depends_on: [] }],
      tasks: [{ id: "task-upload", title: "Implement temporary uploads", node_id: "uploads", estimate_hours: 3, depends_on: [] }],
      questions: [], negotiation: [], outcome: { status: "in_progress", summary: "Review pending", rounds_used: 0, open_disputes: [], hard_constraint_violations: [] } },
  };
}
export function withRisk(): Session {
  const s = session();
  s.plan.outcome.status = "unresolved";
  s.plan.negotiation.push({ round: 1, phase: "critique", agent: "evidence", message: "A retention policy is needed.", target_node_ids: ["uploads"], vote: "reject", evidence: evidence() });
  return s;
}
export function consensus(): Session {
  const s = withRisk();
  s.plan.outcome.status = "consensus";
  s.plan.outcome.summary = "Protocol reports all agents accepted the revised plan.";
  s.plan.nodes[0].status = "agreed";
  const e = evidence(); e.round_id = "2"; e.review.proposal_id = "proposal-2"; e.review.vote = "accept"; e.review.objections = []; e.risk_ids = [];
  s.plan.negotiation.push({ round: 2, phase: "critique", agent: "evidence", message: "Revised retention is acceptable.", target_node_ids: ["uploads"], vote: "accept", evidence: e });
  return s;
}
