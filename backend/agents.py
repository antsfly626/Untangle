"""The three decision agents.

Design choices, each grounded in prompt/debate research:
- Agents are defined by a scoring rule + a minimum acceptable score, not just a
  persona (personas alone don't reliably help; private scores are what make
  negotiation benchmarks like LLM-Deliberation work). Part of every score is
  measured in code (constraints.metrics) so it can't be talked away.
- Output keys go reasoning-first, vote-last (format-first answers hurt reasoning).
- Anti-conformity is enforced structurally, since prompts alone don't stop it:
  round 1 is independent, peers' arguments are shown without vote counts, an
  "accept" below the agent's minimum score is overridden to "reject", and vote
  flips without a stated reason are flagged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from constraints import metrics, violations
from llm import complete_json
from schemas import Plan

Vote = Literal["accept", "reject"]


# ---------- what agents return ----------


class Position(BaseModel):
    node_id: str
    position: Literal["keep", "defer", "reject"]
    severity: Literal["blocking", "concern"] = "concern"
    reason: str = ""


class NewNode(BaseModel):
    label: str
    type: Literal["feature", "component", "constraint", "risk", "question"] = "feature"
    parent: Optional[str] = "root"
    rationale: str = ""
    estimate_hours: Optional[float] = None
    cost_usd: Optional[float] = None


class StatusChange(BaseModel):
    node_id: str
    status: Literal["agreed", "deferred", "rejected"]


class EstimateUpdate(BaseModel):
    node_id: str
    estimate_hours: Optional[float] = None
    cost_usd: Optional[float] = None


class Counteroffer(BaseModel):
    summary: str
    changes: list[StatusChange] = []
    new_nodes: list[NewNode] = []
    estimate_updates: list[EstimateUpdate] = []


class Review(BaseModel):
    agent: str
    analysis: str = ""
    positions: list[Position] = []
    ratings: dict[str, float] = {}
    message: str = ""
    counteroffer: Optional[Counteroffer] = None
    changed_because: Optional[str] = None
    llm_vote: Vote = "reject"
    # Filled in by code, not the model:
    vote: Vote = "reject"
    score: float = 0.0
    overridden: bool = False
    unjustified_flip: bool = False
    # Set when the agent's call failed; it then counts as a reject.
    error: Optional[str] = None


class CandidateVote(BaseModel):
    candidate_id: str
    agent: str = ""
    reason: str = ""
    ratings: dict[str, float] = {}
    llm_vote: Vote = "reject"
    vote: Vote = "reject"
    score: float = 0.0
    overridden: bool = False
    # Stated directly, so improvements never depend on comparing scores across calls.
    prefer_over_current: bool = False


@dataclass
class Candidate:
    id: str
    proposer: str
    summary: str
    plan: Plan


# ---------- prompt pieces ----------

SHARED_RULES = """\
You are the {name} agent in Untangle, a team of three independent agents \
(Architect, Delivery, Evidence & Risk) that turn a user's rough idea (a side project, product, \
or startup) into a plan. \
There is no manager. A plan is adopted only if all three agents accept it AND it passes \
hard constraints checked by code.

Your objective: {goal}

Your score = {formula}
Your ratings ({rating_list}) come from you; the other terms are measured by code and shown under \
"measured". You can only accept a plan whose score is at least {threshold}; code enforces this.

How to negotiate:
- Judge plans against YOUR objective, not the others'. Disagreement is expected and useful.
- Change your position only because of a new argument or new information, and name it in \
"changed_because". Politeness or wanting agreement is not a reason.
- When your core objective is protected, prefer a workable compromise to deadlock: offer a \
concrete counteroffer the others could plausibly accept.
- Nodes marked "locked" were decided by the user. Treat them as fixed and plan around them.
- Nodes marked "required" are the user's must-haves. A plan that defers or rejects one fails the \
hard constraints, so make room elsewhere; you may still propose a cheaper way to deliver it.
- Be concrete. Refer to node ids. Estimates are person-hours of work; costs are cash in USD \
(paid APIs, hosting, licenses). "measured" gives the team's capacity and budget use.

Positions and counteroffers:
- severity "blocking" means you would reject ANY plan that takes this node's current direction; \
"concern" means you would live with it.
- A counteroffer must change the plan through "changes", "new_nodes", or "estimate_updates". \
A counteroffer with none of these is ignored.
- Every new feature or component needs estimate_hours and cost_usd; missing estimates are \
assumed to be 8 hours.
- Estimates can't be cut below 50% of a node's original estimate (code enforces this). To make \
something cheaper, propose a smaller variant as a new node instead of shrinking the estimate.
- Instead of all-or-nothing, consider a smaller variant of a contested feature as a new node \
(e.g. "a simple list view" instead of "a live map") and defer the full version.

Ratings are 0 to 1:
{rating_help}
"""

REVIEW_FORMAT = """\
Respond with only a JSON object with these keys, in this order:
{
  "analysis": "your private reasoning, 2-5 sentences",
  "positions": [{"node_id": "...", "position": "keep|defer|reject", "severity": "blocking|concern", "reason": "..."}],
  "ratings": {RATING_KEYS},
  "message": "what you say to the other agents, 1-3 sentences",
  "counteroffer": null or {"summary": "...", "changes": [{"node_id": "...", "status": "agreed|deferred|rejected"}],
                           "new_nodes": [{"label": "...", "type": "feature|component|constraint|risk|question", "parent": "root", "rationale": "...", "estimate_hours": 0, "cost_usd": 0}],
                           "estimate_updates": [{"node_id": "...", "estimate_hours": 0, "cost_usd": 0}]},
  "changed_because": null or "the new argument or information that changed your vote",
  "vote": "accept|reject"
}
Give a position for every feature node. A position is what you want for that node regardless of \
its current status: "keep" = build it now, "defer" = later, "reject" = never."""

VOTE_FORMAT = """\
Respond with only a JSON object with these keys, in this order:
{
  "analysis": "your private reasoning, 2-5 sentences",
  "votes": [{"candidate_id": "...", "reason": "...", "ratings": {RATING_KEYS},
             "prefer_over_current": true or false, "vote": "accept|reject"}]
}
Vote on every candidate independently. "vote": could you accept this plan? \
"prefer_over_current": would you rather have this candidate than the current plan?"""


def plan_brief(plan: Plan) -> dict:
    return {
        "idea": plan.idea,
        "constraints": plan.constraints.model_dump(),
        "nodes": [
            {
                k: v
                for k, v in n.model_dump(
                    include={
                        "id", "label", "type", "status", "parent", "rationale", "locked", "required",
                        "estimate_hours", "cost_usd", "original_estimate_hours", "original_cost_usd",
                    }
                ).items()
                if v not in (None, "", False)
            }
            for n in plan.nodes
        ],
        "measured": {k: round(v, 2) for k, v in metrics(plan).items()},
        "hard_constraint_violations": violations(plan),
    }


def _vote(raw: object) -> Vote:
    return "accept" if str(raw).strip().lower() == "accept" else "reject"


def _valid(model: type[BaseModel], items: object) -> list:
    """Keep the well-formed items, drop the rest, rather than failing the round."""
    out = []
    for item in items if isinstance(items, list) else []:
        try:
            out.append(model.model_validate(item))
        except ValueError:
            pass
    return out


def _parse_review(raw: dict, agent_id: str) -> Review:
    counteroffer = None
    if isinstance(raw.get("counteroffer"), dict):
        co = raw["counteroffer"]
        counteroffer = Counteroffer(
            summary=str(co.get("summary", "")),
            changes=_valid(StatusChange, co.get("changes")),
            new_nodes=_valid(NewNode, co.get("new_nodes")),
            estimate_updates=_valid(EstimateUpdate, co.get("estimate_updates")),
        )
        if not (counteroffer.changes or counteroffer.new_nodes or counteroffer.estimate_updates):
            counteroffer = None  # prose-only counteroffers don't change anything
    return Review(
        agent=agent_id,
        analysis=str(raw.get("analysis", "")),
        positions=_valid(Position, raw.get("positions")),
        message=str(raw.get("message", "")),
        counteroffer=counteroffer,
        changed_because=raw.get("changed_because") or None,
        llm_vote=_vote(raw.get("vote")),
    )


def _clamp_ratings(raw: object, keys: list[str]) -> dict[str, float]:
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in keys:
        try:
            out[key] = min(1.0, max(0.0, float(raw.get(key, 0.5))))
        except (TypeError, ValueError):
            out[key] = 0.5
    return out


# ---------- the agent ----------


@dataclass
class Agent:
    id: str
    name: str
    goal: str
    ratings: dict[str, str]  # key -> what it means
    threshold: float
    score_fn: Callable[[dict[str, float], dict[str, float]], float]  # (measured, ratings) -> 0..1
    formula: str  # score_fn in words, shown to the agent

    def system_prompt(self) -> str:
        return SHARED_RULES.format(
            name=self.name,
            goal=self.goal,
            formula=self.formula,
            rating_list=", ".join(self.ratings),
            threshold=self.threshold,
            rating_help="\n".join(f"- {k}: {v}" for k, v in self.ratings.items()),
        )

    def _rating_keys(self) -> str:
        return json.dumps({k: 0.0 for k in self.ratings})

    def _score(self, plan: Plan, ratings: dict[str, float]) -> float:
        return round(min(1.0, max(0.0, self.score_fn(metrics(plan), ratings))), 3)

    async def review(
        self,
        plan: Plan,
        round_no: int,
        peer_arguments: Optional[list[dict]] = None,
        own_last: Optional[Review] = None,
    ) -> Review:
        """Judge the whole plan. Round 1 is independent: no peer arguments."""
        parts = [f"Round {round_no}. Current plan:\n{json.dumps(plan_brief(plan), indent=1)}"]
        if own_last:
            parts.append(f"Your previous turn: vote={own_last.vote}, message={own_last.message!r}")
        if peer_arguments:
            # Arguments only, never vote tallies, to limit bandwagon pressure.
            parts.append("Other agents' arguments last round:\n" + json.dumps(peer_arguments, indent=1))
        parts.append(REVIEW_FORMAT.replace("{RATING_KEYS}", self._rating_keys()))

        try:
            raw = await complete_json(self.system_prompt(), "\n\n".join(parts))
        except Exception as err:  # unavailable agent: no consent without a voice
            return Review(agent=self.id, message="(unavailable this round)", error=f"{type(err).__name__}: {err}"[:200])
        review = _parse_review(raw, self.id)
        review.ratings = _clamp_ratings(raw.get("ratings") or {}, list(self.ratings))
        review.score = self._score(plan, review.ratings)
        review.vote, review.overridden = self._enforce(review.llm_vote, review.score)
        if own_last and review.llm_vote != own_last.llm_vote and not review.changed_because:
            review.unjustified_flip = True
        return review

    async def vote_on(self, plan: Plan, candidates: list[Candidate]) -> list[CandidateVote]:
        """Vote independently on each counteroffer (one call covers all candidates)."""
        listing = [
            {
                "candidate_id": c.id,
                "proposed_by": c.proposer,
                "summary": c.summary,
                "resulting_plan": plan_brief(c.plan),
            }
            for c in candidates
        ]
        user = (
            f"Current plan:\n{json.dumps(plan_brief(plan), indent=1)}\n\n"
            f"Candidate revisions:\n{json.dumps(listing, indent=1)}\n\n"
            + VOTE_FORMAT.replace("{RATING_KEYS}", self._rating_keys())
        )
        try:
            raw = await complete_json(self.system_prompt(), user)
        except Exception as err:  # unavailable agent: every vote counts as reject
            raw = {"votes": [{"candidate_id": c.id, "reason": f"unavailable ({type(err).__name__})"} for c in candidates]}
        votes_raw = raw.get("votes") if isinstance(raw.get("votes"), list) else []
        by_id = {v.get("candidate_id"): v for v in votes_raw if isinstance(v, dict)}

        votes = []
        for c in candidates:
            v = by_id.get(c.id, {})  # a missing vote counts as reject
            ratings = _clamp_ratings(v.get("ratings") or {}, list(self.ratings))
            score = self._score(c.plan, ratings)
            llm_vote = _vote(v.get("vote"))
            final, overridden = self._enforce(llm_vote, score)
            votes.append(
                CandidateVote(
                    candidate_id=c.id,
                    agent=self.id,
                    reason=str(v.get("reason", "no vote returned")),
                    ratings=ratings,
                    llm_vote=llm_vote,
                    vote=final,
                    score=score,
                    overridden=overridden,
                    prefer_over_current=str(v.get("prefer_over_current")).strip().lower() == "true",
                )
            )
        return votes

    def _enforce(self, llm_vote: Vote, score: float) -> tuple[Vote, bool]:
        if llm_vote == "accept" and score < self.threshold:
            return "reject", True
        return llm_vote, False


# ---------- the three agents ----------


def _fit(use: float) -> float:
    """1.0 with a 20% buffer left, falling to 0 at 20% over the limit."""
    return min(1.0, max(0.0, (1.2 - use) / 0.4))


ARCHITECT = Agent(
    id="architect",
    name="Architect",
    goal=(
        "Make the product compelling and technically coherent. Keep the features that make the idea "
        "worth building, with an architecture that will not need a rewrite. Resist cutting what makes "
        "it distinctive. Unused capacity is value the user never gets: argue to spend spare hours "
        "on the features that matter most."
    ),
    ratings={
        "technical_quality": "architecture is coherent, with no missing critical components",
        "distinctiveness": "the plan still delivers what makes the idea interesting",
    },
    # Utilization pulls opposite to Delivery's buffer (both full at 80% use),
    # so the two genuinely contest the same number. The minimum stays a safety
    # net: a plan using ~40% of capacity with mediocre ratings (~0.6) fails.
    threshold=0.6,
    score_fn=lambda m, r: 0.25 * m["utilization"]
    + 0.375 * r["technical_quality"]
    + 0.375 * r["distinctiveness"],
    formula="0.25 × utilization + 0.375 × technical_quality + 0.375 × distinctiveness, "
    "where utilization = min(1, capacity_use / 0.8)",
)

DELIVERY = Agent(
    id="delivery",
    name="Delivery",
    goal=(
        "Ship a working product within the team's timeline, hours, and budget with low risk. Protect "
        "the team's time and money, prefer simple proven technology, and push back on scope creep, "
        "recurring costs, and risky dependencies."
    ),
    ratings={
        "low_risk": "few risky dependencies, unknowns, or integration points",
    },
    threshold=0.6,
    score_fn=lambda m, r: 0.4 * _fit(m["capacity_use"])
    + 0.3 * _fit(m["budget_use"])
    + 0.3 * r["low_risk"],
    formula="0.4 × fit(capacity_use) + 0.3 × fit(budget_use) + 0.3 × low_risk, "
    "where fit(x) = 1 up to 0.8, falling linearly to 0 at 1.2",
)

EVIDENCE = Agent(
    id="evidence",
    name="Evidence & Risk",
    goal=(
        "Make sure the plan delivers real value to its users, backed by evidence, and is accessible, "
        "safe, and honest about what is unvalidated. Challenge assumptions and protect the user's "
        "must-haves."
    ),
    ratings={
        "user_value": "the plan solves a real user problem, with assumptions marked as unvalidated where needed",
        "safety": "user safety, legal/regulatory risk, accessibility, and privacy are handled",
    },
    threshold=0.6,
    score_fn=lambda m, r: 0.4 * m["must_have_coverage"] + 0.3 * r["user_value"] + 0.3 * r["safety"],
    formula="0.4 × must_have_coverage + 0.3 × user_value + 0.3 × safety",
)

CORE_AGENTS = [ARCHITECT, DELIVERY, EVIDENCE]


if __name__ == "__main__":
    # Smoke test: one independent review round on the sample plan (3 LLM calls).
    import asyncio
    from pathlib import Path

    from llm import stats

    sample = Path(__file__).resolve().parent.parent / "shared" / "sample_plan.json"
    plan = Plan.model_validate_json(sample.read_text())
    # Start from an undecided plan, as in the real flow: the sample's
    # pre-made decisions would anchor the agents.
    for node in plan.nodes:
        if node.id != "root":
            node.status, node.objections, node.supported_by = "proposed", [], []

    async def main() -> None:
        reviews = await asyncio.gather(*(a.review(plan, round_no=1) for a in CORE_AGENTS))
        for r in reviews:
            flag = " (overridden: below minimum score)" if r.overridden else ""
            print(f"\n[{r.agent}] vote={r.vote}{flag} score={r.score} ratings={r.ratings}")
            print(f"  says: {r.message}")
            for p in r.positions:
                if p.position != "keep":
                    print(f"  {p.position} {p.node_id} ({p.severity}): {p.reason}")
            if r.counteroffer:
                co = r.counteroffer
                print(f"  counteroffer: {co.summary}")
                for c in co.changes:
                    print(f"    set {c.node_id} -> {c.status}")
                for n in co.new_nodes:
                    print(f"    add {n.type} '{n.label}' ({n.estimate_hours}h ${n.cost_usd}): {n.rationale}")
                for e in co.estimate_updates:
                    print(f"    estimate {e.node_id} -> {e.estimate_hours}h ${e.cost_usd}")
            else:
                print("  counteroffer: none")
        print(f"\n{stats}")

    asyncio.run(main())
