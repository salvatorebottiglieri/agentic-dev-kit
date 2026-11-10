#!/usr/bin/env python3
"""check_model — validate a feature model and its drift from the code.

A model is a Markdown file under ``docs/models/``. It carries the behavior a
human needs to understand the feature (flow, sequence, state machines) and a
table, ``## Elements``, that maps every diagram ID to a module/symbol/entity/
route in the code. The checker validates the document's shape and, with
``--gate``, that every declared element exists in the code facts.

Facts are language-agnostic: ``--facts <file>`` accepts the ``model-facts/1``
JSON contract (see ``guards/model_facts_python.py``), or the checker
auto-detects ``docs/models/facts.json``. Without either, it tries the Python
adapter; a repo in another language should provide its own facts file, or the
gate degrades to a warning (``--strict`` makes it a failure; the pre-push hook
runs with ``--strict``).

    python3 guards/check_model.py [MODEL.md ...] [--root DIR]
                                  [--facts FILE] [--gate] [--strict] [--json]

Exit 0 = consistent; 1 = violation(s); 2 = usage/read error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)
URL_RE = re.compile(r"https?://\S+")

REQUIRED_SECTIONS = ("lifecycle inventory", "elements", "flow", "model changes")
ALLOWED_KINDS = ("module", "symbol", "entity", "route", "external")

YES = {"yes", "y", "sì", "si", "true", "1"}
NO = {"no", "n", "false", "0", "—", "-", "n/a", "na", "none"}
CHANGE_MARKS = ("+", "~", "-")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _clean(value: str) -> str:
    return value.strip().strip("`").strip().strip("*").strip()


def _is_separator(cells: list[str]) -> bool:
    visible = [cell.replace(" ", "") for cell in cells if cell.strip()]
    return bool(visible) and all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in visible
    )


def parse_table(body: str) -> tuple[list[str], list[list[str]]]:
    """Return the header and data rows of the first Markdown table in *body*."""
    lines = [line for line in body.splitlines() if line.strip().startswith("|")]
    if not lines:
        return [], []
    header = [_clean(cell) for cell in _cells(lines[0])]
    rows: list[list[str]] = []
    for line in lines[1:]:
        cells = [_clean(cell) for cell in _cells(line)]
        if _is_separator(cells):
            continue
        if cells == header:
            continue
        rows.append(cells)
    return header, rows


def parse_front_matter(text: str) -> tuple[dict, str | None]:
    match = FRONT_RE.match(text)
    if not match:
        return {}, "missing YAML front matter (`---` ... `---`)"
    data: dict = {}
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            return {}, f"invalid front-matter line: {line!r}"
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [
                item.strip().strip('"').strip("'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        else:
            data[key] = value.strip('"').strip("'")
    return data, None


def split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer)
            current = match.group(1).strip().lower()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer)
    return sections


def mermaid_blocks(body: str) -> list[str]:
    return [match.group(1).strip() for match in MERMAID_RE.finditer(body)]


def _mermaid_kind(block: str) -> str:
    for line in block.splitlines():
        if line.strip():
            return line.strip().split()[0].lower()
    return ""


def _has_kind(blocks: list[str], kinds: set[str]) -> bool:
    return any(_mermaid_kind(block) in kinds for block in blocks)


def _check_elements(section: str) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    violations: list[str] = []
    header, rows = parse_table(section)
    if len(header) < 4:
        return [], ["## Elements: table needs the columns ID | Kind | Code | Source"]
    elements: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for cells in rows:
        if len(cells) < 4:
            violations.append(f"## Elements: row has {len(cells)} cells, expected 4")
            continue
        element_id, kind, code, source = cells[0], cells[1].lower(), cells[2], cells[3]
        if not element_id or not code or not source:
            violations.append(f"## Elements: incomplete row {cells!r}")
            continue
        if kind not in ALLOWED_KINDS:
            violations.append(
                f"## Elements: {element_id} has kind {kind!r}; "
                f"allowed: {', '.join(ALLOWED_KINDS)}"
            )
            continue
        if element_id in seen:
            violations.append(f"## Elements: duplicate ID {element_id!r}")
            continue
        seen.add(element_id)
        elements.append((element_id, kind, code, source))
    return elements, violations


def _verdict_token(value: str) -> str:
    """``yes — status + transitions`` → ``yes``; ``n/a`` stays ``n/a``."""
    return re.split(r"\s*(?:[—:-]|\s)\s*", value.strip(), maxsplit=1)[0].lower()


def _check_lifecycle(section: str) -> tuple[int, list[str]]:
    violations: list[str] = []
    header, rows = parse_table(section)
    if len(header) < 3:
        return 0, [
            "## Lifecycle inventory: table needs Entity | Moves in time? | Statechart"
        ]
    if not rows:
        return 0, [
            "## Lifecycle inventory: no rows — add a row, or skip the model "
            "with a reason"
        ]
    moving = 0
    for cells in rows:
        if len(cells) < 3:
            violations.append(
                f"## Lifecycle inventory: row has {len(cells)} cells, expected 3"
            )
            continue
        entity, raw_moves = cells[0], cells[1]
        if not entity:
            violations.append("## Lifecycle inventory: row without an entity")
            continue
        moves = _verdict_token(raw_moves)
        if moves in YES:
            moving += 1
        elif moves not in NO:
            violations.append(
                f"## Lifecycle inventory: {entity} has moves={raw_moves!r}; "
                "write yes or no (a note after — is fine)"
            )
    return moving, violations


def _check_model_changes(section: str) -> list[str]:
    violations: list[str] = []
    header, rows = parse_table(section)
    if len(header) < 5:
        return [
            "## Model changes: table needs # | Element | Change | Why | Approval"
        ]
    for cells in rows:
        if len(cells) < 5:
            violations.append(
                f"## Model changes: row has {len(cells)} cells, expected 5"
            )
            continue
        number, element, change, why, approval = cells[:5]
        if not number or not element or not why:
            violations.append(f"## Model changes: incomplete row {cells!r}")
            continue
        if change.strip() not in CHANGE_MARKS:
            violations.append(
                f"## Model changes: row {number} change {change!r} must be "
                "exactly +, ~ or -"
            )
        if not URL_RE.search(approval):
            violations.append(
                f"## Model changes: row {number} ({element}) has no approval URL"
            )
    return violations


def check_facts(
    elements: list[tuple[str, str, str, str]], facts: dict, root: Path
) -> list[str]:
    """Resolve every ## Elements row against the ModelFacts contract."""
    violations: list[str] = []
    modules = facts.get("modules") or []
    symbols = facts.get("symbols") or []
    routes = facts.get("routes") or []
    states = facts.get("states") or []

    module_ids = {str(item.get("id")) for item in modules}
    module_paths = {str(item.get("path")) for item in modules}
    symbol_ids = {str(item.get("id")) for item in symbols}
    symbol_by_name: dict[str, list[str]] = {}
    for item in symbols:
        symbol_by_name.setdefault(str(item.get("name")), []).append(str(item.get("id")))
    state_entities = {str(item.get("entity")) for item in states}
    route_keys = {
        f"{str(item.get('method')).upper()} {item.get('path')}" for item in routes
    }
    route_paths = {str(item.get("path")) for item in routes}
    route_handlers = {str(item.get("handler")) for item in routes}

    for element_id, kind, code, source in elements:
        if kind == "module":
            if code in module_ids or code in module_paths:
                continue
            if code.replace(".", "/") + ".py" in module_paths:
                continue
            violations.append(
                f"## Elements: {element_id} module {code!r} not found in code facts"
            )
        elif kind == "symbol":
            if code in symbol_ids:
                continue
            matches = symbol_by_name.get(code, [])
            if len(matches) == 1:
                continue
            violations.append(
                f"## Elements: {element_id} symbol {code!r} not found in code facts"
            )
        elif kind == "entity":
            if code in symbol_ids or code in state_entities:
                continue
            matches = symbol_by_name.get(code, [])
            if len(matches) == 1:
                continue
            violations.append(
                f"## Elements: {element_id} entity {code!r} not found in code facts"
            )
        elif kind == "route":
            if code in route_keys or code in route_paths or code in route_handlers:
                continue
            violations.append(
                f"## Elements: {element_id} route {code!r} not found in code facts"
            )
        elif kind == "external":
            # An external system has no code fact; a path-like source must exist.
            if code.startswith(("http://", "https://")):
                continue
            candidate = root / code
            if "/" in code or code.endswith((".md", ".json", ".yaml", ".yml")):
                if not candidate.exists():
                    violations.append(
                        f"## Elements: {element_id} external source {code!r} "
                        f"does not exist ({source})"
                    )
    return violations


def _state_machines(section: str) -> dict[str, str]:
    """Split ``## State machines`` into ``### <Entity>`` subsections."""
    parts = re.split(r"^###\s+(.+?)\s*$", section, flags=re.M)
    if len(parts) <= 1:
        return {}
    return {
        parts[index].strip().lower(): parts[index + 1]
        for index in range(1, len(parts), 2)
    }


def _state_names(block: str) -> set[str]:
    """Best-effort set of state names from a stateDiagram-v2 block."""
    names: set[str] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith(
            ("stateDiagram", "%%", "note", "end", "direction", "classDef", "class ", "state ")
        ):
            continue
        line = line.split(":")[0]
        for part in line.split("-->"):
            token = part.strip().split("{")[0].strip()
            if not token or token in ("[*]", "}"):
                continue
            names.add(re.sub(r"[^a-z0-9]+", "", token.lower()))
    return names


def _state_drift(
    elements: list[tuple[str, str, str, str]],
    state_sections: dict[str, str],
    facts: dict,
) -> list[str]:
    """Compare each ``### <Entity>`` state machine with the code's enum values."""
    violations: list[str] = []
    states_by_entity = {
        str(state.get("entity")).lower(): [
            str(value).lower() for value in (state.get("values") or [])
        ]
        for state in (facts.get("states") or [])
    }
    for heading, body in state_sections.items():
        match = next(
            (
                element
                for element in elements
                if element[0].lower() == heading
                or element[2].lower() == heading
                or element[2].split(".")[-1].lower() == heading
            ),
            None,
        )
        if match is None:
            continue
        code = match[2]
        values = states_by_entity.get(code.split(".")[-1].lower()) or states_by_entity.get(
            heading
        )
        if not values:
            continue
        model = _state_names("\n".join(mermaid_blocks(body)))
        code_values = {re.sub(r"[^a-z0-9]+", "", value) for value in values}
        missing = sorted(code_values - model)
        extra = sorted(model - code_values)
        if missing or extra:
            details = []
            if missing:
                details.append("missing in model: " + ", ".join(missing))
            if extra:
                details.append("not in code: " + ", ".join(extra))
            violations.append(
                f"## State machines: {heading} drift ({'; '.join(details)})"
            )
    return violations


def check_model_text(
    text: str, facts: dict | None = None, root: Path | None = None
) -> list[str]:
    """Check one model document.

    Pure except the external-source existence check in ``check_facts``.
    """
    root = Path(root or ".")
    violations: list[str] = []

    front, error = parse_front_matter(text)
    if error:
        return [error]
    for key in ("feature", "area", "issue"):
        if not front.get(key):
            violations.append(f"front matter: missing {key!r}")

    sections = split_sections(text)
    for required in REQUIRED_SECTIONS:
        if required not in sections:
            violations.append(f"missing section ## {required.title()}")

    flow_blocks = mermaid_blocks(sections.get("flow", ""))
    if "flow" in sections and not _has_kind(flow_blocks, {"flowchart", "graph"}):
        violations.append("## Flow: no ```mermaid flowchart/graph block")

    if "sequence" in sections:
        sequence_blocks = mermaid_blocks(sections["sequence"])
        if not _has_kind(sequence_blocks, {"sequencediagram"}):
            violations.append("## Sequence: no ```mermaid sequenceDiagram block")

    state_blocks = mermaid_blocks(sections.get("state machines", ""))
    if "state machines" in sections and not _has_kind(
        state_blocks, {"statediagram-v2", "statediagram"}
    ):
        violations.append("## State machines: no ```mermaid stateDiagram-v2 block")

    elements: list[tuple[str, str, str, str]] = []
    if "elements" in sections:
        elements, element_violations = _check_elements(sections["elements"])
        violations.extend(element_violations)

    moving = 0
    if "lifecycle inventory" in sections:
        moving, lifecycle_violations = _check_lifecycle(
            sections["lifecycle inventory"]
        )
        violations.extend(lifecycle_violations)
    state_sections = _state_machines(sections.get("state machines", ""))
    valid_state_blocks = [
        block for block in state_blocks
        if _mermaid_kind(block) in {"statediagram-v2", "statediagram"}
    ]
    if moving:
        if "state machines" not in sections:
            violations.append(
                "## Lifecycle inventory says an entity moves in time, "
                "but ## State machines is missing"
            )
        elif not valid_state_blocks:
            violations.append(
                "## Lifecycle inventory says an entity moves in time, "
                "but there is no valid state diagram"
            )
        else:
            state_count = len(state_sections) if state_sections else len(valid_state_blocks)
            if state_count < moving:
                violations.append(
                    f"## Lifecycle inventory says {moving} entities move in time, "
                    f"but there are only {state_count} state diagram(s); "
                    "one per entity (use `### <Entity>` headings)"
                )
            for heading, body in state_sections.items():
                if not _has_kind(
                    mermaid_blocks(body), {"statediagram-v2", "statediagram"}
                ):
                    violations.append(
                        f"## State machines: {heading} has no stateDiagram-v2 block"
                    )
    if facts and state_sections:
        violations.extend(_state_drift(elements, state_sections, facts))

    if "model changes" in sections:
        violations.extend(_check_model_changes(sections["model changes"]))

    all_mermaid = "\n".join(
        block for block in (flow_blocks + state_blocks + mermaid_blocks(sections.get("sequence", "")))
    )
    for element_id, _kind, _code, _source in elements:
        pattern = rf"(?<![\w.-]){re.escape(element_id)}(?![\w.-])"
        if not re.search(pattern, all_mermaid):
            violations.append(
                f"## Elements: {element_id} is declared but does not appear "
                "in any Mermaid diagram"
            )

    if facts:
        violations.extend(check_facts(elements, facts, root))
    return violations


def _load_facts(args, root: Path) -> tuple[dict | None, str]:
    if args.facts:
        path = Path(args.facts)
        if not path.is_file():
            print(f"check-model: facts file not found: {path}", file=sys.stderr)
            raise SystemExit(2)
    else:
        path = root / "docs" / "models" / "facts.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")), str(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"check-model: cannot read facts {path}: {exc}", file=sys.stderr)
            raise SystemExit(2)
    if args.adapter == "none":
        return None, "disabled"
    try:
        from model_facts_python import extract
    except ImportError as exc:  # pragma: no cover - packaging error
        return None, f"python adapter unavailable: {exc}"
    facts = extract(root)
    populated = any(facts.get(key) for key in ("modules", "symbols", "routes", "states"))
    return (facts, "python adapter") if populated else (None, "no Python sources")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="model files (default: docs/models/*.md)")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--facts", help="ModelFacts JSON file")
    parser.add_argument("--adapter", default="auto", choices=("auto", "none"),
                        help="facts adapter (default: auto)")
    parser.add_argument("--gate", action="store_true",
                        help="also resolve ## Elements against code facts")
    parser.add_argument("--strict", action="store_true",
                        help="a missing facts provider is a failure")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    paths = [Path(item) for item in args.paths]
    if not paths:
        models_dir = root / "docs" / "models"
        paths = sorted(models_dir.glob("*.md")) if models_dir.is_dir() else []
        paths = [path for path in paths if path.name != "README.md"]
    if not paths:
        if args.json:
            print(json.dumps({"violations": [], "facts": "none", "models": 0}))
        else:
            print("check-model: no model files")
        return 0

    facts: dict | None = None
    facts_source = "not requested"
    if args.gate:
        facts, facts_source = _load_facts(args, root)

    violations: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"{path}: cannot read: {exc}")
            continue
        current = check_model_text(text, facts=facts, root=root)
        for violation in current:
            violations.append(f"{path}: {violation}")

    if args.gate and facts is None:
        message = f"check-model: no code facts ({facts_source}); structural checks only"
        if args.strict:
            violations.append(message)
        else:
            print(message, file=sys.stderr)

    if args.json:
        print(json.dumps({
            "violations": violations,
            "facts": facts_source,
            "models": len(paths),
        }, indent=2))
    elif violations:
        print(f"check-model: {len(violations)} violation(s):")
        for violation in violations:
            print(f"  - {violation}")
    else:
        print(f"check-model: ok ({len(paths)} model(s), facts: {facts_source})")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
