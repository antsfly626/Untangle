"""Plan writer rules enforced in code (no LLM calls)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key, value in (("LLM_MODEL", "test"), ("LLM_BASE_URL", "https://example.invalid"), ("LLM_API_KEY", "test")):
    os.environ.setdefault(key, value)

from plan_writer import WRITER, apply_writer_output, clear_previous  # noqa: E402
from schemas import Constraints, Node, Plan  # noqa: E402


def make_plan() -> Plan:
    return Plan(
        session_id="w",
        idea="Campus food sharing",
        constraints=Constraints(team_size=1, timeline_weeks=8, weekly_hours=10),
        nodes=[
            Node(id="root", label="App", type="idea", status="agreed", proposed_by="user"),
            Node(id="post", label="Post leftover food", type="feature", status="agreed", proposed_by="user", estimate_hours=12),
            Node(id="map", label="Live map", type="feature", status="deferred", proposed_by="user", estimate_hours=30),
        ],
    )


RAW = {
    "architecture": [
        {"id": "a1", "name": "Web app", "tech": "Next.js", "purpose": "UI", "depends_on": ["a2", "ghost"], "node_ids": ["post", "map"]},
        {"id": "a2", "name": "API", "tech": "FastAPI", "purpose": "Posts"},
    ],
    "flow": [
        {"actor": "organizer", "action": "Posts food", "node_id": "post"},
        {"actor": "student", "action": "Opens the map", "node_id": "map"},
    ],
    "edge_cases": [
        {"node_id": "post", "label": "Food posted after it has gone bad", "mitigation": "Pickup window limit"},
        {"node_id": "map", "label": "GPS is wrong indoors", "mitigation": "Use room names"},
    ],
    "tasks": [
        {"id": "t1", "title": "Post form", "node_id": "post", "estimate_hours": 4, "depends_on": ["t2"]},
        {"id": "t2", "title": "Expiry rules", "node_id": "post", "estimate_hours": 4, "depends_on": ["t1"]},
        {"id": "t3", "title": "Map tiles", "node_id": "map", "estimate_hours": 10},
    ],
}


def test_never_changes_existing_nodes():
    plan = make_plan()
    out = apply_writer_output(plan, RAW)
    assert [(n.id, n.status) for n in out.nodes[:3]] == [(n.id, n.status) for n in plan.nodes]


def test_edge_cases_only_under_agreed_nodes():
    out = apply_writer_output(make_plan(), RAW)
    edges = [n for n in out.nodes if n.proposed_by == WRITER]
    assert [(n.parent, n.type) for n in edges] == [("post", "risk")]  # the deferred map's edge case is dropped
    assert "Pickup window limit" in edges[0].rationale


def test_tasks_only_for_agreed_nodes_and_scaled_to_negotiated_estimate():
    out = apply_writer_output(make_plan(), RAW)
    assert {t.node_id for t in out.tasks} == {"post"}
    assert sum(t.estimate_hours for t in out.tasks) == 12  # 4h + 4h scaled to the agreed 12h


def test_dependencies_only_point_backwards():
    out = apply_writer_output(make_plan(), RAW)
    deps = {t.id: t.depends_on for t in out.tasks}
    assert deps == {"t1": [], "t2": ["t1"]}  # t1 -> t2 (forward) was dropped, so no cycle


def test_links_to_unknown_or_unagreed_ids_are_dropped():
    out = apply_writer_output(make_plan(), RAW)
    web = out.architecture[0]
    assert web.depends_on == ["a2"] and web.node_ids == ["post"]
    assert [s.node_id for s in out.flow] == ["post", None]


def test_rerun_replaces_previous_output():
    once = apply_writer_output(make_plan(), RAW)
    twice = apply_writer_output(once, RAW)
    assert len([n for n in twice.nodes if n.proposed_by == WRITER]) == 1
    assert len(twice.tasks) == len(once.tasks)
    cleared = clear_previous(twice)
    assert not cleared.tasks and not cleared.architecture and not cleared.flow


def test_notes_agreed_work_without_tasks():
    out = apply_writer_output(make_plan(), {"tasks": []})
    assert out.plan_notes == ["No tasks written for: Post leftover food"]
