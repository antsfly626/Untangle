"""HTTP and application-message entry points for the same Evidence pipeline."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import model_validator

from .agent import review_with_evidence
from .model import ERROR_DETAILS, EvidenceConfigurationError, EvidenceServiceError
from .models import Contract, EvidenceRequest, EvidenceResponse, Text

router = APIRouter(prefix="/agents/evidence", tags=["Evidence & Risk"])


@router.post("/review", response_model=EvidenceResponse,
             responses={502: {"description": "Model failed or returned an invalid review"},
                        503: {"description": "Model is not configured"}})
async def review_endpoint(request: EvidenceRequest) -> EvidenceResponse:
    try:
        return await review_with_evidence(request)
    except EvidenceConfigurationError:
        raise HTTPException(503, "Evidence model is not configured.") from None
    except EvidenceServiceError as exc:
        raise HTTPException(502, ERROR_DETAILS.get(exc.code, ERROR_DETAILS["review_unavailable"])) from None


class EvaluateMessage(Contract):
    type: Literal["evaluate_plan"]
    session_id: Text
    round_id: Text
    proposal_id: Text
    sender: Text
    recipient: Literal["evidence"]
    payload: EvidenceRequest

    @model_validator(mode="after")
    def matching_ids(self):
        for name in ("session_id", "round_id", "proposal_id"):
            if getattr(self, name) != getattr(self.payload, name):
                raise ValueError(f"envelope/payload {name} mismatch")
        return self


async def handle_evidence_message(message: dict) -> dict:
    """Raise ValidationError on bad messages; safe EvidenceServiceError on model failures."""
    incoming = EvaluateMessage.model_validate(message)
    result = await review_with_evidence(incoming.payload)
    return {
        "type": "agent_review", "session_id": incoming.session_id,
        "round_id": incoming.round_id, "proposal_id": incoming.proposal_id,
        "sender": "evidence", "recipient": incoming.sender,
        "payload": result.review.model_dump(mode="json"),
        "metadata": {"papers": [paper.model_dump(mode="json") for paper in result.papers],
                     "warnings": result.warnings, "risk_ids": result.risk_ids,
                     "research_plan": result.research_plan.model_dump(mode="json")},
    }
