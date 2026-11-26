"""Tests for app/static/panel.js, the pure logic of the workflow side panel (#86, Decision 5).

Seam: `node tests/app/panel_runner.mjs` loads the real panel.js and runs the functions on JSON
cases (stdin -> stdout); no DOM, no dependency. The tests are skipped, saying why, where `node`
is not on PATH. The DOM half of the panel is covered by tests/app/smoke/panel_smoke.mjs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent / "panel_runner.mjs"
NOW = {"$date": "2026-11-18T15:00:00+00:00"}
ACTIVE = {"process": "claude", "pid": 4242, "since": "2026-11-18T13:00:00+00:00"}
PHASE_NAMES = ["Start", "Design & alignment", "Product review", "System architecture",
               "Tickets", "Program design", "Implement-loop"]

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH: the panel's logic tests run panel.js through node (design Decision 5)")


def call(*cases):
    """The results of `(function, args...)` cases, one node run for all of them."""
    payload = json.dumps([[name, list(args)] for name, *args in cases])
    done = subprocess.run(["node", str(RUNNER)], input=payload, capture_output=True, text=True,
                          env={**os.environ, "TZ": "UTC"}, timeout=60)
    assert done.returncode == 0, f"panel_runner failed ({done.returncode}): {done.stderr}"
    results = json.loads(done.stdout)
    for result in results:
        assert not (isinstance(result, dict) and "$error" in result), result["$error"]
        assert not (isinstance(result, dict) and "$undefined" in result), "a function answered undefined"
    return results


def one(name, *args):
    return call((name, *args))[0]


def verdict(n, status, problems=(), reason=None):
    return {"phase": n, "status": status, "problems": list(problems), "reason": reason}


def derived_row(phase=3, verdicts=None, agent=None, slug="feat", links=None, activity="2026-11-18T14:02:00+00:00"):
    if verdicts is None:
        verdicts = [verdict(n, "pass" if n < phase else "todo") for n in range(7)]
    return {"id": "abc123abc123", "slug": slug, "repo": "o/r", "last_activity": activity,
            "phase": {"kind": "derived", "phase": phase, "verdicts": verdicts},
            "agent": agent, "links": links or {"pr": None, "ticket": None, "tracker": None}}


def delivered_row(agent=None, verdicts=None, links=None):
    row = derived_row(agent=agent, links=links, verdicts=verdicts if verdicts is not None
                      else [verdict(0, "pass"), verdict(1, "todo", ["x"]), verdict(2, "skip", reason="r")])
    row["phase"] = {"kind": "delivered", "phase": None, "verdicts": row["phase"]["verdicts"]}
    return row


def bare_row(kind, slug="feat"):
    row = derived_row(slug=slug, verdicts=[])
    row["phase"] = {"kind": kind, "phase": None, "verdicts": []}
    return row


def doc(id, name, kind="design"):
    return {"id": id, "kind": kind, "name": name, "path": f"/x/{name}"}


def workflow_map(artefacts=None, sections=None, diagrams=None, invariants=None):
    """The answer of /api/workflow?feature= as the panel reads it: name, exit check, artefacts, and
    (#100) the gate sections of the phases that have some."""
    artefacts = artefacts or {}
    sections = sections or {}
    diagrams = diagrams or {}
    invariants = invariants or {}
    return {"phases": [{"n": n, "name": f"Name {n}", "exit_check": f"exit check {n}",
                        "artefacts": artefacts.get(n, []),
                        **({"sections": sections[n]} if n in sections else {}),
                        **({"diagrams": diagrams[n]} if n in diagrams else {}),
                        **({"invariants": invariants[n]} if n in invariants else {})} for n in range(7)]}


def view(row, map_=None, failure=None, chosen=None, open_ids=None, open_folds=None):
    return one("panelView", {"row": row, "map": map_, "failure": failure, "chosen": chosen,
                             "openIds": open_ids or {}, "openFolds": open_folds or {}, "now": NOW})


# --- the live dot and the words --------------------------------------------------------------

def test_a_derived_row_has_one_dot_on_its_derived_phase():
    row = derived_row(phase=3)
    dot = one("liveDot", row)
    shown = view(row, workflow_map())
    assert dot["phase"] == 3
    assert [s["n"] for s in shown["steps"] if s["live"]] == [3]


def test_the_dot_pulses_only_while_an_agent_is_active():
    idle, busy = call(("liveDot", derived_row(agent=None)), ("liveDot", derived_row(agent=ACTIVE)))
    assert (idle["pulse"], busy["pulse"]) == (False, True)
    assert idle["phase"] == busy["phase"] == 3


def test_a_delivered_row_has_no_dot_and_every_step_passes():
    row = delivered_row(agent=ACTIVE)
    dot, words = call(("liveDot", row), ("stepWords", row))
    shown = view(row, workflow_map())
    assert dot == {"phase": None, "pulse": False}
    assert [w["word"] for w in words] == ["PASS"] * 7
    assert not any(s["live"] for s in shown["steps"])
    assert shown["agent"] is False
    assert "delivered" in shown["stage"]["tags"]


def test_a_computing_row_has_no_dot_no_words_and_says_it_is_checking():
    row = bare_row("computing")
    shown = view(row, workflow_map())
    assert shown["mode"] == "computing"
    assert "checking" in shown["banner"]["text"].lower()
    assert shown["stage"] is None
    assert [s["word"] for s in shown["steps"]] == [None] * 7
    assert not any(s["live"] for s in shown["steps"])
    assert not any(s["current"] for s in shown["steps"])
    assert one("liveDot", row) == {"phase": None, "pulse": False}


def test_a_row_without_a_spec_has_no_dot_and_says_there_is_no_workflow_to_lay():
    row = bare_row("no-spec", slug="lonely-slug")
    shown = view(row, workflow_map())
    assert shown["mode"] == "no-spec"
    assert "lonely-slug has no spec" in shown["banner"]["text"]
    assert "no workflow to lay" in shown["banner"]["text"]
    assert shown["stage"] is None
    assert not any(s["live"] for s in shown["steps"])


def test_the_panel_opens_on_the_derived_phase_and_on_phase_6_when_delivered():
    got = call(("opensOn", derived_row(phase=2)), ("opensOn", derived_row(phase=6)),
               ("opensOn", delivered_row()), ("opensOn", bare_row("computing")),
               ("opensOn", bare_row("no-spec")))
    assert got == [2, 6, 6, 0, 0]


def test_the_stage_follows_the_dot_until_the_owner_chooses():
    got = call(("followedPhase", None, derived_row(phase=3)), ("followedPhase", None, derived_row(phase=4)),
               ("followedPhase", 2, derived_row(phase=4)), ("followedPhase", 0, derived_row(phase=4)))
    assert got == [3, 4, 2, 0]


def test_the_verdict_words_are_the_harness_words():
    row = derived_row(phase=3, verdicts=[verdict(0, "pass"), verdict(1, "skip", reason="r"),
                                         verdict(2, "fail", ["p"]), verdict(3, "todo", ["q"])])
    words = one("stepWords", row)
    assert [w["word"] for w in words[:4]] == ["PASS", "SKIP", "FAIL", "TODO"]
    assert [w["word"] for w in words[4:]] == [None, None, None]
    assert len({w["tone"] for w in words[:4]}) == 4
    assert [w["n"] for w in words] == list(range(7))


# --- the stage ---------------------------------------------------------------------------------

def test_a_skipped_phase_gives_its_reason_and_nothing_to_read():
    row = derived_row(phase=3, verdicts=[verdict(0, "pass"), verdict(1, "pass"), verdict(2, "skip", reason="CLI only, no UI."),
                                         verdict(3, "todo", ["x"])])
    shown = view(row, workflow_map({2: [doc("m1", "mock.html", "mockup")]}), chosen=2)
    assert shown["mode"] == "skipped"
    assert shown["stage"]["word"] == "SKIP"
    assert shown["stage"]["idea"]["reason"] == "CLI only, no UI."
    assert shown["stage"]["idea"]["heading"] == "why it was skipped"
    assert shown["stage"]["nothing"] == "Nothing to read: the phase was skipped."
    assert shown["stage"]["cards"] == []


def test_a_failed_phase_names_its_problems_and_counts_them():
    row = derived_row(phase=3, verdicts=[verdict(n, "pass") for n in range(3)] + [verdict(3, "fail", ["no invariants table", "no model"])]
                      + [verdict(n, "todo") for n in range(4, 7)])
    shown = view(row, workflow_map())
    stage = shown["stage"]
    assert stage["counters"]["problems"] == 2
    assert stage["idea"]["problems"] == ["no invariants table", "no model"]
    assert "FAIL" in stage["tags"] and stage["word"] == "FAIL"


def test_a_todo_phase_counts_its_problems_like_a_failed_one():
    todo = view(derived_row(phase=3, verdicts=[verdict(n, "pass") for n in range(3)] + [verdict(3, "todo", ["one"])]
                            + [verdict(n, "todo") for n in range(4, 7)]), workflow_map())
    assert todo["stage"]["word"] == "TODO" and todo["stage"]["counters"]["problems"] == 1


def test_a_passed_phase_has_zero_open_problems_and_a_phase_with_no_verdict_has_a_dash():
    row = derived_row(phase=3, verdicts=[verdict(0, "pass"), verdict(1, "pass", ["stale"]), verdict(2, "pass"), verdict(3, "todo", ["x"])])
    passed = view(row, workflow_map(), chosen=1)["stage"]
    unknown = view(row, workflow_map(), chosen=6)["stage"]
    assert passed["counters"]["problems"] == 0 and passed["idea"]["problems"] == []
    assert "PASS" in passed["tags"]
    assert unknown["counters"]["problems"] is None and unknown["word"] is None


def test_the_artefact_counter_counts_documents_and_links():
    links = {"pr": "https://github.com/o/r/pull/9", "ticket": "https://github.com/o/r/issues/74",
             "tracker": "https://github.com/o/r/issues/74"}
    row = derived_row(phase=6, links=links)
    shown = view(row, workflow_map({6: [doc("d1", "m.md", "model"), doc("d2", "r.jsonl", "report")],
                                    4: [doc("s1", "spec.md", "spec")]}))
    last = shown["stage"]["cards"]
    assert shown["stage"]["counters"]["artefacts"] == 3   # phase 6 is a design phase (#127): the documents are folds
    assert [c["kind"] for c in last] == ["link"] and [f["id"] for f in shown["stage"]["folds"]] == ["d1", "d2"]
    assert last[0]["href"] == links["pr"] and last[0]["id"] is None
    four = view(row, workflow_map({4: [doc("s1", "spec.md", "spec")]}), chosen=4)["stage"]["cards"]
    assert [c["kind"] for c in four] == ["doc", "link"]
    assert four[1]["href"] == links["ticket"] and four[1]["name"] == "#74"
    bare = derived_row(phase=6)
    spec_only = workflow_map({4: [doc("s1", "spec.md", "spec")]})
    assert [c["kind"] for c in view(bare, spec_only, chosen=4)["stage"]["cards"]] == ["doc"]
    assert view(bare, workflow_map())["stage"]["counters"]["artefacts"] == 0


def test_a_phase_with_no_artefact_says_there_is_nothing_to_read_and_keeps_the_idea():
    shown = view(derived_row(phase=6), workflow_map())
    stage = shown["stage"]
    assert stage["cards"] == [] and stage["openId"] is None
    assert stage["nothing"] == "Nothing to read here yet."
    assert stage["idea"]["text"] == "exit check 6"
    only_links = view(derived_row(phase=4, links={"pr": None, "ticket": "https://github.com/o/r/issues/1", "tracker": "https://github.com/o/r/issues/1"}),
                      workflow_map())["stage"]
    assert [c["kind"] for c in only_links["cards"]] == ["link"]
    assert only_links["nothing"] == "Nothing to read here: only links."
    assert only_links["openId"] is None


def test_the_first_document_is_open_by_default_and_a_vanished_open_id_falls_back_to_it():
    row = derived_row(phase=2)   # a plain phase: the design phases fold their texts (#127)
    map_ = workflow_map({2: [doc("a1", "one.md"), doc("b2", "two.md")]})
    default = view(row, map_)["stage"]
    chosen = view(row, map_, open_ids={"2": "b2"})["stage"]
    gone = view(row, map_, open_ids={"2": "vanished"})["stage"]
    assert default["openId"] == "a1" and [c["open"] for c in default["cards"]] == [True, False]
    assert chosen["openId"] == "b2" and [c["open"] for c in chosen["cards"]] == [False, True]
    assert gone["openId"] == "a1"
    assert default["nothing"] is None


# --- loading, error --------------------------------------------------------------------------

def test_loading_has_the_steps_of_the_row_and_no_stage():
    shown = view(derived_row(phase=3))
    assert shown["mode"] == "loading"
    assert shown["banner"]["tone"] == "info" and "Loading the workflow" in shown["banner"]["text"]
    assert shown["stage"] is None
    assert [s["name"] for s in shown["steps"]] == PHASE_NAMES
    assert [s["word"] for s in shown["steps"]][:4] == ["PASS", "PASS", "PASS", "TODO"]
    assert [s["n"] for s in shown["steps"] if s["live"]] == [3]
    assert [s["n"] for s in shown["steps"] if s["current"]] == [3]


def test_an_error_with_no_map_keeps_the_steps_and_says_why():
    shown = view(derived_row(phase=3), failure="HTTP 503")
    assert shown["mode"] == "error"
    assert shown["banner"]["tone"] == "bad"
    assert shown["banner"]["text"] == "Cannot load the workflow: HTTP 503. Trying again at the next refresh."
    assert shown["stage"] is None and len(shown["steps"]) == 7


def test_an_error_with_a_map_keeps_the_stage_and_adds_the_banner():
    shown = view(derived_row(phase=3), workflow_map(), failure="HTTP 503")
    assert shown["mode"] == "ready" and shown["stage"] is not None
    assert shown["stage"]["n"] == 3 and shown["stage"]["idea"]["text"] == "exit check 3"
    assert shown["banner"] == {"tone": "bad",
                               "text": "Cannot load the workflow: HTTP 503. Trying again at the next refresh."}
    assert [s["name"] for s in shown["steps"]] == [f"Name {n}" for n in range(7)]


def test_the_last_activity_is_the_time_today_the_date_otherwise_and_a_dash_when_absent():
    today, yesterday, none, junk = call(
        ("activityLabel", "2026-11-18T14:02:00+00:00", NOW), ("activityLabel", "2026-11-17T23:59:00+00:00", NOW),
        ("activityLabel", None, NOW), ("activityLabel", "not a date", NOW))
    assert (today, yesterday, none, junk) == ("14:02", "2026-11-17", "—", "—")
    assert view(derived_row(), workflow_map())["stage"]["counters"]["activity"] == "14:02"


def test_back_and_next_name_their_neighbour_and_are_absent_at_the_ends():
    first = view(derived_row(phase=3), workflow_map(), chosen=0)["stage"]
    middle = view(derived_row(phase=3), workflow_map(), chosen=3)["stage"]
    last = view(derived_row(phase=3), workflow_map(), chosen=6)["stage"]
    assert first["back"] is None and first["next"] == {"n": 1, "name": "Name 1"}
    assert middle["back"] == {"n": 2, "name": "Name 2"} and middle["next"] == {"n": 4, "name": "Name 4"}
    assert last["back"] == {"n": 5, "name": "Name 5"} and last["next"] is None


# --- keys --------------------------------------------------------------------------------------

NONE_HELD = {"ctrl": False, "alt": False, "meta": False}
BODY = {"tag": "body", "editable": False}


def test_the_arrow_keys_r_and_escape_map_to_back_next_refresh_close():
    got = call(*[("keyAction", key, BODY, NONE_HELD) for key in
                 ("ArrowLeft", "ArrowRight", "r", "R", "Escape", "Enter", "x")])
    assert got == ["back", "next", "refresh", "refresh", "close", None, None]


def test_a_key_with_a_modifier_or_typed_in_a_field_does_nothing_but_escape_still_closes():
    cases = []
    for mod in ("ctrl", "alt", "meta"):
        cases.append(("keyAction", "r", BODY, {**NONE_HELD, mod: True}))
        cases.append(("keyAction", "Escape", BODY, {**NONE_HELD, mod: True}))
    fields = [{"tag": "input", "editable": False}, {"tag": "textarea", "editable": False},
              {"tag": "select", "editable": False}, {"tag": "div", "editable": True}]
    for field in fields:
        cases += [("keyAction", "ArrowLeft", field, NONE_HELD), ("keyAction", "r", field, NONE_HELD),
                  ("keyAction", "Escape", field, NONE_HELD)]
    got = call(*cases)
    assert got[:6] == [None] * 6
    assert got[6:] == [None, None, "close"] * 4


def test_key_action_gives_next_row_for_j_and_prev_row_for_k_and_nothing_in_a_field_or_with_a_modifier():
    assert call(("keyAction", "j", BODY, NONE_HELD), ("keyAction", "k", BODY, NONE_HELD)) == ["nextRow", "prevRow"]
    cases = []
    for mod in ("ctrl", "alt", "meta"):
        cases += [("keyAction", "j", BODY, {**NONE_HELD, mod: True}), ("keyAction", "k", BODY, {**NONE_HELD, mod: True})]
    for field in ({"tag": "input", "editable": False}, {"tag": "textarea", "editable": False},
                  {"tag": "select", "editable": False}, {"tag": "div", "editable": True}):
        cases += [("keyAction", "j", field, NONE_HELD), ("keyAction", "k", field, NONE_HELD)]
    assert call(*cases) == [None] * len(cases)


# --- what is redrawn -------------------------------------------------------------------------

def keys(row, map_=None, **kw):
    shown = view(row, map_ or workflow_map({5: [doc("a1", "one.md"), doc("b2", "two.md")]}), **kw)
    return one("stageKeys", shown)


def test_the_artefact_key_ignores_counters_verdicts_and_the_activity():
    base = derived_row(phase=5, activity="2026-11-18T14:02:00+00:00")
    later = derived_row(phase=5, activity="2026-11-18T14:40:00+00:00")
    passed = derived_row(phase=5, verdicts=[verdict(n, "pass") for n in range(7)])
    failing = derived_row(phase=5, verdicts=[verdict(n, "pass") for n in range(5)] + [verdict(5, "fail", ["a", "b"]), verdict(6, "todo")])
    a, b, c, d = keys(base), keys(later), keys(passed), keys(failing)
    assert a["art"] == b["art"] == c["art"] == d["art"]
    assert a["head"] != b["head"]          # the activity is in the head
    assert a["head"] != c["head"] and a["head"] != d["head"] and c["head"] != d["head"]


def test_the_artefact_key_moves_with_the_phase_the_cards_or_the_open_card():
    row = derived_row(phase=2)   # a plain phase: the design phases key their folds instead (#127)
    base_map = workflow_map({2: [doc("a1", "one.md"), doc("b2", "two.md")]})
    base = keys(row, base_map)["art"]
    other_phase = keys(row, base_map, chosen=4)["art"]
    renamed = keys(row, workflow_map({2: [doc("a1", "one.md"), doc("b2", "renamed.md")]}))["art"]
    more = keys(row, workflow_map({2: [doc("a1", "one.md"), doc("b2", "two.md"), doc("c3", "three.md")]}))["art"]
    other_open = keys(row, base_map, open_ids={"2": "b2"})["art"]
    assert len({base, other_phase, renamed, more, other_open}) == 5
    assert keys(row, base_map)["art"] == base


def test_the_head_key_moves_when_the_phase_passes_or_its_problems_change():
    def row_with(problems, status="todo"):
        return derived_row(phase=3, verdicts=[verdict(n, "pass") for n in range(3)] + [verdict(3, status, problems)]
                           + [verdict(n, "todo") for n in range(4, 7)])
    before = keys(row_with(["a"]))["head"]
    assert keys(row_with(["a"]))["head"] == before
    assert keys(row_with(["a", "b"]))["head"] != before
    assert keys(row_with(["b"]))["head"] != before   # same count, other text
    assert keys(row_with(["a"], "fail"))["head"] != before
    passed = derived_row(phase=4, verdicts=[verdict(n, "pass") for n in range(4)] + [verdict(n, "todo") for n in range(4, 7)])
    assert keys(passed, chosen=3)["head"] != before


def test_the_panel_closes_when_its_feature_leaves_the_snapshot_and_stays_when_it_is_present():
    got = call(("panelAfterSnapshot", "r2", ["r1", "r2"]), ("panelAfterSnapshot", "r3", ["r1", "r2"]),
               ("panelAfterSnapshot", "r1", []))
    assert got == ["keep", "close", "close"]


# --- the mockup's crumb, lede and key hints; the counter label; no choice without a stage (#86 round 2) ---

def test_the_crumb_names_the_feature_and_the_phase_of_seven():
    shown = view(derived_row(phase=3, slug="my-feature"), workflow_map(), chosen=5)
    assert shown["stage"]["crumb"] == "my-feature / phase 5 of 7"


def test_the_lede_is_the_phases_condition_and_empty_when_it_has_none():
    map_ = workflow_map()
    map_["phases"][2]["condition"] = "only when the feature touches UI"
    map_["phases"][3]["condition"] = None
    row = derived_row(phase=3)
    assert view(row, map_, chosen=2)["stage"]["lede"] == "only when the feature touches UI"
    assert view(row, map_, chosen=3)["stage"]["lede"] == ""
    assert view(row, workflow_map(), chosen=3)["stage"]["lede"] == ""   # a map with no `condition` key at all


def test_the_view_lists_the_key_hints_of_the_mockup():
    assert view(derived_row(), workflow_map())["hints"] == [
        {"keys": ["←", "→"], "text": "phase"}, {"keys": ["R"], "text": "refresh"}, {"keys": ["Esc"], "text": "close"}]


def test_the_counters_are_labelled_artefacts_open_problems_and_feature_last_activity():
    row = derived_row(phase=3, activity="2026-11-18T14:02:00+00:00",
                      verdicts=[verdict(n, "pass") for n in range(3)] + [verdict(3, "todo", ["x"])])
    stage = view(row, workflow_map())["stage"]
    assert [(s["label"], s["value"]) for s in stage["stats"]] == [
        ("artefacts", "0"), ("open problems", "1"), ("feature last activity", "14:02")]


def test_only_a_counter_with_open_problems_is_marked_bad():
    open_ = derived_row(phase=3, verdicts=[verdict(n, "pass") for n in range(3)] + [verdict(3, "todo", ["x"])])
    clean = derived_row(phase=3, verdicts=[verdict(n, "pass") for n in range(4)])
    no_verdict = derived_row(phase=3, verdicts=[verdict(3, "todo", ["x"])])
    bad = lambda row, chosen: [s["label"] for s in view(row, workflow_map(), chosen=chosen)["stage"]["stats"] if s["bad"]]
    assert [bad(open_, 3), bad(clean, 3), bad(no_verdict, 6)] == [["open problems"], [], []]


def test_a_skipped_phase_shows_no_counters():
    row = derived_row(phase=3, verdicts=[verdict(0, "pass"), verdict(1, "pass"), verdict(2, "skip", reason="r"),
                                         verdict(3, "todo", ["x"])])
    assert view(row, workflow_map(), chosen=2)["stage"]["stats"] == []


def test_a_phase_without_a_verdict_shows_a_dash_for_its_open_problems():
    row = derived_row(phase=3, verdicts=[verdict(3, "todo", ["x"])])
    stats = view(row, workflow_map(), chosen=6)["stage"]["stats"]
    assert [s["value"] for s in stats if s["label"] == "open problems"] == ["—"]


def test_only_a_view_with_a_stage_accepts_a_chosen_phase():
    row = derived_row(phase=3)
    cases = {
        "loading": view(row, None),
        "error": view(row, None, failure="HTTP 503"),
        "computing": view(bare_row("computing"), workflow_map()),
        "no-spec": view(bare_row("no-spec"), workflow_map()),
        "ready": view(row, workflow_map()),
    }
    assert {name: shown["chooses"] for name, shown in cases.items()} == {
        "loading": False, "error": False, "computing": False, "no-spec": False, "ready": True}


# --- the progress line and the stamp (#87) -----------------------------------------------------

def lit_and_flowing(row):
    steps = view(row, workflow_map())["steps"]
    return [n for n, s in enumerate(steps) if s["lit"]], [n for n, s in enumerate(steps) if s["flows"]]


def test_the_progress_line_is_lit_up_to_the_live_phase_and_flows_only_with_an_agent():
    assert lit_and_flowing(derived_row(phase=3, agent=ACTIVE)) == ([1, 2, 3], [3])
    assert lit_and_flowing(derived_row(phase=3, agent=None)) == ([1, 2, 3], [])


def test_a_delivered_row_lights_the_whole_line_and_nothing_flows():
    assert lit_and_flowing(delivered_row(agent=ACTIVE)) == ([1, 2, 3, 4, 5, 6], [])


def test_a_computing_or_no_spec_row_lights_nothing():
    assert lit_and_flowing(bare_row("computing")) == ([], [])
    assert lit_and_flowing(bare_row("no-spec")) == ([], [])


def test_a_row_whose_dot_is_on_phase_0_has_no_segment_to_light():
    assert lit_and_flowing(derived_row(phase=0, agent=ACTIVE)) == ([], [])


def test_trackof_adds_lit_and_flows_to_the_steps_it_is_given_and_keeps_the_rest():
    steps = [{"n": n, "name": f"S{n}", "word": None} for n in range(7)]
    out = one("trackOf", steps, derived_row(phase=2, agent=ACTIVE))
    assert [(s["n"], s["name"]) for s in out] == [(n, f"S{n}") for n in range(7)]
    assert [s["lit"] for s in out] == [False, True, True, False, False, False, False]
    assert [s["flows"] for s in out] == [False, False, True, False, False, False, False]


def test_a_passed_phase_is_stamped_only_when_the_dot_moves_forward_past_it():
    words = [{"word": "PASS" if n < 5 else "TODO"} for n in range(7)]
    words[5] = {"word": "PASS"}
    words[3] = {"word": "TODO"}
    assert call(("dotMove", 5, 6, words), ("dotMove", 5, 5, words), ("dotMove", 6, 5, words),
                ("dotMove", 3, 4, words), ("dotMove", None, 3, words)) == [5, None, None, None, None]


# --- #91 #95: the base on the Open screen ---------------------------------------------

def test_ahead_label_names_the_base_without_origin():
    assert call(("aheadLabel", {"ahead": 362, "base": "origin/develop"}),
                ("aheadLabel", {"ahead": 3, "base": "origin/master"})) == ["362 ahead of develop", "3 ahead of master"]



def test_base_note_line_for_base_unknown_is_the_reason():
    note = "no origin/develop, origin/HEAD, origin/main or origin/master"
    assert one("baseNoteLine", {"name": "base-unknown"}, {"base": None, "base_note": note}) == note


def test_base_note_line_is_empty_without_a_note_or_for_another_signal():
    unknown = {"name": "base-unknown"}
    assert call(("baseNoteLine", unknown, {"base": None}),
                ("baseNoteLine", unknown, {"base": None, "base_note": None}),
                ("baseNoteLine", unknown, {"base": None, "base_note": ""}),
                ("baseNoteLine", {"name": "removable"}, {"base_note": "a reason"})) == ["", "", "", ""]


# --- #93: the group of other worktrees ------------------------------------------------------

def test_other_rows_reads_the_additive_key():
    first, second = {"id": "a" * 12, "slug": "release-0.0.23"}, {"id": "b" * 12, "slug": "backlog"}
    assert one("otherRows", {"open": [], "history": [], "other_worktrees": [first, second]}) == [first, second]


def test_other_rows_is_empty_for_an_old_server_without_the_key():
    assert call(("otherRows", {"open": [], "history": []}),
                ("otherRows", {"other_worktrees": None})) == [[], []]


def test_other_group_label_names_the_count():
    assert call(("otherGroupLabel", 1), ("otherGroupLabel", 3)) == [
        "Other worktrees (1)", "Other worktrees (3)"]


# --- #105 window-updates: the conditional poll and the rows the page keeps -----------------------

def pairs(spec: str):
    """`"a:1 b:1"` -> [["a", "1"], ["b", "1"]]: a list of (id, print) in display order."""
    return [item.split(":") for item in spec.split()]


def test_conditional_headers_are_empty_without_a_tag_and_carry_it_with_one():
    assert call(("conditionalHeaders", None), ("conditionalHeaders", ""),
                ("conditionalHeaders", '"abc"')) == [{}, {}, {"If-None-Match": '"abc"'}]


def test_answer_kind_for_304_200_and_failures():
    assert call(("answerKind", 304, True), ("answerKind", 200, True), ("answerKind", 200, False),
                ("answerKind", 304, False), ("answerKind", 500, True), ("answerKind", 503, True),
                ("answerKind", 0, True)) == [
        "unchanged", "changed", "changed", "failed", "failed", "failed", "failed"]


def test_row_print_is_equal_for_equal_rows_and_differs_for_any_changed_field():
    row = derived_row(links={"pr": "https://x/1", "ticket": None, "tracker": "#1"})
    row["where"] = {"kind": "worktree", "ahead": 2}
    copy = json.loads(json.dumps(row))
    changed = [
        {**row, "slug": "other"},
        {**row, "last_activity": "2026-11-18T14:03:00+00:00"},
        {**row, "agent": ACTIVE},
        {**row, "phase": {**row["phase"], "phase": 4}},
        {**row, "where": {"kind": "worktree", "ahead": 3}},
        {**row, "links": {**row["links"], "pr": None}},
    ]
    prints = call(("rowPrint", row), ("rowPrint", copy), *[("rowPrint", c) for c in changed])
    assert isinstance(prints[0], str) and prints[0] != ""
    assert prints[0] == prints[1]
    distinct = [prints[0], *prints[2:]]
    assert len(set(distinct)) == len(distinct), "a changed field left the print equal to another row's"


def test_plan_rows_of_identical_lists_has_nothing_to_do():
    same = pairs("a:1 b:1 c:1")
    assert one("planRows", same, same) == {"remove": [], "draw": [], "move": [], "order": ["a", "b", "c"]}


def test_plan_rows_one_changed_row_draws_only_that_row():
    assert one("planRows", pairs("a:1 b:1 c:1"), pairs("a:1 b:2 c:1")) == {
        "remove": [], "draw": ["b"], "move": [], "order": ["a", "b", "c"]}


def test_plan_rows_a_new_row_is_drawn_and_a_missing_row_is_removed():
    assert one("planRows", pairs("a:1 b:1 c:1"), pairs("a:1 c:1 d:1")) == {
        "remove": ["b"], "draw": ["d"], "move": [], "order": ["a", "c", "d"]}


def test_plan_rows_a_swap_moves_one_row_and_a_rise_to_the_top_moves_one_row():
    swap = one("planRows", pairs("a:1 b:1 c:1"), pairs("a:1 c:1 b:1"))
    assert len(swap["move"]) == 1 and swap["move"][0] in ("b", "c")
    assert swap["draw"] == [] and swap["remove"] == [] and swap["order"] == ["a", "c", "b"]
    assert one("planRows", pairs("a:1 b:1 c:1 d:1"), pairs("d:1 a:1 b:1 c:1")) == {
        "remove": [], "draw": [], "move": ["d"], "order": ["d", "a", "b", "c"]}


def test_plan_rows_from_empty_draws_everything_and_to_empty_removes_everything():
    assert one("planRows", [], pairs("a:1 b:1")) == {
        "remove": [], "draw": ["a", "b"], "move": [], "order": ["a", "b"]}
    assert one("planRows", pairs("a:1 b:1"), []) == {
        "remove": ["a", "b"], "draw": [], "move": [], "order": []}


def test_plan_rows_a_changed_and_moved_row_is_drawn_and_not_listed_twice():
    plan = one("planRows", pairs("a:1 b:1 c:1"), pairs("c:2 a:1 b:1"))
    assert plan == {"remove": [], "draw": ["c"], "move": [], "order": ["c", "a", "b"]}


def test_draw_figure_of_unchanged_keeps_the_last_value_and_names_it():
    assert call(("drawFigure", "unchanged", 3, 120), ("drawFigure", "changed", 45, 120)) == [
        {"word": "unchanged", "ms": 120}, {"word": None, "ms": 45}]


def test_tab_load_mode_is_full_when_not_loaded_or_forced_revalidate_when_shown_none_otherwise():
    def mode(loaded, shown, force):
        return ("tabLoadMode", {"loaded": loaded, "shown": shown, "force": force})
    assert call(mode(False, True, False), mode(False, False, False), mode(True, True, True),
                mode(True, False, True), mode(True, True, False), mode(True, False, False)) == [
        "full", "full", "full", "full", "revalidate", "none"]


# --- #100 (gate-sections): the sections of a phase, their titles, the exit sentence ---------------------

def section(heading="Tickets", state="present", id="sec1", rev="r1", note=None, source="docs/specs/feat.md"):
    return {"heading": heading, "source": source, "state": state, "id": id, "rev": rev, "note": note}


def test_section_title_drops_the_markdown_markers():
    plain, one_hash, two, three, spaced = call(
        ("sectionTitle", "Tickets"), ("sectionTitle", "# Tickets"), ("sectionTitle", "## Tickets"),
        ("sectionTitle", "### System invariants"), ("sectionTitle", "  ##   Model changes "))
    assert (plain, one_hash, two, three, spaced) == (
        "Tickets", "Tickets", "Tickets", "System invariants", "Model changes")


def test_exit_sentence_drops_the_heading_markers_and_keeps_an_issue_number():
    text = ("The spec has a `## Tickets` section; a `### System invariants` table, see #100 and (## Model) "
            "and C# code")
    assert one("exitSentence", text) == (
        "The spec has a `Tickets` section; a `System invariants` table, see #100 and (Model) and C# code")


def test_the_idea_block_of_a_phase_shows_the_exit_check_without_heading_markers():
    row = derived_row(phase=4)
    map_ = workflow_map()
    map_["phases"][4]["exit_check"] = "The spec has a `## Tickets` section with `### Items`."
    idea = view(row, map_)["stage"]["idea"]
    assert idea["text"] == "The spec has a `Tickets` section with `Items`."
    assert idea["heading"] == "what leaves phase 4"


def test_a_missing_section_is_shown_only_when_a_verdict_problem_names_its_heading():
    phase = {"sections": [section("Tickets", "missing", id=None, rev=None)]}
    named, other = call(("phaseSections", phase, ["no ## Tickets"]),
                        ("phaseSections", phase, ["no ## Premises"]))
    assert [(s["title"], s["state"], s["tip"]) for s in named] == [("Tickets", "missing", "no ## Tickets")]
    assert other == []
    mixed_case = one("phaseSections", {"sections": [section("Lifecycle inventory", "missing", id=None)]},
                     ["missing section ## Lifecycle Inventory"])
    assert [s["tip"] for s in mixed_case] == ["missing section ## Lifecycle Inventory"]


def test_a_missing_section_is_not_named_by_a_problem_that_only_contains_its_title_or_a_longer_heading():
    model = {"sections": [section("Model", "missing", id=None, rev=None)]}
    assert one("phaseSections", model, ["mutation matrix is invalid for model X"]) == []
    delivery = {"sections": [section("Delivery", "missing", id=None, rev=None)]}
    assert one("phaseSections", delivery, ["no ## Deliverynote section"]) == []


def test_a_title_with_regex_metacharacters_is_matched_literally():
    plus = {"sections": [section("C++", "missing", id=None, rev=None)]}
    assert [s["tip"] for s in one("phaseSections", plus, ["no ## C++"])] == ["no ## C++"]
    assert one("phaseSections", plus, ["no ## C++note"]) == []
    dot = {"sections": [section("A.B", "missing", id=None, rev=None)]}
    assert one("phaseSections", dot, ["no ## AxB"]) == []


def test_an_unreadable_section_shows_the_error():
    phase = {"sections": [section("Tickets", "unreadable", id=None, rev=None, note="cannot read it")]}
    shown = one("phaseSections", phase, [])
    assert [(s["title"], s["state"], s["tip"], s["id"]) for s in shown] == [
        ("Tickets", "unreadable", "cannot read it", None)]


def test_a_present_section_carries_its_id_rev_source_and_title_without_markers():
    shown = one("phaseSections", {"sections": [section("## Solution", id="abc", rev="r9")]}, [])
    assert shown == [{"title": "Solution", "source": "docs/specs/feat.md", "state": "present",
                      "id": "abc", "rev": "r9", "tip": None}]


def test_a_phase_with_sections_opens_no_whole_document_by_itself_and_a_phase_without_keeps_today_behaviour():
    spec = doc("s1", "spec.md", "spec")
    design = doc("d1", "design.md", "design")
    with_sections = view(derived_row(phase=4), workflow_map({4: [spec], 5: [design]}, {4: [section()]}),
                         chosen=4)["stage"]
    assert with_sections["openId"] is None and [c["open"] for c in with_sections["cards"]] == [False]
    assert with_sections["nothing"] is None
    assert [s["title"] for s in with_sections["sections"]] == ["Tickets"]
    asked = view(derived_row(phase=4), workflow_map({4: [spec]}, {4: [section()]}), chosen=4,
                 open_ids={4: "s1"})["stage"]
    assert asked["openId"] == "s1"
    without = view(derived_row(phase=2), workflow_map({2: [design]}, {2: []}), chosen=2)["stage"]
    assert without["openId"] == "d1" and without["sections"] == []


def test_the_art_key_changes_when_a_section_rev_changes_and_not_when_only_the_verdict_counters_do():
    spec = doc("s1", "spec.md", "spec")

    def keys(rev, problems=()):
        verdicts = [verdict(n, "pass" if n < 4 else "todo", problems if n == 4 else ()) for n in range(7)]
        shown = view(derived_row(phase=4, verdicts=verdicts),
                     workflow_map({4: [spec]}, {4: [section(rev=rev)]}), chosen=4)
        return one("stageKeys", shown)

    base, same_rev_other_problems, new_rev = keys("r1"), keys("r1", ["no ## Tickets"]), keys("r2")
    assert same_rev_other_problems["art"] == base["art"] and same_rev_other_problems["head"] != base["head"]
    assert new_rev["art"] != base["art"]


def test_a_skipped_phase_has_no_sections():
    verdicts = [verdict(n, "skip" if n == 4 else "pass" if n < 4 else "todo", reason="r" if n == 4 else None)
                for n in range(7)]
    shown = view(derived_row(phase=5, verdicts=verdicts), workflow_map({4: [doc("s1", "spec.md", "spec")]},
                                                                        {4: [section()]}), chosen=4)
    assert shown["stage"].get("sections") == [] and shown["stage"]["cards"] == []


# --- #113 (row-view): the address of the open row, its neighbours, the tab it lives in ----------

def test_address_of_a_row_is_the_row_fragment_and_of_none_is_empty():
    assert call(("addressOf", "8d172cdfca2a"), ("addressOf", None)) == ["#row=8d172cdfca2a", ""]


def test_row_from_address_reads_a_row_fragment_and_nothing_else():
    got = call(*[("rowFromAddress", h) for h in
                 ("", "#", "#other=1", "#row=", "#row=xyz", "#row=8d172cdfca2a0", "#row=8D172CDFCA2A", "#row=8d172cdfca2a")])
    assert got == [None, None, None, None, None, None, None, "8d172cdfca2a"]


def test_neighbour_row_gives_the_next_and_the_previous_id_and_none_at_the_ends_and_for_an_unknown_row():
    ids = ["a", "b", "c"]
    got = call(("neighbourRow", ids, "b", 1), ("neighbourRow", ids, "b", -1), ("neighbourRow", ids, "c", 1),
               ("neighbourRow", ids, "a", -1), ("neighbourRow", ids, "zz", 1), ("neighbourRow", [], "a", 1))
    assert got == ["c", "a", None, None, None, None]


def test_tab_of_row_names_open_before_history_and_none_for_an_unlisted_row():
    data = {"open": [{"id": "o1"}, {"id": "both"}], "history": [{"id": "h1"}, {"id": "both"}]}
    got = call(("tabOfRow", data, "o1"), ("tabOfRow", data, "h1"), ("tabOfRow", data, "both"), ("tabOfRow", data, "zz"))
    assert got == ["open", "history", "open", None]


# --- steady-rows #111: a refreshing row ---------------------------------------------------------

CHECKING_AGAIN = "Checking this phase again. It shows the last known phase until the check ends."


def refreshing(row):
    row["phase"] = {**row["phase"], "refreshing": True}
    return row


def test_poll_is_fast_while_a_row_refreshes():
    """witness a snapshot with one refreshing delivered row in History and no computing row -
    distinguishes 'refreshing counts' (true) from a test on kind computing only (false); none of
    either is false, one computing is true."""
    calm = {"open": [derived_row()], "history": [delivered_row()]}
    refresh = {"open": [derived_row()], "history": [refreshing(delivered_row())]}
    computing = {"open": [bare_row("computing")], "history": []}
    assert call(("pollFast", calm), ("pollFast", refresh), ("pollFast", computing),
                ("pollFast", None)) == [False, True, True, False]


def test_the_panel_of_a_refreshing_row_says_it_is_checked_again():
    """witness a refreshing delivered row with a map, and the same with a failure - distinguishes
    'ready mode with the info banner' from computing mode (no stage) and from the failure banner
    losing to the refreshing one."""
    row = refreshing(delivered_row())
    shown = view(row, workflow_map())
    assert shown["mode"] == "ready"
    assert shown["banner"] == {"tone": "info", "text": CHECKING_AGAIN}
    assert shown["stage"] is not None
    failed = view(row, workflow_map(), failure="boom")
    assert failed["banner"]["tone"] == "bad" and "boom" in failed["banner"]["text"]
    assert view(delivered_row(), workflow_map())["banner"] is None


# --- #127 (design-diagrams): the stage of phases 1, 3, 5, 6 -- diagrams first, the texts folded ----------

def diagram(title="Model › Flow", mermaid="flowchart LR\n  A --> B", doc="s1", source="docs/specs/feat.md § Model",
            generated=None):
    return {"title": title, "source": source, "doc": doc, "generated": generated, "mermaid": mermaid}


def design_view(n=3, open_folds=None, diagrams=None, sections=None, artefacts=None, links=None, invariants=None):
    """The panel on phase `n` of a row at that phase, with a spec and gate sections (lengths on the wire)."""
    spec = {**doc("s1", "docs/specs/feat.md", "spec"), "lines": 120}
    model = {**doc("m1", "docs/models/feat.md", "model"), "lines": 40}
    sections = sections if sections is not None else [
        {**section("System Architecture", id="sec-a", rev="ra"), "lines": 64},
        {**section("Model", id="sec-m", rev="rm"), "lines": 92}]
    arts = artefacts if artefacts is not None else [spec, model]
    return view(derived_row(phase=n, links=links), workflow_map({n: arts}, {n: sections},
                {n: diagrams if diagrams is not None else [diagram()]},
                {n: invariants} if invariants is not None else None), chosen=n, open_folds=open_folds)


def test_the_stage_of_phases_0_2_4_is_unchanged():
    """witness a map that even carries diagrams for phases 0, 2 and 4, and today's literal keys of phase 4
    (spec card, Tickets section, ticket link) - distinguishes the plain layout kept as it was (cards, open
    document, sections, the same two keys byte for byte) from one that routes every phase through folds."""
    spec = doc("s1", "spec.md", "spec")
    links = {"pr": None, "ticket": "https://github.com/o/r/issues/7", "tracker": "https://github.com/o/r/issues/7"}
    for n in (0, 2, 4):
        shown = view(derived_row(phase=n, links=links),
                     workflow_map({n: [spec]}, {n: [section()]}, {n: [diagram()]}), chosen=n)["stage"]
        assert shown.get("layout") == "plain", n
        assert shown["diagrams"] == [] and shown["folds"] == [], n
        assert [c["kind"] for c in shown["cards"]] == (["doc", "link"] if n == 4 else ["doc"]), n
        assert [s["title"] for s in shown["sections"]] == ["Tickets"], n
    shown = view(derived_row(phase=4, links=links), workflow_map({4: [spec]}, {4: [section(rev="r1", id="t1")]}),
                 chosen=4)
    keys = one("stageKeys", shown)
    assert keys["art"] == "4|doc:s1:spec.md,link:https://github.com/o/r/issues/7:#7||ready|t1:present:r1:Tickets:null"
    assert keys["head"].startswith("4|TODO|TODO,● live phase|2|0|")
    assert one("DESIGN_PHASES") == [1, 3, 5, 6]


def inv_row(n=1, law="A law", status="pinned", evidence=None, verified="`tests/a.py::test_x`", negation="a negation"):
    tests = [e["test"] for e in evidence or []]
    return {"n": n, "law": law, "negation": negation, "verified": verified, "tests": tests, "status": status,
            "evidence": evidence or []}


def inv_table(*rows, source="docs/specs/feat.md", tree="/repo/feat", why=None):
    return {"source": source, "readable": True, "tree": tree, "why": why, "rows": list(rows)}


def evid(test="tests/a.py::test_x", matrix="m1", mutant="M3", outcome="pinned", at="2026-11-20T10:00:00"):
    return {"test": test, "matrix": matrix, "mutant": mutant, "outcome": outcome, "at": at}


def test_design_stage_orders_invariants_diagrams_folds():
    """witness phases 1, 3, 5, 6 with a diagram and an invariants table, phase 3 with no table and a skipped
    phase 3 - distinguishes the order of the blocks (invariants first, then diagrams, then folds, in phases 3
    and 6 only; phases 1 and 5 have no Invariants block and say nothing about them), the one-line note for a
    phase 3 or 6 with no table, and no claim at all on a skipped phase, from a flat or reordered list; the
    section carries the table as `invariantView` reads it, and nothing of it leaks into phases 0, 2, 4."""
    table = inv_table(inv_row(evidence=[evid()]))
    blocks = {n: design_view(n, invariants=table)["stage"] for n in (1, 3, 5, 6)}
    assert {n: s["blocks"] for n, s in blocks.items()} == {
        1: ["diagrams", "folds"], 3: ["invariants", "diagrams", "folds"],
        5: ["diagrams", "folds"], 6: ["invariants", "diagrams", "folds"]}
    assert blocks[3]["invariants"] == one("invariantView", table) and blocks[3]["noInvariants"] is None
    assert blocks[1]["invariants"] is None and blocks[1]["noInvariants"] is None
    assert blocks[5]["invariants"] is None and blocks[5]["noInvariants"] is None
    none = design_view(3, invariants=None)["stage"]
    assert none["invariants"] is None and none["noInvariants"] == "The spec has no § System invariants."
    no_spec = design_view(3, invariants=None, artefacts=[{**doc("m1", "docs/models/feat.md", "model"), "lines": 40}])["stage"]
    assert no_spec["invariants"] is None and no_spec["noInvariants"] == "No spec among this phase's documents."
    verdicts = [verdict(n, "skip" if n == 3 else "pass" if n < 3 else "todo", reason="r" if n == 3 else None)
                for n in range(7)]
    skipped = view(derived_row(phase=4, verdicts=verdicts), workflow_map({3: [doc("s1", "s.md", "spec")]},
                   {3: [section()]}, {3: [diagram()]}, {3: table}), chosen=3)["stage"]
    assert skipped["blocks"] == ["diagrams", "folds"] and skipped["invariants"] is None and skipped["noInvariants"] is None
    plain = view(derived_row(phase=4), workflow_map({4: [doc("s1", "s.md", "spec")]}, None, None, {4: table}),
                 chosen=4)["stage"]
    assert plain["invariants"] is None and plain["noInvariants"] is None and plain["blocks"] == []


def test_the_design_stage_puts_diagrams_before_the_folded_texts():
    """witness phase 3 with 2 gate sections, 2 documents, a diagram and a ticket-less row - distinguishes the
    design layout (diagrams, then the folds: sections first, then documents; `cards` only for links) from
    the plain one, and a section's length (from its own `lines`) from its document's."""
    shown = design_view(3)["stage"]
    assert shown["layout"] == "design"
    assert [d["title"] for d in shown["diagrams"]] == ["Model › Flow"]
    assert set(shown["diagrams"][0]) == {"key", "title", "source", "doc", "generated", "mermaid"}
    assert [(f["id"], f["kind"], f["lines"], f["open"], f["state"]) for f in shown["folds"]] == [
        ("sec-a", "section", 64, False, "present"), ("sec-m", "section", 92, False, "present"),
        ("s1", "spec", 120, False, "present"), ("m1", "model", 40, False, "present")]
    assert shown["folds"][0]["title"] == "System Architecture" and shown["folds"][0]["source"] == "docs/specs/feat.md"
    assert shown["cards"] == [] and shown["openId"] is None and shown["nothing"] is None
    assert shown["counters"]["artefacts"] == 2   # the counter still counts the documents
    pr = design_view(6, links={"pr": "https://example.com/pr/1", "ticket": None, "tracker": None})["stage"]
    assert [(c["kind"], c["href"]) for c in pr["cards"]] == [("link", "https://example.com/pr/1")]
    assert [view(derived_row(phase=n), workflow_map(), chosen=n)["stage"]["layout"] for n in range(7)] == [
        "plain", "design", "plain", "design", "plain", "design", "design"]


def test_a_fold_survives_a_refresh_and_resets_with_the_row():
    """witness `sec-m` opened in phase 3, the map answered again (a refresh: a new object, same content),
    then a panel for another row (`openFolds` empty) - distinguishes state kept in the page's per-row
    `openFolds` (open after the refresh, closed with the new row, closed in another phase) from state
    kept in the map or the DOM."""
    opened = {"3": {"$set": ["sec-m"]}}
    first = design_view(3, open_folds=opened)["stage"]
    refreshed = design_view(3, open_folds=opened)["stage"]
    again = [f["id"] for f in refreshed["folds"] if f["open"]]
    assert [f["id"] for f in first["folds"] if f["open"]] == ["sec-m"] and again == ["sec-m"]
    assert [f["id"] for f in design_view(3, open_folds={})["stage"]["folds"] if f["open"]] == []
    assert [f["id"] for f in design_view(1, open_folds=opened)["stage"]["folds"] if f["open"]] == []


def test_fold_toggled_is_pure():
    """witness an opened set toggled twice and another phase's set - distinguishes a new object per call
    (the input untouched, the other phase kept) from an in-place update; the input is read back after the
    call through the runner's `$observe:` form - witness the caller's own Set after the toggle - distinguishes
    a copy from an in-place toggle."""
    start = {"1": {"$set": ["x"]}, "3": {"$set": ["q"]}}
    seen = one("$observe:foldToggled", start, 3, "a")
    assert seen["result"] == {"1": {"$set": ["x"]}, "3": {"$set": ["q", "a"]}}
    assert seen["args"][0] == start
    assert seen["args"][2:] == ["a"]
    opened = one("foldToggled", {"1": {"$set": ["x"]}}, 3, "a")
    assert opened == {"1": {"$set": ["x"]}, "3": {"$set": ["a"]}}
    assert one("foldToggled", opened, 3, "a") == {"1": {"$set": ["x"]}, "3": {"$set": []}}
    assert one("foldToggled", opened, 3, "b")["3"] == {"$set": ["a", "b"]}
    assert one("foldToggled", {}, 6, "z") == {"6": {"$set": ["z"]}}


def test_stage_keys_ignore_open_folds_and_follow_diagram_sources():
    """witness the same stage with a fold open and closed, a diagram source changed by one character, and a
    fold whose length changed - distinguishes an art key without the open flag (a drawn diagram stays drawn)
    that still moves with the sources, the fold ids and their lengths."""
    def art(open_folds=None, mermaid="flowchart LR\n  A --> B", lines=92):
        sections = [{**section("Model", id="sec-m", rev="rm"), "lines": lines}]
        return one("stageKeys", design_view(3, open_folds=open_folds, diagrams=[diagram(mermaid=mermaid)],
                                            sections=sections))["art"]
    base = art()
    assert art(open_folds={"3": {"$set": ["sec-m"]}}) == base
    assert art(mermaid="flowchart LR\n  A --> C") != base
    assert art(lines=93) != base
    other = one("stageKeys", design_view(3, diagrams=[diagram(), diagram(title="Model › Sequence", mermaid="x")]))["art"]
    assert other != one("stageKeys", design_view(3))["art"]
    # an invariant whose status moved (a matrix landed) redraws the stage; the same table does not
    pinned = lambda status: one("stageKeys", design_view(3, invariants=inv_table(inv_row(status=status))))["art"]
    assert pinned("pinned") == pinned("pinned") and pinned("pinned") != pinned("blind")
    assert pinned("pinned") != one("stageKeys", design_view(3, invariants=None))["art"]


def test_a_design_phase_with_no_diagram_has_none_and_a_skipped_one_has_nothing():
    """witness phase 3 with no diagram and phase 3 skipped - distinguishes an empty `diagrams` (the page
    says so in one line) from a missing key, and a skipped phase with no folds and no diagrams."""
    assert design_view(3, diagrams=[])["stage"]["diagrams"] == []
    verdicts = [verdict(n, "skip" if n == 3 else "pass" if n < 3 else "todo", reason="r" if n == 3 else None)
                for n in range(7)]
    skipped = view(derived_row(phase=4, verdicts=verdicts), workflow_map({3: [doc("s1", "s.md", "spec")]},
                   {3: [section()]}, {3: [diagram()]}), chosen=3)["stage"]
    assert skipped["diagrams"] == [] and skipped["folds"] == []


def test_invariant_view_counts_and_orders_statuses():
    """witness a table of seven laws in the order blind, pinned, never-mutated, pinned, test-missing,
    not-checkable, pinned (one law with two tests), a table of two statuses, an unreadable table and null -
    distinguishes the heading counts in the fixed order (pinned, blind, never mutated, test missing, not
    checkable; a status with none left out; the total in the heading) and the cards in the spec's order, each
    with its status word, its test as written, and its `matrix mutant outcome` evidence line (a line per test;
    the reason in words when no matrix counts), from counts in the order met, from the row's raw status, or
    from a card with no evidence; an unreadable table is its raw text and reason, no cards; null is null."""
    rows = [inv_row(1, "L1", "blind", [evid(matrix="m2", mutant="M5", outcome="blind-test")]),
            inv_row(2, "L2", "pinned", [evid()]),
            inv_row(3, "L3", "never-mutated", [evid(matrix=None, mutant=None, outcome=None, at=None)]),
            inv_row(4, "L4", "pinned", [evid(), evid(test="tests/a.py::test_y", matrix="m4", mutant="M9")],
                    verified="`tests/a.py::test_x`, `::test_y`"),
            inv_row(5, "L5", "test-missing", [evid(matrix=None, mutant=None, outcome=None, at=None)]),
            inv_row(6, "L6", "not-checkable", [], verified="`scripts/check_x.py`", negation=None),
            inv_row(7, "L7", "pinned", [evid()])]
    shown = one("invariantView", inv_table(*rows, source="docs/specs/feat.md"))
    assert set(shown) == {"heading", "counts", "cards", "raw", "why", "tree", "source"} and shown["raw"] is None and shown["why"] is None
    assert shown["source"] == "docs/specs/feat.md § System invariants"
    assert shown["tree"] == "status from /repo/feat"
    assert one("invariantView", inv_table(inv_row(1, "x", "not-checkable", []), tree=None, why="the spec is in no git repo"))["tree"] is None
    assert one("invariantView", inv_table(inv_row(1, "x", "not-checkable", []), tree=None, why="the spec is in no git repo"))["why"] == "the spec is in no git repo"
    assert shown["heading"] == "Invariants · 7"
    assert [(c["status"], c["label"], c["n"]) for c in shown["counts"]] == [
        ("pinned", "pinned", 3), ("blind", "blind", 1), ("never-mutated", "never mutated", 1),
        ("test-missing", "test missing", 1), ("not-checkable", "not checkable", 1)]
    assert [(c["n"], c["law"], c["status"], c["word"]) for c in shown["cards"]] == [
        (1, "L1", "blind", "blind"), (2, "L2", "pinned", "pinned"), (3, "L3", "never-mutated", "never mutated"),
        (4, "L4", "pinned", "pinned"), (5, "L5", "test-missing", "test missing"),
        (6, "L6", "not-checkable", "not checkable"), (7, "L7", "pinned", "pinned")]
    cards = shown["cards"]
    assert cards[0]["evidence"] == ["m2 M5 blind-test"] and cards[1]["evidence"] == ["m1 M3 pinned"]
    assert cards[3]["evidence"] == ["m1 M3 pinned", "m4 M9 pinned"]
    assert cards[2]["evidence"] == ["the test exists, no matrix row targets it"]
    assert cards[4]["evidence"] == ["no def in the file today"]
    assert cards[5]["evidence"] == ["names no test: shown as written"]
    assert [(c["negation"], c["test"]) for c in (cards[0], cards[3], cards[5])] == [
        ("a negation", "`tests/a.py::test_x`"), ("a negation", "`tests/a.py::test_x`, `::test_y`"),
        (None, "`scripts/check_x.py`")]
    two = one("invariantView", inv_table(inv_row(1, "a", "blind", [evid(outcome="blind-test")]), inv_row(2, "b")))
    assert [(c["status"], c["n"]) for c in two["counts"]] == [("pinned", 1), ("blind", 1)]
    raw = one("invariantView", {"source": "docs/specs/feat.md", "readable": False,
                                "raw": "| Module | Owns |\n|---|---|", "why": "no column reads as law"})
    assert raw == {"heading": "Invariants", "counts": [], "cards": [], "raw": "| Module | Owns |\n|---|---|",
                   "why": "no column reads as law", "source": "docs/specs/feat.md § System invariants"}
    assert one("invariantView", None) is None
    assert one("invariantView", inv_table())["heading"] == "Invariants · 0"


def test_an_unreadable_spec_file_gives_its_reason_and_no_raw_text():
    """witness an unreadable invariants table whose raw text is the empty string (the spec file could not be
    opened, `why` holds the OSError) - distinguishes a view with no raw text and the reason from one that
    carries `raw: ""` (an empty <pre> under a claim that the columns are wrong)."""
    shown = one("invariantView", {"source": "docs/specs/feat.md", "readable": False, "raw": "", "why": "Permission denied"})
    assert shown["raw"] is None and shown["why"] == "Permission denied"
    assert shown["cards"] == [] and shown["counts"] == []


# --- ticket 130 (design-diagrams): motion ------------------------------------------------------------------

NO_MOTION = {"rows": "none", "fold": "none", "stage": "none", "draw": "none"}


def env(reduced=False, view_transitions=True, interpolate_size=True):
    return {"reduced": reduced, "viewTransitions": view_transitions, "interpolateSize": interpolate_size}


def test_reduced_motion_silences_all_four_and_a_missing_api_silences_only_its_rows_or_its_folds():
    """witness every combination of the three flags - distinguishes `reduced` that silences ALL four motions even
    with every API present, `rows` that needs View Transitions and `fold` that needs interpolate-size (each
    independent of the other), from a function that lets one flag stand in for another or ignores `reduced`."""
    for vt in (True, False):
        for isz in (True, False):
            assert one("motionOf", env(reduced=True, view_transitions=vt, interpolate_size=isz)) == NO_MOTION
    full = one("motionOf", env())
    assert full == {"rows": "transition", "fold": "height", "stage": "fade", "draw": "in"}
    assert one("motionOf", env(view_transitions=False)) == {**full, "rows": "none"}
    assert one("motionOf", env(interpolate_size=False)) == {**full, "fold": "none"}
    assert one("motionOf", env(view_transitions=False, interpolate_size=False)) == {
        "rows": "none", "fold": "none", "stage": "fade", "draw": "in"}


def test_motion_ms_within_the_spec_range():
    """witness the constant read as exported - distinguishes the five durations all inside the spec's
    180-280 ms (story 13) and named exactly rows, fold, stage, draw, zoom from a missing key or a motion that
    would slow reading."""
    ms = one("MOTION_MS")
    assert sorted(ms) == ["draw", "fold", "rows", "stage", "zoom"]
    assert all(isinstance(v, int) and 180 <= v <= 280 for v in ms.values()), ms
