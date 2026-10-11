"""Actual runtime adapter and SDK boundaries; model/research calls remain mocked."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# The pulled backend's supported entry points use script-style imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_API_KEY", "test")
    import agents
    import transport
    from evidence import negotiator
    from evidence.models import EvidenceResponse, EvidenceReview, ResearchPlan
    from schemas import Constraints, Node, Plan
    plan = Plan(session_id="s", idea="Study app",
                constraints=Constraints(team_size=2, timeline_weeks=1, weekly_hours=20),
                nodes=[Node(id="root", label="App", type="idea", proposed_by="user"),
                       Node(id="uploads", label="Retain documents forever", type="feature", proposed_by="architect")])
    calls = []
    async def evaluate(request):
        calls.append(request)
        return EvidenceResponse(session_id=request.session_id, round_id=request.round_id,
            research_plan=ResearchPlan(research_needed=False, evidence_essential=False,
                                      assumptions=[], queries=[], rationale="Design risk."),
            warnings=["No empirical claims assessed."],
            review=EvidenceReview(proposal_id=request.proposal_id, vote="reject", score=92,
                objections=[dict(category="privacy", severity="high", description="No deletion policy.",
                                 affected_feature="uploads")],
                requested_changes=["Use temporary processing."], reasoning="Indefinite retention is unnecessary.",
                counteroffer=dict(summary="Replace permanent retention with temporary processing.",
                                  changes=[dict(node_id="uploads", status="rejected")],
                                  new_nodes=[dict(label="Temporary uploads", parent="root", rationale="Delete after processing.",
                                                  estimate_hours=2, cost_usd=0)])))
    monkeypatch.setattr(negotiator, "review_with_evidence", evaluate)
    return SimpleNamespace(agents=agents, transport=transport, adapter=negotiator, plan=plan, calls=calls)


def test_registry_and_shared_request_path(runtime):
    r = runtime
    result = asyncio.run(r.transport.handle_request(r.agents.EVIDENCE, dict(
        op="review", plan=r.plan.model_dump(), round_no=2, last_reviews={})))
    review = r.agents.Review.model_validate(result["review"])
    assert r.transport.AGENTS_BY_ID["evidence"] is r.agents.EVIDENCE
    assert review.vote == "reject" and review.score == .92 and not review.overridden
    assert review.counteroffer.new_nodes[0].estimate_hours == 2
    assert review.evidence["warnings"] == ["No empirical claims assessed."]
    r.adapter.validate_metadata(review, r.plan, r.adapter.current_proposal_id(r.plan, 2), 2)


def test_counteroffer_applies_and_candidate_reference_survives(runtime):
    from negotiation import apply_counteroffer
    r = runtime
    review = asyncio.run(r.agents.EVIDENCE.review(r.plan, 1))
    revised, _ = apply_counteroffer(r.plan, review.counteroffer, "evidence", 1)
    assert revised.nodes[1].status == "rejected"
    assert any(node.label == "Temporary uploads" for node in revised.nodes)
    candidate = r.agents.Candidate("r1-evidence", "evidence", "Safer retention", revised)
    result = asyncio.run(r.agents.EVIDENCE.vote_on(r.plan, [candidate]))[0]
    assert result.candidate_id == "r1-evidence"
    assert result.evidence["review"]["proposal_id"] == "r1-evidence"
    assert r.calls[-1].candidate_plan.nodes[1].status == "rejected"


def test_abstention_survives_failure_and_blocks_consensus(runtime, monkeypatch):
    from evidence.model import EvidenceServiceError
    from negotiation import choose
    r = runtime
    async def fail(request):
        raise EvidenceServiceError("Provider unavailable.")
    monkeypatch.setattr(r.adapter, "review_with_evidence", fail)
    review = asyncio.run(r.agents.EVIDENCE.review(r.plan, 1))
    assert review.vote == "abstain" and review.error
    candidate = r.agents.Candidate("p", "architect", "MVP", r.plan)
    vote = asyncio.run(r.agents.EVIDENCE.vote_on(r.plan, [candidate]))[0]
    tally = {"p": [r.agents.CandidateVote(candidate_id="p", agent=a, vote="accept")
                   for a in ("architect", "delivery")] + [vote]}
    assert not choose([candidate], tally, {a: .6 for a in ("architect", "delivery", "evidence")})[1]


def test_score_is_informational(runtime):
    from negotiation import nash_product
    r = runtime
    review = asyncio.run(r.agents.EVIDENCE.review(r.plan, 1))
    before = nash_product([review], {"evidence": .6})
    review.score = 0
    assert nash_product([review], {"evidence": .6}) == before


@pytest.mark.parametrize("tamper", ["none", "digest", "proposal", "duplicate"])
def test_slim_ballot_validation(runtime, monkeypatch, tamper):
    r = runtime
    candidate = r.agents.Candidate("p", "architect", "Draft", r.plan)
    vote = asyncio.run(r.agents.EVIDENCE.vote_on(r.plan, [candidate]))[0].model_dump()
    if tamper == "digest":
        vote["evidence"]["candidate_sha256"] = "wrong"
    if tamper == "proposal":
        vote["evidence"]["review"]["proposal_id"] = "wrong"
    async def broadcast(payload, agents):
        return {"evidence": {"votes": [vote, vote] if tamper == "duplicate" else [vote]}}
    transport = r.transport.SlimTransport()
    monkeypatch.setattr(transport, "_broadcast", broadcast)
    ballot = asyncio.run(transport.vote_all([r.agents.EVIDENCE], r.plan, [candidate]))[0][0]
    assert ballot.vote == ("reject" if tamper == "none" else "abstain")


def test_history_retains_evidence(runtime):
    from negotiation import negotiate
    r = runtime
    result = asyncio.run(negotiate(r.plan, agents=[r.agents.EVIDENCE], max_rounds=1,
                                  transport=r.transport.LocalTransport()))
    assert any(message.evidence and message.evidence["warnings"] for message in result.negotiation)


def test_real_a2a_executor_serialization(runtime):
    pytest.importorskip("agntcy_app_sdk")
    from agent_server import NegotiatorExecutor
    from a2a.types import Message
    r = runtime
    events = []
    class Queue:
        async def enqueue_event(self, event):
            events.append(event)
    context = SimpleNamespace(get_user_input=lambda: json.dumps(dict(
        op="review", round_no=1, plan=r.plan.model_dump(), last_reviews={})))
    asyncio.run(NegotiatorExecutor(r.agents.EVIDENCE).execute(context, Queue()))
    event = Message.model_validate_json(events[0].model_dump_json())
    data = json.loads(event.parts[0].root.text)
    assert data["review"]["vote"] == "reject"
    assert data["review"]["evidence"]["review"]["proposal_id"].startswith("r1-current-")
    card = r.transport.agent_card(r.agents.EVIDENCE)
    assert card.preferred_transport == "slim"
    assert card.url.endswith("/evidence")


def test_a2a_broadcast_to_executor_roundtrip(runtime, monkeypatch):
    pytest.importorskip("agntcy_app_sdk")
    from agent_server import NegotiatorExecutor
    from a2a.types import SendMessageResponse, SendMessageSuccessResponse
    r = runtime
    transport = r.transport.SlimTransport()
    class Client:
        async def broadcast_message(self, request, recipients, timeout):
            assert len(recipients) == 1
            events = []
            class Queue:
                async def enqueue_event(self, event):
                    events.append(event)
            context = SimpleNamespace(get_user_input=lambda: request.params.message.parts[0].root.text)
            await NegotiatorExecutor(r.agents.EVIDENCE).execute(context, Queue())
            return [SendMessageResponse(SendMessageSuccessResponse(id=request.id, result=events[0]))]
    async def client(agents):
        return Client()
    monkeypatch.setattr(transport, "_new_client", client)
    result = asyncio.run(transport.review_all([r.agents.EVIDENCE], r.plan, 3, {}))[0]
    assert result.vote == "reject" and not result.error
    assert result.evidence["round_id"] == "3"


def test_executor_does_not_expose_exception_details(runtime, monkeypatch):
    pytest.importorskip("agntcy_app_sdk")
    import agent_server
    r = runtime
    async def fail(agent, request):
        raise RuntimeError("private-api-key")
    monkeypatch.setattr(agent_server, "handle_request", fail)
    events = []
    class Queue:
        async def enqueue_event(self, event):
            events.append(event)
    asyncio.run(agent_server.NegotiatorExecutor(r.agents.EVIDENCE).execute(
        SimpleNamespace(get_user_input=lambda: '{"op":"review"}'), Queue()))
    assert "private-api-key" not in events[0].model_dump_json()
    assert "error" in json.loads(events[0].parts[0].root.text)
