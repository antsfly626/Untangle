"""Two model calls: neutral research planning, then an independent vote."""

from pydantic import ValidationError

from .model import EvidenceServiceError, structured_call
from .models import EvidenceRequest, EvidenceResponse, EvidenceReview, ResearchPlan
from .prompts import RESEARCH_PROMPT, REVIEW_PROMPT
from .research import search_papers


async def review_with_evidence(request: EvidenceRequest) -> EvidenceResponse:
    # No cross-request vote memory or score-based overrides.
    request = EvidenceRequest.model_validate(request)
    data = request.model_dump(mode="json")
    try:
        research = ResearchPlan.model_validate(await structured_call(RESEARCH_PROMPT, data, ResearchPlan))
        papers = {}
        warnings = []
        for query in dict.fromkeys(research.queries):
            result = await search_papers(query)
            warnings.extend(result.warnings)
            for paper in result.papers:
                papers.setdefault(paper.paper_id, paper)
        if papers:
            warnings.append("Only paper metadata and available abstracts were retrieved; full text was not reviewed.")
        warnings = list(dict.fromkeys(warnings))
        payload = {"request": data, "research_plan": research.model_dump(mode="json"),
                   "papers": [paper.model_dump(mode="json") for paper in papers.values()],
                   "research_limitations": warnings}
        review = EvidenceReview.model_validate(await structured_call(REVIEW_PROMPT, payload, EvidenceReview))
        if review.proposal_id != request.proposal_id:
            raise ValueError("proposal mismatch")
        if review.counteroffer:
            plan = data["candidate_plan"]
            nodes = {node["id"]: node for node in plan.get("nodes", [])}
            for change in review.counteroffer.changes:
                node = nodes.get(change.node_id)
                if (not node or node.get("locked") or change.node_id == "root"
                        or (node.get("required") and change.status != "agreed")):
                    raise ValueError("invalid counteroffer target")
            if any(node.parent is not None and node.parent not in nodes
                   for node in review.counteroffer.new_nodes):
                raise ValueError("invalid mitigation parent")
        # Fail closed on a contradictory essential-evidence decision; never manufacture a vote.
        if research.evidence_essential and not any(p.abstract for p in papers.values()) and review.vote != "abstain":
            raise ValueError("essential evidence missing without abstention")
        return EvidenceResponse(session_id=request.session_id, round_id=request.round_id,
                                review=review, papers=list(papers.values()), warnings=warnings,
                                research_plan=research)
    except (ValidationError, ValueError):
        raise EvidenceServiceError("Evidence model returned an inconsistent review; no valid vote was recorded.",
                                   code="invalid_output") from None


async def review_plan(request: EvidenceRequest) -> EvidenceReview:
    """Review-only convenience API; HTTP/transport use the envelope to retain metadata."""
    return (await review_with_evidence(request)).review
