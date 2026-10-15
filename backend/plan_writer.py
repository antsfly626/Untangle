"""Plan writer: turns the agents' decisions into a buildable plan.

Runs after every negotiation. It is a scribe, not a decision-maker: it elaborates
only what the agents agreed (and the user locked), and code enforces that:

- it never changes an existing node; it only appends risk/edge-case nodes under
  agreed features
- tasks exist only for active (agreed) nodes, and each feature's task hours are
  scaled to its negotiated estimate, so the plan stays within capacity
- task dependencies may only point to earlier tasks, so there are no cycles
- edge-case mitigations live inside the feature's tasks: no hidden extra hours
- a rerun (after renegotiation) replaces its previous output
"""

from __future__ import annotations

import json
from typing import Optional

from agents import plan_brief
from constraints import similar_labels
from llm import complete_json
from schemas import Component, FlowStep, Node, Plan, Task

WRITER = "plan_writer"
DEFAULT_TASK_HOURS = 4.0

SYSTEM = """\
You are the plan writer for Untangle. A team of agents has already negotiated WHAT to build. \
Your job is HOW: turn their agreed decisions into a buildable plan. You do not decide scope. \
Only elaborate nodes listed in "agreed_nodes"; never add new features.

Respond with only a JSON object:
{
  "architecture": [{"id": "a1", "name": "...", "tech": "...", "purpose": "...",
                    "depends_on": ["a2"], "node_ids": ["agreed node ids it serves"]}],
  "flow": [{"actor": "who (e.g. organizer, student, system)", "action": "...", "node_id": "agreed node id or null"}],
  "edge_cases": [{"node_id": "agreed node id", "label": "short name of what can go wrong",
                  "mitigation": "how the plan handles it (built inside that node's tasks)"}],
  "tasks": [{"id": "t1", "title": "...", "node_id": "agreed node id",
             "estimate_hours": number, "depends_on": ["earlier task ids"]}]
}
Rules:
- Architecture: the few components needed (3-8), concrete tech that fits the budget.
- Flow: the main user journey in order, 4-10 steps.
- Edge cases: 1-3 per important node: realistic failures, abuse, safety, and data problems.
- Tasks: concrete and buildable, 2-16 hours each, covering every agreed node that needs work, \
including the edge-case mitigations. A node's tasks should add up to about its estimate_hours. \
List tasks in build order; depends_on may only reference tasks listed earlier."""


def _num(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def clear_previous(plan: Plan) -> Plan:
    """Remove an earlier writer pass so a renegotiated plan is rewritten from scratch."""
    out = plan.model_copy(deep=True)
    out.nodes = [n for n in out.nodes if n.proposed_by != WRITER]
    out.architecture, out.flow, out.tasks, out.plan_notes = [], [], [], []
    return out


def _agreed(plan: Plan) -> dict[str, Node]:
    return {
        n.id: n
        for n in plan.nodes
        if n.id != "root" and n.status == "agreed" and n.type != "question" and n.proposed_by != WRITER
    }


def apply_writer_output(plan: Plan, raw: dict) -> Plan:
    """Validate the writer's JSON and attach it to the plan. Pure: no LLM calls."""
    out = clear_previous(plan)
    agreed = _agreed(out)

    # Architecture: keep components with a name; drop links to unknown ids.
    comps = [c for c in raw.get("architecture") or [] if isinstance(c, dict) and c.get("name")]
    comp_ids = {str(c.get("id") or f"a{i}") for i, c in enumerate(comps, 1)}
    for i, c in enumerate(comps, 1):
        out.architecture.append(
            Component(
                id=str(c.get("id") or f"a{i}"),
                name=str(c["name"]),
                tech=str(c.get("tech") or ""),
                purpose=str(c.get("purpose") or ""),
                status="agreed",
                depends_on=[str(d) for d in c.get("depends_on") or [] if str(d) in comp_ids],
                node_ids=[str(n) for n in c.get("node_ids") or [] if str(n) in agreed],
            )
        )

    # Flow: ordered steps; a step may only point at an agreed node.
    steps = [s for s in raw.get("flow") or [] if isinstance(s, dict) and s.get("action")]
    for i, s in enumerate(steps, 1):
        node_id = s.get("node_id") if s.get("node_id") in agreed else None
        out.flow.append(FlowStep(step=i, actor=str(s.get("actor") or "user"), action=str(s["action"]), node_id=node_id))

    # Edge cases: risk nodes under agreed nodes, no extra hours, no duplicates.
    for i, e in enumerate(raw.get("edge_cases") or [], 1):
        if not isinstance(e, dict) or e.get("node_id") not in agreed or not e.get("label"):
            continue
        label = str(e["label"])
        if any(similar_labels(label, n.label) for n in out.nodes):
            continue
        out.nodes.append(
            Node(
                id=f"edge-{i}",
                label=label,
                type="risk",
                status="agreed",
                parent=e["node_id"],
                proposed_by=WRITER,
                rationale=f"Edge case. Mitigation: {e.get('mitigation') or 'not specified'}",
            )
        )

    # Tasks: only for agreed nodes; dependencies only on earlier tasks (acyclic).
    seen: set[str] = set()
    for i, t in enumerate(raw.get("tasks") or [], 1):
        if not isinstance(t, dict) or t.get("node_id") not in agreed or not t.get("title"):
            continue
        task_id = str(t.get("id") or f"t{i}")
        if task_id in seen:
            task_id = f"t{i}"
        hours = _num(t.get("estimate_hours"))
        out.tasks.append(
            Task(
                id=task_id,
                title=str(t["title"]),
                node_id=t["node_id"],
                estimate_hours=hours if hours and hours > 0 else DEFAULT_TASK_HOURS,
                depends_on=[str(d) for d in t.get("depends_on") or [] if str(d) in seen],
            )
        )
        seen.add(task_id)

    # Each node's tasks add up to its negotiated estimate: the agents set the budget.
    for node_id, node in agreed.items():
        tasks = [t for t in out.tasks if t.node_id == node_id]
        total = sum(t.estimate_hours for t in tasks)
        if tasks and node.estimate_hours and total:
            scale = node.estimate_hours / total
            for t in tasks:
                t.estimate_hours = round(t.estimate_hours * scale, 1)

    missing = [n.label for n in agreed.values() if (n.estimate_hours or 0) > 0 and not any(t.node_id == n.id for t in out.tasks)]
    if missing:
        out.plan_notes.append(f"No tasks written for: {', '.join(missing)}")
    return out


async def write_plan(plan: Plan) -> Plan:
    """Elaborate the agreed plan. On failure, return the plan unchanged with a note."""
    base = clear_previous(plan)
    agreed = _agreed(base)
    if not agreed:
        base.plan_notes.append("Nothing agreed yet, so there is no build plan to write.")
        return base
    brief = plan_brief(base)
    context = {
        "idea": base.idea,
        "constraints": brief["constraints"],
        "measured": brief["measured"],
        "agreed_nodes": [
            {k: v for k, v in n.model_dump(include={"id", "label", "type", "rationale", "estimate_hours", "cost_usd", "parent"}).items() if v not in (None, "")}
            for n in agreed.values()
        ],
        "deferred_or_disputed": [n.label for n in base.nodes if n.status in ("deferred", "rejected", "disputed")],
        "outcome": base.outcome.summary,
    }
    try:
        raw = await complete_json(SYSTEM, json.dumps(context, indent=1))
    except Exception as err:
        base.plan_notes.append(f"Plan writer unavailable ({type(err).__name__}); the negotiated decisions still stand.")
        return base
    return apply_writer_output(base, raw)


if __name__ == "__main__":
    # Write a build plan for the last negotiation (backend/runs/latest.json): 1 LLM call.
    import asyncio
    from pathlib import Path

    path = Path(__file__).resolve().parent / "runs" / "latest.json"
    written = asyncio.run(write_plan(Plan.model_validate_json(path.read_text())))
    print("Architecture:")
    for c in written.architecture:
        print(f"  {c.id} {c.name} ({c.tech}): {c.purpose}  depends on {c.depends_on or '-'}")
    print("\nUser flow:")
    for s in written.flow:
        print(f"  {s.step}. {s.actor}: {s.action}")
    print("\nEdge cases:")
    labels = {n.id: n.label for n in written.nodes}
    for n in written.nodes:
        if n.proposed_by == WRITER:
            print(f"  [{labels.get(n.parent, n.parent)}] {n.label}: {n.rationale}")
    print("\nTasks:")
    for t in written.tasks:
        print(f"  {t.id} {t.title} ({t.estimate_hours:g}h) for {labels.get(t.node_id)}  after {t.depends_on or '-'}")
    print(f"\nTotal task hours: {sum(t.estimate_hours for t in written.tasks):g}")
    for note in written.plan_notes:
        print(f"Note: {note}")
    out = path.with_name("latest_written.json")
    out.write_text(written.model_dump_json(indent=2))
    print(f"Saved {out.relative_to(path.parent.parent)}")
