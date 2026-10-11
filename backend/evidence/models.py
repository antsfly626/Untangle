"""Evidence-specific contracts; shared negotiation contracts remain unchanged."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator

if __package__.startswith("backend."):
    from backend.schemas import Constraints, Plan, Vote
else:  # `python agent_server.py` from backend/, matching the shared runtime.
    from schemas import Constraints, Plan, Vote


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value  # IDs must be preserved exactly, including whitespace.


Text = Annotated[str, AfterValidator(nonblank)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRequest(Contract):
    session_id: Text
    round_id: Text
    proposal_id: Text
    project_idea: Text
    constraints: Constraints | dict
    candidate_plan: Plan | dict
    prior_objections: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_plan(self):
        plan = self.candidate_plan
        if isinstance(plan, dict):
            if not plan:
                raise ValueError("candidate_plan must contain project information")
            # A serialized shared Plan must not silently bypass typed validation.
            if {"session_id", "idea", "constraints"} <= plan.keys():
                plan = Plan.model_validate(plan)
                self.candidate_plan = plan
        if isinstance(plan, Plan):
            if plan.session_id != self.session_id or plan.idea != self.project_idea:
                raise ValueError("candidate plan session/idea mismatch")
            constraints = Constraints.model_validate(self.constraints)
            if plan.constraints != constraints:
                raise ValueError("candidate plan constraints mismatch")
            self.constraints = constraints
        return self


class RiskFinding(Contract):
    category: Literal["privacy", "security", "evidence", "user_value", "accessibility", "other"]
    severity: Literal["low", "medium", "high"]
    description: Text
    affected_feature: Text | None = None

    @property
    def id(self) -> str:
        return "risk-" + str(uuid5(NAMESPACE_URL, self.model_dump_json()))


class ResearchPaper(Contract):
    paper_id: Text
    title: Text
    year: int | None = None
    url: HttpUrl
    abstract: Text | None = None
    doi: Text | None = None
    authors: list[Text] = Field(default_factory=list)
    source: Literal["semantic_scholar"] = "semantic_scholar"


class ResearchPlan(Contract):
    research_needed: bool
    evidence_essential: bool
    assumptions: list[Text]
    queries: list[Text] = Field(max_length=2)
    rationale: Text

    @model_validator(mode="after")
    def consistent_research(self):
        if self.research_needed != bool(self.queries):
            raise ValueError("research_needed must match presence of queries")
        if self.evidence_essential and not self.research_needed:
            raise ValueError("essential research needs queries")
        return self


class EvidenceAssessment(Contract):
    paper_id: Text
    assumption: Text
    stance: Literal["supports", "challenges", "mixed", "inconclusive"]
    summary: Text
    abstract_only: Literal[True] = True


class NodeChange(Contract):
    node_id: Text
    status: Literal["agreed", "deferred", "rejected"]


class MitigationNode(Contract):
    label: Text
    parent: Text | None
    rationale: Text
    estimate_hours: float = Field(ge=0)
    cost_usd: float = Field(ge=0)


class EvidenceCounteroffer(Contract):
    summary: Text
    changes: list[NodeChange]
    new_nodes: list[MitigationNode]

    @model_validator(mode="after")
    def concrete(self):
        if not self.changes and not self.new_nodes:
            raise ValueError("counteroffer must change the plan")
        return self


class EvidenceReview(Contract):
    proposal_id: Text
    agent_id: Literal["evidence"] = "evidence"
    vote: Vote
    score: int = Field(ge=0, le=100, strict=True)
    objections: list[RiskFinding]
    requested_changes: list[Text]
    reasoning: Text
    research_paper_ids: list[Text] = Field(default_factory=list)
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list)
    counteroffer: EvidenceCounteroffer | None = None

    @model_validator(mode="after")
    def reject_requires_changes(self):
        if self.vote == "reject" and not self.requested_changes:
            raise ValueError("reject requires concrete requested changes")
        return self


class EvidenceResponse(Contract):
    session_id: Text
    round_id: Text
    review: EvidenceReview
    papers: list[ResearchPaper] = Field(default_factory=list)
    warnings: list[Text] = Field(default_factory=list)
    research_plan: ResearchPlan

    @model_validator(mode="after")
    def genuine_references(self):
        ids = {paper.paper_id for paper in self.papers}
        cited = set(self.review.research_paper_ids)
        assessed = {item.paper_id for item in self.review.evidence_assessments}
        if not cited <= ids or not assessed <= cited:
            raise ValueError("citations must reference retrieved papers; assessments must be cited")
        if len(ids) != len(self.papers):
            raise ValueError("duplicate retrieved papers")
        return self

    @computed_field
    @property
    def risk_ids(self) -> list[str]:
        return [risk.id for risk in self.review.objections]
