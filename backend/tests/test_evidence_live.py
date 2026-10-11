"""Explicitly opt in: RUN_EVIDENCE_LIVE=1 python -m pytest backend/tests/test_evidence_live.py -s"""

import asyncio
import os

import pytest

from backend.evidence.agent import review_with_evidence
from backend.evidence.models import EvidenceRequest


@pytest.mark.skipif(os.getenv("RUN_EVIDENCE_LIVE") != "1", reason="Live model/research calls are opt-in")
def test_live_review():
    request = EvidenceRequest(
        session_id="manual-demo", round_id="round-1", proposal_id="proposal-1",
        project_idea="Study assistant using retrieval practice",
        constraints={"team_size": 2, "deadline_days": 7},
        candidate_plan={"features": ["Practice quizzes with keyboard-accessible controls"],
                        "retention": "Delete uploaded documents immediately after processing",
                        "security": "Private per-user uploads, no public document URLs",
                        "assumption": "Retrieval practice may improve learning; evaluate research and pilot feedback",
                        "scope": "MVP with no guaranteed improvement claims"},
    )
    result = asyncio.run(review_with_evidence(request))
    assert result.review.proposal_id == request.proposal_id
    assert result.review.vote in {"accept", "reject", "abstain"}
    assert set(result.review.research_paper_ids) <= {paper.paper_id for paper in result.papers}
    print(result.model_dump_json(indent=2))
