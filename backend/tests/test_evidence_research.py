import asyncio

import httpx
import pytest

from backend.evidence.research import search_papers


PAPER = {"paperId": "a" * 40, "title": "A study of retrieval practice", "year": 2021,
         "url": "https://www.semanticscholar.org/paper/" + "a" * 40,
         "abstract": "A controlled study found mixed outcomes across settings.",
         "externalIds": {"DOI": "10.1234/example"}, "authors": [{"name": "Test Author"}]}


def search(handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_papers("retrieval practice outcomes limitations", client=client)
    return asyncio.run(run())


def test_metadata_dedup_and_missing_abstract(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-secret")
    def handler(request):
        assert request.headers["x-api-key"] == "test-secret"
        assert request.url.params["limit"] == "4"
        assert "abstract" in request.url.params["fields"]
        return httpx.Response(200, json={"data": [PAPER, PAPER, {**PAPER, "paperId": "b" * 40, "abstract": None}]})
    result = search(handler)
    assert len(result.papers) == 2
    assert result.papers[0].doi == "10.1234/example"
    assert result.papers[0].authors == ["Test Author"]
    assert any("no abstract" in warning for warning in result.warnings)


def test_empty_results():
    result = search(lambda _: httpx.Response(200, json={"data": []}))
    assert result.papers == []
    assert result.warnings


@pytest.mark.parametrize("row", [{**PAPER, "paperId": None}, {**PAPER, "url": "javascript:bad"},
                                  {**PAPER, "title": " "}, "invalid"])
def test_invalid_records(row):
    result = search(lambda _: httpx.Response(200, json={"data": [row]}))
    assert not result.papers
    assert "invalid" in result.warnings[0]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_bounded_transient_retry(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive upstream body")
    result = search(handler)
    assert len(calls) == 2
    assert not result.papers
    assert "sensitive" not in str(result)


def test_long_retry_after_does_not_delay_demo():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})
    assert search(handler).warnings
    assert len(calls) == 1


def test_timeout_is_sanitized():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("test-secret", request=request)
    result = search(handler)
    assert len(calls) == 2
    assert "test-secret" not in str(result)
    assert result.warnings


@pytest.mark.parametrize("response", [httpx.Response(200, json={"data": "wrong"}),
                                      httpx.Response(200, text="not-json"),
                                      httpx.Response(403, text="secret")])
def test_bad_responses(response):
    assert search(lambda _: response).warnings


def test_retry_recovers():
    responses = iter([httpx.Response(500), httpx.Response(200, json={"data": [PAPER]})])
    assert len(search(lambda _: next(responses)).papers) == 1
