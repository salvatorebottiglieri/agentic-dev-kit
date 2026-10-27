#!/usr/bin/env python3
"""check_design — the checks of a DesignDoc, without a browser.

    coherence    the same thing said in several places must not contradict itself
    signatures   the declared signatures hold against the real code
    state        "new" / "modified" / "already present" verified against the code
    exits        every contract declares all four exit kinds (returns, absent,
                 raises, effect) and every dispatchable exit says WHERE it is handled
    frontier     a contract calling outside the repo declares its residual
                 (what happens to the exits it did not enumerate)

Phase 5: the design says how things will be built, and for functions that
*already exist* that claim can be verified **before** implementing. It is
needed because a document can be internally coherent and false about the
world: no check on its form sees that (B-023, "case B"), this one does — for
the part that concerns the signatures.

    python3 guards/check_design.py <design.md|design.json> [repo-root] [--quiet] [--gate]

`--gate` runs the subset that must exit **0** at pre-push and in CI —
signatures + coherence + exits + frontier — and **not** the state: the state's
premise ("the code does not exist yet") expires when the implementation
arrives, so at the gate it would be stale by construction. The state runs at
the end of phase 5, when the premise holds.

Verdicts, per function declared in the document:
    new            not in the code: the design proposes it, nothing to compare
    compatible     every required parameter of the real one is in the declared signature
    shortened      the document lists a subset: it omits only parameters with a
                   default, or parameters the framework passes (click commands)
    proposed       the document adds parameters the code does not have (proposed change)
    incompatible   the document omits a REQUIRED parameter of the code        [exit 1]

Exit 1 on incompatible signatures is deliberate: at the pre-push gate the same
script verifies that the implemented signatures respect the design contract
(I2), and a divergence must be declared or fixed, not ignored.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
             ".mypy_cache", "node_modules", "build", "dist"}
FRAMEWORK_MARKERS = ("command", "option", "argument", "group")

# The exits a contract must all declare. The four mandatory kinds are the
# mechanical way of saying "do not write only the happy path": a contract
# without `raises` does not state that the callee does not raise — it is
# silent, and silence is indistinguishable from a forgotten exit. This holds
# for library calls too (the external world), where the exception is exactly
# the exit one tends not to write.
EXIT_KINDS = ("returns", "absent", "raises", "effect")
# The exits "to be dispatched": they must say where they are handled. `effect`
# exits are only annotated (B-023): they are not dispatched, `handled_at` may
# be null.
DISPATCHABLE = ("returns", "absent", "raises")
NOT_A_PLACE = {"", "n/a", "n.a.", "none", "null", "-"}


def load_design(path: Path) -> dict:
    """A DesignDoc is a markdown file with the machine part in a fenced
    ```json design block. Pure JSON is accepted too, for fixtures."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    hit = re.search(r"```json design\s*\n(.*?)\n```", text, re.S)
    if not hit:
        raise SystemExit(f"{path}: no ```json design block found")
    return json.loads(hit.group(1))


def _enclosing_classes(tree: ast.AST) -> dict[ast.AST, str]:
    """Innermost `class` of every function node (absent for module-level functions)."""
    owner: dict[ast.AST, str] = {}

    def visit(node: ast.AST, cls: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and cls:
                owner[child] = cls
            visit(child, child.name if isinstance(child, ast.ClassDef) else cls)

    visit(tree, None)
    return owner


def module_matches(real_module: str, declared: str) -> bool:
    """`declared` names the module by its path or a trailing part of it, cut at a `/`:
    `workflow/tools.py` is `a/workflow/tools.py`, not `a_workflow/tools.py`."""
    return real_module == declared or real_module.endswith("/" + declared)


def real_functions(root: Path) -> dict[str, list[dict]]:
    """Every `def` in the repo, indexed by name."""
    out: dict[str, list[dict]] = {}
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        classes = _enclosing_classes(tree)
        for node in ast.walk(tree):
            # `ast.AsyncFunctionDef` is NOT a subclass of `ast.FunctionDef`
            # (both derive from `stmt`); indexing only the latter makes every
            # `async def` invisible to the gate, which in an ASGI codebase is
            # most of the code.
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            pos = [a.arg for a in args.args if a.arg not in ("self", "cls")]
            ndef = len(args.defaults)
            required = (pos[: len(pos) - ndef] if ndef else pos)
            optional = (pos[len(pos) - ndef:] if ndef else [])
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                (optional if default is not None else required).append(arg.arg)
            decos = [ast.unparse(d) for d in node.decorator_list]
            out.setdefault(node.name, []).append({
                "module": str(path.relative_to(root)),
                "cls": classes.get(node),
                "line": node.lineno,
                "required": required,
                "optional": optional,
                "framework": any(m in d for d in decos for m in FRAMEWORK_MARKERS),
                "returns": ast.unparse(node.returns) if node.returns else None,
            })
    return out


def declared_defs(doc: dict) -> list[dict]:
    """The functions the document declares, with their arguments."""
    out = []
    for entry in doc.get("signatures", []):
        if (entry.get("language") or "python") != "python":
            continue
        where = entry.get("module") or ""
        path = re.search(r"[\w./-]+\.py", where)
        for cls, name, raw in _defs_with_class(entry.get("code") or ""):
            out.append({"name": name, "args": raw, "cls": cls,
                        "module": path.group(0) if path else None,
                        "in": where})
    return out


def _defs_with_class(code: str) -> list[tuple[str | None, str, str]]:
    """`(class, name, args)` of each `def` in a declared snippet. The snippet is
    pseudo-code (bodies are `...`), so the class comes from indentation, not from `ast`."""
    out, stack = [], []          # stack: (indent, class name)
    for line in code.splitlines():
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if not text:
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        cls = re.match(r"class\s+(\w+)", text)
        if cls:
            stack.append((indent, cls.group(1)))
            continue
        fn = re.match(r"(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", text)
        if fn:
            out.append((stack[-1][1] if stack else None, fn.group(1), fn.group(2)))
    return out


def _split_top_level(raw: str) -> list[str]:
    """Split an argument list on the commas outside brackets and quotes: the commas in
    `dict[str, Any]`, `(1, 2)` or `sep=","` belong to one argument."""
    parts, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(raw):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(raw[start:i])
            start = i + 1
    parts.append(raw[start:])
    return parts


def tokens(raw: str) -> list[str]:
    names = []
    for part in _split_top_level(raw):
        t = part.split(":")[0].split("=")[0].strip()
        if not t or t in ("*", "/") or t.startswith("*") or t in ("self", "cls"):
            continue
        names.append(t)
    return names


def classify(decl: dict, real: dict) -> tuple[str, str]:
    given = set(tokens(decl["args"]))
    known = set(real["required"]) | set(real["optional"])
    missing = [p for p in real["required"] if p not in given]
    if missing and not real["framework"]:
        return "incompatible", "missing " + ", ".join(missing)
    if missing:
        return "shortened", "the framework passes " + ", ".join(missing)
    extra = [p for p in given if p not in known]
    if extra:
        return "proposed", "adds " + ", ".join(sorted(extra))
    return "compatible", ""


def check_states(doc: dict, real: dict[str, list[dict]]) -> list[str]:
    """The declared state of an element (new / modified / already present) is a
    claim about the world, and the world can be looked at: if a node is declared
    new and exists in the code, the document is wrong."""
    out = []
    for node in (doc.get("data_flow") or {}).get("nodes", []):
        kind = node.get("kind")
        if kind not in ("add", "mod", "keep"):
            continue
        name = node["id"].split(".")[-1]
        module = node.get("module")
        here = [c for c in real.get(name, []) if not module or module_matches(c["module"], module)]
        if kind == "add" and here:
            out.append(f"{node['id']}: declared NEW, but it exists in "
                       f"{here[0]['module']}:{here[0]['line']}")
        elif kind in ("mod", "keep") and not here:
            where = f" in {module}" if module else " in the repo"
            out.append(f"{node['id']}: declared {kind.upper()}, but it does not exist{where}")
    return out


def check_coherence(doc: dict) -> list[str]:
    """The same thing is said in several places of the document (contract site,
    nodes, edges): here we verify they do not contradict each other. Signatures
    are not compared: they live in one place (`signatures`)."""
    out: list[str] = []
    flow = doc.get("data_flow") or {}
    nodes = flow.get("nodes") or []
    edges = flow.get("edges") or []
    contracts = doc.get("contracts") or []
    ids = {n["id"] for n in nodes}
    short = lambda i: str(i).split(".")[-1]
    declared = {d["name"].lstrip("_") for d in declared_defs(doc)}

    for c in contracts:
        if c.get("site") not in ids:
            out.append(f"contract with site «{c['site']}» that is not a declared node")
        targets = [c["callee"]] if c.get("callee") else (c.get("callees") or [])
        for t in targets:
            if t not in ids:
                out.append(f"contract of {c['site']} towards «{t}» that is not a declared node")
            elif not any(e["from"] == c["site"] and e["to"] == t for e in edges):
                out.append(f"contract of {c['site']} for {t}, but no edge draws it")
    for e in edges:
        if e["from"] not in ids:
            out.append(f"edge from «{e['from']}»: not a declared node")
        if e["to"] not in ids:
            out.append(f"edge to «{e['to']}»: not a declared node")
    for e in edges:
        ok = False
        for c in contracts:
            if c.get("site") != e["from"]:
                continue
            if c.get("callee") == e["to"] or e["to"] in (c.get("callees") or []):
                ok = True
            elif not c.get("callee") and not c.get("callees") and short(e["to"]) in (c.get("call") or ""):
                ok = True
        if not ok:
            out.append(f"call {e['from']} → {short(e['to'])} drawn but without a contract of the exits")
    for n in nodes:
        name = short(n["id"])
        if re.search(r"[\s/]", name):
            continue
        if name.lstrip("_") not in declared:
            out.append(f"node {n['id']} without a signature declared in `signatures`")
    variants: dict[str, set] = {}
    for c in contracts:
        if c.get("callee") or c.get("callees"):
            continue
        for name, args in re.findall(r"([A-Za-z_][\w.]*)\(([^)]*)\)", c.get("call") or ""):
            variants.setdefault(name.split(".")[-1], set()).add(re.sub(r"\s+", " ", args).strip())
    for name, seen in variants.items():
        if len(seen) > 1:
            out.append(f"the signature of {name} is written in {len(seen)} different "
                       f"ways across the contracts")
    for n in nodes:
        used = (any(e["from"] == n["id"] or e["to"] == n["id"] for e in edges)
                or any(c.get("site") == n["id"] for c in contracts))
        if not used:
            out.append(f"node {n['id']} appears in no edge and no contract")
    return sorted(set(out))


def check_exits(doc: dict) -> list[str]:
    """The exits of every contract.

    Two rules, both falsifiable:
    - the four kinds (returns / absent / raises / effect) are **all** present:
      omitting `raises` is indistinguishable from a forgotten exit, and "do not
      write only the happy path" must be a structural condition, not an
      intention;
    - every dispatchable exit (all but `effect`) says WHERE it is handled: an
      empty `handled_at` or one reduced to "n/a" is the undispatched exit of
      B-023's rule. The bare "n/a" was the hole: one could write it to avoid
      answering. If the exit cannot happen, one declares **why**."""
    out: list[str] = []
    for c in doc.get("contracts") or []:
        where = c.get("site") or "?"
        towards = c.get("callee") or c.get("call") or ", ".join(c.get("callees") or []) or "?"
        kinds = [e.get("kind") for e in c.get("exits") or []]
        for k in EXIT_KINDS:
            if k not in kinds:
                out.append(f"contract {where} → {towards}: missing the exit «{k}»")
        for k in kinds:
            if k not in EXIT_KINDS:
                out.append(f"contract {where} → {towards}: exit «{k}» out of schema "
                           f"(allowed: {', '.join(EXIT_KINDS)})")
        for e in c.get("exits") or []:
            if e.get("kind") not in DISPATCHABLE:
                continue
            place = (e.get("handled_at") or "").strip()
            if place.lower() in NOT_A_PLACE:
                out.append(f"contract {where} → {towards}: the exit «{e.get('kind')}» "
                           f"({e.get('type')}) does not say where it is handled")
    return out


def check_frontier(doc: dict, root: Path) -> list[str]:
    """The frontier with the external world.

    When the contract calls OUTSIDE the repo — free text in `call`, or a node
    whose module is not a file of the repo (a ghost node) — the list of exits
    is **not verifiable**: no `ast` reads the exceptions a library raises. The
    contract must then declare the **residual**: what happens to what it did
    not enumerate. The check verifies that the residual is declared, NOT that
    the library's set is complete — the latter is a claim the check cannot
    keep, and pretending otherwise would be the self-confident false alarm of
    § 2.G.1."""
    out: list[str] = []
    nodes = {n["id"]: n for n in (doc.get("data_flow") or {}).get("nodes", [])}

    def outside(c: dict) -> bool:
        """Outside the repo: a call described in free text (`call`, without
        `callee`/`callees`, hence towards something that is not a node), or a
        node whose module is not a .py file of the repo (a ghost). A new module
        (`src/memex/integrity.py` not created yet) stays internal: its folder
        exists, it is our code that the design proposes."""
        if c.get("call") and not (c.get("callee") or c.get("callees")):
            return True
        targets = [c["callee"]] if c.get("callee") else (c.get("callees") or [])
        for t in targets:
            module = (nodes.get(t) or {}).get("module") or ""
            if module and (not module.endswith(".py") or not (root / module).parent.exists()):
                return True
        return False

    for c in doc.get("contracts") or []:
        if not outside(c):
            continue
        towards = c.get("call") or c.get("callee") or ", ".join(c.get("callees") or []) or "?"
        if not (c.get("residual") or "").strip():
            out.append(f"external contract {c.get('site')} → {towards}: missing `residual` "
                       f"(what happens to the non-enumerated exits: the library "
                       f"exceptions not foreseen)")
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    quiet = "--quiet" in argv
    gate = "--gate" in argv
    if not args:
        print(__doc__)
        return 2
    doc = load_design(Path(args[0]))
    root = Path(args[1] if len(args) > 1 else ".")
    real = real_functions(root)

    rows = []
    for decl in declared_defs(doc):
        cands = real.get(decl["name"], [])
        if decl["module"]:
            here = [c for c in cands if module_matches(c["module"], decl["module"])]
            cands = here or cands
        if decl["cls"]:
            # a method is compared with the method of ITS class, never with
            # a same-named one of another class (B-028)
            cands = [c for c in cands if c["cls"] == decl["cls"]]
        if not cands:
            rows.append((decl["name"], decl["module"] or decl["in"], "new", ""))
            continue
        real_fn = cands[0]
        verdict, note = classify(decl, real_fn)
        if len(cands) > 1:
            note = (note + " · " if note else "") + f"{len(cands)} definitions with this name"
        if decl["module"] and not module_matches(real_fn["module"], decl["module"]):
            note = (note + " · " if note else "") + f"in the code it is {real_fn['module']}"
        rows.append((decl["name"], f"{real_fn['module']}:{real_fn['line']}", verdict, note))

    order = {"incompatible": 0, "proposed": 1, "shortened": 2, "compatible": 3, "new": 4}
    rows.sort(key=lambda r: order.get(r[2], 9))
    width = max((len(r[0]) for r in rows), default=10)
    for name, where, verdict, note in rows:
        if quiet and verdict in ("compatible", "new"):
            continue
        print(f"  {name:<{width}}  {verdict:<14} {where}")
        if note:
            print(f"  {'':<{width}}  {'':<14} {note}")

    bad = [r for r in rows if r[2] == "incompatible"]
    # The design's state has a window: it is true only while the code does not
    # exist. At the gate (pre-push, after implementation) it would be stale by
    # construction, and a gate that always fails is one people learn to bypass.
    # So `--gate` does not check it; the state runs at the end of phase 5, when
    # its premise holds.
    state_problems = [] if gate else check_states(doc, real)
    incoherent = check_coherence(doc)
    exit_problems = check_exits(doc)
    frontier_problems = check_frontier(doc, root)
    counts = {v: sum(1 for r in rows if r[2] == v) for v in order}
    print("\n  " + " · ".join(f"{v}: {counts[v]}" for v in order if counts[v]) +
          f"  ({len(real)} function names in the repo)")
    if gate:
        print("\n  gate mode: signatures + coherence + exits + frontier "
              "(the design's state is not checked here: it expires when the code exists)")
    if incoherent:
        print("\n  the document contradicts itself:")
        for p in incoherent:
            print("    · " + p)
    if state_problems:
        print("\n  state inconsistent with the code:")
        for p in state_problems:
            print("    · " + p)
    if exit_problems:
        print("\n  exits not dispatched or out of schema:")
        for p in exit_problems:
            print("    · " + p)
    if frontier_problems:
        print("\n  external frontier without a residual:")
        for p in frontier_problems:
            print("    · " + p)
    if bad:
        print(f"\n  {len(bad)} signature(s) to reconcile: the document claims "
              f"something the code does not keep.")
    return 1 if (bad or state_problems or incoherent or exit_problems or frontier_problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
