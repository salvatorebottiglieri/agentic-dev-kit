"""B-004 wiring tests — the JSONL report checks must be reached by audit.main.

The checks existed but were never called (handoff-b024-b004-promotion): the
audit printed ``ok`` while no report was read. These tests pin the gatherers,
the repo scoping, the brief manifest, and the fact that ``main`` surfaces a
violation from a report.
"""
from __future__ import annotations

import json
import sys
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


def test_gather_reporter_filters_repo_and_infers_feature(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "feature-a-spec.jsonl").write_text(_row() + "\n", encoding="utf-8")
    (reports / "feature-b-spec.jsonl").write_text(
        _row(repo="other/repo") + "\n", encoding="utf-8"
    )
    rows = audit.gather_reviewer_reports(
        "o/r", reports_dir=reports, handoffs_dir=tmp_path / "none"
    )
    assert len(rows) == 1
    assert rows[0]["_feature"] == "feature-a"
    assert rows[0]["_axis"] == "spec"


def test_gather_reporter_reads_handoff_dirs_and_dedupes(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "feature-a-spec.jsonl").write_text(_row() + "\n", encoding="utf-8")
    archived = tmp_path / "handoffs" / "agentic-5061-reports"
    archived.mkdir(parents=True)
    (archived / "agentic-5061-s44-review.jsonl").write_text(
        _row() + "\n", encoding="utf-8"
    )
    (archived / "feature-a-spec.jsonl").write_text(_row() + "\n", encoding="utf-8")
    rows = audit.gather_reviewer_reports(
        "o/r", reports_dir=reports, handoffs_dir=tmp_path / "handoffs"
    )
    assert len(rows) == 2
    assert {r["_feature"] for r in rows} == {"feature-a", "agentic-5061-s44"}


def test_gather_reporter_rejects_malformed_jsonl(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "bad-spec.jsonl").write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        audit.gather_reviewer_reports(
            "o/r", reports_dir=reports, handoffs_dir=tmp_path / "none"
        )


def test_gather_brief_items_filters_repo(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "feature-a-brief.json").write_text(
        json.dumps({"repo": "o/r", "feature": "feature-a", "items": ["1", "2"]}),
        encoding="utf-8",
    )
    (reports / "feature-b-brief.json").write_text(
        json.dumps({"repo": "other/repo", "feature": "feature-b", "items": ["1"]}),
        encoding="utf-8",
    )
    assert audit.gather_brief_items(
        "o/r", reports_dir=reports, handoffs_dir=tmp_path / "none"
    ) == {"feature-a": ["1", "2"]}


def test_gather_brief_items_rejects_a_bad_manifest(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "feature-a-brief.json").write_text(
        json.dumps({"repo": "o/r", "feature": "feature-a", "items": "1"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="items"):
        audit.gather_brief_items(
            "o/r", reports_dir=reports, handoffs_dir=tmp_path / "none"
        )


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


def test_main_surfaces_a_b004_violation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(audit, "gather_prs", lambda repo, since: [])
    monkeypatch.setattr(audit, "gather_issues", lambda repo, since: [])
    monkeypatch.setattr(
        audit, "gather_gate_config", lambda repo: {"configured": {}, "ci_text": ""}
    )
    monkeypatch.setattr(audit, "gather_handoffs", lambda days=7: [])
    monkeypatch.setattr(audit, "gather_known_refs", lambda repo: set())
    monkeypatch.setattr(
        audit, "gather_reviewer_reports",
        lambda repo: [{
            "repo": repo, "actor": "", "criterion": "4", "claim": "coverage",
            "item_id": "1", "verdict": "pass", "severity": "fatal",
            "reviewer": "spec", "status": "resolved", "_feature": "f",
        }],
    )
    monkeypatch.setattr(audit, "gather_brief_items", lambda repo: {})
    monkeypatch.setattr(audit, "gather_pr_diffs", lambda repo, reports: {})
    monkeypatch.setattr(sys, "argv", ["audit.py", "--repo", "o/r"])

    assert audit.main() == 1
    out = capsys.readouterr().out
    assert "missing actor" in out or "actor" in out
