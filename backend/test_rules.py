"""Checks for the code-only rules (no LLM calls). Run: python test_rules.py"""

from agents import CandidateVote, Counteroffer, EstimateUpdate, NewNode
from constraints import capacity_hours, similar_labels, violations
from negotiation import (
    RESOLVED,
    apply_counteroffer,
    choose,
    close_stale_questions,
    nash_product,
    pick_improvement,
    relax_options,
)
from schemas import Constraints, Node, Outcome, Plan, Question
from agents import Candidate


def plan(**limits) -> Plan:
    c = {"team_size": 2, "timeline_weeks": 10, "weekly_hours": 18, "budget_usd": 100, **limits}
    return Plan(
        session_id="t",
        idea="test",
        constraints=Constraints(**c),
        nodes=[
            Node(id="root", label="App", type="idea", proposed_by="user"),
            Node(id="map", label="Live map", type="feature", proposed_by="user", estimate_hours=30, original_estimate_hours=30),
            Node(id="push", label="Push notifications", type="feature", proposed_by="user", estimate_hours=10),
        ],
    )


def test_capacity_uses_team_total():
    assert capacity_hours(plan()) == 18 * 10 * 0.7  # 12h + 6h teammates, not 2 x 12h


def test_must_haves_are_required_nodes():
    p = plan()
    p.nodes[1].required = True
    assert violations(p) == []  # no wording to match: the node itself is required
    p.nodes[1].status = "deferred"
    assert violations(p) == ["must-have deferred: Live map"]
    assert "drop the must-have 'Live map'" in relax_options(p)


def test_required_nodes_cant_be_dropped_by_agents():
    p = plan()
    p.nodes[1].required = True
    offer = Counteroffer(summary="x", changes=[{"node_id": "map", "status": "deferred"}])
    revised, notes = apply_counteroffer(p, offer, "delivery", 1)
    assert revised.nodes[1].status == "proposed" and "must-have" in notes[0]


def test_relax_options_are_computed():
    p = plan(weekly_hours=10, timeline_weeks=4)  # capacity 28h, planned 40h
    options = relax_options(p)
    assert "extend to about 6 weeks at 10h/week" in options[0]
    assert "raise to about 15h/week to keep 4 weeks" in options[1]


def test_estimate_floor_and_notes():
    offer = Counteroffer(summary="shrink", estimate_updates=[EstimateUpdate(node_id="map", estimate_hours=5)])
    revised, notes = apply_counteroffer(plan(), offer, "delivery", 1)
    assert revised.nodes[1].estimate_hours == 15  # 50% of the original 30h, not 5h
    assert "floor" in notes[0]


def test_new_nodes_dedupe_and_default_estimate():
    offer = Counteroffer(
        summary="add",
        new_nodes=[NewNode(label="Live maps"), NewNode(label="Claim a portion", estimate_hours=None)],
    )
    revised, notes = apply_counteroffer(plan(), offer, "architect", 1)
    labels = [n.label for n in revised.nodes]
    assert labels.count("Live maps") == 0 and "skipped duplicate" in notes[0]
    assert revised.nodes[-1].estimate_hours == 8  # no free features


def test_locked_and_question_nodes_cant_be_changed():
    p = plan()
    p.nodes[1].locked = True
    p.nodes.append(Node(id="q", label="Who posts?", type="question", proposed_by="evidence"))
    offer = Counteroffer(summary="x", changes=[{"node_id": "map", "status": "deferred"}, {"node_id": "q", "status": "agreed"}])
    revised, _ = apply_counteroffer(p, offer, "delivery", 1)
    assert revised.nodes[1].status == "proposed" and revised.nodes[-1].status == "proposed"


def test_nash_tiebreak_is_neutral():
    thresholds = {"a": 0.6, "b": 0.6, "c": 0.6}
    lopsided = [CandidateVote(candidate_id="x", agent="a", score=1.0, vote="accept"),
                CandidateVote(candidate_id="x", agent="b", score=0.61, vote="accept"),
                CandidateVote(candidate_id="x", agent="c", score=0.61, vote="accept")]
    balanced = [CandidateVote(candidate_id="y", agent=v.agent, score=0.75, vote="accept") for v in lopsided]
    assert nash_product(balanced, thresholds) > nash_product(lopsided, thresholds)
    cands = [Candidate("x", "a", "", plan()), Candidate("y", "b", "", plan())]
    chosen, consensus, why = choose(cands, {"x": lopsided, "y": balanced}, thresholds)
    assert chosen.id == "y" and consensus and "Nash" in why


def test_improvement_needs_everyone_to_accept_and_prefer():
    thresholds = {"a": 0.6, "b": 0.6, "c": 0.6}

    def votes(cid, accept, prefer):
        return [CandidateVote(candidate_id=cid, agent=a, score=0.7, vote="accept" if acc else "reject",
                              prefer_over_current=pre) for a, acc, pre in zip("abc", accept, prefer)]

    small_fix = votes("fix", [1, 1, 1], [True, True, True])  # round 3's safety fix: everyone wants it
    expansion = votes("big", [1, 0, 1], [True, False, True])  # Delivery rejects
    cands = [Candidate("fix", "c", "", plan()), Candidate("big", "a", "", plan())]
    chosen, why = pick_improvement(cands, {"fix": small_fix, "big": expansion}, thresholds)
    assert chosen.id == "fix" and "adopted" in why

    lukewarm = votes("fix", [1, 1, 1], [True, False, True])  # accepts but one prefers the current plan
    chosen, why = pick_improvement(cands[:1], {"fix": lukewarm}, thresholds)
    assert chosen is None and "Keeping it" in why


def test_stale_questions_close():
    p = plan()
    p.nodes[1].status = "agreed"
    p.questions = [Question(id="decide-map", asked_by="protocol", question="?", why="")]
    p.outcome = Outcome(status="consensus")
    close_stale_questions(p)
    assert p.questions[0].answer == RESOLVED


def test_similar_labels():
    assert similar_labels("Push notifications", "push notification")
    assert not similar_labels("Live map", "Push notifications")


if __name__ == "__main__":
    tests = [f for name, f in dict(globals()).items() if name.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nall {len(tests)} passed")
