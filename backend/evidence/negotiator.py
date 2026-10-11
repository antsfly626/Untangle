"""Bridge the Evidence pipeline to Untangle's existing local and AGNTCY runtime.

Imported lazily by agents.EvidenceAgent, using backend's script-style imports.
"""

import asyncio
import hashlib
import json

from agents import CandidateVote, Counteroffer, Position, Review

from .agent import review_with_evidence
from .model import EvidenceServiceError
from .models import EvidenceRequest, EvidenceResponse


def plan_digest(plan) -> str:
    return hashlib.sha256(json.dumps(plan.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def current_proposal_id(plan, round_no: int) -> str:
    return f"r{round_no}-current-{plan_digest(plan)[:16]}"


def validate_metadata(vote, plan, proposal_id, round_no):
    """Reject stale/misrouted research votes after an AGNTCY round trip."""
    if vote.evidence is None:
        if vote.vote != "abstain":
            raise ValueError("evidence vote has no provenance")
        return
    data = dict(vote.evidence)
    digest = data.pop("candidate_sha256", None)
    data.pop("risk_ids", None)  # computed output, not an input field
    result = EvidenceResponse.model_validate(data)
    if (digest != plan_digest(plan) or result.session_id != plan.session_id
            or result.round_id != str(round_no) or result.review.proposal_id != proposal_id
            or result.review.vote != vote.vote or vote.llm_vote != vote.vote
            or not vote.score_informational or vote.overridden):
        raise ValueError("evidence vote does not match candidate")


async def evaluate(plan, round_no, proposal_id, prior_objections=None):
    result = await review_with_evidence(EvidenceRequest(
        session_id=plan.session_id, round_id=str(round_no), proposal_id=proposal_id,
        project_idea=plan.idea, constraints=plan.constraints.model_dump(mode="json"),
        candidate_plan=plan.model_dump(mode="json"), prior_objections=prior_objections or [],
    ))
    metadata = result.model_dump(mode="json")
    metadata["candidate_sha256"] = plan_digest(plan)
    return result.review, metadata


async def review(plan, round_no, peer_arguments=None, own_last=None):
    prior = []
    if own_last and own_last.message:
        prior.append(own_last.message)
    # Peers' argument text only; do not pass votes/scores to the independent reviewer.
    for argument in peer_arguments or []:
        if argument.get("message"):
            prior.append(str(argument["message"]))
    try:
        result, metadata = await evaluate(plan, round_no, current_proposal_id(plan, round_no), prior)
    except EvidenceServiceError as exc:
        return Review(agent="evidence", vote="abstain", llm_vote="abstain",
                      message="Evidence review unavailable.", error=str(exc), score_informational=True)
    offer = None
    if result.counteroffer:
        offer = Counteroffer.model_validate(result.counteroffer.model_dump())
    node_ids = {node.id for node in plan.nodes}
    positions = []
    # Express only explicitly proposed direction changes; do not invent positions.
    for change in result.counteroffer.changes if result.counteroffer else []:
        positions.append(Position(node_id=change.node_id,
                                  position={"agreed": "keep", "deferred": "defer", "rejected": "reject"}[change.status],
                                  severity="blocking" if result.vote == "reject" else "concern",
                                  reason=result.counteroffer.summary))
    # Findings are always retained in metadata, including concerns with no status change.
    assert all(position.node_id in node_ids for position in positions)
    return Review(agent="evidence", vote=result.vote, llm_vote=result.vote,
                  score=result.score / 100, score_informational=True,
                  message=result.reasoning, positions=positions, counteroffer=offer,
                  changed_because=result.reasoning if own_last and own_last.vote != result.vote else None,
                  evidence=metadata)


async def vote_on(plan, candidates):
    round_no = max((message.round for message in plan.negotiation), default=1)

    async def vote(candidate):
        try:
            result, metadata = await evaluate(candidate.plan, round_no, candidate.id)
            return CandidateVote(candidate_id=candidate.id, agent="evidence", reason=result.reasoning,
                                 vote=result.vote, llm_vote=result.vote, score=result.score / 100,
                                 score_informational=True, evidence=metadata)
        except EvidenceServiceError as exc:
            return CandidateVote(candidate_id=candidate.id, agent="evidence", reason=str(exc),
                                 vote="abstain", llm_vote="abstain", score_informational=True)

    return list(await asyncio.gather(*(vote(candidate) for candidate in candidates)))
