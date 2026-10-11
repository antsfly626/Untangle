import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError

from backend.evidence import interfaces as api
from backend.evidence import interfaces as handler
from backend.evidence import model
from backend.evidence.models import EvidenceResponse, EvidenceReview, ResearchPlan
from backend.main import app


def request_data():
    return dict(session_id="s", round_id="r", proposal_id="p", project_idea="Study app",
                constraints={"deadline_days": 7}, candidate_plan={"retention": "temporary"})


def response():
    return EvidenceResponse(session_id="s", round_id="r", review=EvidenceReview(
        proposal_id="p", vote="accept", score=80, objections=[], requested_changes=[],
        reasoning="Temporary processing limits retention risk."),
        research_plan=ResearchPlan(research_needed=False, evidence_essential=False,
                                   assumptions=[], queries=[], rationale="Design review suffices."))


def message():
    return dict(type="evaluate_plan", session_id="s", round_id="r", proposal_id="p",
                sender="negotiation", recipient="evidence", payload=request_data())


async def fake_review(request):
    assert request.proposal_id == "p"
    return response()


def test_endpoint_and_swagger(monkeypatch):
    monkeypatch.setattr(api, "review_with_evidence", fake_review)
    with TestClient(app) as client:
        result = client.post("/agents/evidence/review", json=request_data())
        assert result.status_code == 200
        assert result.json()["review"]["vote"] == "accept"
        assert result.json()["risk_ids"] == []
        assert client.get("/docs").status_code == 200
        assert "/agents/evidence/review" in client.get("/openapi.json").json()["paths"]


def test_endpoint_invalid_input():
    data = request_data()
    del data["proposal_id"]
    assert TestClient(app).post("/agents/evidence/review", json=data).status_code == 422


@pytest.mark.parametrize("error,status", [(model.EvidenceServiceError, 502), (model.EvidenceConfigurationError, 503)])
def test_endpoint_safe_errors(monkeypatch, error, status):
    async def fail(request):
        raise error("secret upstream contents")
    monkeypatch.setattr(api, "review_with_evidence", fail)
    result = TestClient(app).post("/agents/evidence/review", json=request_data())
    assert result.status_code == status
    assert "secret" not in result.text


def test_handler(monkeypatch):
    monkeypatch.setattr(handler, "review_with_evidence", fake_review)
    result = asyncio.run(handler.handle_evidence_message(message()))
    assert result["type"] == "agent_review"
    assert result["sender"] == "evidence" and result["recipient"] == "negotiation"
    assert result["payload"]["proposal_id"] == result["proposal_id"] == "p"
    assert result["session_id"] == "s" and result["round_id"] == "r"
    assert "papers" in result["metadata"]


@pytest.mark.parametrize("field", ["session_id", "round_id", "proposal_id", "type", "recipient"])
def test_handler_rejects_bad_envelopes_before_model(monkeypatch, field):
    data = message()
    data[field] = "wrong"
    async def forbidden(request):
        pytest.fail("model should not be called")
    monkeypatch.setattr(handler, "review_with_evidence", forbidden)
    with pytest.raises(ValidationError):
        asyncio.run(handler.handle_evidence_message(data))


def test_missing_configuration_is_lazy_and_safe(monkeypatch):
    monkeypatch.setattr(model, "load_dotenv", lambda *args: None)
    for name in ("OPENAI_API_KEY", "LLM_API_KEY", "EVIDENCE_MODEL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(model.EvidenceConfigurationError):
        asyncio.run(model.structured_call("system", {}, EvidenceReview))


@pytest.mark.parametrize("mode", ["success", "refusal", "connection", "invalid"])
def test_sdk_structured_boundary(monkeypatch, mode):
    monkeypatch.setattr(model, "load_dotenv", lambda *args: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("EVIDENCE_MODEL", "configured-model")
    calls = []
    class Client:
        def __init__(self, **kwargs):
            self.beta = SimpleNamespace(chat=SimpleNamespace(completions=self))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def parse(self, **kwargs):
            calls.append(kwargs)
            if mode == "connection":
                raise APIConnectionError(message="test-secret", request=httpx.Request("POST", "https://example.org"))
            parsed = response().review if mode != "invalid" else {"score": 1000}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                refusal="declined" if mode == "refusal" else None, parsed=parsed))])
    monkeypatch.setattr(model, "AsyncOpenAI", Client)
    if mode == "success":
        result = asyncio.run(model.structured_call("system", {}, EvidenceReview))
        assert result.vote == "accept"
        assert calls[0]["response_format"] is EvidenceReview
    else:
        with pytest.raises(model.EvidenceServiceError) as exc:
            asyncio.run(model.structured_call("system", {}, EvidenceReview))
        assert "test-secret" not in str(exc.value)


@pytest.mark.parametrize("url,expected", [
    ("https://generativelanguage.googleapis.com/v1beta", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("https://generativelanguage.googleapis.com/v1beta/openai/", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("https://generativelanguage.googleapis.com/v1beta/v1", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("https://example.org", "https://example.org/v1"),
    ("https://example.org/v1/", "https://example.org/v1"),
])
def test_provider_endpoint_normalization(url, expected):
    assert model.normalize_base_url(url) == expected


def test_explicit_custom_endpoint_is_preserved():
    assert model.normalize_base_url("https://example.org/custom/", append_v1=False) == "https://example.org/custom"


def test_groq_token_options_preserve_other_providers():
    assert model.model_options("openai/gpt-oss-20b", "https://api.groq.com/openai/v1") == {
        "reasoning_effort": "low", "max_completion_tokens": 4096,
    }
    assert model.model_options("openai/gpt-oss-20b", "https://example.org/v1") == {}
    assert model.model_options("gemini-3.8-flash", "https://generativelanguage.googleapis.com/v1beta/openai/") == {"reasoning_effort": "low"}


@pytest.mark.parametrize("status,code", [(400, "provider_request"), (401, "authentication"),
                                       (403, "access_denied"), (404, "not_found"), (429, "quota"),
                                       (500, "provider_unavailable"), (503, "provider_unavailable")])
def test_provider_errors_are_actionable_and_safe(monkeypatch, status, code):
    monkeypatch.setattr(model, "load_dotenv", lambda *args: None)
    monkeypatch.setenv("LLM_API_KEY", "private-api-key")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EVIDENCE_BASE_URL", raising=False)
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
            self.beta = SimpleNamespace(chat=SimpleNamespace(completions=self))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def parse(self, **kwargs):
            assert kwargs["reasoning_effort"] == "low"
            response = httpx.Response(status, request=httpx.Request("POST", "https://example.org"))
            raise APIStatusError("private-api-key", response=response, body={"secret": "private-api-key"})
    monkeypatch.setattr(model, "AsyncOpenAI", Client)
    with pytest.raises(model.EvidenceServiceError) as exc:
        asyncio.run(model.structured_call("system", {}, EvidenceReview))
    assert exc.value.code == code
    assert "private-api-key" not in str(exc.value)
    async def fail(request):
        raise exc.value
    monkeypatch.setattr(api, "review_with_evidence", fail)
    result = TestClient(app).post("/agents/evidence/review", json=request_data())
    assert result.status_code == 502
    assert result.json()["detail"] == model.ERROR_DETAILS[code]
    assert "private-api-key" not in result.text
