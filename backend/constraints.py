"""Hard constraints, checked in code. Agents can vote however they like, but a plan
that fails these checks can never be accepted."""

from __future__ import annotations

from schemas import Plan

# Fraction of scheduled hours that turns into planned work
# (meetings, bugs, and life eat the rest).
FOCUS_FACTOR = 0.7

INACTIVE = {"deferred", "rejected"}


def capacity_hours(plan: Plan) -> float:
    c = plan.constraints
    return c.team_size * c.timeline_weeks * c.hours_per_week * FOCUS_FACTOR


def planned_hours(plan: Plan) -> float:
    return sum(n.estimate_hours or 0 for n in plan.nodes if n.status not in INACTIVE)


def planned_cost(plan: Plan) -> float:
    return sum(n.cost_usd or 0 for n in plan.nodes if n.status not in INACTIVE)


def _covers(must: str, active_labels: str) -> bool:
    return any(word in active_labels for word in must.lower().split() if len(word) > 3)


def metrics(plan: Plan) -> dict[str, float]:
    """Facts measured in code. Agents' scores are partly built from these, so
    they can't be argued away in negotiation."""
    active_labels = " | ".join(n.label.lower() for n in plan.nodes if n.status not in INACTIVE)
    musts = plan.constraints.must_have
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
        "must_have_coverage": sum(_covers(m, active_labels) for m in musts) / len(musts) if musts else 1.0,
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
    active_labels = " | ".join(n.label.lower() for n in plan.nodes if n.status not in INACTIVE)
    for must in plan.constraints.must_have:
        if not _covers(must, active_labels):
            found.append(f"must_have missing: {must}")
    return found
