"""Shared plan schema: the contract between the agents (backend) and the mind map (frontend).

The agents produce a `Plan`; the frontend renders it. Every negotiation round,
event, and agent selection is recorded so the map can explain itself.

Run `python schemas.py` to validate shared/sample_plan.json and regenerate
shared/plan.schema.json (JSON Schema the frontend can generate TS types from).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Core decision agents always present; specialists (e.g. "research_scout")
# are recruited from the registry, so agent ids are free strings.
CORE_AGENTS = ("architect", "delivery", "evidence")

NodeType = Literal["idea", "feature", "component", "constraint", "risk", "question"]
NodeStatus = Literal["proposed", "agreed", "disputed", "deferred", "rejected"]
Vote = Literal["accept", "reject", "abstain"]
# "decision" messages come from the protocol (agent="protocol"), never from an LLM.
Phase = Literal["propose", "critique", "counteroffer", "vote", "decision"]


class Constraints(BaseModel):
    team_size: int = Field(ge=1)
    timeline_weeks: float = Field(gt=0)
    # Total for the whole team (people can differ: 12h + 6h = 18).
    weekly_hours: float = Field(gt=0)
    budget_usd: Optional[float] = None
    # Must-haves are nodes with required=True, not text, so checks never depend on wording.
    avoid: list[str] = []
    # Limits the user didn't give, filled with defaults (e.g. ["budget_usd"]).
    assumed: list[str] = []


class Objection(BaseModel):
    agent: str
    reason: str
    severity: Literal["blocking", "concern"] = "concern"


class Evidence(BaseModel):
    title: str
    url: str
    year: Optional[int] = None
    source: Literal["semantic_scholar", "openalex"]
    stance: Literal["supports", "challenges", "inconclusive"]
    summary: str
    # True when only the abstract was read, never the full text.
    abstract_only: bool = True


class Node(BaseModel):
    id: str
    label: str
    type: NodeType
    status: NodeStatus = "proposed"
    parent: Optional[str] = None
    proposed_by: str
    rationale: str = ""
    supported_by: list[str] = []
    objections: list[Objection] = []
    evidence: list[Evidence] = []
    # Person-hours of work, and cash cost (paid APIs, hosting, licenses).
    estimate_hours: Optional[float] = None
    cost_usd: Optional[float] = None
    # First estimates, kept so negotiation can't shrink them below a floor.
    original_estimate_hours: Optional[float] = None
    original_cost_usd: Optional[float] = None
    # A user must-have: a plan that defers or rejects it fails the hard constraints.
    required: bool = False
    # Set when the user decided this node's status; agents can't change it.
    locked: bool = False


class Question(BaseModel):
    id: str
    asked_by: str
    question: str
    why: str
    priority: int = Field(ge=1, le=5, default=3)
    answer: Optional[str] = None


class Component(BaseModel):
    id: str
    name: str
    tech: str
    purpose: str
    status: NodeStatus = "proposed"
    depends_on: list[str] = []


class Task(BaseModel):
    id: str
    title: str
    node_id: str
    estimate_hours: float
    depends_on: list[str] = []


class SelectedAgent(BaseModel):
    """Why an agent is on the team: answers Level 2's 'explain selection'."""

    id: str
    name: str
    capabilities: list[str]
    reason: str
    score: float
    status: Literal["active", "unavailable", "replaced"] = "active"
    replaced_by: Optional[str] = None


class NegotiationMessage(BaseModel):
    round: int = Field(ge=1)
    phase: Phase
    agent: str
    message: str
    target_node_ids: list[str] = []
    vote: Optional[Vote] = None
    # Agent's own objective scores, 0-1 (e.g. {"feasibility": 0.4}).
    scores: dict[str, float] = {}
    counteroffer: Optional[str] = None
    # Complete Evidence response (papers, warnings, exact proposal reference).
    evidence: Optional[dict] = None


class Event(BaseModel):
    id: str
    kind: Literal[
        "deadline_change",
        "constraint_change",
        "provider_failure",
        "agent_unavailable",
        "user_input",
        "agent_recruited",
    ]
    description: str
    at_round: int
    reopened_node_ids: list[str] = []


class Outcome(BaseModel):
    status: Literal["in_progress", "consensus", "unresolved", "escalated"]
    summary: str = ""
    rounds_used: int = 0
    open_disputes: list[str] = []
    hard_constraint_violations: list[str] = []


class Plan(BaseModel):
    session_id: str
    idea: str
    constraints: Constraints
    nodes: list[Node] = []
    questions: list[Question] = []
    architecture: list[Component] = []
    tasks: list[Task] = []
    agents: list[SelectedAgent] = []
    negotiation: list[NegotiationMessage] = []
    events: list[Event] = []
    outcome: Outcome = Outcome(status="in_progress")


if __name__ == "__main__":
    shared = Path(__file__).resolve().parent.parent / "shared"
    plan = Plan.model_validate_json((shared / "sample_plan.json").read_text())
    (shared / "plan.schema.json").write_text(json.dumps(Plan.model_json_schema(), indent=2) + "\n")
    print(f"sample_plan.json valid: {len(plan.nodes)} nodes, {len(plan.negotiation)} messages")
    print("wrote shared/plan.schema.json")
