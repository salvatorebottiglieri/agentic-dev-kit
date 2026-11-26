"""B-004 wiring tests — the report checks must be reached by audit.main.

The checks existed but were never called (handoff-b024-b004-promotion): the
audit printed ``ok`` while no report was read. These tests pin the gatherers
(which read the report store only, #70), the repo scoping, the brief manifest,
and the fact that ``main`` surfaces a violation from a report.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit


def _row(**kw) -> str:
    base = {
        "criterion": "4",
        "claim": "code",
        "item_id": "1",
        "verdict": "pass",
        "severity": "advisory",
        "reviewer": "spec",
        "repo": "o/r",
        "actor": "spec-1",
        "quote": "x",
        "commit": "abc",
    }
    base.update(kw)
    return json.dumps(base)


def _imported(tmp_path: Path, files: dict[str, str]) -> Path:
    """A store made by importing evidence files (`rel path -> text`) from a workflow dir under
    tmp_path, the way `wf.py report import` does: a row takes its feature as the import gives it."""
    wd = tmp_path / "wd"
    for rel, text in files.items():
        (wd / rel).parent.mkdir(parents=True, exist_ok=True)
        (wd / rel).write_text(text, encoding="utf-8")
    mod = audit._store_module()
    conn = mod.open_store(tmp_path / "imported.sqlite")
    try:
        mod.import_files(conn, wd, [Path(rel) for rel in files], datetime.now())
    finally:
        conn.close()
    return tmp_path / "imported.sqlite"


def _rejection(tmp_path: Path, files: dict[str, str]) -> str:
    """`BatchRejected: <reason>` of an import that is refused (the module is loaded by path, so the
    class cannot be named here)."""
    try:
        _imported(tmp_path, files)
    except Exception as err:  # noqa: BLE001
        return f"{type(err).__name__}: {err}"
    return "accepted"


def test_gather_reviewer_reports_filters_repo_and_infers_feature(tmp_path: Path) -> None:
    store = _imported(tmp_path, {
        "reports/feature-a-spec.jsonl": _row() + "\n",
        "reports/feature-b-spec.jsonl": _row(repo="other/repo") + "\n",
    })
    rows = audit.gather_reviewer_reports("o/r", store=store)
    assert len(rows) == 1
    assert rows[0]["_feature"] == "feature-a"
    assert rows[0]["_axis"] == "spec"


def test_gather_reviewer_reports_counts_a_row_stored_twice_once(tmp_path: Path) -> None:
    row = json.loads(_row())
    store = _fill_store(tmp_path / "r.sqlite", [
        (row, "feature-a", "feature-a-spec"),
        (row, "feature-a", "feature-a-spec"),               # the same row imported from an archived copy
        (row, "example-5061-s44", "example-5061-s44-review"),
    ])
    rows = audit.gather_reviewer_reports("o/r", store=store)
    # witness: three stored rows, two distinct (feature, axis) — a reader that keeps every row gives 3
    assert len(rows) == 2
    assert {r["_feature"] for r in rows} == {"feature-a", "example-5061-s44"}


def test_a_malformed_report_file_is_refused_by_the_import(tmp_path: Path) -> None:
    got = _rejection(tmp_path, {"reports/bad-spec.jsonl": "{not json}\n"})
    assert got.startswith("BatchRejected:") and "line 1 is not JSON" in got


def test_gather_brief_items_filters_repo(tmp_path: Path) -> None:
    store = _imported(tmp_path, {
        "reports/feature-a-brief.json": json.dumps({"repo": "o/r", "feature": "feature-a", "items": ["1", "2"]}),
        "reports/feature-b-brief.json": json.dumps({"repo": "other/repo", "feature": "feature-b", "items": ["1"]}),
    })
    assert audit.gather_brief_items("o/r", store=store) == {"feature-a": ["1", "2"]}


def test_a_bad_brief_manifest_is_refused_by_the_import(tmp_path: Path) -> None:
    got = _rejection(tmp_path, {
        "reports/feature-a-brief.json": json.dumps({"repo": "o/r", "feature": "feature-a", "items": "1"})})
    assert got.startswith("BatchRejected:") and "items must be a list" in got


def test_gather_pr_diffs_builds_the_patch(monkeypatch) -> None:
    monkeypatch.setattr(
        audit, "_gh",
        lambda args: {"files": [{"patch": "+added\n"}, {"patch": "-removed\n"}]},
    )
    diffs = audit.gather_pr_diffs("o/r", [{"commit": "abc"}])
    assert diffs == {"abc": "+added\n-removed"}


def test_check_report_actor_detects_a_missing_actor() -> None:
    assert audit.check_report_actor([{"actor": "spec-1", "_feature": "f"}]) == []
    violations = audit.check_report_actor([{"_feature": "f", "item_id": "1"}])
    assert any("actor" in v for v in violations)


def test_main_surfaces_a_b004_violation(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(audit, "gather_prs", lambda repo, since: [])
    monkeypatch.setattr(audit, "gather_issues", lambda repo, since: [])
    monkeypatch.setattr(
        audit, "gather_gate_config", lambda repo: {"configured": {}, "ci_text": ""}
    )
    monkeypatch.setattr(audit, "gather_handoffs", lambda days=7: [])
    monkeypatch.setattr(audit, "gather_known_refs", lambda repo: set())
    monkeypatch.setattr(
        audit, "gather_reviewer_reports",
        lambda repo, store=None: [{
            "repo": repo, "actor": "", "criterion": "4", "claim": "coverage",
            "item_id": "1", "verdict": "pass", "severity": "fatal",
            "reviewer": "spec", "status": "resolved", "_feature": "f",
        }],
    )
    monkeypatch.setattr(audit, "gather_brief_items", lambda repo, store=None: {})
    monkeypatch.setattr(audit, "gather_feature_dates", lambda repo, store=None: {})
    monkeypatch.setattr(audit, "gather_pr_diffs", lambda repo, reports: {})
    store = _fill_store(tmp_path / "r.sqlite", [])
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", "--store", str(store)])

    assert audit.main() == 1
    out = capsys.readouterr().out
    assert "missing actor" in out or "actor" in out


def _attribute_report_file(
    tmp_path: Path, briefs: list[str], report_name: str, brief_repo: str = "o/r"
) -> tuple[str, str]:
    files = {f"reports/{feature}-brief.json": json.dumps({"repo": brief_repo, "feature": feature, "items": ["1"]})
             for feature in briefs}
    files[f"reports/{report_name}"] = _row() + "\n"
    rows = audit.gather_reviewer_reports("o/r", store=_imported(tmp_path, files))
    return rows[0]["_feature"], rows[0]["_axis"]


@pytest.mark.parametrize(
    "name, axis",
    [
        ("phase-harness-spec.jsonl", "spec"),
        ("phase-harness-spec-r2.jsonl", "spec-r2"),
        ("phase-harness-r2-spec.jsonl", "r2-spec"),
    ],
)
def test_gather_reviewer_reports_feature_from_brief_with_dashed_axis(
    tmp_path: Path, name: str, axis: str
) -> None:
    assert _attribute_report_file(tmp_path, ["phase-harness"], name) == ("phase-harness", axis)


def test_gather_reviewer_reports_longest_brief_prefix_wins(tmp_path: Path) -> None:
    assert _attribute_report_file(tmp_path, ["demo", "demo-v2"], "demo-v2-spec.jsonl") == (
        "demo-v2",
        "spec",
    )


def test_gather_reviewer_reports_shorter_brief_still_matches(tmp_path: Path) -> None:
    assert _attribute_report_file(tmp_path, ["demo", "demo-v2"], "demo-spec.jsonl") == (
        "demo",
        "spec",
    )


def test_gather_reviewer_reports_feature_without_brief_keeps_last_dash_split(
    tmp_path: Path,
) -> None:
    assert _attribute_report_file(tmp_path, ["other"], "no-brief-spec-r2.jsonl") == (
        "no-brief-spec",
        "r2",
    )


def test_gather_reviewer_reports_longest_prefix_with_dashed_axis(tmp_path: Path) -> None:
    assert _attribute_report_file(tmp_path, ["demo", "demo-v2"], "demo-v2-spec-r2.jsonl") == (
        "demo-v2",
        "spec-r2",
    )


def test_gather_reviewer_reports_ignores_brief_declared_for_another_repo(
    tmp_path: Path,
) -> None:
    """Characterization: brief repo scoping in gather_brief_items is honoured."""
    assert _attribute_report_file(
        tmp_path, ["phase-harness"], "phase-harness-spec-r2.jsonl", brief_repo="x/y"
    ) == ("phase-harness-spec", "r2")


# --- #54, #70: the readers take the store only -------------------------------

ROOT = Path(__file__).resolve().parent.parent


def _fill_store(path: Path, rows: list[tuple[dict, str, str]], briefs: dict[str, list[str]] | None = None,
                repo: str = "o/r") -> Path:
    """Rows go in as given (`(row, feature, report)`), straight into the table, so that
    rows the `report add` command would refuse (no actor, a quote not in the commit) can
    be stored too: the readers must judge them, not the writer."""
    mod = audit._store_module()
    conn = mod.open_store(path)
    with conn:
        for row, feature, report in rows:
            conn.execute(
                "INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion,"
                " verdict, severity, status, actor, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("t", row["repo"], feature, report, row["item_id"], row["criterion"], row["verdict"],
                 row["severity"], row.get("status", "open"), row.get("actor", ""), json.dumps(row)))
    for feature, items in (briefs or {}).items():
        mod.add_brief_items(conn, repo, feature, items, datetime.now())
    conn.close()
    return path


def _four_checks(rows: list[dict], briefs: dict[str, list[str]], diffs: dict[str, str]) -> list[str]:
    found = [*audit.check_report_actor(rows), *audit.check_reviewer_evidence_verbatim(rows, diffs),
             *audit.check_i6_zero_actionable(rows)]
    for feature, items in sorted(briefs.items()):
        found += audit.check_brief_coverage([r for r in rows if r.get("_feature") == feature], items)
    return sorted(found)


def _open_fatal(item: str = "1", **kw) -> dict:
    return json.loads(_row(claim="coverage", item_id=item, verdict="fail", severity="fatal", **kw))


def test_the_four_checks_give_the_verdicts_of_the_stored_rows(tmp_path: Path) -> None:
    good_code = json.loads(_row(item_id="1", quote="the line", commit="c1"))
    corpus = [
        good_code,
        _open_fatal("2"),                                                    # I6: an open fatal
        {**json.loads(_row(item_id="3", claim="coverage")), "actor": ""},    # actor: none
        json.loads(_row(item_id="4", quote="not in the commit", commit="c1")),  # verbatim: a paraphrase
    ]
    briefs = {"f": ["1", "2", "3", "4", "9"]}                                # coverage: "9" has no row
    diffs = {"c1": "+the line\n"}
    store = _fill_store(tmp_path / "s" / "r.sqlite", [(r, "f", "f-spec") for r in corpus], briefs)
    got = _four_checks(audit.gather_reviewer_reports("o/r", store=store),
                       audit.gather_brief_items("o/r", store=store), diffs)
    # witness: one violation of each of the four checks — an empty list cannot pass
    assert [v.split("]")[0] for v in got] == ["[B-004", "[B-025/8", "[B-025/9", "[I6/B-025/10"]


def test_the_audit_reads_only_the_store(tmp_path: Path, monkeypatch) -> None:
    open_fatal = _open_fatal("1")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "x.jsonl").write_text(json.dumps(open_fatal) + "\n", encoding="utf-8")
    archived = tmp_path / "handoffs" / "y-reports"
    archived.mkdir(parents=True)
    (archived / "x-brief.json").write_text(
        json.dumps({"repo": "o/r", "feature": "x", "items": ["1", "2"]}), encoding="utf-8")
    monkeypatch.setattr(audit, "HANDOFFS_DIR", tmp_path / "handoffs")
    empty = _fill_store(tmp_path / "empty.sqlite", [])
    # witness: the file row is an open fatal and its brief lists two items — files still read would
    # give a row, an I6 violation and a brief
    assert audit.gather_reviewer_reports("o/r", store=empty) == []
    assert audit.gather_brief_items("o/r", store=empty) == {}
    stored = _fill_store(tmp_path / "stored.sqlite", [(json.loads(_row(item_id="1")), "x", "x-spec")], {"x": ["1"]})
    rows = audit.gather_reviewer_reports("o/r", store=stored)
    assert [(r["item_id"], r["verdict"], r["_feature"]) for r in rows] == [("1", "pass", "x")]
    assert audit.check_i6_zero_actionable(rows) == []
    assert audit.gather_brief_items("o/r", store=stored) == {"x": ["1"]}


def _stub_the_gh_calls(monkeypatch) -> None:
    for name, value in (("gather_prs", lambda repo, since: []), ("gather_issues", lambda repo, since: []),
                        ("gather_gate_config", lambda repo: {"configured": {}, "ci_text": ""}),
                        ("gather_handoffs", lambda days=7: []), ("gather_known_refs", lambda repo: set()),
                        ("gather_pr_diffs", lambda repo, reports: {})):
        monkeypatch.setattr(audit, name, value)


def test_the_audit_without_a_store_is_a_violation(tmp_path: Path, monkeypatch, capsys) -> None:
    _stub_the_gh_calls(monkeypatch)
    monkeypatch.setattr(audit, "check_recent_handoff", lambda handoffs: [])
    absent = tmp_path / "state" / "r.sqlite"
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", "--store", str(absent)])
    # witness: nothing else is wrong (every gatherer is stubbed empty) — without the violation the
    # audit would print `ok`, a pass that verified nothing; and it must not create the file or its folder
    assert audit.main() == 1
    assert f"[B-004] no report store at {absent}" in capsys.readouterr().out
    assert not absent.exists() and not absent.parent.exists()


def test_the_audit_tells_an_absent_store_file_from_its_folder(tmp_path: Path, monkeypatch, capsys) -> None:
    _stub_the_gh_calls(monkeypatch)
    monkeypatch.setattr(audit, "check_recent_handoff", lambda handoffs: [])
    # witness 1: the folder exists, the file does not — a check on the folder alone would say `ok`
    absent = tmp_path / "r.sqlite"
    assert absent.parent.is_dir() and not absent.exists()
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", "--store", str(absent)])
    assert audit.main() == 1
    assert f"[B-004] no report store at {absent}" in capsys.readouterr().out
    assert not absent.exists()
    # witness 2: a real, empty store at the path — the violation goes away
    present = _fill_store(tmp_path / "present.sqlite", [])
    assert present.is_file()
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", "--store", str(present)])
    audit.main()
    assert "no report store" not in capsys.readouterr().out


def test_a_stored_row_belongs_to_its_feature_column(tmp_path: Path) -> None:
    store = _fill_store(tmp_path / "r.sqlite", [
        (_open_fatal("1"), "alpha", "beta-spec"),
        (_open_fatal("2"), "alpha", "alpha-spec-r2"),
        (_open_fatal("3", repo="other/repo"), "alpha", "alpha-spec"),
    ])
    rows = audit.gather_reviewer_reports("o/r", store=store)
    # witness: feature `alpha`, report `beta-spec` — `_feature_axis` on the report name says `beta`;
    # the other repo's row is not read
    assert [(r["_feature"], r["_axis"]) for r in rows] == [("alpha", "beta-spec"), ("alpha", "spec-r2")]


def test_a_missing_store_is_no_store_and_is_not_created(tmp_path: Path) -> None:
    gone = tmp_path / "state" / "r.sqlite"
    # witness: a path that does not exist — a reader that opens it would create the file and its folder
    assert audit.gather_reviewer_reports("o/r", store=gone) == []
    assert audit.gather_brief_items("o/r", store=gone) == {}
    assert not gone.exists() and not gone.parent.exists()


def test_a_store_that_is_not_sqlite_is_a_runtime_error(tmp_path: Path) -> None:
    bad = tmp_path / "r.sqlite"
    bad.write_text("this is not a database, just text that is long enough " * 20)
    with pytest.raises(RuntimeError, match="store"):
        audit.gather_reviewer_reports("o/r", store=bad)
    with pytest.raises(RuntimeError, match="store"):
        audit.gather_brief_items("o/r", store=bad)


def test_audit_main_reads_the_store_named_by_flag_or_env(tmp_path: Path, monkeypatch, capsys) -> None:
    _stub_the_gh_calls(monkeypatch)
    monkeypatch.setattr(audit, "HANDOFFS_DIR", tmp_path / "no-handoffs")
    stores = {name: _fill_store(tmp_path / name / "r.sqlite", [(_open_fatal(name), "f", "f-spec")],
                                {"f": [name]}) for name in ("FLAG", "ENV", "XDG")}
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "XDG" / "state"))
    default = audit._store_module().default_store_path({"XDG_STATE_HOME": str(tmp_path / "XDG" / "state")}, tmp_path)
    default.parent.mkdir(parents=True)
    stores["XDG"].rename(default)

    def audited(*extra: str) -> str:
        monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r", *extra])
        assert audit.main() == 1
        return capsys.readouterr().out

    # witness: three stores each holding a different open item — the one named in the output is the
    # one read, which tells the precedence (flag, then variable, then the XDG default) from any one source
    monkeypatch.setenv("AGENT_REPORT_STORE", str(stores["ENV"]))
    out = audited("--store", str(stores["FLAG"]))
    assert "item_id=FLAG" in out and "item_id=ENV" not in out and "item_id=XDG" not in out
    out = audited()
    assert "item_id=ENV" in out and "item_id=FLAG" not in out and "item_id=XDG" not in out
    monkeypatch.delenv("AGENT_REPORT_STORE")
    out = audited()
    assert "item_id=XDG" in out and "item_id=ENV" not in out and "item_id=FLAG" not in out
