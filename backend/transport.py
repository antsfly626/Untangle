"""How the facilitator reaches the agents.

- LocalTransport: agents are Python objects in this process (fast, no infra).
- SlimTransport: each agent is its own A2A server on AGNTCY SLIM; the facilitator
  broadcasts each round to all of them over SLIM pub/sub (the same pattern as
  coffeeAgntcy Lungo's auction supervisor querying its farms).

The protocol in negotiation.py is identical either way; only delivery changes.
Pick one with UNTANGLE_TRANSPORT=local|slim (default local).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from uuid import uuid4

from agents import CORE_AGENTS, Agent, Candidate, CandidateVote, Review, peer_arguments
from schemas import Plan

logger = logging.getLogger(__name__)

SLIM_SERVER = os.getenv("SLIM_SERVER", "localhost:46357")
# Agents answer with LLM calls that can take a while on big plans.
BROADCAST_TIMEOUT_SECONDS = float(os.getenv("UNTANGLE_BROADCAST_TIMEOUT", "300"))
# All SLIM participants must share this; matches coffeeAgntcy's dev default.
os.environ.setdefault("SLIM_SHARED_SECRET", "slim-shared-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS")


class LocalTransport:
    name = "local"

    async def review_all(
        self, agents: list[Agent], plan: Plan, round_no: int, last_reviews: dict[str, Review]
    ) -> list[Review]:
        return list(
            await asyncio.gather(
                *(a.review(plan, round_no, peer_arguments(last_reviews, a.id), last_reviews.get(a.id)) for a in agents)
            )
        )

    async def vote_all(self, agents: list[Agent], plan: Plan, candidates: list[Candidate]) -> list[list[CandidateVote]]:
        return list(await asyncio.gather(*(a.vote_on(plan, candidates) for a in agents)))


# ---------- AGNTCY / SLIM ----------


def agent_card(agent: Agent):
    """A2A AgentCard advertising the agent on SLIM (shape copied from Lungo's farm cards)."""
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

    url = f"slim://{SLIM_SERVER}/untangle/agents/{agent.id}"
    return AgentCard(
        name=f"Untangle {agent.name}",
        description=agent.goal,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="review_plan",
                name="Review plan",
                description="Critique a project plan against this agent's objective; vote and counteroffer.",
                tags=["negotiation", agent.id],
            ),
            AgentSkill(
                id="vote_on_candidates",
                name="Vote on candidates",
                description="Vote independently on candidate revisions of the plan.",
                tags=["negotiation", agent.id],
            ),
        ],
        supportsAuthenticatedExtendedCard=False,
        preferred_transport="slim",
        url=url,
        additional_interfaces=[AgentInterface(transport="slim", url=url)],
    )


class SlimTransport:
    name = "slim"

    def __init__(self, timeout: float = BROADCAST_TIMEOUT_SECONDS, attempts: int = 2):
        self.timeout = timeout
        self.attempts = attempts
        self._factory = None

    async def _new_client(self, agents: list[Agent]):
        """A fresh client per broadcast, as Lungo's supervisor does: the SDK tears down
        the group session after each broadcast, so reusing a client fails the next one."""
        from agntcy_app_sdk import A2AClientFactory, ClientConfig, SlimTransportConfig

        if self._factory is None:
            self._factory = A2AClientFactory(
                ClientConfig(
                    slim_config=SlimTransportConfig(
                        endpoint=f"http://{SLIM_SERVER}",
                        name="untangle/agents/facilitator",
                        shared_secret_identity=os.environ["SLIM_SHARED_SECRET"],
                    )
                )
            )
        # Any card initializes the client; the recipient list routes the broadcast.
        card = copy.deepcopy(agent_card(agents[0]))
        card.preferred_transport = "slim"
        return await self._factory.create(card)

    async def _send_once(self, payload: dict, agents: list[Agent]) -> dict[str, dict]:
        from a2a.types import Message, MessageSendParams, Part, Role, SendMessageRequest, TextPart
        from agntcy_app_sdk import get_agent_identifier

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message=Message(
                    messageId=str(uuid4()),
                    role=Role.user,
                    parts=[Part(TextPart(text=json.dumps(payload)))],
                )
            ),
        )
        recipients = [get_agent_identifier(agent_card(a)) for a in agents]
        try:
            client = await self._new_client(agents)
            # The SDK logs and swallows most transport errors, returning [].
            responses = await client.broadcast_message(request, recipients=recipients, timeout=self.timeout)
        except (TimeoutError, asyncio.TimeoutError):
            logger.error("SLIM broadcast timed out after %ss", self.timeout)
            responses = []
        except Exception as err:
            logger.error("SLIM broadcast failed: %s", err)
            responses = []

        replies: dict[str, dict] = {}
        for response in responses:
            result = getattr(response.root, "result", None)
            parts = getattr(result, "parts", None)
            if not parts:
                continue
            try:
                data = json.loads(parts[0].root.text)
            except (ValueError, AttributeError):
                continue
            if isinstance(data, dict) and data.get("agent"):
                replies[data["agent"]] = data
        return replies

    async def _broadcast(self, payload: dict, agents: list[Agent]) -> dict[str, dict]:
        """Send one message to every agent over SLIM; return replies keyed by agent id.
        Agents that didn't answer get one retry; any still missing are left out
        (callers mark them unavailable)."""
        replies: dict[str, dict] = {}
        pending = list(agents)
        for attempt in range(self.attempts):
            if attempt:
                logger.warning("Retrying SLIM broadcast for %s", ", ".join(a.id for a in pending))
                await asyncio.sleep(1.0)  # let the previous group session finish tearing down
            replies.update(await self._send_once(payload, pending))
            pending = [a for a in pending if a.id not in replies]
            if not pending:
                break
        return replies

    async def review_all(
        self, agents: list[Agent], plan: Plan, round_no: int, last_reviews: dict[str, Review]
    ) -> list[Review]:
        replies = await self._broadcast(
            {
                "op": "review",
                "plan": plan.model_dump(mode="json"),
                "round_no": round_no,
                # Each agent picks out its own last turn and the others' arguments.
                "last_reviews": {k: v.model_dump(mode="json") for k, v in last_reviews.items()},
            },
            agents,
        )
        reviews = []
        for a in agents:
            reply = replies.get(a.id) or {}
            if "review" in reply:
                reviews.append(Review.model_validate(reply["review"]))
            else:
                reason = reply.get("error") or "no reply over SLIM"
                reviews.append(Review(agent=a.id, message="(unavailable this round)", error=reason))
        return reviews

    async def vote_all(self, agents: list[Agent], plan: Plan, candidates: list[Candidate]) -> list[list[CandidateVote]]:
        replies = await self._broadcast(
            {
                "op": "vote",
                "plan": plan.model_dump(mode="json"),
                "candidates": [
                    {"id": c.id, "proposer": c.proposer, "summary": c.summary, "plan": c.plan.model_dump(mode="json")}
                    for c in candidates
                ],
            },
            agents,
        )
        ballots = []
        for a in agents:
            reply = replies.get(a.id) or {}
            if "votes" in reply:
                ballots.append([CandidateVote.model_validate(v) for v in reply["votes"]])
            else:
                reason = f"unavailable ({reply.get('error') or 'no reply over SLIM'})"
                ballots.append([CandidateVote(candidate_id=c.id, agent=a.id, reason=reason) for c in candidates])
        return ballots


async def handle_request(agent: Agent, request: dict) -> dict:
    """Server side: run one agent's turn for a request received over SLIM."""
    plan = Plan.model_validate(request["plan"])
    if request.get("op") == "review":
        last = {k: Review.model_validate(v) for k, v in (request.get("last_reviews") or {}).items()}
        review = await agent.review(plan, int(request["round_no"]), peer_arguments(last, agent.id), last.get(agent.id))
        return {"agent": agent.id, "review": review.model_dump(mode="json")}
    if request.get("op") == "vote":
        candidates = [
            Candidate(c["id"], c["proposer"], c["summary"], Plan.model_validate(c["plan"]))
            for c in request.get("candidates") or []
        ]
        votes = await agent.vote_on(plan, candidates)
        return {"agent": agent.id, "votes": [v.model_dump(mode="json") for v in votes]}
    return {"agent": agent.id, "error": f"unknown op {request.get('op')!r}"}


def default_transport() -> LocalTransport | SlimTransport:
    choice = os.getenv("UNTANGLE_TRANSPORT", "local").strip().lower()
    return SlimTransport() if choice == "slim" else LocalTransport()


AGENTS_BY_ID: dict[str, Agent] = {a.id: a for a in CORE_AGENTS}
