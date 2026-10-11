import pytest
from pydantic import ValidationError

from backend.evidence.models import (
    EvidenceRequest, EvidenceResponse, EvidenceReview, ResearchPaper, ResearchPlan, RiskFinding,
)
from backend.schemas import Constraints, Plan


def request_data(**updates):
    return dict(session_id="s", round_id="r", proposal_id="p", project_idea="Study app",
                constraints={"deadline_days": 7}, candidate_plan={"features": ["quizzes"]}, **updates)


def review_data(vote="accept", **updates):
    data = dict(proposal_id="p", vote=vote, score=80, objections=[], requested_changes=[],
                reasoning="Temporary processing adequately limits retention risk.")
    data.update(updates)
    return data


def test_request_and_exact_ids():
    data = request_data()
    data["proposal_id"] = " p "
    assert EvidenceRequest(**data).proposal_id == " p "


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_proposal(value):
    data = request_data()
    data["proposal_id"] = value
    with pytest.raises(ValidationError):
        EvidenceRequest(**data)
    del data["proposal_id"]
    with pytest.raises(ValidationError):
        EvidenceRequest(**data)


@pytest.mark.parametrize("vote", ["accept", "reject", "abstain"])
def test_votes(vote):
    changes = ["Delete uploads after processing."] if vote == "reject" else []
    assert EvidenceReview(**review_data(vote, requested_changes=changes)).vote == vote


@pytest.mark.parametrize("score", [-1, 101, 1.5, True])
def test_invalid_scores(score):
    with pytest.raises(ValidationError):
        EvidenceReview(**review_data(score=score))


@pytest.mark.parametrize("changes", [[], [" "]])
def test_reject_needs_changes(changes):
    with pytest.raises(ValidationError):
        EvidenceReview(**review_data("reject", requested_changes=changes))


def test_shared_plan_compatibility():
    constraints = Constraints(team_size=2, timeline_weeks=1, weekly_hours=20)
    plan = Plan(session_id="s", idea="Study app", constraints=constraints)
    data = request_data()
    data.update(constraints=constraints, candidate_plan=plan)
    assert isinstance(EvidenceRequest(**data).candidate_plan, Plan)
    data["candidate_plan"] = plan.model_dump()
    assert isinstance(EvidenceRequest(**data).candidate_plan, Plan)
    data["session_id"] = "different"
    with pytest.raises(ValidationError):
        EvidenceRequest(**data)


def test_research_plan_query_limit_and_consistency():
    data = dict(research_needed=True, evidence_essential=False, assumptions=[],
                queries=["one", "two", "three"], rationale="Empirical evaluation.")
    with pytest.raises(ValidationError):
        ResearchPlan(**data)
    data["queries"] = []
    with pytest.raises(ValidationError):
        ResearchPlan(**data)


def test_citation_provenance_and_stable_risk_ids():
    risk = RiskFinding(category="privacy", severity="high", description="Indefinite retention.",
                       affected_feature="uploads")
    paper = ResearchPaper(paper_id="api-id", title="Study", url="https://example.org/paper")
    review = EvidenceReview(**review_data("reject", requested_changes=["Delete uploads after processing."],
                                         objections=[risk], research_paper_ids=[paper.paper_id]))
    data = dict(session_id="s", round_id="r", review=review, papers=[paper],
                research_plan=ResearchPlan(research_needed=True, evidence_essential=False,
                                          assumptions=["Retention affects trust"], queries=["retention trust"],
                                          rationale="Check user-value assumption."))
    response = EvidenceResponse(**data)
    assert response.model_dump()["risk_ids"] == [RiskFinding(**risk.model_dump()).id]
    data["papers"] = []
    with pytest.raises(ValidationError):
        EvidenceResponse(**data)
