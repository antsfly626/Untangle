"""Regression checks for the pulled session API/baseline and local Evidence agent."""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def merged(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_API_KEY", "test")
    api = importlib.import_module("api")
    from schemas import Constraints, Plan
    plan = Plan(session_id="merge-test", idea="Study assistant",
                constraints=Constraints(team_size=2, timeline_weeks=1, weekly_hours=20))
    monkeypatch.setattr(api, "SESSIONS", {})
    return api, plan


def test_unified_swagger_and_health(merged):
    api, _ = merged
    client = TestClient(api.app)
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/sessions", "/sessions/{session_id}", "/sessions/{session_id}/messages",
            "/sessions/{session_id}/stream", "/agents/evidence/review", "/health"} <= paths.keys()
    assert client.get("/health").json()["ok"] is True
    assert client.get("/docs").status_code == 200


def test_session_creation_and_messages_remain_functional(merged, monkeypatch):
    api, plan = merged
    async def extract(text):
        assert text == "My project"
        return plan
    async def ask(candidate):
        return candidate, []
    async def update(candidate, text):
        return candidate.model_copy(update={"idea": text}), "Updated idea.", True
    monkeypatch.setattr(api, "extract", extract)
    monkeypatch.setattr(api, "ask", ask)
    monkeypatch.setattr(api, "update_from_user", update)
    monkeypatch.setattr(api, "ready_to_negotiate", lambda _: False)
    with TestClient(api.app) as client:
        created = client.post("/sessions", json={"text": "My project"})
        assert created.status_code == 200
        assert created.json()["status"] == "needs_answers"
        session_id = created.json()["id"]
        assert client.get(f"/sessions/{session_id}").json()["plan"]["idea"] == plan.idea
        updated = client.post(f"/sessions/{session_id}/messages", json={"text": "Revised idea"})
        assert updated.status_code == 200
        assert updated.json()["plan"]["idea"] == "Revised idea"
        assert client.get("/sessions/unknown").status_code == 404


def test_unified_server_invokes_evidence(merged, monkeypatch):
    api, _ = merged
    from evidence import interfaces
    from evidence.models import EvidenceResponse, EvidenceReview, ResearchPlan
    async def review(request):
        return EvidenceResponse(session_id=request.session_id, round_id=request.round_id,
            research_plan=ResearchPlan(research_needed=False, evidence_essential=False,
                                      assumptions=[], queries=[], rationale="Design review suffices."),
            review=EvidenceReview(proposal_id=request.proposal_id, vote="accept", score=80,
                                  objections=[], requested_changes=[], reasoning="Temporary processing limits risk."))
    monkeypatch.setattr(interfaces, "review_with_evidence", review)
    result = TestClient(api.app).post("/agents/evidence/review", json={
        "session_id": "s", "round_id": "r", "proposal_id": "p", "project_idea": "Study app",
        "constraints": {}, "candidate_plan": {"retention": "temporary"}})
    assert result.status_code == 200
    assert result.json()["review"]["proposal_id"] == "p"


def test_comparison_counts_both_model_clients(merged, monkeypatch):
    _, plan = merged
    compare = importlib.import_module("compare")
    regular = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    evidence = dict(regular)
    monkeypatch.setattr(compare, "stats", regular)
    monkeypatch.setattr(compare, "evidence_stats", evidence)
    async def extract(text):
        return plan
    async def baseline(candidate):
        regular.update(calls=1, prompt_tokens=10, completion_tokens=1)
        return candidate
    async def negotiate(candidate, transport):
        regular.update(calls=2, prompt_tokens=20, completion_tokens=2)
        evidence.update(calls=2, prompt_tokens=20, completion_tokens=2)
        return candidate
    monkeypatch.setattr(compare, "extract", extract)
    monkeypatch.setattr(compare, "single_agent_plan", baseline)
    monkeypatch.setattr(compare, "negotiate", negotiate)
    result = asyncio.run(compare.run_one("test", "My project"))
    assert result["baseline"]["llm_calls"] == 1
    assert result["baseline"]["tokens"] == 11
    assert result["agents"]["llm_calls"] == 3
    assert result["agents"]["tokens"] == 33
