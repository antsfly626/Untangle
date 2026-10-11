"""Hard constraints, checked in code. Agents can vote however they like, but a plan
that fails these checks can never be accepted."""

from __future__ import annotations

import re

from schemas import Plan

# Fraction of scheduled hours that turns into planned work
# (meetings, bugs, and life eat the rest).
FOCUS_FACTOR = 0.7

INACTIVE = {"deferred", "rejected"}


def capacity_hours(plan: Plan) -> float:
    c = plan.constraints
    return c.weekly_hours * c.timeline_weeks * FOCUS_FACTOR


def planned_hours(plan: Plan) -> float:
    return sum(n.estimate_hours or 0 for n in plan.nodes if n.status not in INACTIVE)


def planned_cost(plan: Plan) -> float:
    return sum(n.cost_usd or 0 for n in plan.nodes if n.status not in INACTIVE)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _stem(word: str) -> str:
    # Crude but dependency-free: "posts" -> "post", "notifications"/"notify" -> "notif".
    if len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    return word[:5]


STOPWORDS = {"a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "be", "is", "it", "my", "our"}


def _keywords(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in STOPWORDS}


def similar_labels(a: str, b: str) -> bool:
    """True when two labels name the same thing (word-stem overlap > 60%)."""
    ka, kb = _keywords(a), _keywords(b)
    return bool(ka and kb) and len(ka & kb) / len(ka | kb) > 0.6


def required_nodes(plan: Plan) -> list:
    return [n for n in plan.nodes if n.required]


def metrics(plan: Plan) -> dict[str, float]:
    """Facts measured in code. Agents' scores are partly built from these, so
    they can't be argued away in negotiation."""
    required = required_nodes(plan)
    features = [n for n in plan.nodes if n.type == "feature"]
    planned, capacity = planned_hours(plan), capacity_hours(plan)
    budget = plan.constraints.budget_usd
    cost = planned_cost(plan)
    if budget is None:
        budget_use = 0.0  # no budget given: cost is unconstrained
    elif budget == 0:
        budget_use = 0.0 if cost == 0 else 2.0  # any spend on a $0 budget is far over
    else:
        budget_use = cost / budget
    return {
        "planned_hours": planned,
        "capacity_hours": capacity,
        "capacity_use": planned / capacity if capacity else 0.0,
        # 1.0 once 80% of capacity is planned: unused capacity is value never delivered.
        "utilization": min(1.0, planned / (0.8 * capacity)) if capacity else 0.0,
        "planned_cost_usd": cost,
        "budget_use": budget_use,
        "must_have_coverage": (
            sum(n.status not in INACTIVE for n in required) / len(required) if required else 1.0
        ),
        "active_feature_ratio": (
            sum(n.status not in INACTIVE for n in features) / len(features) if features else 1.0
        ),
    }


def violations(plan: Plan) -> list[str]:
    found = []
    planned, capacity = planned_hours(plan), capacity_hours(plan)
    if planned > capacity:
        found.append(f"capacity: {planned:.0f}h planned > {capacity:.0f}h available")
    budget, cost = plan.constraints.budget_usd, planned_cost(plan)
    if budget is not None and cost > budget:
        found.append(f"budget: ${cost:.0f} planned > ${budget:.0f} available")
    for n in required_nodes(plan):
        if n.status in INACTIVE:
            found.append(f"must-have {n.status}: {n.label}")
    return found
