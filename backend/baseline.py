"""Single-LLM baseline: one call plans everything, with none of the protocol's safeguards.

It sees exactly what the agents see (the extracted plan) and returns a finished plan.
Nothing protects it: it can drop must-haves, overspend, or slash estimates, and the
comparison in compare.py measures whether it does.
"""

from __future__ import annotations

import json

from agents import plan_brief
from llm import complete_json
from schemas import Node, Outcome, Plan

SYSTEM = """\
You are an expert product planner. Turn the user's idea into a realistic plan that balances \
technical quality, delivery within the team's limits, and real user value and safety.

Respond with only a JSON object:
{
  "analysis": "your reasoning, 2-5 sentences",
  "changes": [{"node_id": "...", "status": "agreed|deferred|rejected"}],
  "new_nodes": [{"label": "...", "type": "feature|component|constraint|risk|question",
                 "rationale": "...", "estimate_hours": 0, "cost_usd": 0}],
  "estimate_updates": [{"node_id": "...", "estimate_hours": 0, "cost_usd": 0}],
  "summary": "one sentence"
}
Give every existing node a status in "changes". Estimates are person-hours; costs are cash in USD."""


async def single_agent_plan(plan: Plan) -> Plan:
    raw = await complete_json(SYSTEM, f"Plan:\n{json.dumps(plan_brief(plan), indent=1)}")
    out = plan.model_copy(deep=True)
    nodes = {n.id: n for n in out.nodes}

    for change in raw.get("changes") or []:
        node = nodes.get(change.get("node_id")) if isinstance(change, dict) else None
        if node and node.id != "root" and change.get("status") in ("agreed", "deferred", "rejected"):
            node.status = change["status"]
    for update in raw.get("estimate_updates") or []:
        node = nodes.get(update.get("node_id")) if isinstance(update, dict) else None
        if not node:
            continue
        for key in ("estimate_hours", "cost_usd"):
            try:
                if update.get(key) is not None:
                    setattr(node, key, max(0.0, float(update[key])))
            except (TypeError, ValueError):
                pass
    for i, new in enumerate(raw.get("new_nodes") or [], 1):
        if not isinstance(new, dict) or not new.get("label"):
            continue
        out.nodes.append(
            Node(
                id=f"b-{i}",
                label=str(new["label"]),
                type=new.get("type") if new.get("type") in ("feature", "component", "constraint", "risk", "question") else "feature",
                parent="root",
                status="agreed",
                proposed_by="baseline",
                rationale=str(new.get("rationale") or ""),
                estimate_hours=_num(new.get("estimate_hours")),
                cost_usd=_num(new.get("cost_usd")),
            )
        )
    # Anything it didn't mention, it implicitly kept.
    for node in out.nodes:
        if node.status == "proposed" and node.type != "question":
            node.status = "agreed"
    out.outcome = Outcome(status="consensus", summary=f"Single-LLM baseline: {raw.get('summary', '')}")
    return out


def _num(value: object):
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
