"""Decentralized negotiation protocol.

No LLM decides the outcome. Agents critique, counteroffer, and vote; this module
only applies fixed, explainable rules:

  node status   all keep -> agreed, all defer/reject -> deferred/rejected, mixed -> disputed
  consensus     every agent accepts AND hard constraints pass
  next draft    the candidate with the most accepts (min 2) advances; a majority can
                move the draft forward but can never finish the negotiation
  tie-break     Nash bargaining product of agents' margins above their minimums
                (symmetric: favors no agent), then fewest hours as a last resort
  stop          consensus, deadlock (no candidate reaches 2 accepts), or MAX_ROUNDS
  escalate      constraints still fail -> "unresolved" (user must relax them);
                subjective disputes remain -> "escalated" (user decides each one)
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from agents import CORE_AGENTS, Agent, Candidate, CandidateVote, Counteroffer, Review
from constraints import INACTIVE, metrics, planned_hours, violations
from schemas import NegotiationMessage, Node, Objection, Outcome, Plan, Question

MAX_ROUNDS = 3
MIN_ACCEPTS_TO_ADVANCE = 2

OnUpdate = Optional[Callable[[Plan], Awaitable[None]]]


# ---------- pure rules (no LLM) ----------


def _current_direction(node: Node) -> str:
    return {"deferred": "defer", "rejected": "reject"}.get(node.status, "keep")


def derive_statuses(plan: Plan, reviews: list[Review]) -> Plan:
    """Set each node's status from the agents' positions. A missing position
    means the agent accepts the node's current direction."""
    out = plan.model_copy(deep=True)
    by_agent = {r.agent: {p.node_id: p for p in r.positions} for r in reviews}
    for node in out.nodes:
        if node.id == "root" or node.type == "question":
            continue  # questions stay open until the user answers them
        positions = {agent: stated.get(node.id) for agent, stated in by_agent.items()}
        wants = {a: (p.position if p else _current_direction(node)) for a, p in positions.items()}
        values = set(wants.values())
        if values == {"keep"}:
            node.status = "agreed"
        elif "keep" not in values:
            node.status = "rejected" if values == {"reject"} else "deferred"
        else:
            node.status = "disputed"
        node.supported_by = [a for a, w in wants.items() if w == "keep"]
        node.objections = [
            Objection(agent=a, reason=p.reason or f"wants to {p.position}", severity=p.severity)
            for a, p in positions.items()
            if p and p.position != "keep"
        ]
    return out


def apply_counteroffer(plan: Plan, offer: Counteroffer, proposer: str, round_no: int) -> Plan:
    out = plan.model_copy(deep=True)
    nodes = {n.id: n for n in out.nodes}
    for change in offer.changes:
        if change.node_id in nodes and change.node_id != "root":
            nodes[change.node_id].status = change.status
    for update in offer.estimate_updates:
        if update.node_id in nodes:
            if update.estimate_hours is not None:
                nodes[update.node_id].estimate_hours = max(0.0, update.estimate_hours)
            if update.cost_usd is not None:
                nodes[update.node_id].cost_usd = max(0.0, update.cost_usd)
    for i, new in enumerate(offer.new_nodes, 1):
        out.nodes.append(
            Node(
                id=f"r{round_no}-{proposer}-{i}",
                label=new.label,
                type=new.type,
                parent=new.parent if new.parent in nodes else "root",
                proposed_by=proposer,
                rationale=new.rationale,
                estimate_hours=new.estimate_hours,
                cost_usd=new.cost_usd,
            )
        )
    return out


def _signature(plan: Plan) -> tuple:
    return tuple(sorted((n.label.lower(), n.status, n.estimate_hours or 0) for n in plan.nodes))


def build_candidates(plan: Plan, reviews: list[Review], round_no: int) -> list[Candidate]:
    seen = {_signature(plan)}
    out = []
    for r in reviews:
        if not r.counteroffer:
            continue
        revised = apply_counteroffer(plan, r.counteroffer, r.agent, round_no)
        sig = _signature(revised)
        if sig in seen:
            continue  # duplicate of another offer, or no actual change
        seen.add(sig)
        out.append(Candidate(id=f"r{round_no}-{r.agent}", proposer=r.agent, summary=r.counteroffer.summary, plan=revised))
    return out


def nash_product(votes: list[CandidateVote], thresholds: dict[str, float]) -> float:
    """Nash bargaining: product of each agent's margin above its own minimum.
    Symmetric, so the tie-break favors no agent; a plan that leaves everyone
    comfortably above their minimum beats one that thrills one and barely
    passes another. The 0.01 floor keeps one zero margin from erasing the rest."""
    product = 1.0
    for v in votes:
        product *= max(0.0, v.score - thresholds.get(v.agent, 0.0)) + 0.01
    return product


def choose(
    candidates: list[Candidate], tally: dict[str, list[CandidateVote]], thresholds: dict[str, float]
) -> tuple[Optional[Candidate], bool, str]:
    """Fixed rule. Returns (chosen candidate or None, is_consensus, explanation).
    Ranking: most accepts, then fewest hard-constraint violations, then the
    Nash bargaining product, then fewest planned hours as a last resort."""
    n_agents = len(thresholds)

    def accepts(c: Candidate) -> int:
        return sum(v.vote == "accept" for v in tally[c.id])

    def nash(c: Candidate) -> float:
        return nash_product(tally[c.id], thresholds)

    ranked = sorted(
        candidates,
        key=lambda c: (-accepts(c), len(violations(c.plan)), -nash(c), planned_hours(c.plan), c.id),
    )
    best = ranked[0]
    n = accepts(best)
    tied = [c for c in ranked if accepts(c) == n and len(violations(c.plan)) == len(violations(best.plan))]
    tiebreak = ""
    if len(tied) > 1:
        scores = ", ".join(f"{c.id} {nash(c):.4f}" for c in tied)
        tiebreak = f" Tie broken by Nash bargaining product (balance of every agent's margin above its minimum): {scores}."

    if n == n_agents and not violations(best.plan):
        return best, True, f"{best.id} was accepted by all {n_agents} agents and passes the hard constraints.{tiebreak}"
    if n >= MIN_ACCEPTS_TO_ADVANCE:
        return best, False, (
            f"No candidate was accepted by everyone. {best.id} has the most support ({n}/{n_agents}) "
            f"and becomes the draft for the next round.{tiebreak}"
        )
    return None, False, f"No candidate reached {MIN_ACCEPTS_TO_ADVANCE}/{n_agents} accepts: deadlock."


def peer_arguments(reviews: dict[str, Review], exclude: str) -> Optional[list[dict]]:
    """Other agents' arguments, deliberately without their votes."""
    args = [
        {
            "agent": r.agent,
            "message": r.message,
            "positions": [
                {"node_id": p.node_id, "position": p.position, "reason": p.reason}
                for p in r.positions
                if p.position != "keep"
            ],
        }
        for r in reviews.values()
        if r.agent != exclude
    ]
    return args or None


def _critique_text(r: Review) -> str:
    text = r.message
    if r.changed_because:
        text += f" (Changed because: {r.changed_because})"
    if r.overridden:
        text += " [Vote overridden to reject: score below this agent's minimum.]"
    if r.unjustified_flip:
        text += " [Flagged: changed vote without naming a new argument.]"
    return text


def finalize(plan: Plan, reached_consensus: bool, rounds_used: int, last_reviews: dict[str, Review]) -> Plan:
    viol = violations(plan)
    asked = {q.id for q in plan.questions}

    def ask(qid: str, question: str, why: str) -> None:
        if qid not in asked:
            plan.questions.append(Question(id=qid, asked_by="protocol", question=question, why=why, priority=5))

    # Never silently drop something the user asked for: the user has the final say.
    for node in plan.nodes:
        if node.proposed_by == "user" and node.status in INACTIVE:
            verb = "deferring" if node.status == "deferred" else "dropping"
            reasons = "; ".join(f"{o.agent}: {o.reason}" for o in node.objections) or "no reason recorded"
            ask(
                f"confirm-{node.id}",
                f"The agents recommend {verb} '{node.label}', which you asked for. OK, or keep it?",
                reasons,
            )

    if reached_consensus:
        for node in plan.nodes:
            if node.status not in INACTIVE and node.type != "question":
                node.status = "agreed"
        plan.outcome = Outcome(
            status="consensus",
            summary=f"All agents accepted the plan after {rounds_used} round(s).",
            rounds_used=rounds_used,
        )
        return plan

    disputed = [n for n in plan.nodes if n.status == "disputed"]
    if viol:
        ask(
            "relax-constraints",
            "The agents could not fit the plan within your constraints. Extend the deadline, add people, or cut a must-have?",
            "; ".join(viol),
        )
        status, summary = "unresolved", "Hard constraints still fail, so no plan can be adopted. The user must relax a constraint."
    elif disputed:
        for n in disputed:
            sides = "; ".join(f"{o.agent}: {o.reason}" for o in n.objections)
            ask(
                f"decide-{n.id}",
                f"The agents disagree on '{n.label}'. Build it now, defer it, or drop it?",
                f"Supported by {', '.join(n.supported_by) or 'no one'}. Objections: {sides}",
            )
        status = "escalated"
        summary = f"{len(disputed)} subjective tradeoff(s) remain disputed. Keeping the disagreement visible and asking the user."
    else:
        rejecting = [
            f"{a} (its score {r.score} is below its minimum)" if r.overridden else a
            for a, r in last_reviews.items()
            if r.vote == "reject"
        ]
        ask(
            "decide-overall",
            "The agents agree feature by feature, but some still reject the overall plan. Accept it anyway?",
            f"Still rejecting: {', '.join(rejecting)}",
        )
        status, summary = "escalated", "No node-level disputes, but not every agent accepts the overall plan."

    plan.outcome = Outcome(
        status=status,
        summary=summary,
        rounds_used=rounds_used,
        open_disputes=[n.id for n in disputed],
        hard_constraint_violations=viol,
    )
    return plan


# ---------- the protocol loop ----------


async def negotiate(
    plan: Plan,
    agents: list[Agent] = CORE_AGENTS,
    max_rounds: int = MAX_ROUNDS,
    on_update: OnUpdate = None,
) -> Plan:
    """Run up to max_rounds of critique -> counteroffer -> vote. Continues the
    round numbering of any earlier negotiation on this plan (e.g. after an event)."""
    current = plan.model_copy(deep=True)
    history = current.negotiation
    first = max((m.round for m in history), default=0) + 1
    last_reviews: dict[str, Review] = {}
    rounds_used = 0

    def log(round_no: int, phase: str, agent: str, message: str, **extra) -> None:
        history.append(NegotiationMessage(round=round_no, phase=phase, agent=agent, message=message, **extra))

    async def emit() -> None:
        current.negotiation = history
        if on_update:
            await on_update(current)

    for round_no in range(first, first + max_rounds):
        rounds_used += 1

        # 1. Critique: independent parallel calls.
        reviews = await asyncio.gather(
            *(
                a.review(current, round_no, peer_arguments(last_reviews, a.id), last_reviews.get(a.id))
                for a in agents
            )
        )
        last_reviews = {r.agent: r for r in reviews}
        for r in reviews:
            log(
                round_no,
                "critique",
                r.agent,
                _critique_text(r),
                target_node_ids=[p.node_id for p in r.positions if p.position != "keep"],
                vote=r.vote,
                scores={**r.ratings, "score": r.score},
                counteroffer=r.counteroffer.summary if r.counteroffer else None,
            )

        # 2. Node statuses from positions.
        current = derive_statuses(current, reviews)
        await emit()

        # 3. Consensus on the plan as it stands?
        if all(r.vote == "accept" for r in reviews) and not violations(current):
            log(round_no, "decision", "protocol", "All agents accept and hard constraints pass: consensus.")
            return finalize(current, True, rounds_used, last_reviews)

        # 4. Counteroffers become candidate plans.
        candidates = build_candidates(current, reviews, round_no)
        if not candidates:
            log(round_no, "decision", "protocol", "No agent made a usable counteroffer: deadlock.")
            break
        for c in candidates:
            log(round_no, "counteroffer", c.proposer, f"{c.id}: {c.summary}", counteroffer=c.summary)
        await emit()

        # 5. Every agent votes on every candidate, independently.
        ballots = await asyncio.gather(*(a.vote_on(current, candidates) for a in agents))
        tally: dict[str, list[CandidateVote]] = {c.id: [] for c in candidates}
        for agent, votes in zip(agents, ballots):
            for v in votes:
                tally[v.candidate_id].append(v)
                flag = " [Vote overridden to reject: score below this agent's minimum.]" if v.overridden else ""
                log(
                    round_no,
                    "vote",
                    agent.id,
                    f"On {v.candidate_id}: {v.reason}{flag}",
                    vote=v.vote,
                    scores={**v.ratings, "score": v.score},
                )

        # 6. Fixed rule picks what happens next.
        chosen, consensus, why = choose(candidates, tally, {a.id: a.threshold for a in agents})
        log(round_no, "decision", "protocol", why)
        if chosen is None:
            break
        current = chosen.plan
        current.negotiation = history
        await emit()
        if consensus:
            return finalize(current, True, rounds_used, last_reviews)

    current.negotiation = history
    return finalize(current, False, rounds_used, last_reviews)


if __name__ == "__main__":
    # Full negotiation on the sample idea, printed live (up to 18 LLM calls).
    import json
    from pathlib import Path

    from llm import stats

    root = Path(__file__).resolve().parent
    plan = Plan.model_validate_json((root.parent / "shared" / "sample_plan.json").read_text())
    for node in plan.nodes:
        if node.id != "root":
            node.status, node.objections, node.supported_by = "proposed", [], []
    plan.negotiation, plan.events, plan.questions = [], [], []

    printed = 0

    async def show(p: Plan) -> None:
        global printed
        for m in p.negotiation[printed:]:
            vote = f" [{m.vote}]" if m.vote else ""
            print(f"r{m.round} {m.phase:<12} {m.agent:<10}{vote} {m.message[:240]}")
        printed = len(p.negotiation)

    async def main() -> Plan:
        final = await negotiate(plan, on_update=show)
        await show(final)
        return final

    final = asyncio.run(main())
    print("\nNodes:")
    for n in final.nodes:
        print(f"  {n.status:<9} {n.label}  ({n.estimate_hours or 0}h, ${n.cost_usd or 0})")
    print(f"\nOutcome: {final.outcome.status}: {final.outcome.summary}")
    for v in final.outcome.hard_constraint_violations:
        print(f"  violation: {v}")
    for q in final.questions:
        print(f"  ask user: {q.question}\n    why: {q.why}")
    m = metrics(final)
    print(
        f"Planned {m['planned_hours']:.0f}h of {m['capacity_hours']:.0f}h, "
        f"${m['planned_cost_usd']:.0f} of ${final.constraints.budget_usd}. {stats}"
    )

    out = root / "runs" / "latest.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(final.model_dump_json(indent=2))
    print(f"Saved {out.relative_to(root.parent)}")
