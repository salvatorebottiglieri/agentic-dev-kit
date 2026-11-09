#!/usr/bin/env python3
"""check_fixtures — validate the provenance manifest of external fixtures.

B-022: a test fixture that stands in for a system the repo does not own must
declare where it came from. The manifest is a markdown table (default
``tests/fixtures/PROVENANCE.md``) with one row per external fixture:

    | fixture | source | reference | sanitized / why |
    |---|---|---|---|
    | `tests/fixtures/external/jira.json` | captured | Jira dev, 2026-09-03 | removed e-mail |
    | `tests/fixtures/external/stripe.json` | contract | docs/contracts/stripe.md | n/a |
    | `tests/fixtures/external/sast.xml` | synthetic | none available | real report under NDA |

- ``captured``: a real sample, sanitized; ``sanitized`` says what was removed
  (write ``none`` if nothing was).
- ``contract``: taken from a named contract document; ``reference`` names it.
- ``synthetic``: invented because no real sample is available; the fourth
  column says why.

The checker proves the row's shape and that every listed fixture exists. It
does **not** prove that the fixture is external or that the source is the right
one: that is the Spec axis's judgment (REVIEW-CRITERIA.md (g)). A scope can be
passed to prove that no file in a directory was forgotten.

    python3 guards/check_fixtures.py <PROVENANCE.md> [repo-root] [--scope DIR]

Exit 0 = manifest consistent; exit 1 = violation(s); exit 2 = usage/read error.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_SOURCES = ("captured", "contract", "synthetic")
IGNORED = {"PROVENANCE.md", "README.md", "README", ".gitkeep", ".DS_Store"}
REQUIRED = ("fixture", "source", "reference", "sanitized")


def _norm(cell: str) -> str:
    """Header names are prose: ``sanitized / why`` → ``sanitized``."""
    return re.sub(r"[^a-z]+", " ", cell.lower()).strip()


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean(value: str) -> str:
    return value.strip().strip("`").strip().strip("*").strip()


def parse_manifest(text: str) -> list[dict]:
    """Parse the first markdown table with a ``fixture`` + ``source`` header.

    A missing table returns ``[]``; ``check_rows`` turns that into a violation.
    Unknown columns are ignored, so a repo may add ``used_in`` or ``owner``
    without breaking the checker.
    """
    header: dict[str, int] | None = None
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            if header is not None and rows:
                break
            continue
        cells = _cells(line)
        if header is None:
            names = [_norm(c) for c in cells]
            if "fixture" in names and "source" in names:
                header = {}
                for i, name in enumerate(names):
                    if name.startswith("sanitized"):
                        header["sanitized"] = i
                    elif name in ("fixture", "source", "reference"):
                        header[name] = i
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if "fixture" in [_norm(c) for c in cells]:
            continue  # a repeated header is not a data row
        row: dict = {"line": 0}
        for name in REQUIRED:
            idx = header.get(name)
            row[name] = _clean(cells[idx]) if idx is not None and idx < len(cells) else ""
        rows.append(row)
    return rows


def check_rows(
    rows: list[dict], root: Path, scope: Path | None = None
) -> list[str]:
    """Validate parsed rows. Pure: no filesystem writes, no network."""
    root = Path(root).resolve()
    if not rows:
        return ["manifest has no fixture table or no rows (B-022)"]

    violations: list[str] = []
    seen: set[str] = set()
    for row in rows:
        fixture = row.get("fixture", "")
        source = row.get("source", "").lower()
        reference = row.get("reference", "")
        sanitized = row.get("sanitized", "")
        if not fixture:
            violations.append("row without a fixture path")
            continue
        rel = Path(fixture)
        if rel.is_absolute():
            violations.append(f"{fixture}: fixture path must be relative to the repo root")
            continue
        path = (root / rel).resolve()
        if root not in path.parents:
            violations.append(f"{fixture}: fixture path escapes the repo root")
            continue
        if not path.is_file():
            violations.append(f"{fixture}: fixture does not exist")
        if rel.as_posix() in seen:
            violations.append(f"{fixture}: duplicate row")
        seen.add(rel.as_posix())
        if source not in ALLOWED_SOURCES:
            violations.append(
                f"{fixture}: source '{source or '(missing)'}' is not one of "
                f"{'/'.join(ALLOWED_SOURCES)}"
            )
            continue
        if source in ("captured", "contract") and not reference:
            violations.append(f"{fixture}: {source} fixture needs a reference")
        if source == "captured" and not sanitized:
            violations.append(
                f"{fixture}: captured fixture needs a sanitized note (write 'none' if nothing was removed)"
            )
        if source == "synthetic" and not sanitized:
            violations.append(f"{fixture}: synthetic fixture needs a why")

    if scope is not None:
        scope_path = Path(scope)
        if not scope_path.is_absolute():
            scope_path = root / scope_path
        scope_path = scope_path.resolve()
        if not scope_path.is_dir():
            violations.append(f"scope '{scope}' is not a directory")
        else:
            for path in sorted(scope_path.rglob("*")):
                if not path.is_file() or path.name in IGNORED:
                    continue
                rel = path.relative_to(root).as_posix()
                if rel not in seen:
                    violations.append(
                        f"{rel}: file in scope has no provenance row (B-022)"
                    )
    return violations


def main(argv: list[str]) -> int:
    scope: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--scope":
            if i + 1 >= len(argv):
                print("check_fixtures: --scope needs a directory", file=sys.stderr)
                return 2
            scope = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--scope="):
            scope = arg.split("=", 1)[1]
            i += 1
            continue
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        positional.append(arg)
        i += 1

    if not positional:
        print("usage: check_fixtures.py <PROVENANCE.md> [repo-root] [--scope DIR]",
              file=sys.stderr)
        return 2
    manifest = Path(positional[0])
    root = Path(positional[1]) if len(positional) > 1 else Path(".")
    if not manifest.is_file():
        print(f"check_fixtures: manifest not found: {manifest}", file=sys.stderr)
        return 2
    try:
        rows = parse_manifest(manifest.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"check_fixtures: cannot read {manifest}: {exc}", file=sys.stderr)
        return 2
    violations = check_rows(rows, root, Path(scope) if scope else None)
    if violations:
        print(f"check_fixtures: {len(violations)} violation(s):")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(f"check_fixtures: ok — {len(rows)} fixture(s) with provenance")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
