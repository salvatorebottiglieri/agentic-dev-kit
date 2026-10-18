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
