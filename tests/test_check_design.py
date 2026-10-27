"""Mutation test of the two new rules of `check_design.py`.

- **exits**: every contract declares the four kinds (returns / absent / raises /
  effect) and every dispatchable exit says where it is handled — not only the
  happy path;
- **frontier**: a contract that calls outside the repo declares its `residual`
  (what happens to the non-enumerated exits).

If a test stops failing, the rule has gone back to being a prompt.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "guards" / "check_design.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_design_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


CD = _load()


def _exit(kind: str, handled: str | None, type_: str = "x") -> dict:
    return {"kind": kind, "type": type_, "handled_at": handled}


def _contract(**kw) -> dict:
    exits = kw.pop("exits", None) or [
        _exit("returns", "cli.py:1"), _exit("absent", "cli.py:2"),
        _exit("raises", "cli.py:3"), _exit("effect", None, "none")]
    c = {"site": "a", "exits": exits}
    c.update(kw)
    return c


def test_complete_contract_passes() -> None:
    assert CD.check_exits({"contracts": [_contract(callee="b")]}) == []


def test_missing_raises() -> None:
    exits = [_exit("returns", "x"), _exit("absent", "x"), _exit("effect", None)]
    doc = {"contracts": [_contract(exits=exits, callee="b")]}
    assert any("raises" in p for p in CD.check_exits(doc))


def test_exit_without_a_place() -> None:
    exits = [_exit("returns", None), _exit("absent", "x"),
             _exit("raises", "x"), _exit("effect", None)]
    doc = {"contracts": [_contract(exits=exits, callee="b")]}
    assert any("returns" in p and "handled" in p for p in CD.check_exits(doc))


def test_bare_n_a_is_not_a_place() -> None:
    exits = [_exit("returns", "x"), _exit("absent", "n/a"),
             _exit("raises", "x"), _exit("effect", None)]
    doc = {"contracts": [_contract(exits=exits, callee="b")]}
    assert any("absent" in p for p in CD.check_exits(doc))


def test_effect_without_a_place_is_allowed() -> None:
    problems = CD.check_exits({"contracts": [_contract(callee="b")]})
    assert all("effect" not in p for p in problems)


def test_external_call_without_residual(tmp_path: Path) -> None:
    doc = {"contracts": [_contract(call="Path.exists()")]}
    assert any("residual" in p for p in CD.check_frontier(doc, tmp_path))


def test_external_call_with_residual(tmp_path: Path) -> None:
    doc = {"contracts": [_contract(call="Path.exists()",
                                   residual="propagates to the caller")]}
    assert CD.check_frontier(doc, tmp_path) == []


def test_ghost_node_is_external(tmp_path: Path) -> None:
    doc = {"data_flow": {"nodes": [{"id": "lib.read_text", "module": "stdlib"}]},
           "contracts": [_contract(callee="lib.read_text")]}
    assert any("residual" in p for p in CD.check_frontier(doc, tmp_path))


def test_new_module_of_the_repo_stays_internal(tmp_path: Path) -> None:
    (tmp_path / "src" / "memex").mkdir(parents=True)
    doc = {"data_flow": {"nodes": [{"id": "integrity.check",
                                    "module": "src/memex/integrity.py"}]},
           "contracts": [_contract(callee="integrity.check")]}
    assert CD.check_frontier(doc, tmp_path) == []


def test_gate_ignores_the_stale_state(tmp_path: Path, capsys) -> None:
    """At the gate the design's state is stale by construction: a node declared
    `add` when the code exists is a violation at the end of phase 5, not at
    pre-push. `--gate` ignores it, the full mode sees it."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("def f() -> int:\n    return 1\n")
    doc = {
        "data_flow": {"nodes": [{"id": "mod.f", "module": "src/mod.py",
                                   "owner": None, "kind": "add"}]},
        "signatures": [{"module": "src/mod.py", "language": "python",
                         "code": "def f() -> int: ..."}],
        "contracts": [_contract(site="mod.f", call="Path.exists()",
                                  residual="propagates")],
    }
    design = tmp_path / "design.json"
    design.write_text(json.dumps(doc))
    argv = ["check_design", str(design), str(tmp_path)]
    assert CD.main(argv) == 1               # full: the state is false
    assert CD.main(argv + ["--gate"]) == 0  # gate: the state is not checked
    capsys.readouterr()


def test_async_functions_are_indexed(tmp_path: Path) -> None:
    """`ast.AsyncFunctionDef` is not a subclass of `ast.FunctionDef`: indexing
    only the latter makes every `async def` look new, so the gate can verify
    neither a signature nor a state against an ASGI codebase — where most
    functions are async. Found by the field test on an async project (#333)."""
    (tmp_path / "mod.py").write_text(
        "async def handler(a, b=1):\n    return a\n\n\ndef plain(c):\n    return c\n",
        encoding="utf-8")
    real = CD.real_functions(tmp_path)
    assert "handler" in real, "the async function is invisible to the gate"
    assert real["handler"][0]["required"] == ["a"]
    assert real["handler"][0]["optional"] == ["b"]
    assert "plain" in real


def test_state_of_an_async_function_is_visible(tmp_path: Path) -> None:
    """The concrete failure the missing index produced: a node declared `mod`
    on an existing async function was reported as non-existent."""
    (tmp_path / "mod.py").write_text("async def sync_it(x):\n    return x\n",
                                     encoding="utf-8")
    doc = {"data_flow": {"nodes": [{"id": "mod.sync_it", "module": "mod.py",
                                    "kind": "mod"}]}}
    assert CD.check_states(doc, CD.real_functions(tmp_path)) == []


def _declared_vs_code(tmp_path: Path, files: dict[str, str], module: str, code: str,
                      capsys) -> tuple[int, str]:
    """Run the gate over a repo made of `files` and a design declaring `code` in `module`."""
    for rel, src in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(src, encoding="utf-8")
    design = tmp_path / "design.json"
    design.write_text(json.dumps({"signatures": [
        {"module": module, "language": "python", "code": code}]}))
    status = CD.main(["check_design", str(design), str(tmp_path), "--gate"])
    return status, capsys.readouterr().out


def test_module_suffix_needs_a_path_boundary(tmp_path: Path, capsys) -> None:
    """B-028 (1): `workflow/tools.py` must not match `collection_builder_workflow/tools.py`.
    The declared signature is identical to the code in `workflow/`; the same name in the
    look-alike module has another one. Matching by bare suffix compared against the wrong
    file and reported `incompatible`."""
    status, out = _declared_vs_code(
        tmp_path,
        {"collection_builder_workflow/tools.py": "def run(a, b):\n    return a\n",
         "workflow/tools.py": "def run(a):\n    return a\n"},
        "workflow/tools.py", "def run(a): ...", capsys)
    assert status == 0, out
    assert "workflow/tools.py:1" in out and "collection_builder_workflow" not in out


def test_declared_class_selects_the_method(tmp_path: Path, capsys) -> None:
    """B-028 (2): four `_arun` in one module, one per tool. The declared signature sits
    inside `class DemoSavePageTool:`; it is compared with that class's method, not with
    the first `_arun` of the file."""
    src = ("class OtherTool:\n    def _arun(self, query, limit):\n        ...\n\n"
           "class DemoSavePageTool:\n    def _arun(self, page):\n        ...\n")
    status, out = _declared_vs_code(
        tmp_path, {"workflow/tools.py": src}, "workflow/tools.py",
        "class DemoSavePageTool:\n    def _arun(self, page): ...", capsys)
    assert status == 0, out
    assert "compatible" in out and "incompatible" not in out


def test_declared_class_really_incompatible_is_still_caught(tmp_path: Path, capsys) -> None:
    """Control for the class filter: if the method of the declared class does differ,
    the gate still fails — filtering by class must not make the check lenient."""
    src = ("class OtherTool:\n    def _arun(self, page):\n        ...\n\n"
           "class DemoSavePageTool:\n    def _arun(self, page, force):\n        ...\n")
    status, out = _declared_vs_code(
        tmp_path, {"workflow/tools.py": src}, "workflow/tools.py",
        "class DemoSavePageTool:\n    def _arun(self, page): ...", capsys)
    assert status == 1 and "incompatible" in out and "missing force" in out


def test_method_missing_in_the_declared_class_is_new(tmp_path: Path, capsys) -> None:
    """A `__init__` declared in a class that has none is new, even if other classes of the
    module have one — the same-mechanism false positive of B-028."""
    src = "class OtherTool:\n    def __init__(self, x, y):\n        ...\n\nclass Fresh:\n    pass\n"
    status, out = _declared_vs_code(
        tmp_path, {"workflow/tools.py": src}, "workflow/tools.py",
        "class Fresh:\n    def __init__(self, z): ...", capsys)
    assert status == 0 and "new" in out and "proposed" not in out, out


def test_state_module_match_needs_a_path_boundary(tmp_path: Path) -> None:
    """The same suffix looseness lived in `check_states`: a node declared `mod` in
    `workflow/tools.py` was found in `collection_builder_workflow/tools.py`."""
    (tmp_path / "collection_builder_workflow").mkdir()
    (tmp_path / "collection_builder_workflow" / "tools.py").write_text("def run(a):\n    ...\n")
    doc = {"data_flow": {"nodes": [{"id": "tools.run", "module": "workflow/tools.py",
                                    "kind": "mod"}]}}
    problems = CD.check_states(doc, CD.real_functions(tmp_path))
    assert problems and "does not exist" in problems[0]


def test_commas_inside_brackets_do_not_split_arguments() -> None:
    """B-029: `tokens()` split the argument list on every comma, so the type
    `dict[str, Any]` produced a phantom argument `Any]` and a false `proposed`."""
    assert CD.tokens("self, payload: dict[str, Any], limit: int = 3") == ["payload", "limit"]
    assert CD.tokens("cb: Callable[[int, str], bool], *, n=(1, 2)") == ["cb", "n"]
    assert CD.tokens('sep=",", rows: list[tuple[int, int]]') == ["sep", "rows"]


def test_bracketed_type_is_compatible_end_to_end(tmp_path: Path, capsys) -> None:
    """The same case through the gate: a declared signature identical to the code,
    with a multi-parameter generic, must be `compatible`, not `proposed`."""
    status, out = _declared_vs_code(
        tmp_path, {"m.py": "def call(name, args: dict[str, Any]):\n    ...\n"}, "m.py",
        "def call(name, args: dict[str, Any]): ...", capsys)
    assert status == 0 and "compatible" in out and "proposed" not in out, out


def test_a_really_extra_argument_is_still_proposed(tmp_path: Path, capsys) -> None:
    """Control: the bracket-aware split must not hide an argument the code lacks."""
    status, out = _declared_vs_code(
        tmp_path, {"m.py": "def call(name):\n    ...\n"}, "m.py",
        "def call(name, args: dict[str, Any]): ...", capsys)
    assert "proposed" in out and "adds args" in out, out


def test_signature_spread_over_several_lines_is_read(tmp_path: Path, capsys) -> None:
    """Declared code is often a formatted multi-line `def`: the arguments must be read
    across lines, not only when the closing parenthesis is on the same line."""
    code = "class T:\n    def run(\n        self,\n        a,\n        b,\n    ): ..."
    status, out = _declared_vs_code(
        tmp_path, {"m.py": "class T:\n    def run(self, a, b):\n        ...\n"}, "m.py", code, capsys)
    assert status == 0 and "compatible" in out and "new" not in out, out


def test_parenthesis_inside_a_default_does_not_cut_the_arguments(tmp_path: Path, capsys) -> None:
    """The argument list ends at the MATCHING parenthesis: `n=(1, 2)` must not hide the
    required keyword-only `m` that follows it (false `incompatible: missing m`)."""
    code = "def f(a, n=(1, 2), *, m): ..."
    status, out = _declared_vs_code(
        tmp_path, {"m.py": "def f(a, n=(1, 2), *, m):\n    ...\n"}, "m.py", code, capsys)
    assert status == 0 and "incompatible" not in out, out
