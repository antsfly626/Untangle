"""The intake extractor turns explicit parent labels into plan node IDs."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_extract_resolves_parent_labels_and_keeps_independent_topics_at_root(monkeypatch):
    import intake

    async def complete_json(system, _prompt):
        assert '"parent": "label of a broader node from this response, or root"' in system
        return {
            "idea": "A project workspace",
            "constraints": {},
            "nodes": [
                {"label": "Team collaboration", "type": "feature", "parent": "root"},
                {"label": "Comments", "type": "feature", "parent": "Team collaboration"},
                {"label": "Mentions", "type": "feature", "parent": "Team collaboration"},
                {"label": "Private workspaces", "type": "constraint", "parent": "root"},
                {"label": "Unknown parent child", "type": "feature", "parent": "Missing group"},
            ],
        }

    monkeypatch.setattr(intake, "complete_json", complete_json)
    plan = asyncio.run(intake.extract("A collaborative project workspace"))
    nodes = {node.label: node for node in plan.nodes}
    assert nodes["Comments"].parent == nodes["Team collaboration"].id
    assert nodes["Mentions"].parent == nodes["Team collaboration"].id
    assert nodes["Team collaboration"].parent == "root"
    assert nodes["Private workspaces"].parent == "root"
    assert nodes["Unknown parent child"].parent == "root"


def test_user_updates_can_nest_new_nodes_under_a_new_broader_topic(monkeypatch):
    import intake
    from schemas import Constraints, Node, Plan

    async def complete_json(_system, _prompt):
        return {
            "new_nodes": [
                {"label": "Team collaboration", "type": "feature", "parent": "root"},
                {"label": "Comments", "type": "feature", "parent": "u1"},
            ],
            "summary": "Grouped collaboration updates.",
        }

    monkeypatch.setattr(intake, "complete_json", complete_json)
    plan = Plan(
        session_id="hierarchy-test",
        idea="A collaboration tool",
        constraints=Constraints(team_size=2, timeline_weeks=4, weekly_hours=10, budget_usd=0),
        nodes=[Node(id="root", label="Collaboration tool", type="idea", proposed_by="user")],
    )
    updated, _, _ = asyncio.run(intake.update_from_user(plan, "Add comments"))
    nodes = {node.label: node for node in updated.nodes}
    assert nodes["Team collaboration"].id == "u1"
    assert nodes["Comments"].parent == "u1"
