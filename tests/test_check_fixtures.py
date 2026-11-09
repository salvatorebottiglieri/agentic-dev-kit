"""Mutation tests for guards/check_fixtures.py — the fixture-provenance gate.

B-022: a fixture that stands in for a system the repo does not own must declare
where it came from (captured / contract / synthetic), and a synthetic fixture
must say why. The checker validates the manifest's shape and that every listed
fixture exists; the Spec axis judges whether the fixture is external and
whether the source is the right one. Every test below fails if its check is
removed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "guards" / "check_fixtures.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_fixtures_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


CF = _load()


def _manifest(*rows: str, header: str | None = None) -> str:
    head = header or (
        "| fixture | source | reference | sanitized / why |\n"
        "|---|---|---|---|"
    )
    return "# Fixture provenance\n\n" + head + "\n" + "\n".join(rows) + "\n"


def _fixture(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "PROVENANCE.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_manifest_passes(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    _fixture(tmp_path, "tests/fixtures/external/stripe.json")
    _fixture(tmp_path, "tests/fixtures/external/sast.xml")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev, 2026-09-03 | removed e-mail |",
        "| `tests/fixtures/external/stripe.json` | contract | docs/contracts/stripe.md | n/a |",
        "| `tests/fixtures/external/sast.xml` | synthetic | none available | real report under NDA |",
    ))
    rows = CF.parse_manifest(path.read_text(encoding="utf-8"))
    assert CF.check_rows(rows, tmp_path) == []


def test_missing_fixture_file_is_a_violation(tmp_path: Path) -> None:
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev | none |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path)
    assert any("does not exist" in v for v in violations)


def test_invalid_source_is_a_violation(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | invented | Jira dev | none |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path)
    assert any("source" in v and "invented" in v for v in violations)


def test_captured_fixture_needs_sanitization_note(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev | |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path)
    assert any("sanitized" in v for v in violations)


def test_contract_fixture_needs_a_reference(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/stripe.json")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/stripe.json` | contract | | n/a |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path)
    assert any("reference" in v for v in violations)


def test_synthetic_fixture_needs_a_reason(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/sast.xml")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/sast.xml` | synthetic | none available | |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path)
    assert any("why" in v or "synthetic" in v for v in violations)


def test_duplicate_fixture_is_a_violation(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    row = "| `tests/fixtures/external/jira.json` | captured | Jira dev | none |"
    path = _write(tmp_path, _manifest(row, row))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path)
    assert any("duplicate" in v for v in violations)


def test_scope_flags_an_unlisted_file(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    _fixture(tmp_path, "tests/fixtures/external/forgotten.json")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev | none |",
    ))
    scope = tmp_path / "tests" / "fixtures" / "external"
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path, scope)
    assert any("forgotten.json" in v for v in violations)


def test_scope_outside_root_is_a_violation(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev | none |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path, tmp_path.parent)
    assert any("outside the repo root" in v for v in violations)


def test_scope_ignores_manifest_readme_and_gitkeep(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    scope = tmp_path / "tests" / "fixtures" / "external"
    (scope / "README.md").write_text("readme\n", encoding="utf-8")
    (scope / ".gitkeep").write_text("", encoding="utf-8")
    (scope / "PROVENANCE.md").write_text("manifest\n", encoding="utf-8")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev | none |",
    ))
    violations = CF.check_rows(CF.parse_manifest(path.read_text()), tmp_path, scope)
    assert violations == []


def test_missing_table_is_a_violation(tmp_path: Path) -> None:
    path = _write(tmp_path, "no table here\n")
    rows = CF.parse_manifest(path.read_text())
    violations = CF.check_rows(rows, tmp_path)
    assert any("table" in v for v in violations)


def test_empty_table_is_a_violation(tmp_path: Path) -> None:
    path = _write(tmp_path, _manifest())
    rows = CF.parse_manifest(path.read_text())
    violations = CF.check_rows(rows, tmp_path)
    assert any("no fixture" in v or "no rows" in v for v in violations)


def test_cli_exits_nonzero_on_violation(tmp_path: Path) -> None:
    path = _write(tmp_path, "no table here\n")
    assert CF.main([str(path), str(tmp_path)]) == 1


def test_cli_exits_zero_on_compliant_manifest(tmp_path: Path) -> None:
    _fixture(tmp_path, "tests/fixtures/external/jira.json")
    path = _write(tmp_path, _manifest(
        "| `tests/fixtures/external/jira.json` | captured | Jira dev | none |",
    ))
    assert CF.main([str(path), str(tmp_path)]) == 0
