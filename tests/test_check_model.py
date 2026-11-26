"""Mutation tests for guards/check_model.py — the feature-model gate.

Every test feeds a deliberately violating model and asserts the violation is
detected; the valid twin asserts the compliant model passes. The adapter test
proves the Python skeleton is extracted from the AST, not hand-written.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "guards" / "check_model.py"
ADAPTER = ROOT / "guards" / "model_facts_python.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


CM = _load("check_model_under_test", TOOL)
FACTS_MOD = _load("model_facts_python_under_test", ADAPTER)

FACTS = {
    "schema": "model-facts/1",
    "language": "python",
    "modules": [
        {"id": "pkg.api", "path": "pkg/api.py"},
        {"id": "pkg.models", "path": "pkg/models.py"},
    ],
    "symbols": [
        {"id": "pkg.models.Order", "name": "Order", "kind": "class"},
        {"id": "pkg.api.create", "name": "create", "kind": "function"},
    ],
    "routes": [
        {"method": "POST", "path": "/orders", "handler": "pkg.api.create"},
    ],
    "states": [
        {"entity": "OrderStatus", "values": ["DRAFT", "PAID"]},
    ],
}


def _model(
    *,
    approval: str = "https://example.com/comment/1",
    lifecycle_moves: str = "yes",
    state_section: bool = True,
    element_code: str = "pkg.models.Order",
    element_kind: str = "entity",
) -> str:
    lines = [
        "---",
        "feature: demo",
        "area: core",
        "entities: [Order]",
        "issue: https://example.com/issues/1",
        "---",
        "",
        "# Model: demo",
        "",
        "## Lifecycle inventory",
        "",
        "| Entity | Moves in time? | Statechart |",
        "|---|---|---|",
        f"| Order | {lifecycle_moves} | required |",
        "",
        "## Elements",
        "",
        "| ID | Kind | Code | Source |",
        "|---|---|---|---|",
        f"| Order | {element_kind} | {element_code} | pkg/models.py:1 |",
        "| OrderStatus | entity | OrderStatus | pkg/models.py:3 |",
        "| API | module | pkg.api | pkg/api.py |",
        "",
        "## Flow",
        "",
        "```mermaid",
        "flowchart LR",
        "  API --> Order",
        "```",
        "",
        "## Sequence",
        "",
        "```mermaid",
        "sequenceDiagram",
        "  API->>Order: create",
        "```",
    ]
    if state_section:
        lines += [
            "",
            "## State machines",
            "",
            "### OrderStatus",
            "",
            "```mermaid",
            "stateDiagram-v2",
            "  %% OrderStatus",
            "  [*] --> Draft",
            "  Draft --> Paid",
            "  Paid --> [*]",
            "```",
        ]
    lines += [
        "",
        "## Model changes",
        "",
        "| # | Element | Change | Why | Approval |",
        "|---|---|---|---|---|",
        f"| 1 | Order | + | refunded state | {approval} |",
        "",
    ]
    text = "\n".join(lines)
    if not state_section:
        text = text.replace(
            "| OrderStatus | entity | OrderStatus | pkg/models.py:3 |\n", ""
        )
    return text


def test_valid_model_passes(tmp_path: Path) -> None:
    violations = CM.check_model_text(_model(), facts=FACTS, root=tmp_path)
    assert violations == []


def test_missing_approval_url_is_a_violation(tmp_path: Path) -> None:
    text = _model(approval="approved in chat")
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("approval URL" in v for v in violations), violations


def test_lifecycle_yes_without_state_machine_is_a_violation(tmp_path: Path) -> None:
    text = _model(state_section=False)
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("State machines is missing" in v for v in violations), violations


def test_lifecycle_no_does_not_require_state_machine(tmp_path: Path) -> None:
    text = _model(lifecycle_moves="no", state_section=False)
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert violations == []


def test_element_absent_from_facts_is_a_violation(tmp_path: Path) -> None:
    text = _model(element_code="pkg.models.Missing")
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("not found in code facts" in v for v in violations), violations


def test_unknown_element_kind_is_a_violation(tmp_path: Path) -> None:
    text = _model(element_kind="thing")
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("allowed" in v for v in violations), violations


def test_element_declared_but_not_in_a_diagram_is_a_violation(tmp_path: Path) -> None:
    text = _model().replace(
        "| API | module | pkg.api | pkg/api.py |",
        "| API | module | pkg.api | pkg/api.py |\n"
        "| Ghost | module | pkg.api | pkg/api.py |",
    )
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("Ghost" in v and "does not appear" in v for v in violations), violations


def test_missing_front_matter_is_a_violation(tmp_path: Path) -> None:
    violations = CM.check_model_text(_model().split("---", 2)[-1], facts=FACTS, root=tmp_path)
    assert any("front matter" in v for v in violations), violations


def test_lifecycle_note_after_yes_is_accepted(tmp_path: Path) -> None:
    text = _model(lifecycle_moves="yes — status + transitions")
    assert CM.check_model_text(text, facts=FACTS, root=tmp_path) == []


def test_two_lifecycle_entities_need_two_statecharts(tmp_path: Path) -> None:
    text = _model().replace(
        "| Order | yes | required |",
        "| Order | yes | required |\n| Payment | yes | required |",
    )
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("one per entity" in v for v in violations), violations


def test_state_machine_drift_is_a_violation(tmp_path: Path) -> None:
    text = _model().replace("Draft --> Paid", "Draft --> Cancelled")
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("drift" in v and "cancelled" in v.lower() for v in violations), violations


def test_model_change_mark_must_be_exact(tmp_path: Path) -> None:
    text = _model().replace("| 1 | Order | + |", "| 1 | Order | re-sync |")
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("must be exactly" in v for v in violations), violations


def test_cli_strict_without_facts(tmp_path: Path) -> None:
    model = tmp_path / "docs" / "models" / "demo.md"
    model.parent.mkdir(parents=True)
    model.write_text(_model(), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(model), "--root", str(tmp_path),
         "--gate", "--strict"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "no code facts" in proc.stdout or "no code facts" in proc.stderr


def test_adapter_extracts_python_facts(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "api.py").write_text(
        "from enum import Enum\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "class OrderStatus(Enum):\n"
        "    DRAFT = 'draft'\n"
        "    PAID = 'paid'\n"
        "\n"
        "@app.post('/orders')\n"
        "def create_order() -> dict:\n"
        "    return {}\n",
        encoding="utf-8",
    )
    facts = FACTS_MOD.extract(tmp_path)
    module_ids = {item["id"] for item in facts["modules"]}
    symbol_ids = {item["id"] for item in facts["symbols"]}
    routes = {(item["method"], item["path"]) for item in facts["routes"]}
    states = {item["entity"] for item in facts["states"]}
    assert "pkg.api" in module_ids
    assert "pkg.api.create_order" in symbol_ids
    assert ("POST", "/orders") in routes
    assert "OrderStatus" in states


def test_cli_passes_with_facts(tmp_path: Path) -> None:
    model = tmp_path / "docs" / "models" / "demo.md"
    model.parent.mkdir(parents=True)
    model.write_text(_model(), encoding="utf-8")
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(FACTS), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(model), "--root", str(tmp_path),
         "--facts", str(facts_path), "--gate"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_cli_fails_without_approval(tmp_path: Path) -> None:
    model = tmp_path / "docs" / "models" / "demo.md"
    model.parent.mkdir(parents=True)
    model.write_text(_model(approval="chat"), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(model), "--root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "approval URL" in proc.stdout


# --- #100 (gate-sections): the headings the model check contracts ---------------------------------

def test_contracted_sections_are_the_four_required_for_a_model_with_no_moving_entity() -> None:
    """witness an inventory that says `no` and a model that still carries `## Sequence` - the
    result is the four required headings in the check's order and nothing else (an optional
    heading is not contracted)."""
    text = _model(lifecycle_moves="no", state_section=False)
    assert CM.contracted_sections(text) == ("lifecycle inventory", "elements", "flow", "model changes")


def test_contracted_sections_add_state_machines_when_the_inventory_says_an_entity_moves() -> None:
    """witness a model whose inventory has a moving entity against the same model with its inventory
    turned to `no` - distinguishes a constant list from one that reads the inventory."""
    text = _model(lifecycle_moves="yes", state_section=True)
    assert CM.contracted_sections(text) == (
        "lifecycle inventory", "elements", "flow", "model changes", "state machines")
    assert "state machines" not in CM.contracted_sections(_model(lifecycle_moves="no", state_section=False))


def test_removing_state_machines_from_a_model_that_moves_makes_check_model_fail(tmp_path: Path) -> None:
    """pins that `state machines` is contracted: the check says it is missing."""
    text = _model(lifecycle_moves="yes", state_section=False)
    violations = CM.check_model_text(text, facts=FACTS, root=tmp_path)
    assert any("but ## State machines is missing" in v for v in violations), violations
