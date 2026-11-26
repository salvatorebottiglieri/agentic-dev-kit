"""Mutation test of the coverage check of `gen-flow.py`.

The defect fixed (2026-10-11): coverage compared the map with the model, never
the model with the sources — an extraction that came back empty was not a miss,
so the map could lose all the phases (or all the rules, or all the checks) and
still print "coverage: complete". These tests falsify the check: for each
tampering with the sources, `emit` must produce at least one miss. If a test
stops failing, the check has gone blind again.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ("WORKFLOW.md", "INVARIANTS.md", "REVIEW-CRITERIA.md", "audit.py", "gen-flow.py")
DIRS = ("phases",)


def _copy(tmp: Path) -> None:
    for f in FILES:
        shutil.copy(ROOT / f, tmp / f)
    for d in DIRS:
        shutil.copytree(ROOT / d, tmp / d)


def _misses(tmp: Path) -> list[str]:
    spec = importlib.util.spec_from_file_location("gen_flow_under_test", tmp / "gen-flow.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.HERE = tmp
    mod.WORKFLOW = tmp / "WORKFLOW.md"
    mod.INVARIANTS = tmp / "INVARIANTS.md"
    mod.CRITERIA = tmp / "REVIEW-CRITERIA.md"
    mod.AUDIT = tmp / "audit.py"
    return mod.emit(mod.build_model())[1]


def _tampering(tmp: Path, name: str, old: str, new: str) -> list[str]:
    p = tmp / name
    text = p.read_text(encoding="utf-8")
    assert old in text, f"the tampering does not find {old!r} in {name} (source changed?)"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return _misses(tmp)


def test_intact_sources_give_no_misses(tmp_path: Path) -> None:
    _copy(tmp_path)
    assert _misses(tmp_path) == []


def test_pipeline_heading_renamed(tmp_path: Path) -> None:
    _copy(tmp_path)
    misses = _tampering(tmp_path, "WORKFLOW.md", "## Pipeline", "## PipelineX")
    assert any("phases" in m and "Pipeline" in m for m in misses), misses


def test_a_phase_lost_partially(tmp_path: Path) -> None:
    _copy(tmp_path)
    misses = _tampering(tmp_path, "WORKFLOW.md",
                             "\n| 5 | Program design", "\n| 5x | Program design")
    assert any("phases" in m and "6 rows" in m for m in misses), misses


def test_general_rules_heading_renamed(tmp_path: Path) -> None:
    _copy(tmp_path)
    misses = _tampering(tmp_path, "WORKFLOW.md", "## General rules", "## General rulesX")
    assert any("general rules" in m for m in misses), misses


def test_transversal_files_label_renamed(tmp_path: Path) -> None:
    _copy(tmp_path)
    misses = _tampering(tmp_path, "WORKFLOW.md", "Transversal files:", "Transversal filesX:")
    assert any("transversal files" in m for m in misses), misses


def test_audit_checks_renamed(tmp_path: Path) -> None:
    _copy(tmp_path)
    misses = _tampering(tmp_path, "audit.py", "def check_", "def validate_")
    assert any("check" in m for m in misses), misses


def test_a_rule_level_renamed_to_unrecognisable(tmp_path: Path) -> None:
    """The D-024 cornerstone: every load-bearing rule declares its level.
    A row whose level ('structure'/'script'/'prompt') `level_of` can no longer
    recognise is a rule without a level — `emit` must report it as a miss.
    Without this test, the level check of `gen-flow.py` could be removed and no
    other test would notice.
    """
    _copy(tmp_path)
    # "prompt (judgment: Spec axis)" is a unique string in WORKFLOW.md: only
    # one row of the enforcement table contains it (I2). Renaming it,
    # level_of no longer recognises the level of that rule.
    misses = _tampering(tmp_path, "WORKFLOW.md",
                             "prompt (judgment: Spec axis)",
                             "judgment-only")
    assert any("rule without an enforcement level" in m for m in misses), misses
