"""Tests for app/static/theme.js, the pure choice logic of the window's theme (#87, Decisions 5 and 7).

Seam: `node tests/app/theme_runner.mjs` loads the real theme.js and runs the functions on JSON cases
(stdin -> stdout); a case may carry a fake Storage (`{"$storage": {...}}`). No DOM, no dependency. Skipped,
saying why, where `node` is not on PATH. The DOM half (`applyTheme`, `bootTheme`, the switches) is covered by
tests/app/smoke/panel_smoke.mjs (S18-S25, S44).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parent / "theme_runner.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH: the theme's logic tests run theme.js through node (design Decision 5)")


def call(*cases, raw=False):
    """The results of `(function, args...)` cases, one node run for all of them. A case that holds a fake storage
    answers `{"$result", "$items"}` from the runner; `raw` keeps that, otherwise only the result is returned."""
    payload = json.dumps([[name, list(args)] for name, *args in cases])
    done = subprocess.run(["node", str(RUNNER)], input=payload, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, f"theme_runner failed ({done.returncode}): {done.stderr}"
    results = json.loads(done.stdout)
    for result in results:
        assert not (isinstance(result, dict) and "$error" in result), result["$error"]
        assert not (isinstance(result, dict) and "$undefined" in result), "a function answered undefined"
    if raw:
        return results
    return [r["$result"] if isinstance(r, dict) and "$result" in r else r for r in results]


def one(name, *args, raw=False):
    return call((name, *args), raw=raw)[0]


def storage(items=None, throws=None):
    return {"$storage": {"items": items or {}, "throws": throws}}


def test_a_stored_choice_wins_over_the_system_in_both_directions():
    assert call(("effectiveTheme", "light", True), ("effectiveTheme", "dark", False)) == ["light", "dark"]


def test_with_nothing_stored_the_theme_follows_the_system():
    assert call(("effectiveTheme", None, True), ("effectiveTheme", None, False)) == ["dark", "light"]


def test_a_stored_value_that_is_not_a_theme_is_ignored_and_the_system_decides():
    for junk in ("blue", "", "Dark"):
        assert one("readTheme", storage({"wf-theme": junk})) is None, junk
        assert one("effectiveTheme", junk, True) == "dark", junk
        assert one("effectiveTheme", junk, False) == "light", junk


def test_a_stored_light_or_dark_is_read_back_as_the_choice():
    assert call(("readTheme", storage({"wf-theme": "light"})),
                ("readTheme", storage({"wf-theme": "dark"})),
                ("readTheme", storage())) == ["light", "dark", None]


def test_reading_a_storage_that_throws_or_is_missing_gives_no_choice():
    assert call(("readTheme", storage({"wf-theme": "dark"}, throws="get")), ("readTheme", None)) == [None, None]


def test_saving_into_a_storage_that_throws_reports_false_and_does_not_raise():
    assert call(("saveTheme", storage(throws="set"), "dark"), ("saveTheme", None, "dark")) == [False, False]


def test_saving_writes_the_theme_under_the_key_wf_theme():
    out = one("saveTheme", storage(), "dark", raw=True)
    assert out == {"$result": True, "$items": {"wf-theme": "dark"}}


def test_a_click_is_kept_for_the_page_when_the_system_changes_later_even_if_the_storage_refuses():
    spec = {"items": {}, "throws": "both", "systemDark": False}
    kept = one("bootScenario", spec, ["click", "system:dark", "system:light"])
    assert kept == ["light", "dark", "dark", "dark"]


def test_with_nothing_chosen_the_theme_follows_the_system_while_it_changes():
    spec = {"items": {}, "systemDark": False}
    assert one("bootScenario", spec, ["system:dark", "system:light"]) == ["light", "dark", "light"]


def test_a_click_that_is_stored_wins_over_a_later_system_change():
    spec = {"items": {}, "systemDark": True}
    assert one("bootScenario", spec, ["click", "system:light", "system:dark"]) == ["dark", "light", "light", "light"]


def test_the_switch_goes_to_the_other_theme():
    assert call(("nextTheme", "light"), ("nextTheme", "dark")) == ["dark", "light"]


def test_the_diagram_variables_come_from_the_tokens_of_the_theme():
    light = {"--surface": "#fcfaf6", "--fg": "#1d1c1a", "--edge": "#1d1c1a", "--dim": "#66625a",
             "--surface2": "#f0ece3", "--bg": "#f6f3ec"}
    dark = {"--surface": "#1b1d1c", "--fg": "#e9eeeb", "--edge": "#a8b5ae", "--dim": "#8e9a94",
            "--surface2": "#232625", "--bg": "#131413"}
    a, b = call(("mermaidVariables", light), ("mermaidVariables", dark))
    for out, tokens in ((a, light), (b, dark)):
        assert out.get("primaryColor") == tokens["--surface"]
        assert out.get("primaryTextColor") == tokens["--fg"] and out.get("textColor") == tokens["--fg"]
        assert out.get("primaryBorderColor") == tokens["--edge"]
        assert out.get("lineColor") == tokens["--dim"]
        assert out.get("secondaryColor") == tokens["--surface2"]
        assert out.get("tertiaryColor") == tokens["--bg"] and out.get("background") == tokens["--bg"]
        assert out.get("edgeLabelBackground") == tokens["--surface"]
    assert a.get("primaryColor") != b.get("primaryColor") and a.get("textColor") != b.get("textColor")
