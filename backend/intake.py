"""Intake: brain dump -> plan, questions for the user, and merging everything the
user says afterwards (answers, new ideas, changed limits, decisions).

The extractor and updater are note-takers, not planners: they record only what
the user said and never add ideas. New ideas come from the agents, labelled as
theirs, during negotiation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

from agents import CORE_AGENTS, Agent, plan_brief
from constraints import similar_labels
from llm import complete_json
from schemas import Constraints, Event, Node, Plan, Question

# Used when the user doesn't know; always recorded in constraints.assumed.
DEFAULTS = {"team_size": 1, "timeline_weeks": 12, "weekly_hours": 10, "budget_usd": 0}

LIMIT_QUESTIONS = {
    "team_size": ("How many people are working on this?", "Capacity depends on who's building it."),
    "timeline_weeks": ("When do you want a first version done?", "The plan has to fit your timeline."),
    "weekly_hours": (
        "Roughly how many hours a week can the whole team put in, in total?",
        "A full-time team and a side project need very different plans.",
    ),
    "budget_usd": (
        "What's your cash budget for things like hosting and paid APIs? $0 is fine.",
        "Paid services have to fit the budget.",
    ),
}

MAX_QUESTIONS_PER_TURN = 4
NODE_TYPES = {"feature", "component", "constraint", "risk", "question"}

EXTRACT_SYSTEM = """\
You turn a user's brain dump about a product idea into structured notes. You are a note-taker, \
not a planner: record only what the user said. Never add features, risks, or ideas of your own.

Respond with only a JSON object:
{
  "idea": "one-sentence summary of the idea",
  "constraints": {"team_size": int or null, "timeline_weeks": number or null,
                  "weekly_hours": number or null, "budget_usd": number or null,
                  "avoid": ["..."]},
  "nodes": [{"label": "...", "type": "feature|component|constraint|risk|question",
             "parent": "label of a broader node from this response, or root",
             "rationale": "why the user wants it, in their words",
             "required": true or false,
             "estimate_hours": number or null, "cost_usd": number or null}]
}
Rules:
- Use null for any limit the user did not state. Convert durations to weeks (1 month = 4.3 weeks).
- weekly_hours is the TOTAL for the whole team per week (e.g. "two of us, 12h each" = 24; \
"me 12h and a friend 6h" = 18).
- Nodes are the specific features, requirements, and risks the user mentioned. The idea as a \
whole is NOT a node (it's the summary). Team size, schedule, budget, and the user's own \
uncertainties ("not sure how long it takes") are NOT nodes. It's fine to return no nodes.
- One node per distinct thing: merge items that mean the same (e.g. "notifications" and \
"push notifications" are one node).
- Group directly related nodes under a broader node when the user described a clear parent-child \
relationship (for example, put specific collaboration tools under "Team collaboration"). Emit \
the broader node before its children and set each child's parent to that broader node's exact \
label. Use "root" for independent nodes or when no existing node is a genuine broader topic. \
Parent links only organize what the user described; do not invent features to act as categories.
- required: true only for things the user said are essential ("must", "need", "has to"). \
Requirements like "must be free" become a constraint node with required: true.
- estimate_hours: rough person-hours to build a solid first version. cost_usd: cash cost over the \
timeline (paid APIs, hosting, licenses), 0 if free. null for things that aren't built.
- avoid: things the user said to avoid."""

QUESTION_PROMPT = """\
Before negotiation starts: what information is missing that most affects YOUR objective?
Ask at most 2 short questions the user can answer quickly. Do not ask about team size, timeline, \
weekly hours, or budget (asked separately). Do not ask about things already answered.

Current plan:
{brief}

Respond with only a JSON object:
{{"questions": [{{"question": "...", "why": "one sentence", "priority": 1-5}}]}}"""

UPDATE_SYSTEM = """\
You update a project plan with what the user just said. You are a note-taker: record only what \
the user said, never invent.

Respond with only a JSON object (omit keys or use empty lists when nothing applies):
{
  "answers": [{"question_id": "...", "answer": "short paraphrase"}],
  "constraints": {"team_size": ..., "timeline_weeks": ..., "weekly_hours": ..., "budget_usd": ...},
  "dont_know": ["team_size|timeline_weeks|weekly_hours|budget_usd"],
  "require": ["id of an existing node the user now says is essential"],
  "add_avoid": ["..."],
  "new_nodes": [{"label": "...", "type": "feature|component|constraint|risk|question",
                 "rationale": "...", "required": true or false,
                 "estimate_hours": number or null, "cost_usd": number or null,
                 "parent": "node id or root"}],
  "decisions": [{"node_id": "...", "status": "agreed|deferred|rejected"}],
  "summary": "one sentence describing what changed"
}
Rules:
- "constraints": only limits the user gave or changed, as NEW TOTALS computed from the current \
values. weekly_hours is the whole team's total. A teammate joining at 6h/week: team_size + 1, \
weekly_hours + 6. A teammate leaving: team_size - 1 and weekly_hours minus their hours (if unknown, \
minus the current per-person average). Durations in weeks (1 month = 4.3 weeks).
- Team size, schedule, budget, and the user's own uncertainties are never nodes.
- "new_nodes": only genuinely new ideas, not things already in the plan. If the user says an \
existing node is essential, put its id in "require" instead of adding a node. \
Requirements like "must be safe" become a constraint node with required: true.
- "dont_know": limits the user explicitly said they don't know.
- "decisions": only when the user clearly decides about a specific existing node \
(e.g. "keep the map" -> agreed, "skip chat" -> deferred)."""


def _num(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _valid_limit(key: str, value: Optional[float]) -> bool:
    if value is None:
        return False
    return value >= 0 if key == "budget_usd" else value > 0


def _node_from(raw: object, node_id: str, parent: str = "root") -> Optional[Node]:
    if not isinstance(raw, dict) or not raw.get("label"):
        return None
    hours, cost = _num(raw.get("estimate_hours")), _num(raw.get("cost_usd"))
    return Node(
        id=node_id,
        label=str(raw["label"]),
        type=raw.get("type") if raw.get("type") in NODE_TYPES else "feature",
        parent=parent,
        proposed_by="user",
        rationale=str(raw.get("rationale") or ""),
        required=raw.get("required") is True,
        estimate_hours=hours,
        cost_usd=cost,
        original_estimate_hours=hours,
        original_cost_usd=cost,
    )


def _next_user_id(plan: Plan) -> str:
    taken = {n.id for n in plan.nodes}
    i = 1
    while f"u{i}" in taken:
        i += 1
    return f"u{i}"


def _add_unique(plan: Plan, node: Optional[Node]) -> bool:
    """Append unless an existing node already names the same thing."""
    if node is None or any(similar_labels(node.label, n.label) for n in plan.nodes):
        return False
    plan.nodes.append(node)
    return True


# ---------- 1. brain dump -> plan ----------


async def extract(text: str) -> Plan:
    raw = await complete_json(EXTRACT_SYSTEM, text)
    found = raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {}

    limits, assumed = {}, []
    for key, default in DEFAULTS.items():
        value = _num(found.get(key))
        if _valid_limit(key, value):
            limits[key] = max(1, round(value)) if key == "team_size" else value
        else:
            limits[key] = default
            assumed.append(key)

    constraints = Constraints(
        **limits,
        avoid=[str(a) for a in found.get("avoid") or []],
        assumed=assumed,
    )
    summary = str(raw.get("idea") or text[:80])
    plan = Plan(
        session_id=uuid.uuid4().hex[:8],
        idea=text,
        constraints=constraints,
        nodes=[Node(id="root", label=summary, type="idea", status="agreed", proposed_by="user")],
    )
    by_label: dict[str, Optional[str]] = {}
    for item in raw.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        parent_label = item.get("parent")
        parent = "root"
        if isinstance(parent_label, str) and parent_label.strip().casefold() != "root":
            parent = by_label.get(parent_label.strip().casefold()) or "root"
        node = _node_from(item, _next_user_id(plan), parent)
        if _add_unique(plan, node) and node is not None:
            normalized = label.casefold()
            if normalized in by_label:
                by_label[normalized] = None
            else:
                by_label[normalized] = node.id
    return plan


# ---------- 2. what to ask ----------


async def _agent_questions(agent: Agent, plan: Plan) -> list[dict]:
    prompt = QUESTION_PROMPT.format(brief=json.dumps(plan_brief(plan), indent=1))
    try:
        raw = await complete_json(agent.system_prompt(), prompt)
    except Exception:
        return []  # a failed question call shouldn't block intake
    out = []
    for q in (raw.get("questions") or [])[:2]:
        if isinstance(q, dict) and q.get("question"):
            priority = _num(q.get("priority")) or 3
            out.append(
                {
                    "agent": agent.id,
                    "question": str(q["question"]),
                    "why": str(q.get("why") or ""),
                    "priority": int(min(5, max(1, priority))),
                }
            )
    return out


async def ask(plan: Plan, agents: list[Agent] = CORE_AGENTS) -> tuple[Plan, list[Question]]:
    """Add the next batch of questions: missing limits first (decided by code),
    then the agents' highest-priority questions, at most MAX_QUESTIONS_PER_TURN."""
    plan = plan.model_copy(deep=True)
    existing = {q.id for q in plan.questions}
    new: list[Question] = []

    for key in plan.constraints.assumed:
        qid = f"limit-{key}"
        if qid not in existing:
            question, why = LIMIT_QUESTIONS[key]
            new.append(Question(id=qid, asked_by="intake", question=question, why=why, priority=5))

    proposals = [q for batch in await asyncio.gather(*(_agent_questions(a, plan) for a in agents)) for q in batch]
    proposals.sort(key=lambda q: -q["priority"])
    asked_texts = [q.question for q in plan.questions] + [q.question for q in new]
    for p in proposals:
        if len(new) >= MAX_QUESTIONS_PER_TURN:
            break
        if any(similar_labels(p["question"], t) for t in asked_texts):
            continue
        n = sum(q.asked_by == p["agent"] for q in plan.questions + new) + 1
        new.append(
            Question(id=f"q-{p['agent']}-{n}", asked_by=p["agent"], question=p["question"], why=p["why"], priority=p["priority"])
        )
        asked_texts.append(p["question"])

    plan.questions.extend(new)
    return plan, new


def open_questions(plan: Plan) -> list[Question]:
    return [q for q in plan.questions if q.answer is None]


def ready_to_negotiate(plan: Plan) -> bool:
    """Only the limits block negotiation; agent questions are optional."""
    return not any(q.id.startswith("limit-") and q.answer is None for q in plan.questions)


def skip_open_questions(plan: Plan) -> Plan:
    """User chose to go ahead: open limit questions fall back to the labelled defaults."""
    plan = plan.model_copy(deep=True)
    for q in open_questions(plan):
        key = q.id.removeprefix("limit-")
        q.answer = f"Skipped: assuming {DEFAULTS[key]}" if key in DEFAULTS else "Skipped"
    return plan


# ---------- 3. everything the user says afterwards ----------


async def update_from_user(plan: Plan, text: str) -> tuple[Plan, str, bool]:
    """Merge a user message into the plan. Returns (plan, summary, renegotiate).
    After a negotiation, changed limits or new ideas reopen the plan as an event."""
    plan = plan.model_copy(deep=True)
    negotiated = bool(plan.negotiation)
    context = {
        "plan": plan_brief(plan),
        "assumed_limits": plan.constraints.assumed,
        "open_questions": [{"question_id": q.id, "question": q.question} for q in open_questions(plan)],
        "user_message": text,
    }
    raw = await complete_json(UPDATE_SYSTEM, json.dumps(context, indent=1))

    questions = {q.id: q for q in plan.questions}
    for a in raw.get("answers") or []:
        if isinstance(a, dict) and a.get("question_id") in questions:
            questions[a["question_id"]].answer = str(a.get("answer") or text)

    c = plan.constraints
    changes = []
    given = raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {}
    for key in DEFAULTS:
        value = _num(given.get(key))
        if not _valid_limit(key, value):
            continue
        value = max(1, round(value)) if key == "team_size" else value
        old = getattr(c, key)
        if key in c.assumed:
            c.assumed.remove(key)
        if value != old:
            changes.append(f"{key} {old if old is None else f'{old:g}'} -> {value:g}")
            setattr(c, key, value)
        q = questions.get(f"limit-{key}")
        if q and q.answer is None:
            q.answer = f"{value:g}"
    for key in raw.get("dont_know") or []:
        q = questions.get(f"limit-{key}")
        if key in DEFAULTS and q and q.answer is None:
            q.answer = f"Don't know: assuming {DEFAULTS[key]}"

    c.avoid += [str(a) for a in raw.get("add_avoid") or [] if str(a) not in c.avoid]

    ids = {n.id for n in plan.nodes}
    added = []
    for item in raw.get("new_nodes") or []:
        parent = item.get("parent") if isinstance(item, dict) and item.get("parent") in ids else "root"
        node = _node_from(item, _next_user_id(plan), parent)
        if _add_unique(plan, node):
            added.append(node.label)
            ids.add(node.id)

    nodes = {n.id: n for n in plan.nodes}
    new_musts = []
    for node_id in raw.get("require") or []:
        node = nodes.get(node_id) if isinstance(node_id, str) else None
        if node and node.id != "root" and not node.required:
            node.required = True
            new_musts.append(node.label)
    new_musts += [n.label for n in plan.nodes if n.required and n.label in added]
    decided = []
    for d in raw.get("decisions") or []:
        node = nodes.get(d.get("node_id")) if isinstance(d, dict) else None
        if node and node.id != "root" and node.type != "question" and d.get("status") in ("agreed", "deferred", "rejected"):
            node.status, node.locked = d["status"], True
            if d["status"] != "agreed":
                node.required = False  # the user dropped their own must-have
            decided.append(node.id)
            for q in plan.questions:  # the escalation questions about this node are now answered
                if q.answer is None and q.id in (f"decide-{node.id}", f"confirm-{node.id}"):
                    q.answer = f"User decided: {d['status']}"

    renegotiate = negotiated and bool(changes or added or decided or new_musts)
    if negotiated and (changes or added or new_musts):
        # New information: reopen everything the user hasn't locked.
        reopened = []
        for n in plan.nodes:
            if n.id != "root" and n.type != "question" and not n.locked and n.status != "proposed":
                n.status = "proposed"
                reopened.append(n.id)
        kind = (
            "deadline_change"
            if any(ch.startswith("timeline_weeks") for ch in changes)
            else "constraint_change" if changes else "user_input"
        )
        plan.events.append(
            Event(
                id=f"e{len(plan.events) + 1}",
                kind=kind,
                description="; ".join(
                    changes + [f"user added: {label}" for label in added] + [f"new must-have: {m}" for m in new_musts]
                ),
                at_round=max((m.round for m in plan.negotiation), default=0),
                reopened_node_ids=reopened,
            )
        )
    return plan, str(raw.get("summary") or "Updated the plan."), renegotiate


# ---------- terminal chat for testing ----------

if __name__ == "__main__":
    from constraints import metrics
    from llm import stats
    from negotiation import negotiate
    from plan_writer import write_plan

    printed = 0

    async def show_debate(p: Plan) -> None:
        global printed
        for m in p.negotiation[printed:]:
            vote = f" [{m.vote}]" if m.vote else ""
            print(f"  r{m.round} {m.phase:<12} {m.agent:<10}{vote} {m.message[:200]}")
        printed = len(p.negotiation)

    def show_plan(p: Plan) -> None:
        c = p.constraints
        assumed = f"  (assumed: {', '.join(c.assumed)})" if c.assumed else ""
        print(
            f"\nLimits: {c.team_size} people, {c.timeline_weeks:g} weeks, {c.weekly_hours:g}h/week total, "
            f"${c.budget_usd or 0:g}{assumed}"
        )
        for n in p.nodes[1:]:
            lock = (" [required]" if n.required else "") + (" [locked]" if n.locked else "")
            was = (
                f", was {n.original_estimate_hours:g}h"
                if n.original_estimate_hours is not None and n.original_estimate_hours != n.estimate_hours
                else ""
            )
            print(f"  {n.status:<9} {n.label} ({n.estimate_hours or 0:g}h{was}, ${n.cost_usd or 0:g}) by {n.proposed_by}{lock}")
        m = metrics(p)
        print(f"  -> {m['planned_hours']:.0f}h of {m['capacity_hours']:.0f}h, ${m['planned_cost_usd']:.0f}")

    def show_build_plan(p: Plan) -> None:
        labels = {n.id: n.label for n in p.nodes}
        if p.architecture:
            print("\nArchitecture:")
            for c in p.architecture:
                print(f"  {c.name} ({c.tech}): {c.purpose}")
        if p.flow:
            print("\nUser flow:")
            for s in p.flow:
                print(f"  {s.step}. {s.actor}: {s.action}")
        if p.tasks:
            print("\nTasks:")
            for t in p.tasks:
                print(f"  {t.title} ({t.estimate_hours:g}h) for {labels.get(t.node_id, t.node_id)}")
        for note in p.plan_notes:
            print(f"Note: {note}")

    def show_questions(p: Plan) -> None:
        qs = [q for q in open_questions(p)]
        if qs:
            print("\nQuestions:")
            for q in qs:
                print(f"  [{q.asked_by}] {q.question}")

    async def main() -> None:
        while True:
            dump = await asyncio.to_thread(input, "Brain dump> ")
            try:
                plan = await extract(dump)
                plan, _ = await ask(plan)
                break
            except Exception as err:
                print(f"Couldn't read that ({type(err).__name__}: {err}). Try again.")
        show_plan(plan)
        pending = True
        while True:
            if pending and ready_to_negotiate(plan):
                print("\nNegotiating...")
                try:
                    plan = await negotiate(plan, on_update=show_debate)
                    await show_debate(plan)
                    print(f"\nOutcome: {plan.outcome.status}: {plan.outcome.summary}")
                    print("\nWriting the build plan...")
                    plan = await write_plan(plan)
                    show_plan(plan)
                    show_build_plan(plan)
                    pending = False
                except Exception as err:  # stays pending: retried after the next message
                    print(f"Negotiation failed ({type(err).__name__}: {err}). Send any message to retry.")
            show_questions(plan)
            msg = (await asyncio.to_thread(input, "\nYou (or 'go' / 'quit')> ")).strip()
            if msg.lower() in ("quit", "exit"):
                break
            if not msg:
                continue
            if msg.lower() == "go":
                plan = skip_open_questions(plan)
                continue
            try:
                plan, summary, renegotiate = await update_from_user(plan, msg)
            except Exception as err:
                print(f"Couldn't process that ({type(err).__name__}: {err}). Try again.")
                continue
            print(f"\nUpdated: {summary}")
            if renegotiate or not plan.negotiation:
                pending = True
            if plan.events and renegotiate:
                print(f"Event: {plan.events[-1].kind}: {plan.events[-1].description}")
        print(stats)

    asyncio.run(main())
