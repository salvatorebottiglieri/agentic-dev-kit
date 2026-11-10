#!/usr/bin/env python3
"""ModelFacts adapter for Python — the mechanical skeleton of a model.

The checker (`guards/check_model.py`) never imports this module: it accepts
the JSON contract below, so any language can provide facts the same way.
This adapter is the v1 implementation for Python; a TypeScript adapter is a
later addition, and ``--facts <file>`` is the escape hatch for anything else.

Contract ``model-facts/1``::

    {
      "schema": "model-facts/1",
      "language": "python",
      "root": ".",
      "modules":   [{"id": "pkg.mod", "path": "pkg/mod.py",
                     "imports": ["pkg.other"]}],
      "symbols":   [{"id": "pkg.mod.func", "name": "func", "kind": "function",
                     "module": "pkg.mod", "calls": ["other"], "source": "..."}],
      "routes":    [{"method": "POST", "path": "/orders",
                     "handler": "pkg.mod.create"}],
      "states":    [{"entity": "OrderStatus", "values": ["DRAFT", "PAID"],
                     "source": "pkg/models.py:3"}],
      "state_candidates": [{"entity": "Order", "field": "status",
                            "source": "pkg/models.py:10"}],
      "entrypoints": ["pkg.mod.create"]
    }

The adapter is deliberately best-effort: it extracts what an AST can know
without executing the code. It does not resolve imports, aliases, or dynamic
dispatch. The model's semantics are authored; this is the skeleton that the
gate compares against.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SCHEMA = "model-facts/1"

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", "site-packages", ".tox",
}
SKIP_PARTS = {"tests", "test", "docs", "examples"}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
ROUTE_METHODS = {"route", "api_route"}
ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}


def _name(node: ast.AST | None) -> str:
    """Best-effort dotted name of a decorator/call target."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _module_id(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        parts = [root.name]
    return ".".join(parts)


def _first_string_arg(node: ast.Call) -> str | None:
    if node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _route_methods(node: ast.Call) -> list[str]:
    for keyword in node.keywords:
        if keyword.arg == "methods" and isinstance(
            keyword.value, (ast.List, ast.Tuple)
        ):
            methods = []
            for element in keyword.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    methods.append(element.value.upper())
            return methods or ["GET"]
    return ["GET"]


def _call_targets(node: ast.AST) -> list[str]:
    targets: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            target = _name(child.func)
            if target:
                targets.add(target)
    return sorted(targets)


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    return [_name(decorator) for decorator in node.decorator_list if _name(decorator)]


def _enum_values(node: ast.ClassDef) -> list[str]:
    values: list[str] = []
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    values.append(target.id)
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and not statement.target.id.startswith("_"):
                values.append(statement.target.id)
    return values


class _Visitor(ast.NodeVisitor):
    def __init__(self, module: str, path: str):
        self.module = module
        self.path = path
        self.symbols: list[dict] = []
        self.routes: list[dict] = []
        self.states: list[dict] = []
        self.state_candidates: list[dict] = []
        self.class_stack: list[str] = []

    def _symbol_id(self, name: str) -> str:
        return ".".join([self.module, *self.class_stack, name])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol_id = self._symbol_id(node.name)
        self.symbols.append({
            "id": symbol_id,
            "name": node.name,
            "kind": "class",
            "module": self.module,
            "calls": [],
            "decorators": _decorators(node),
            "source": f"{self.path}:{node.lineno}",
        })

        base_names = {_name(base).split(".")[-1] for base in node.bases}
        if base_names & ENUM_BASES:
            values = _enum_values(node)
            if values:
                self.states.append({
                    "entity": node.name,
                    "values": values,
                    "source": f"{self.path}:{node.lineno}",
                })

        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                target = statement.target
                if isinstance(target, ast.Name) and target.id in {"status", "state"}:
                    self.state_candidates.append({
                        "entity": node.name,
                        "field": target.id,
                        "source": f"{self.path}:{statement.lineno}",
                    })

        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        symbol_id = self._symbol_id(node.name)
        kind = "method" if self.class_stack else "function"
        self.symbols.append({
            "id": symbol_id,
            "name": node.name,
            "kind": kind,
            "module": self.module,
            "calls": _call_targets(node),
            "decorators": _decorators(node),
            "source": f"{self.path}:{node.lineno}",
        })

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            attribute = decorator.func
            if not isinstance(attribute, ast.Attribute):
                continue
            name = attribute.attr
            path = _first_string_arg(decorator)
            if path is None:
                continue
            if name in HTTP_METHODS:
                self.routes.append({
                    "method": name.upper(),
                    "path": path,
                    "handler": symbol_id,
                    "source": f"{self.path}:{node.lineno}",
                })
            elif name in ROUTE_METHODS:
                for method in _route_methods(decorator):
                    self.routes.append({
                        "method": method.upper(),
                        "path": path,
                        "handler": symbol_id,
                        "source": f"{self.path}:{node.lineno}",
                    })

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _module_imports(tree: ast.Module) -> list[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = "." * (node.level or 0) + (node.module or "")
            if base:
                imports.add(base)
                for alias in node.names:
                    imports.add(f"{base}.{alias.name}")
    return sorted(imports)


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if any(part in SKIP_PARTS for part in relative.parts[:-1]):
            continue
        yield path


def extract(root: Path) -> dict:
    """Extract ModelFacts from every Python file under *root*."""
    root = Path(root).resolve()
    modules: list[dict] = []
    symbols: list[dict] = []
    routes: list[dict] = []
    states: list[dict] = []
    state_candidates: list[dict] = []

    for path in _iter_python_files(root):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        relative = path.relative_to(root).as_posix()
        module_id = _module_id(root, path)
        modules.append({
            "id": module_id,
            "path": relative,
            "imports": _module_imports(tree),
        })

        visitor = _Visitor(module_id, relative)
        visitor.visit(tree)
        symbols.extend(visitor.symbols)
        routes.extend(visitor.routes)
        states.extend(visitor.states)
        state_candidates.extend(visitor.state_candidates)

    entrypoints = sorted({route["handler"] for route in routes})
    return {
        "schema": SCHEMA,
        "language": "python",
        "root": str(root),
        "modules": modules,
        "symbols": symbols,
        "routes": routes,
        "states": states,
        "state_candidates": state_candidates,
        "entrypoints": entrypoints,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()
    json.dump(extract(Path(args.root)), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
