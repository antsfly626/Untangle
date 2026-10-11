"""Small, bounded Semantic Scholar client. Metadata only comes from HTTP results."""

import asyncio
import os

import httpx
from pydantic import Field, ValidationError

from .models import Contract, ResearchPaper

SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "paperId,title,abstract,year,url,externalIds,authors"


class SearchResult(Contract):
    papers: list[ResearchPaper] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


async def search_papers(query: str, *, client: httpx.AsyncClient | None = None) -> SearchResult:
    if not query.strip():
        raise ValueError("query must not be blank")
    if client is None:
        async with httpx.AsyncClient(timeout=6.0) as owned:
            return await search_papers(query, client=owned)
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"x-api-key": key} if key else {}
    for attempt in range(2):
        try:
            response = await client.get(SEARCH_URL, params={"query": query, "limit": 4, "fields": FIELDS},
                                        headers=headers, timeout=6.0)
        except httpx.RequestError:
            if attempt == 0:
                await asyncio.sleep(0.25)
                continue
            return SearchResult(warnings=["Research request failed or timed out; no results available for this query."])
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 0:
                # Do not wait through a long rate-limit window during the demo.
                try:
                    delay = float(response.headers.get("Retry-After", "0.25"))
                except ValueError:
                    delay = 0.25
                if delay <= 1:
                    await asyncio.sleep(max(0.25, delay))
                    continue
            return SearchResult(warnings=["Research service rate limited or unavailable; results are incomplete."])
        if response.status_code != 200:
            return SearchResult(warnings=["Research service rejected the request; results are unavailable."])
        try:
            body = response.json()
            rows = body.get("data") if isinstance(body, dict) else None
            if not isinstance(rows, list):
                raise ValueError("missing data list")
        except ValueError:
            return SearchResult(warnings=["Research service returned malformed data."])
        return parse_papers(rows)
    raise AssertionError("unreachable")


def parse_papers(rows: list) -> SearchResult:
    papers: dict[str, ResearchPaper] = {}
    warnings = []
    for row in rows[:4]:
        try:
            if not isinstance(row, dict):
                raise ValueError("invalid record")
            paper = ResearchPaper(
                paper_id=row.get("paperId"), title=row.get("title"), url=row.get("url"),
                year=row.get("year"), abstract=row.get("abstract") or None,
                doi=(row.get("externalIds") or {}).get("DOI"),
                authors=[author["name"] for author in (row.get("authors") or [])
                         if isinstance(author, dict) and author.get("name")],
            )
        except (ValidationError, ValueError, TypeError, AttributeError):
            warnings.append("Skipped a research record with invalid or missing metadata.")
            continue
        papers.setdefault(paper.paper_id, paper)
        if not paper.abstract:
            warnings.append(f"Paper {paper.paper_id} has no abstract; findings cannot be inferred from its title.")
    if not papers:
        warnings.append("No usable papers found; absence of results is not evidence of failure.")
    return SearchResult(papers=list(papers.values()), warnings=list(dict.fromkeys(warnings)))
