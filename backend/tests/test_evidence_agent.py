import asyncio

import pytest

from backend.evidence import agent
from backend.evidence.model import EvidenceServiceError
from backend.evidence.models import EvidenceRequest, EvidenceReview, ResearchPaper, ResearchPlan
from backend.evidence.research import SearchResult


def request(proposal="p", retention="temporary"):
    return EvidenceRequest(session_id="s", round_id="r", proposal_id=proposal,
                           project_idea="Study app", constraints={"deadline_days": 7},
                           candidate_plan={"features": ["quizzes"], "retention": retention})


def review(vote="accept", proposal="p", **kwargs):
    return dict(proposal_id=proposal, vote=vote, score=80, objections=[],
                requested_changes=["Delete documents after processing."] if vote == "reject" else [],
                reasoning="Retention policy is appropriate." if vote == "accept" else "Retention needs clarification.",
                **kwargs)


def planner(needed=False, essential=False):
    return ResearchPlan(research_needed=needed, evidence_essential=essential,
                        assumptions=["Practice may improve learning."] if needed else [],
                        queries=["retrieval practice learning outcomes", "tutoring learning limitations"] if needed else [],
                        rationale="Check empirical learning claim." if needed else "Design risk review is sufficient.")


def mock_calls(monkeypatch, plan, output):
    calls = []
    async def complete(system, payload, schema):
        calls.append((payload, schema))
        return plan if schema is ResearchPlan else output
    monkeypatch.setattr(agent, "structured_call", complete)
    return calls


@pytest.mark.parametrize("vote", ["accept", "reject", "abstain"])
def test_independent_votes_without_research(monkeypatch, vote):
    calls = mock_calls(monkeypatch, planner(), review(vote))
    result = asyncio.run(agent.review_with_evidence(request()))
    assert result.review.vote == vote
    assert result.review.proposal_id == "p"
    assert result.papers == []
    assert len(calls) == 2


def test_retrieved_evidence_passed_and_deduplicated(monkeypatch):
    paper = ResearchPaper(paper_id="real", title="Study", url="https://example.org/study", abstract="Mixed findings.")
    calls = mock_calls(monkeypatch, planner(True), review(research_paper_ids=["real"],
        evidence_assessments=[dict(paper_id="real", assumption="Learning improves", stance="mixed", summary="Mixed abstract findings.")]))
    queries = []
    async def search(query):
        queries.append(query)
        return SearchResult(papers=[paper])
    monkeypatch.setattr(agent, "search_papers", search)
    result = asyncio.run(agent.review_with_evidence(request()))
    assert len(queries) == 2 and len(result.papers) == 1
    assert calls[1][0]["papers"][0]["abstract"] == "Mixed findings."
    assert result.warnings


@pytest.mark.parametrize("essential,vote", [(False, "accept"), (True, "abstain")])
def test_research_outage_remains_visible(monkeypatch, essential, vote):
    calls = mock_calls(monkeypatch, planner(True, essential), review(vote))
    async def search(query):
        return SearchResult(warnings=["Research timed out."])
    monkeypatch.setattr(agent, "search_papers", search)
    result = asyncio.run(agent.review_with_evidence(request()))
    assert "Research timed out." in result.warnings
    assert calls[1][0]["research_limitations"] == result.warnings
    assert not result.papers


def test_essential_missing_evidence_cannot_accept(monkeypatch):
    mock_calls(monkeypatch, planner(True, True), review())
    async def search(query):
        return SearchResult(warnings=["No papers found."])
    monkeypatch.setattr(agent, "search_papers", search)
    with pytest.raises(EvidenceServiceError):
        asyncio.run(agent.review_with_evidence(request()))


@pytest.mark.parametrize("output", [review(proposal="wrong"), review(research_paper_ids=["invented"]),
    {**review(), "agent_id": "architect"}, {**review("reject"), "requested_changes": []},
    review(evidence_assessments=[dict(paper_id="invented", assumption="x", stance="supports", summary="x")])])
def test_invalid_outputs_fail_closed(monkeypatch, output):
    mock_calls(monkeypatch, planner(), output)
    with pytest.raises(EvidenceServiceError):
        asyncio.run(agent.review_with_evidence(request()))


def test_revised_proposal_is_evaluated_afresh(monkeypatch):
    reviewed = []
    async def complete(system, payload, schema):
        if schema is ResearchPlan:
            return planner()
        data = payload["request"]
        reviewed.append(data)
        vote = "reject" if data["candidate_plan"]["retention"] == "forever" else "accept"
        return review(vote, data["proposal_id"])
    monkeypatch.setattr(agent, "structured_call", complete)
    first = asyncio.run(agent.review_plan(request("p1", "forever")))
    revision = request("p2", "temporary")
    revision.prior_objections = ["Indefinite retention creates privacy risk."]
    second = asyncio.run(agent.review_plan(revision))
    assert (first.vote, second.vote) == ("reject", "accept")
    assert (first.proposal_id, second.proposal_id) == ("p1", "p2")
    assert reviewed[1]["prior_objections"] == revision.prior_objections
