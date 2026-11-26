"""report-store #53: the store, `wf.py report add|show` and the spec invariants.

Seam: the wf CLI run as a subprocess; the store is read back with the stdlib
sqlite3. Every run gets HOME and XDG_STATE_HOME under tmp_path, so the real
store (~/.local/state/agentic-dev-kit/reports.sqlite) cannot be reached even
by a bug in the default path.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

WF = Path(__file__).resolve().parent.parent / "wf.py"
REPO = "owner/name"


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, capture_output=True, text=True, check=True, env=env,
    ).stdout


def wf(tmp_path: Path, *args: str, stdin: str = "", env: dict | None = None):
    base = {k: v for k, v in os.environ.items() if k not in ("AGENT_REPORT_STORE", "XDG_STATE_HOME")}
    base.update(HOME=str(tmp_path / "home"), XDG_STATE_HOME=str(tmp_path / "xdg"))
    base.update(env or {})
    return subprocess.run([sys.executable, str(WF), *args], input=stdin,
                          capture_output=True, text=True, check=False, env=base)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "state" / "reports.sqlite"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """A repo: commit 1 has ten lines, commit 2 changes only the last one."""
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    (root / "f.txt").write_text("".join(f"line {i}\n" for i in range(1, 11)))
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "one")
    (root / "f.txt").write_text("".join(f"line {i}\n" for i in range(1, 10)) + "the added line\n")
    git(root, "commit", "-q", "-am", "two")
    return root


def row(item: str = "1", **over) -> dict:
    base = {"criterion": "1", "item_id": item, "verdict": "pass", "severity": "advisory",
            "reviewer": "spec", "repo": REPO, "actor": "spec-1", "claim": "coverage"}
    return {**base, **over}


def jsonl(*rows: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def add(tmp_path, work, store, rows, report="r1", feature="feat"):
    return wf(tmp_path, "report", "add", "--repo-root", str(work), "--feature", feature,
              "--report", report, "--store", str(store), stdin=jsonl(*rows))


def stored(store: Path) -> list[tuple]:
    with sqlite3.connect(store) as c:
        return c.execute("SELECT seq, item_id, report FROM review_row ORDER BY seq").fetchall()


def test_report_show_prints_what_was_added(tmp_path, work, store):
    rows = [row("a"), row("b", verdict="fail", extra_key="kept")]
    done = add(tmp_path, work, store, rows)
    assert (done.returncode, done.stdout.strip()) == (0, "2 rows added to r1"), done.stderr
    add(tmp_path, work, store, [row("other")], report="r2")
    shown = wf(tmp_path, "report", "show", "--report", "r1", "--store", str(store))
    assert shown.returncode == 0, shown.stderr
    got = [json.loads(line) for line in shown.stdout.splitlines()]
    # witness: report r2 also in the store, b carries an unknown key — distinguishes a
    # filter on the report and row_json kept verbatim from a select-all / a column rebuild
    assert got == [{**r, "feature": "feat", "report": "r1"} for r in rows]
    by_feature = wf(tmp_path, "report", "show", "--feature", "feat", "--repo", REPO,
                    "--store", str(store))
    assert [json.loads(x)["item_id"] for x in by_feature.stdout.splitlines()] == ["a", "b", "other"]
    none = wf(tmp_path, "report", "show", "--feature", "feat", "--repo", "x/y", "--store", str(store))
    assert (none.returncode, none.stdout) == (0, "")


def test_a_bad_row_rejects_the_whole_batch(tmp_path, work, store):
    bad = [row("a"), row("b", severity="major"), row("c")]
    out = add(tmp_path, work, store, bad)
    assert out.returncode == 1
    assert "row 2:" in out.stderr and "row 1" not in out.stderr and "row 3" not in out.stderr
    # witness: the bad row is the second, not the first — distinguishes all-or-nothing
    # from "stop at the first bad row" / "keep the rows before it"
    assert not store.exists() or stored(store) == []
    fixed = add(tmp_path, work, store, [row("a"), row("b"), row("c")])
    assert fixed.returncode == 0, fixed.stderr
    assert [r[1] for r in stored(store)] == ["a", "b", "c"]


@pytest.mark.parametrize("over,reason", [
    ({"verdict": "advisory"}, "verdict"),
    ({"reviewer": "boss"}, "reviewer"),
    ({"repo": "noslash"}, "repo"),
    ({"actor": ""}, "actor"),
    ({"item_id": 7}, "item_id"),
    ({"status": "wip"}, "status"),
    ({"line": "3"}, "line"),
    ({"claim": "guess"}, "claim"),
])
def test_a_row_outside_the_schema_is_refused_with_its_field(tmp_path, work, store, over, reason):
    out = add(tmp_path, work, store, [row(**over)])
    assert out.returncode == 1 and out.stderr.startswith("row 1:") and reason in out.stderr


def test_a_missing_field_and_a_non_object_line_are_refused(tmp_path, work, store):
    incomplete = row()
    del incomplete["actor"]
    out = wf(tmp_path, "report", "add", "--repo-root", str(work), "--feature", "feat",
             "--report", "r1", "--store", str(store),
             stdin=jsonl(incomplete) + "not json\n")
    assert out.returncode == 1
    assert "row 1:" in out.stderr and "actor" in out.stderr and "row 2:" in out.stderr


def test_a_verdict_na_with_a_reason_is_accepted(tmp_path, work, store):
    out = add(tmp_path, work, store, [row(verdict="n/a — no UI")])
    assert out.returncode == 0, out.stderr
    assert add(tmp_path, work, store, [row(verdict="n/a")]).returncode == 1


def test_a_row_of_another_repo_rejects_the_batch(tmp_path, work, store):
    out = add(tmp_path, work, store, [row("a"), row("b", repo="other/repo")])
    assert out.returncode == 1 and out.stderr.startswith("row 2:") and "repo" in out.stderr
    assert not store.exists() or stored(store) == []


def test_a_quote_not_in_the_commit_is_refused(tmp_path, work, store):
    head = git(work, "rev-parse", "HEAD").strip()

    def code(quote, commit=head):
        return row(claim="code", quote=quote, commit=commit, file="f.txt", line=10)

    ok = add(tmp_path, work, store, [code("the added line")])
    assert ok.returncode == 0, ok.stderr
    # witness "line 2": in f.txt but outside the commit's diff context — distinguishes
    # "in the cited commit's diff" from "in the file at that commit"
    for quote in ("line 2", "a paraphrase of the added line"):
        out = add(tmp_path, work, store, [code(quote)])
        assert out.returncode == 1 and "row 1:" in out.stderr, quote
    unknown = add(tmp_path, work, store, [code("the added line", commit="0" * 40)])
    assert unknown.returncode == 1 and "row 1:" in unknown.stderr
    missing = add(tmp_path, work, store, [row(claim="code")])
    assert missing.returncode == 1
    assert [r[1] for r in stored(store)] == ["1"]


TABLES = {
    "review_row": (("INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion,"
                   " verdict, severity, actor, row_json) VALUES ('t','o/n','f','r','1','1','pass',"
                   "'advisory','a','{}')"), "UPDATE review_row SET verdict = 'fail'"),
    "brief_item": ("INSERT INTO brief_item VALUES ('o/n','f','1','t')",
                   "UPDATE brief_item SET item_id = '2'"),
    "mutation_manifest": ("INSERT INTO mutation_manifest VALUES ('n','t','{}')",
                          "UPDATE mutation_manifest SET manifest_json = '[]'"),
    "mutation_result": (("INSERT INTO mutation_result (name, seq, recorded_at, mutant_id, kind, outcome)"
                        " VALUES ('n',1,'t','m','k','red')"),
                        "UPDATE mutation_result SET outcome = 'pinned'"),
}


@pytest.mark.parametrize("table", sorted(TABLES))
def test_the_store_is_append_only(tmp_path, work, store, table):
    assert add(tmp_path, work, store, [row()]).returncode == 0
    insert, update = TABLES[table]
    with sqlite3.connect(store) as c:
        c.execute(insert)
    # witness: UPDATE to a value that differs from the stored one, and a DELETE of the
    # only row — a trigger on only one of the two verbs would leave the other green
    for stmt in (update, f"DELETE FROM {table}"):
        with sqlite3.connect(store) as c, pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(stmt)
    with sqlite3.connect(store) as c:
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 1
        assert c.execute("SELECT verdict FROM review_row").fetchone() == ("pass",)


def test_the_real_store_is_never_opened_by_tests(tmp_path, work):
    import importlib.util
    spec = importlib.util.spec_from_file_location("report_store", WF.parent / "report_store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "h"
    # witness: three sources all set at once — distinguishes the precedence from any one source
    via_env = mod.default_store_path({"AGENT_REPORT_STORE": str(tmp_path / "e.sqlite"),
                                      "XDG_STATE_HOME": str(tmp_path / "x")}, home)
    via_xdg = mod.default_store_path({"XDG_STATE_HOME": str(tmp_path / "x")}, home)
    via_home = mod.default_store_path({}, home)
    assert via_env == tmp_path / "e.sqlite"
    assert via_xdg == tmp_path / "x" / "agentic-dev-kit" / "reports.sqlite"
    assert via_home == home / ".local" / "state" / "agentic-dev-kit" / "reports.sqlite"
    # the CLI without --store follows the variable, then the XDG directory (both under tmp_path)
    viaenv = wf(tmp_path, "report", "add", "--repo-root", str(work), "--feature", "f",
                "--report", "r", stdin=jsonl(row()), env={"AGENT_REPORT_STORE": str(tmp_path / "v.sqlite")})
    assert viaenv.returncode == 0 and (tmp_path / "v.sqlite").exists()
    viaxdg = wf(tmp_path, "report", "add", "--repo-root", str(work), "--feature", "f",
                "--report", "r", stdin=jsonl(row()))
    assert viaxdg.returncode == 0
    assert (tmp_path / "xdg" / "agentic-dev-kit" / "reports.sqlite").exists()
    assert not (tmp_path / "home").exists()


def test_nothing_is_written_into_a_repo(tmp_path, work, store):
    before = git(work, "status", "--porcelain")
    assert add(tmp_path, work, store, [row()]).returncode == 0
    assert wf(tmp_path, "report", "show", "--report", "r1", "--store", str(store)).returncode == 0
    assert git(work, "status", "--porcelain") == before == ""


def test_a_store_that_is_not_sqlite_exits_2(tmp_path, work, store):
    store.parent.mkdir(parents=True)
    store.write_text("this is not a database, just text that is long enough " * 20)
    out = add(tmp_path, work, store, [row()])
    assert out.returncode == 2 and out.stderr.startswith("wf:")
    assert wf(tmp_path, "report", "show", "--report", "r1", "--store", str(store)).returncode == 2


def test_an_empty_batch_adds_zero_rows(tmp_path, work, store):
    out = add(tmp_path, work, store, [])
    assert (out.returncode, out.stdout.strip()) == (0, "0 rows added to r1")


def test_row_json_is_the_line_as_sent_not_a_re_serialisation(tmp_path, work, store):
    # witness: the em dash (json.dumps escapes it) and the space after a colon
    # (json.dumps re-spaces it) tell the stored line from a re-serialised one
    line = json.dumps(row("a", note="a — b"), ensure_ascii=False).replace('"item_id":', '"item_id" :')
    done = wf(tmp_path, "report", "add", "--repo-root", str(work), "--feature", "feat",
              "--report", "r1", "--store", str(store), stdin=line + "\n")
    assert done.returncode == 0, done.stderr
    with sqlite3.connect(store) as c:
        assert c.execute("SELECT row_json FROM review_row").fetchall() == [(line,)]
    shown = wf(tmp_path, "report", "show", "--report", "r1", "--store", str(store))
    assert "a — b" in shown.stdout


# --- #54: briefs, the append-only completion (Decision 5) ---------------------

def load_store_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("report_store_under_test", WF.parent / "report_store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def brief(tmp_path, store, items, repo=REPO, feature="feat"):
    return wf(tmp_path, "report", "brief", "--repo", repo, "--feature", feature,
              "--store", str(store), stdin="".join(f"{i}\n" for i in items))


def test_report_brief_appends_new_items_and_ignores_known_ones(tmp_path, store):
    first = brief(tmp_path, store, ["1", "2"])
    assert (first.returncode, first.stdout.strip()) == (0, "2 items added to the brief of feat"), first.stderr
    # witness: "3" twice inside one input and "2" already stored — distinguishes
    # "append what is new once" (1) from a plain insert (3) or a per-line count
    second = brief(tmp_path, store, ["2", "3", "3"])
    assert (second.returncode, second.stdout.strip()) == (0, "1 items added to the brief of feat"), second.stderr
    mod = load_store_module()
    conn = mod.open_store(store)
    assert mod.brief_items(conn, REPO) == {"feat": ["1", "2", "3"]}
    other = brief(tmp_path, store, ["1"], feature="other")
    assert other.stdout.strip() == "1 items added to the brief of other"
    assert mod.brief_items(conn, REPO) == {"feat": ["1", "2", "3"], "other": ["1"]}
    assert mod.brief_items(conn, "x/y") == {}


def test_report_brief_skips_blank_lines(tmp_path, store):
    done = wf(tmp_path, "report", "brief", "--repo", REPO, "--feature", "feat",
              "--store", str(store), stdin="1\n\n  \n2\n")
    assert (done.returncode, done.stdout.strip()) == (0, "2 items added to the brief of feat")


@pytest.mark.parametrize("repo,feature,stdin,reason", [
    ("not-a-repo", "feat", "1\n", "repo"),
    (REPO, "feat", "", "item"),
    (REPO, "feat", "\n  \n\n", "item"),
    (REPO, "Bad Slug", "1\n", "feature"),
])
def test_report_brief_rejects_a_bad_brief_and_writes_nothing(tmp_path, store, repo, feature, stdin, reason):
    done = wf(tmp_path, "report", "brief", "--repo", repo, "--feature", feature,
              "--store", str(store), stdin=stdin)
    # witness: each bad input has exactly one thing wrong — distinguishes a check per
    # problem from one that lets that input through
    assert done.returncode == 1 and reason in done.stderr, (done.stdout, done.stderr)
    assert len(done.stderr.splitlines()) == 1
    if store.exists():
        with sqlite3.connect(store) as c:
            assert c.execute("SELECT count(*) FROM brief_item").fetchone() == (0,)


def test_report_brief_gives_one_stderr_line_per_reason(tmp_path, store):
    done = wf(tmp_path, "report", "brief", "--repo", "bad", "--feature", "Bad Slug",
              "--store", str(store), stdin="1\n")
    assert done.returncode == 1 and len(done.stderr.splitlines()) == 2


def test_report_brief_on_a_store_that_is_not_sqlite_exits_2(tmp_path, store):
    store.parent.mkdir(parents=True)
    store.write_text("this is not a database, just text that is long enough " * 20)
    done = brief(tmp_path, store, ["1"])
    assert done.returncode == 2 and done.stderr.startswith("wf:")


def test_brief_items_come_in_append_order_not_sorted(tmp_path, store):
    # witness: "b" appended before "a" — distinguishes rowid order from an ORDER BY item_id
    assert brief(tmp_path, store, ["b", "a"]).returncode == 0
    assert brief(tmp_path, store, ["c"]).returncode == 0
    mod = load_store_module()
    assert mod.brief_items(mod.open_store(store), REPO) == {"feat": ["b", "a", "c"]}


def test_review_records_give_the_row_as_given_with_feature_and_report(tmp_path, work, store):
    rows = [row("a"), row("b", extra="kept")]
    assert add(tmp_path, work, store, rows, report="alpha-spec", feature="alpha").returncode == 0
    assert add(tmp_path, work, store, [row("z", repo="o/other")], report="o").returncode == 0
    mod = load_store_module()
    # witness: a row of another repo and an extra key — distinguishes the repo filter and
    # "the row as given" (no feature/report keys) from review_rows' output
    assert mod.review_records(mod.open_store(store), REPO) == [
        (rows[0], "alpha", "alpha-spec"), (rows[1], "alpha", "alpha-spec")]


REPLACES = {
    "review_row": (
        ("{verb} INTO review_row (seq, recorded_at, repo, feature, report, item_id, criterion,"
         " verdict, severity, actor, row_json) VALUES ({key},'forged','o/n','f','r','1','1','pass',"
         "'advisory','a','{{}}')"), "SELECT recorded_at FROM review_row WHERE seq = 1", (1, 2)),
    "brief_item": (
        "{verb} INTO brief_item VALUES ('o/n','f','{key}','forged')",
        "SELECT recorded_at FROM brief_item WHERE item_id = '1'", ("1", "2")),
    "mutation_manifest": (
        "{verb} INTO mutation_manifest VALUES ('{key}','forged','{{}}')",
        "SELECT recorded_at FROM mutation_manifest WHERE name = 'n1'", ("n1", "n2")),
    "mutation_result": (
        ("{verb} INTO mutation_result (name, seq, recorded_at, mutant_id, kind, outcome)"
         " VALUES ('n',{key},'forged','m','k','red')"),
        "SELECT recorded_at FROM mutation_result WHERE seq = 1", (1, 2)),
}


@pytest.mark.parametrize("table", sorted(REPLACES))
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
def test_the_store_refuses_replacing_a_stored_row(tmp_path, work, store, table, verb):
    assert add(tmp_path, work, store, [row()]).returncode == 0
    with sqlite3.connect(store) as c:
        for name, (insert, _) in TABLES.items():
            if name != "review_row":
                c.execute(insert.replace("VALUES ('n',", "VALUES ('n1',") if name == "mutation_manifest" else insert)
    template, probe, (stored_key, new_key) = REPLACES[table]
    # witness: a REPLACE with the stored key (P8) — with only the UPDATE/DELETE triggers it
    # succeeds and the row becomes 'forged'; the new key must still go in
    with sqlite3.connect(store) as c, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute(template.format(verb=verb, key=stored_key))
    with sqlite3.connect(store) as c:
        assert c.execute(probe).fetchone() != ("forged",)
        before = c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        c.execute(template.format(verb=verb, key=new_key))
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before + 1


def test_a_store_made_by_53_gets_the_replace_triggers_when_opened(tmp_path):
    old = tmp_path / "old.sqlite"
    mod = load_store_module()
    # witness: a store created from the #53 schema (update/delete triggers only) — distinguishes
    # CREATE TRIGGER IF NOT EXISTS on open from triggers made only with the tables
    legacy = mod.SCHEMA.split("CREATE TRIGGER IF NOT EXISTS review_row_no_replace")[0]
    with sqlite3.connect(old) as c:
        c.executescript(legacy)
        c.execute("INSERT INTO brief_item VALUES ('o/n','f','1','t')")
        c.execute("INSERT OR REPLACE INTO brief_item VALUES ('o/n','f','1','replaced-before')")
    conn = mod.open_store(old)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("INSERT OR REPLACE INTO brief_item VALUES ('o/n','f','1','forged')")


# --- #55: manifests and matrices in the store --------------------------------

def a_manifest(**over) -> dict:
    base = {"feature": "f", "base_ref": "HEAD", "test_command": ["python", "-m", "pytest", "-q", "{test}"],
            "control": {"id": "M0", "file": "a.py", "patch": "--- a\n+++ b\n", "test": "t.py::t"},
            "mutants": [{"id": "M1", "file": "a.py", "patch": "--- a\n+++ b\n", "test": "t.py::t"}]}
    return {**base, **over}


def matrix_row(id_: str, kind: str = "mutant", outcome: str = "pinned", **over) -> dict:
    return {"id": id_, "kind": kind, "invariant": "I-1", "test": "t.py::t", "outcome": outcome,
            "exit_code": 1, "diff": "--- a\n+++ b\n", "command": ["python", "-m", "pytest"],
            "note": "n", "stdout": "", "stderr": "", **over}


def put_matrix(store: Path, name: str, rows: list[dict]) -> None:
    """A stored matrix written straight to the table, so a reader's test needs no writer."""
    load_store_module().open_store(store).close()
    with sqlite3.connect(store) as c:
        c.executemany(
            "INSERT INTO mutation_result (name, seq, recorded_at, mutant_id, kind, invariant, test,"
            " outcome, exit_code, diff, command, note, output_tail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(name, k, "t", r["id"], r["kind"], r.get("invariant"), r.get("test"), r["outcome"],
              r.get("exit_code"), r.get("diff"), json.dumps(r.get("command")), r.get("note"),
              r.get("output_tail")) for k, r in enumerate(rows)])


def test_report_manifest_stores_a_valid_manifest_once(tmp_path, store):
    given = a_manifest()
    first = wf(tmp_path, "report", "manifest", "--name", "m1", "--store", str(store), stdin=json.dumps(given))
    assert (first.returncode, first.stdout.strip()) == (0, "manifest m1 stored"), first.stderr
    again = wf(tmp_path, "report", "manifest", "--name", "m1", "--store", str(store),
               stdin=json.dumps(a_manifest(feature="other")))
    # witness: the same name a second time — a plain insert would end as an `append-only`
    # IntegrityError (exit 2), not as a reason (exit 1)
    assert again.returncode == 1 and "already stored" in again.stderr, again.stderr
    conn = load_store_module().open_store(store)
    assert load_store_module().manifest(conn, "m1") == given
    assert load_store_module().manifest(conn, "nope") is None


def test_report_manifest_rejects_an_invalid_manifest_and_writes_nothing(tmp_path, store):
    no_control = {k: v for k, v in a_manifest().items() if k != "control"}
    # witness: a manifest with no control (the prover's own rule), a name outside the slug
    # grammar and a text that is no JSON — distinguish the prover's reason (exit 1), the
    # name reason (exit 1) and the parse error (exit 2) from a store that takes anything
    bad = wf(tmp_path, "report", "manifest", "--name", "m1", "--store", str(store), stdin=json.dumps(no_control))
    assert bad.returncode == 1 and "control is required" in bad.stderr, bad.stderr
    name = wf(tmp_path, "report", "manifest", "--name", "a b", "--store", str(store), stdin=json.dumps(a_manifest()))
    assert name.returncode == 1 and "a b" in name.stderr, name.stderr
    junk = wf(tmp_path, "report", "manifest", "--name", "m1", "--store", str(store), stdin="{not json")
    assert junk.returncode == 2, junk.stderr
    with sqlite3.connect(store) as c:
        assert c.execute("SELECT count(*) FROM mutation_manifest").fetchone() == (0,)


def test_report_show_prints_a_stored_matrix(tmp_path, store):
    put_matrix(store, "m", [matrix_row("M0", "control", "red"), matrix_row("M1")])
    put_matrix(store, "other", [matrix_row("X0", "control", "red")])
    shown = wf(tmp_path, "report", "show", "--matrix", "m", "--store", str(store))
    # witness: a second matrix in the store — distinguishes a filter on the name from select-all
    assert shown.returncode == 0, shown.stderr
    got = [json.loads(line) for line in shown.stdout.splitlines()]
    assert [(r["id"], r["kind"], r["outcome"]) for r in got] == [("M0", "control", "red"), ("M1", "mutant", "pinned")]
    unknown = wf(tmp_path, "report", "show", "--matrix", "nope", "--store", str(store))
    assert unknown.returncode == 1 and unknown.stdout == ""


def test_a_matrix_name_is_written_once(store):
    mod = load_store_module()
    conn = mod.open_store(store)
    now = datetime.now()
    assert mod.add_matrix(conn, "m", [matrix_row("M0", "control", "red"), matrix_row("M1")], now) == 2
    # witness: the same name with different rows — a plain insert gives an IntegrityError,
    # a merge keeps both; the first rows must be unchanged and the refusal a BatchRejected
    with pytest.raises(mod.BatchRejected, match="already stored"):
        mod.add_matrix(conn, "m", [matrix_row("Z0", "control", "red")], now)
    assert [r["id"] for r in mod.matrix_rows(conn, "m")] == ["M0", "M1"]
    assert mod.matrix_rows(conn, "unknown") is None


@pytest.mark.parametrize("rows", [
    [], [{**matrix_row("M0"), "id": ""}], [matrix_row("M0", kind="other")],
    [matrix_row("M0", "control", "green")]])
def test_a_matrix_outside_the_schema_is_refused_and_nothing_is_written(store, rows):
    mod = load_store_module()
    conn = mod.open_store(store)
    with pytest.raises(mod.BatchRejected):
        mod.add_matrix(conn, "m", [matrix_row("A0", "control", "red"), *rows] if rows else rows, datetime.now())
    assert conn.execute("SELECT count(*) FROM mutation_result").fetchone() == (0,)


def test_a_missing_store_is_no_store(tmp_path, store):
    mod = load_store_module()
    assert mod.open_existing(None) is None and mod.open_existing(store) is None
    assert not store.parent.exists()
    mod.open_store(store).close()
    conn = mod.open_existing(store)
    assert conn is not None and conn.execute("SELECT count(*) FROM mutation_result").fetchone() == (0,)


def test_report_show_never_creates_a_missing_store(tmp_path):
    absent = tmp_path / "absent.sqlite"
    matrix = wf(tmp_path, "report", "show", "--matrix", "m", "--store", str(absent))
    assert matrix.returncode == 1 and matrix.stdout == ""
    assert "no mutation matrix m" in matrix.stderr
    report = wf(tmp_path, "report", "show", "--report", "r", "--store", str(absent))
    assert report.returncode == 0 and report.stdout == ""
    feature = wf(tmp_path, "report", "show", "--feature", "f", "--repo", "x/y", "--store", str(absent))
    assert feature.returncode == 0 and feature.stdout == ""
    assert not absent.exists()


# --- #56 review r1: the read accessors of the window ---------------------------

def seeded(tmp_path, work, store, rows, **kw):
    assert add(tmp_path, work, store, rows, **kw).returncode == 0
    mod = load_store_module()
    return mod, mod.open_store(store)


def test_review_report_names_are_the_repos_distinct_names_case_folded_in_first_seq_order(tmp_path, work, store):
    # witness: the same repo stored in another case, a report appearing twice and a report of another feature
    mod, conn = seeded(tmp_path, work, store, [row("1", repo="Owner/Name")], report="f-spec", feature="f")
    assert add(tmp_path, work, store, [row("2", repo="Owner/Name")], report="f-a", feature="f").returncode == 0
    assert add(tmp_path, work, store, [row("3", repo="Owner/Name")], report="f-spec", feature="f").returncode == 0
    assert add(tmp_path, work, store, [row("4", repo="o/other")], report="f-z", feature="f").returncode == 0
    assert add(tmp_path, work, store, [row("5", repo="Owner/Name")], report="g-x", feature="g").returncode == 0
    assert mod.review_report_names(conn, "owner/name", "f") == ["f-spec", "f-a"]
    assert mod.review_report_names(conn, "owner/name", "nope") == []


def test_review_report_text_is_the_row_json_verbatim_one_per_line_or_none(tmp_path, work, store):
    # witness: non-default spacing in the lines, which a json.dumps re-dump would change
    lines = [('{"criterion":"c",  "item_id":"1","verdict":"pass","severity":"advisory",'
              '"reviewer":"spec","repo":"owner/name","actor":"a","claim":"coverage"}')]
    lines.append(lines[0].replace('"1"', '"2"'))
    assert wf(tmp_path, "report", "add", "--feature", "f", "--report", "f-spec", "--repo-root", str(work),
              "--store", str(store), stdin="\n".join(lines) + "\n").returncode == 0
    mod = load_store_module()
    conn = mod.open_store(store)
    assert mod.review_report_text(conn, REPO, "f", "f-spec") == "".join(f"{l}\n" for l in lines)
    assert mod.review_report_text(conn, REPO, "f", "nope") is None
    assert mod.review_report_text(conn, "o/other", "f", "f-spec") is None
    # witness: the repo asked in another case than stored, as review_report_names does
    assert mod.review_report_text(conn, "OWNER/NAME", "f", "f-spec") == "".join(f"{l}\n" for l in lines)


def test_brief_text_is_the_one_line_manifest_in_append_order_or_none(tmp_path, store):
    mod = load_store_module()
    conn = mod.open_store(store)
    now = datetime(2026, 11, 17)
    mod.add_brief_items(conn, "Owner/Name", "f", ["b", "a", "é"], now)
    mod.add_brief_items(conn, "o/other", "f", ["z"], now)
    # witness: items appended out of order (not sorted), a non-ASCII item (raw), the same feature in
    # another repo (not mixed in), and the repo asked in another case than stored
    want = '{"repo": "Owner/Name", "feature": "f", "items": ["b", "a", "é"]}'
    assert mod.brief_text(conn, "owner/name", "f") == want
    assert json.loads(want) == {"repo": "Owner/Name", "feature": "f", "items": ["b", "a", "é"]}
    assert mod.brief_text(conn, "owner/name", "nope") is None
    assert mod.brief_text(conn, "nobody/none", "f") is None


def test_manifest_text_is_the_stored_json_verbatim_or_none(tmp_path, store):
    # witness: spacing that a load-and-dump would normalise
    mod = load_store_module()
    conn = mod.open_store(store)
    with conn:
        conn.execute("INSERT INTO mutation_manifest (name, recorded_at, manifest_json) VALUES ('m1','t','{ \"a\":1 }')")
    assert mod.manifest_text(conn, "m1") == '{ "a":1 }'
    assert mod.manifest_text(conn, "m2") is None


def test_matrix_and_manifest_names_are_sorted_and_distinct(tmp_path, store):
    # witness: three rows in the matrix "b" (distinct) and names inserted out of order (sorted)
    mod = load_store_module()
    conn = mod.open_store(store)
    now = datetime(2026, 11, 17)
    for name in ("b", "a"):
        mod.add_matrix(conn, name, [{"id": f"{n}", "kind": "mutant", "outcome": "red"} for n in "xyz"], now)
    with conn:
        for name in ("n2", "n1"):
            conn.execute("INSERT INTO mutation_manifest (name, recorded_at, manifest_json) VALUES (?,'t','{}')", (name,))
    assert mod.matrix_names(conn) == ["a", "b"]
    assert mod.manifest_names(conn) == ["n1", "n2"]


# --- #57: importing the existing evidence ------------------------------------

sys.path.insert(0, str(WF.parent))
import audit


def put(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def importing(tmp_path: Path, wd: Path, store: Path, *paths: str):
    return wf(tmp_path, "report", "import", "--workflow-dir", str(wd), "--store", str(store), *paths)


def compact(r: dict) -> str:
    """A line as an editor, not json.dumps, writes it: no space after separators, raw non-ASCII."""
    return json.dumps(r, separators=(",", ":"), ensure_ascii=False)


def lines_of(*rows: dict) -> str:
    return "".join(compact(r) + "\n" for r in rows)


def table(store: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(store) as c:
        return c.execute(sql).fetchall()


def corpus(tmp_path: Path) -> tuple[Path, list[str]]:
    """A workflow dir: a review report with one row per kind of problem, its archived copy,
    a matrix, a brief and a manifest. Returns the dir and the review lines."""
    wd = tmp_path / "wd"
    review = [
        compact(row("1", note="caffè — naïve")),                  # fine; spacing a re-dump would change
        compact(row("2", severity="major")),                       # outside today's vocabulary
        compact({k: v for k, v in row("3").items() if k != "actor"}),
        compact({k: v for k, v in row("4").items() if k != "repo"}),
    ]
    text = "".join(line + "\n" for line in review)
    put(wd, "reports/f-spec.jsonl", text)
    put(wd, "handoffs/x-reports/f-spec.jsonl", text)
    long_out = "\n".join(f"l{i}" for i in range(40))
    put(wd, "reports/m.jsonl", lines_of(matrix_row("M0", "control", "red", stdout=long_out), matrix_row("M1")))
    put(wd, "reports/f-brief.json", json.dumps({"repo": REPO, "feature": "f", "items": ["1", "2"]}, indent=2))
    put(wd, "reports/m-manifest.json", json.dumps(a_manifest(note="è"), indent=3, ensure_ascii=False) + "\n")
    return wd, review


def test_import_keeps_every_row(tmp_path, store):
    wd, review = corpus(tmp_path)
    done = importing(tmp_path, wd, store, "reports", "handoffs")
    assert done.returncode == 0, done.stderr
    # witness: lines with compact separators and a raw non-ASCII character — a json.dumps
    # re-dump gives ", " and "è", so only the verbatim line passes
    assert sorted(r[0] for r in table(store, "SELECT row_json FROM review_row")) == sorted(review * 2)
    mod = load_store_module()
    conn = mod.open_store(store)
    got = mod.matrix_rows(conn, "m")
    assert [(r["id"], r["kind"], r["outcome"]) for r in got] == [("M0", "control", "red"), ("M1", "mutant", "pinned")]
    # witness: 40 lines of stdout — the last 30 are kept, not the first 30 nor all
    assert got[0]["output_tail"].splitlines() == [f"l{i}" for i in range(10, 40)]
    assert mod.brief_items(conn, REPO) == {"f": ["1", "2"]}
    assert mod.manifest_text(conn, "m-manifest") == (wd / "reports" / "m-manifest.json").read_text(encoding="utf-8")


def test_import_prints_the_summary_line(tmp_path, store):
    wd, _ = corpus(tmp_path)
    done = importing(tmp_path, wd, store, "reports", "handoffs")
    # witness: 8 review rows + 2 matrix rows + 2 brief items + 1 manifest = 13 rows in 5 files,
    # 6 of the review rows historical (3 each in two copies)
    assert done.stdout.strip() == "5 files, 13 rows imported (6 historical), 0 already imported"


def test_rows_failing_todays_checks_are_historical(tmp_path, store):
    wd, review = corpus(tmp_path)
    assert importing(tmp_path, wd, store, "reports/f-spec.jsonl").returncode == 0
    found = table(store, "SELECT item_id, severity, actor, historical, why_historical, row_json"
                         " FROM review_row ORDER BY seq")
    assert [(r[0], r[3]) for r in found] == [("1", 0), ("2", 1), ("3", 1), ("4", 1)]
    major, no_actor = found[1], found[2]
    # witness: severity 'major' — the strict CHECK would refuse it; its text is kept
    assert major[1] == "major" and "severity" in major[4] and major[5] == review[1]
    # witness: a row with no actor — the column holds '' and the reason names the field
    assert no_actor[2] == "" and "actor" in no_actor[4] and no_actor[5] == review[2]
    assert found[0][4] is None


def test_a_row_without_repo_is_historical_and_invisible_to_the_audit(tmp_path, store):
    wd, _ = corpus(tmp_path)
    assert importing(tmp_path, wd, store, "reports/f-spec.jsonl").returncode == 0
    assert table(store, "SELECT repo, historical, why_historical FROM review_row WHERE item_id = '4'") == [
        ("", 1, "no repo")]
    shown = wf(tmp_path, "report", "show", "--report", "f-spec", "--store", str(store))
    assert [json.loads(l)["item_id"] for l in shown.stdout.splitlines()] == ["1", "2", "3", "4"]
    # witness: a no-repo row beside rows of the repo — the audit's reader gives the three with
    # a repo and never the fourth
    got = audit.gather_reviewer_reports(REPO, store=store)
    assert sorted(r["item_id"] for r in got) == ["1", "2", "3"]


def test_a_second_import_adds_only_what_is_new(tmp_path, store):
    wd = tmp_path / "wd"
    path = put(wd, "reports/f-spec.jsonl", lines_of(row("1"), row("2"), row("3")))
    first = importing(tmp_path, wd, store, "reports")
    assert first.stdout.strip() == "1 files, 3 rows imported (0 historical), 0 already imported"
    before = table(store, "SELECT seq, recorded_at, row_json FROM review_row ORDER BY seq")
    # witness: one appended line and one new file — a full re-import would give 8 rows, a skip of
    # the whole known file would miss the appended line
    with path.open("a", encoding="utf-8") as f:
        f.write(lines_of(row("4")))
    put(wd, "reports/g-spec.jsonl", lines_of(row("5"), row("6")))
    second = importing(tmp_path, wd, store, "reports")
    assert second.stdout.strip() == "2 files, 3 rows imported (0 historical), 3 already imported"
    assert table(store, "SELECT seq, recorded_at, row_json FROM review_row ORDER BY seq")[:3] == before
    assert [r[0] for r in table(store, "SELECT item_id FROM review_row ORDER BY seq")] == list("123456")
    third = importing(tmp_path, wd, store, "reports")
    assert third.stdout.strip() == "0 files, 0 rows imported (0 historical), 6 already imported"


def test_two_matrices_with_one_stem_get_their_folder_and_keep_their_path_as_alias(tmp_path, store):
    wd = tmp_path / "wd"
    for folder in ("a-reports", "b-reports"):
        put(wd, f"handoffs/{folder}/m.jsonl", lines_of(matrix_row("M1", outcome="red" if folder[0] == "a" else "pinned")))
    assert importing(tmp_path, wd, store, "handoffs").returncode == 0
    mod = load_store_module()
    conn = mod.open_store(store)
    assert mod.matrix_names(conn) == ["a-reports.m", "b-reports.m"]
    assert mod.alias(conn, "handoffs/a-reports/m.jsonl") == "a-reports.m"
    assert mod.alias(conn, "handoffs/b-reports/m.jsonl") == "b-reports.m"
    assert mod.alias(conn, "handoffs/c-reports/m.jsonl") is None
    # witness: a third file with the stem of two stored ones, imported in a later run — it must
    # be told from them (reports.m) and the earlier names stay
    put(wd, "reports/m.jsonl", lines_of(matrix_row("M1")))
    assert importing(tmp_path, wd, store, "reports").returncode == 0
    assert mod.matrix_names(conn) == ["a-reports.m", "b-reports.m", "reports.m"]
    assert mod.alias(conn, "reports/m.jsonl") == "reports.m"
    assert mod.matrix_rows(conn, "a-reports.m")[0]["outcome"] == "red"


def test_a_stem_alone_in_its_kind_is_the_name_and_a_manifest_is_aliased_too(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "handoffs/a-reports/m-manifest.json", json.dumps(a_manifest()))
    put(wd, "handoffs/b-reports/m-manifest.json", json.dumps(a_manifest()))
    put(wd, "handoffs/b-reports/m.jsonl", lines_of(matrix_row("M1")))
    assert importing(tmp_path, wd, store, "handoffs").returncode == 0
    mod = load_store_module()
    conn = mod.open_store(store)
    # witness: the matrix `m` has no twin of its kind though the manifests do — only the twins are
    # prefixed, and a matrix stem equal to a manifest stem would not count as a collision
    assert mod.matrix_names(conn) == ["m"]
    assert mod.manifest_names(conn) == ["a-reports.m-manifest", "b-reports.m-manifest"]
    assert mod.alias(conn, "handoffs/b-reports/m-manifest.json") == "b-reports.m-manifest"


def test_an_unreadable_or_unknown_file_rejects_the_whole_import(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/good-spec.jsonl", lines_of(row("1")))
    put(wd, "reports/neither.json", json.dumps({"hello": 1}))
    put(wd, "reports/broken.jsonl", lines_of(row("1")) + "{not json\n")
    put(tmp_path, "outside.jsonl", lines_of(row("1")))
    done = importing(tmp_path, wd, store, "reports", "../outside.jsonl")
    assert done.returncode == 1
    reasons = done.stderr.strip().splitlines()
    # witness: three bad inputs next to a good file — one reason each, each naming its file, and
    # the good file's row is not stored either
    assert len(reasons) == 3
    for name in ("neither.json", "broken.jsonl", "outside.jsonl"):
        assert sum(name in r for r in reasons) == 1
    assert "good-spec" not in done.stderr
    if store.exists():
        assert table(store, "SELECT count(*) FROM review_row") == [(0,)]
        assert table(store, "SELECT count(*) FROM import_source") == [(0,)]


def test_report_import_picks_only_evidence_files(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/f-spec.jsonl", lines_of(row("1")))
    for name in ("tool.py", "notes.md", "fix.diff", "fix.patch"):
        put(wd, f"reports/{name}", "not evidence\n")
    put(wd, "handoffs/x-reports/g-spec.jsonl", lines_of(row("2")))
    put(wd, "handoffs/x-reports/readme.md", "# prose\n")
    put(wd, "handoffs/elsewhere/h-spec.jsonl", lines_of(row("3")))
    done = importing(tmp_path, wd, store, "reports", "handoffs")
    # witness: .py/.md/.diff/.patch beside the evidence (picking them would reject the whole run)
    # and a .jsonl in a folder not named *-reports (picking it would give 3 files)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "2 files, 2 rows imported (0 historical), 0 already imported"


def test_import_sources_record_the_path_and_line_of_everything_imported(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/f-spec.jsonl", "\n" + lines_of(row("1")) + "\n" + lines_of(row("2")))
    put(wd, "reports/f-brief.json", json.dumps({"repo": REPO, "feature": "f", "items": ["1"]}))
    assert importing(tmp_path, wd, store, "reports").returncode == 0
    # witness: blank lines before the rows — the line is the physical line number (2 and 4), not a counter
    assert table(store, "SELECT path, line, kind, ref FROM import_source ORDER BY path, line") == [
        ("reports/f-brief.json", 0, "brief", "f"),
        ("reports/f-spec.jsonl", 2, "review", "f-spec"),
        ("reports/f-spec.jsonl", 4, "review", "f-spec")]


def test_a_review_row_takes_the_feature_the_audit_gives_its_file(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/phase-harness-spec-r2.jsonl", lines_of(row("1")))
    put(wd, "reports/phase-harness-brief.json", json.dumps({"repo": REPO, "feature": "phase-harness", "items": ["1"]}))
    put(wd, "reports/example-5061-s44-review.jsonl", lines_of(row("1")))
    put(wd, "reports/other-spec.jsonl", lines_of(row("1", repo="o/other")))
    assert importing(tmp_path, wd, store, "reports").returncode == 0
    # witness: a brief feature with dashes (longest-prefix) vs the plain last-dash split, and a
    # row of a repo that has no brief (split at the last dash)
    assert table(store, "SELECT report, feature FROM review_row ORDER BY report") == [
        ("example-5061-s44-review", "example-5061-s44"), ("other-spec", "other"),
        ("phase-harness-spec-r2", "phase-harness")]


@pytest.mark.parametrize("rel, text, kind", [
    ("a.jsonl", lines_of(matrix_row("M1")), "matrix"),
    ("a.jsonl", lines_of(row("1")), "review"),
    ("a.json", json.dumps({"repo": REPO, "items": ["1"]}), "brief"),
    ("a.json", json.dumps(a_manifest()), "manifest"),
])
def test_evidence_kind_reads_the_content_not_the_name(rel, text, kind):
    # witness: names that say nothing (a.jsonl twice) — only the rows' fields tell matrix from review
    assert load_store_module().evidence_kind(Path(rel), text) == kind


def test_a_legacy_item_verdict_report_is_imported_historical(tmp_path, store):
    legacy = [{"item": "1", "verdict": "pass", "evidence": "docs/x.md:3", "finding": "fine"},
              {"item": "2", "verdict": "fail", "evidence": "docs/y.md:9", "finding": "caffè  — naïve"}]
    wd = tmp_path / "wd"
    put(wd, "handoffs/old-reports/old-review.jsonl", lines_of(*legacy))
    done = importing(tmp_path, wd, store, "handoffs")
    # witness: rows with `item`/`verdict` and neither `item_id` nor `criterion` — the classifier
    # today refuses the file, so the whole import exits 1
    assert done.returncode == 0, done.stderr
    assert load_store_module().evidence_kind(Path("old-review.jsonl"), lines_of(*legacy)) == "review"
    found = table(store, "SELECT repo, historical, why_historical, row_json FROM review_row ORDER BY seq")
    # witness: no `repo` on the rows — stored with '' and historical, the reason starting `no repo`
    assert [(r[0], r[1], r[2].split(";")[0]) for r in found] == [("", 1, "no repo")] * 2
    # witness: compact separators and a raw non-ASCII character — only the verbatim line passes
    assert [r[3] for r in found] == [compact(r) for r in legacy]


@pytest.mark.parametrize("rel, text", [
    ("a.json", json.dumps({"hello": 1})), ("a.jsonl", '{"x": 1}\n'), ("a.jsonl", ""),
    ("a.py", "print(1)"), ("a.json", "[1]"),
])
def test_evidence_kind_refuses_what_it_cannot_classify(rel, text):
    mod = load_store_module()
    with pytest.raises(mod.BatchRejected):
        mod.evidence_kind(Path(rel), text)


def test_a_matrix_with_an_outcome_outside_the_vocabulary_rejects_the_import(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/m.jsonl", lines_of(matrix_row("M0", "control", "red"), matrix_row("M1", outcome="timeout")))
    done = importing(tmp_path, wd, store, "reports")
    # witness: one bad outcome among good rows — refused whole (the CHECK of mutation_result stays strict)
    assert done.returncode == 1 and "timeout" in done.stderr
    assert table(store, "SELECT count(*) FROM mutation_result") == [(0,)]


def test_a_matrix_row_without_kind_is_stored_historical(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/m.jsonl", lines_of(matrix_row("M0", "control", "red"),
                                         {k: v for k, v in matrix_row("M1").items() if k != "kind"}))
    assert importing(tmp_path, wd, store, "reports").returncode == 0
    # witness: the second row lacks `kind` — stored with '' and historical 1, the first untouched
    assert table(store, "SELECT mutant_id, kind, historical FROM mutation_result ORDER BY seq") == [
        ("M0", "control", 0), ("M1", "", 1)]


def test_the_import_source_table_is_append_only_and_severity_is_free_only_for_historical_rows(tmp_path, store):
    wd = tmp_path / "wd"
    put(wd, "reports/f-spec.jsonl", lines_of(row("1")))
    assert importing(tmp_path, wd, store, "reports").returncode == 0
    for stmt in ("UPDATE import_source SET ref = 'x'", "DELETE FROM import_source",
                 "INSERT OR REPLACE INTO import_source VALUES ('reports/f-spec.jsonl', 1, 'review', 'x', 't')"):
        with sqlite3.connect(store) as c, pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(stmt)
    insert = ("INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion, verdict,"
              " severity, actor, row_json, historical) VALUES ('t','','','r','1','1','pass','major','',"
              "'{{}}',{historical})")
    with sqlite3.connect(store) as c:
        c.execute(insert.format(historical=1))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(insert.format(historical=0))


def test_audit_verdicts_survive_the_import(tmp_path, store):
    wd = tmp_path / "wd"
    (wd / "reports").mkdir(parents=True)
    rows = [
        row("1", claim="code", quote="the line", commit="c1"),                  # fine
        row("2", verdict="fail", severity="fatal"),                              # I6: an open fatal
        {k: v for k, v in row("3").items() if k != "actor"},                     # actor: none
        row("4", claim="code", quote="not in the commit", commit="c1"),          # verbatim: a paraphrase
    ]
    put(wd, "reports/f-spec.jsonl", lines_of(*rows))
    put(wd, "reports/f-brief.json", json.dumps({"repo": REPO, "feature": "f", "items": ["1", "2", "3", "4", "9"]}))
    diffs = {"c1": "+the line\n"}

    assert importing(tmp_path, wd, store, "reports").returncode == 0
    found_rows = audit.gather_reviewer_reports(REPO, store=store)
    briefs = audit.gather_brief_items(REPO, store=store)
    found = [*audit.check_report_actor(found_rows),
             *audit.check_reviewer_evidence_verbatim(found_rows, diffs),
             *audit.check_i6_zero_actionable(found_rows)]
    for feature, items in sorted(briefs.items()):
        found += audit.check_brief_coverage([r for r in found_rows if r.get("_feature") == feature], items)
    # witness: one violation of each of the four checks on the imported rows — an empty list cannot pass
    assert [v.split("]")[0] for v in sorted(found)] == ["[B-004", "[B-025/8", "[B-025/9", "[I6/B-025/10"]


def test_a_rejected_import_does_not_create_the_store_but_a_good_one_does(tmp_path):
    wd = tmp_path / "wd"
    put(wd, "reports/neither.json", json.dumps({"hello": 1}))
    absent = tmp_path / "absent.sqlite"
    rejected = importing(tmp_path, wd, absent, "reports")
    # witness: exit 1 and no file at all (an empty store would still be a file)
    assert rejected.returncode == 1
    assert not absent.exists()
    (wd / "reports" / "neither.json").unlink()
    put(wd, "reports/f-spec.jsonl", lines_of(row("1")))
    assert importing(tmp_path, wd, absent, "reports").returncode == 0
    assert absent.exists()


# --- audit-window #102: feature_dates ---------------------------------------


def _dated_store(path, rows=(), briefs=()):
    """Rows and brief items stamped as given: (repo, feature, at) / (repo, feature, item, at).

    Inserts directly: the module's writer stamps recorded_at with now() and these tests need chosen dates.
    """
    mod = load_store_module()
    conn = mod.open_store(path)
    with conn:
        for n, (repo, feature, at) in enumerate(rows):
            conn.execute(
                "INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion,"
                " verdict, severity, status, actor, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (at, repo, feature, f"{feature}-spec", str(n), "4", "pass", "advisory", "open", "a", "{}"))
        for repo, feature, item, at in briefs:
            conn.execute("INSERT INTO brief_item (repo, feature, item_id, recorded_at) VALUES (?,?,?,?)",
                         (repo, feature, item, at))
    return mod, conn


def test_feature_dates_gives_each_feature_its_latest_row_or_brief_item(tmp_path):
    mod, conn = _dated_store(
        tmp_path / "s.sqlite",
        rows=[(REPO, "a", "2026-11-01T10:00:00"), (REPO, "a", "2026-11-05T10:00:00"),
              (REPO, "b", "2026-11-02T10:00:00")],
        briefs=[(REPO, "a", "1", "2026-11-03T10:00:00"), (REPO, "b", "1", "2026-11-09T10:00:00")])
    # witness: a's latest is a row, b's latest is a brief item: neither table alone gives both
    assert mod.feature_dates(conn, REPO) == {"a": "2026-11-05T10:00:00", "b": "2026-11-09T10:00:00"}


def test_feature_dates_of_a_brief_only_feature_is_the_brief_date(tmp_path):
    mod, conn = _dated_store(tmp_path / "s.sqlite", briefs=[(REPO, "only-brief", "1", "2026-11-03T10:00:00")])
    assert mod.feature_dates(conn, REPO) == {"only-brief": "2026-11-03T10:00:00"}


def test_feature_dates_ignores_other_repos_and_an_empty_store_gives_nothing(tmp_path):
    mod, conn = _dated_store(
        tmp_path / "s.sqlite",
        rows=[("o/other", "x", "2026-11-05T10:00:00"), (REPO, "a", "2026-11-01T10:00:00")],
        briefs=[("o/other", "y", "1", "2026-11-06T10:00:00")])
    assert mod.feature_dates(conn, REPO) == {"a": "2026-11-01T10:00:00"}
    empty = mod.open_store(tmp_path / "e.sqlite")
    assert mod.feature_dates(empty, REPO) == {}
