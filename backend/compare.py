"""Evaluation: multi-agent negotiation vs the single-LLM baseline, on the same ideas.

    python compare.py              # 3 ideas, 1 run each (~5-10 min, a few cents)
    python compare.py --repeats 3  # average out LLM randomness

Each idea is extracted once; both approaches start from that same plan. Every metric
is measured in code the same way for both. Results go to shared/baseline_comparison.json.
Agents run in-process here so all LLM calls are counted in this process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from baseline import single_agent_plan
from constraints import INACTIVE, metrics, violations
from intake import extract
from llm import stats
from negotiation import negotiate
from schemas import Plan
from transport import LocalTransport
from evidence.model import stats as evidence_stats

IDEAS = {
    "food-sharing": (
        "An app where campus event organizers post leftover food and students get notified to come "
        "grab it. Two of us, 12h a week each, 8 weeks, $300 budget. Must be free for students and "
        "must have push notifications. Want a live map, SMS alerts, and in-app chat."
    ),
    "study-buddy": (
        "An AI study buddy for college students. Me and a friend, 15 hours a week total, 6 weeks, "
        "$50 budget. Must turn lecture notes into flashcards. Want a voice tutor, multiplayer quiz "
        "battles, and native iOS and Android apps."
    ),
    "bakery-preorders": (
        "A pre-order website for my local bakery. Just me, 8 hours a week, 4 weeks, $100 budget. "
        "Must take online payments. Want loyalty points, SMS pickup reminders, and inventory tracking."
    ),
}

METRICS = [
    ("violations", "Hard-constraint violations", "lower"),
    ("must_haves_dropped", "Must-haves dropped", "lower"),
    ("user_ideas_dropped_silently", "Your ideas dropped without asking", "lower"),
    ("tradeoffs_surfaced", "Tradeoffs surfaced to the user", "higher"),
    ("capacity_use_pct", "Capacity used (%)", "target ~80"),
    ("safeguards_added", "Risks/safeguards added", "higher"),
    ("estimates_slashed", "Estimates cut >50%", "lower"),
    ("llm_calls", "LLM calls", "lower"),
    ("tokens", "Tokens", "lower"),
    ("seconds", "Wall time (s)", "lower"),
]


# A new node counts as a safeguard if it's typed as a risk/constraint, or its label or
# rationale mentions one of these (agents often type safety work as a "component").
SAFEGUARD_WORDS = (
    "safety", "safe ", "allergen", "allergy", "privacy", "private", "security", "secure",
    "moderation", "moderate", "report", "consent", "opt-in", "accessib", "verif", "liability",
    "legal", "compliance", "disclos", "abuse", "permission", "fraud", "data protection",
)


def is_safeguard(node) -> bool:
    text = f"{node.label} {node.rationale}".lower()
    return node.type in ("risk", "constraint") or any(word in text for word in SAFEGUARD_WORDS)


def evaluate(start: Plan, final: Plan) -> dict:
    """Measure a finished plan against the plan it started from, identically for both approaches."""
    before = {n.id: n for n in start.nodes}
    user_nodes = [n for n in final.nodes if n.proposed_by == "user" and n.id != "root"]
    dropped = [n for n in user_nodes if n.status in INACTIVE]
    asked_about = {q.id.partition("-")[2] for q in final.questions if q.asked_by == "protocol"}
    added = [n for n in final.nodes if n.id not in before]
    slashed = [
        n for n in final.nodes
        if n.id in before and before[n.id].estimate_hours and (n.estimate_hours or 0) < 0.5 * before[n.id].estimate_hours
    ]
    return {
        "violations": len(violations(final)),
        "violation_details": violations(final),
        "must_haves_dropped": sum(n.required and n.status in INACTIVE for n in final.nodes),
        "user_ideas_dropped_silently": sum(n.id not in asked_about for n in dropped),
        "dropped_labels": [n.label for n in dropped],
        "tradeoffs_surfaced": sum(q.asked_by == "protocol" and q.answer is None for q in final.questions),
        "capacity_use_pct": round(100 * metrics(final)["capacity_use"]),
        "safeguards_added": sum(is_safeguard(n) for n in added if n.status not in INACTIVE),
        "safeguard_labels": [n.label for n in added if is_safeguard(n) and n.status not in INACTIVE],
        "added": [
            {"label": n.label, "type": n.type, "status": n.status, "by": n.proposed_by, "hours": n.estimate_hours}
            for n in added
        ],
        "estimates_slashed": len(slashed),
        "outcome": final.outcome.status,
    }


async def run_one(name: str, text: str) -> dict:
    start = await extract(text)

    async def timed(coro):
        def totals():
            calls = stats["calls"] + evidence_stats["calls"]
            tokens = sum(source["prompt_tokens"] + source["completion_tokens"]
                         for source in (stats, evidence_stats))
            return calls, tokens

        calls, tokens = totals()
        t0 = time.monotonic()
        plan = await coro
        final_calls, final_tokens = totals()
        return plan, {
            "llm_calls": final_calls - calls,
            "tokens": final_tokens - tokens,
            "seconds": round(time.monotonic() - t0),
        }

    base_plan, base_cost = await timed(single_agent_plan(start))
    agent_plan, agent_cost = await timed(negotiate(start, transport=LocalTransport()))
    return {
        "idea": name,
        "baseline": {**evaluate(start, base_plan), **base_cost},
        "agents": {**evaluate(start, agent_plan), **agent_cost},
    }


def _avg(rows: list[dict], side: str, key: str) -> float:
    return statistics.mean(r[side][key] for r in rows)


def print_table(results: list[dict]) -> None:
    for name in IDEAS:
        rows = [r for r in results if r["idea"] == name]
        print(f"\n== {name} ({len(rows)} run{'s' if len(rows) > 1 else ''}) ==")
        print(f"{'metric':<36}{'baseline':>10}{'agents':>10}   better")
        for key, label, better in METRICS:
            print(f"{label:<36}{_avg(rows, 'baseline', key):>10.1f}{_avg(rows, 'agents', key):>10.1f}   {better}")
        for r in rows:
            for side in ("baseline", "agents"):
                s = r[side]
                print(f"  {side}: {s['outcome']}; dropped {s['dropped_labels'] or 'nothing'}; "
                      f"violations {s['violation_details'] or 'none'}")
                print(f"    safeguards: {s['safeguard_labels'] or 'none'}")
                other = [a["label"] for a in s["added"] if a["label"] not in s["safeguard_labels"]]
                print(f"    other additions: {other or 'none'}")


async def main(repeats: int) -> None:
    results = []
    for _ in range(repeats):
        for name, text in IDEAS.items():
            print(f"running {name}...", flush=True)
            results.append(await run_one(name, text))
    print_table(results)
    out = Path(__file__).resolve().parent.parent / "shared" / "baseline_comparison.json"
    out.write_text(json.dumps({"metrics": METRICS, "results": results}, indent=2) + "\n")
    print(f"\nSaved {out.relative_to(out.parent.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    asyncio.run(main(parser.parse_args().repeats))
