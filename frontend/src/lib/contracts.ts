/** Consumed fields from backend/schemas.py and evidence/models.py. */
export type Vote = "accept" | "reject" | "abstain";
export type DomainStatus = "proposed" | "agreed" | "disputed" | "deferred" | "rejected";
export interface PlanNode {
  id: string; label: string; type: "idea" | "feature" | "component" | "constraint" | "risk" | "question";
  status: DomainStatus; parent: string | null; proposed_by: string; rationale: string;
  supported_by: string[]; objections: { agent: string; reason: string; severity: string }[];
  estimate_hours: number | null;
}
export interface Paper {
  paper_id: string; title: string; year: number | null; url: string; abstract: string | null;
  doi?: string | null; authors: string[]; source: "semantic_scholar";
}
export interface Assessment {
  paper_id: string; assumption: string; stance: "supports" | "challenges" | "mixed" | "inconclusive";
  summary: string; abstract_only: true;
}
export interface EvidenceResponse {
  session_id: string; round_id: string; risk_ids: string[];
  review: {
    proposal_id: string; agent_id: "evidence"; vote: Vote; score: number;
    objections: { category: string; severity: string; description: string; affected_feature: string | null }[];
    requested_changes: string[]; reasoning: string; research_paper_ids: string[];
    evidence_assessments: Assessment[];
  };
  papers: Paper[]; warnings: string[];
  research_plan: { research_needed: boolean; evidence_essential: boolean; assumptions: string[]; queries: string[]; rationale: string };
}
export interface LedgerMessage {
  round: number; phase: "propose" | "critique" | "counteroffer" | "vote" | "decision";
  agent: string; message: string; target_node_ids: string[]; vote: Vote | null;
  counteroffer?: string | null;
  evidence?: unknown;
}
export interface Plan {
  session_id: string; idea: string;
  constraints: { team_size: number; timeline_weeks: number; weekly_hours: number; budget_usd: number | null; assumed?: string[] };
  nodes: PlanNode[];
  architecture: { id: string; name: string; tech: string; purpose: string; status: DomainStatus; depends_on: string[] }[];
  tasks: { id: string; title: string; node_id: string; estimate_hours: number; depends_on: string[] }[];
  questions: { id: string; question: string; why: string; answer: string | null }[];
  negotiation: LedgerMessage[];
  outcome: { status: "in_progress" | "consensus" | "unresolved" | "escalated"; summary: string; rounds_used: number; open_disputes: string[]; hard_constraint_violations: string[] };
}
export interface Session {
  id: string; status: "needs_answers" | "negotiating" | "idle";
  summary: string; error: string | null; transport?: string; plan: Plan;
}
