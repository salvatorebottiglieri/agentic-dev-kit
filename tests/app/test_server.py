"""Tests for app/server.py and app/desk.py, the window's HTTP surface.

Seam (agreed): a real server on 127.0.0.1 port 0 in a thread, driven by
http.client over a real socket, on top of real temporary git repos. The
projection itself is tested in test_traces.py.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
APP = ROOT / "app"


def load(name: str, path: Path):
    """Import a file by path (the app has no package); first loader wins."""
    if name in sys.modules:
        return sys.modules[name]
    found = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(found)
    sys.modules[name] = module
    found.loader.exec_module(module)
    return module


traces = load("traces", APP / "traces.py")
catalog = load("catalog", APP / "catalog.py")
server = load("server", APP / "server.py")
desk = load("desk", APP / "desk.py")

GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                         cwd=cwd, capture_output=True, text=True, check=True, env=GIT_ENV)
    return out.stdout


UNAPPROVED = """\
---
feature: {slug}
repo: owner/{slug}
branch: feature/{slug}
tracker: #1
---

## Phase plan

| Phase | Decision | Reason |
|---|---|---|
{rows}
"""


def make_repo(root: Path, name: str, origin: str | None = None) -> Path:
    primary = root / name
    primary.mkdir(parents=True)
    git(primary, "init", "-q", "-b", "master")
    if origin:
        git(primary, "remote", "add", "origin", origin)
    (primary / "README.md").write_text("x\n")
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "init")
    return primary


def spec_text(slug: str) -> str:
    rows = "".join(f"| {n} · P | run | — |\n" for n in range(7))
    return UNAPPROVED.format(slug=slug, rows=rows)


def populated_roots(tmp_path: Path) -> tuple[Path, Path]:
    """One root with a repo holding a worktree, a claim and an undelivered spec."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    (primary / "docs" / "specs").mkdir(parents=True)
    (primary / "docs" / "specs" / "feat.md").write_text(spec_text("feat"))
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "spec")
    git(primary, "worktree", "add", "-q", "-b", "feature/feat", str(root / "alpha-feat"))
    claims = primary / ".git" / "agentic-sessions"
    claims.mkdir()
    (claims / "other-slug").write_text("claimed\n")
    return root, primary


NEXT_TEXT = "- Resume the claim slug.\n- Then close #25."


@pytest.fixture
def workflow_dir(tmp_path: Path) -> Path:
    """A workflow dir of its own: the real wf.py/audit.py and one real-shaped handoff for the
    claim row `other-slug` (so a scan never reads the owner's corpus)."""
    wdir = tmp_path / "workflow"
    (wdir / "handoffs").mkdir(parents=True)
    for name in ("wf.py", "audit.py"):
        (wdir / name).symlink_to(ROOT / name)
    (wdir / "handoffs" / "handoff-other-slug-20261116.md").write_text(
        "# Handoff other-slug\n\n- **Repo**: ACME/alpha\n\n## Done\n\n- x\n\n"
        f"## Next session\n\n{NEXT_TEXT}\n\n## Ground truth\n\n- ok\n")
    return wdir


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path: Path, monkeypatch) -> None:
    """A server built without a `presence_dir` falls back to `$XDG_STATE_HOME/agentic-dev-kit/presence`;
    pointing it into tmp_path keeps every test off the runner's real presence records."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture
def presence_dir(tmp_path: Path) -> Path:
    return tmp_path / "presence"      # absent until a test writes a record


@pytest.fixture
def running(tmp_path: Path, workflow_dir: Path, presence_dir: Path):
    """(port, root) of a live server over a populated root."""
    root, _ = populated_roots(tmp_path)
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], root
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


def request(port: int, method: str, path: str, host: str | None = None,
            body: bytes | None = None, headers: dict[str, str] | None = None,
            timeout: float = 60):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


# --- host guard -------------------------------------------------------------

def test_host_allowed_is_an_exact_loopback_match():
    """Witness: `localhost:8766` against port 8765 and `127.0.0.1:8765.evil.com` -
    distinguish 'exactly the bound port on a loopback name' from 'any loopback
    name' and from a prefix match."""
    assert server.host_allowed("127.0.0.1:8765", 8765)
    assert server.host_allowed("localhost:8765", 8765)
    assert not server.host_allowed(None, 8765)
    assert not server.host_allowed("127.0.0.1", 8765)
    assert not server.host_allowed("localhost:8766", 8765)
    assert not server.host_allowed("127.0.0.1:8765.evil.com", 8765)
    assert not server.host_allowed("evil.example:8765", 8765)


@pytest.mark.parametrize("method,path", [
    ("GET", "/"), ("GET", "/api/features"), ("GET", "/static/app.js"),
    ("GET", "/nope"), ("POST", "/api/features")])
def test_non_loopback_host_is_403(running, method, path):
    """Witness: a Host naming another machine on the real port (DNS rebinding) -
    distinguishes 'refuse every request first' from 'check Host only on the API'
    (the POST must be 403, not 405)."""
    port, _ = running
    resp, _ = request(port, method, path, host=f"evil.example:{port}")
    assert resp.status == 403


def test_loopback_host_on_another_port_is_403(running):
    port, _ = running
    resp, _ = request(port, "GET", "/api/features", host=f"localhost:{port + 1}")
    assert resp.status == 403


def test_localhost_host_is_served(running):
    port, _ = running
    resp, _ = request(port, "GET", "/api/features", host=f"localhost:{port}")
    assert resp.status == 200


# --- methods ----------------------------------------------------------------

@pytest.mark.parametrize("method", ["PUT", "DELETE", "POST", "PATCH", "OPTIONS", "HEAD"])
def test_other_methods_are_405(running, method):
    """Witness: PUT/DELETE/POST (with a body) on /api/features - distinguish
    'only GET is served' from 'GET plus whatever the framework default allows'
    (BaseHTTPRequestHandler answers 501 to an unknown method). HEAD is
    refused too: 405, not served like GET."""
    port, _ = running
    resp, _ = request(port, method, "/api/features", body=b"{}" if method != "HEAD" else None)
    assert resp.status == 405
    assert resp.getheader("Allow") == "GET"


def test_a_malformed_content_length_is_still_405(running):
    """Witness: PUT with `Content-Length: abc` - distinguishes 405 from a
    dropped connection; a wrong Host with it is 403 first."""
    port, _ = running
    for host, status in ((None, 405), ("evil.example:1", 403)):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        conn.putrequest("PUT", "/api/features", skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        conn.putheader("Content-Length", "abc")
        conn.endheaders()
        assert conn.getresponse().status == status
        conn.close()


# --- headers ----------------------------------------------------------------

@pytest.mark.parametrize("method,path,host,status", [
    ("GET", "/", None, 200), ("GET", "/static/app.js", None, 200),
    ("GET", "/static/app.css", None, 200), ("GET", "/api/features", None, 200),
    ("GET", "/nope", None, 404), ("GET", "/", "evil.example:1", 403),
    ("GET", "/api/features/000000000000", None, 404),
    ("GET", "/api/features/000000000000", "evil.example:1", 403),
    ("DELETE", "/api/features", None, 405), ("POST", "/api/features/000000000000", None, 405),
    ("POST", "/api/open", None, 403), ("POST", "/api/open", "evil.example:1", 403)])
def test_every_response_is_no_store(running, method, path, host, status):
    port, _ = running
    resp, _ = request(port, method, path, host=host)
    assert resp.status == status
    assert resp.getheader("Cache-Control") == "no-store"


def test_page_carries_the_csp_and_no_inline_code(running):
    """Witness: the page body - distinguishes 'script and style in separate
    /static/ files' from an inline `<script>`/`<style>`/`style=` that the CSP
    `default-src 'self'` would block."""
    port, _ = running
    resp, body = request(port, "GET", "/")
    assert resp.getheader("Content-Security-Policy") == server.CSP
    assert resp.getheader("Content-Type").startswith("text/html")
    html = body.decode()
    assert "/static/app.js" in html and "/static/app.css" in html
    lowered = html.lower()
    assert "<style" not in lowered and " style=" not in lowered
    assert " onclick=" not in lowered
    scripts = [s for s in lowered.split("<script")[1:]]
    assert scripts and all(" src=" in s.split(">")[0] for s in scripts)


# --- routes -----------------------------------------------------------------

def test_static_files_are_served_and_nothing_else(running):
    """Witness: `/static/../wf.py` and `/static/nope` - distinguish 'a fixed
    pair of files' from 'any path under static_dir' (traversal)."""
    port, _ = running
    js, body = request(port, "GET", "/static/app.js")
    assert js.status == 200 and "javascript" in js.getheader("Content-Type") and body
    css, body = request(port, "GET", "/static/app.css")
    assert css.status == 200 and css.getheader("Content-Type").startswith("text/css") and body
    for path in ("/static/nope", "/static/../wf.py", "/static/index.html", "/wf.py", "/api"):
        resp, _ = request(port, "GET", path)
        assert resp.status == 404, path


def test_features_endpoint_returns_the_snapshot(running):
    port, _ = running
    resp, body = request(port, "GET", "/api/features?x=1")
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith("application/json")
    data = json.loads(body)
    assert set(data) == {"generated_at", "partial", "errors", "open", "history", "other_worktrees"}
    assert data["partial"] is False and data["errors"] == []
    assert sorted(r["slug"] for r in data["open"]) == ["feat", "other-slug"]


def features(port: int) -> dict:
    resp, body = request(port, "GET", "/api/features")
    assert resp.status == 200
    return json.loads(body)


def test_features_rows_carry_the_agent_of_an_alive_record_in_the_servers_store(running, presence_dir):
    """Witness: a record for the claim row `other-slug` written for THIS process (alive by
    its real pid and start ticks) in the presence_dir the server was built with - the row
    shows it; without the record `agent` is null and the row carries `claim-no-agent`.
    Distinguishes a server that reads the injected store from one that ignores it."""
    port, root = running
    row = next(r for r in features(port)["open"] if r["slug"] == "other-slug")
    assert row["agent"] is None and [s["name"] for s in row["signals"]] == ["claim-no-agent"]
    wf = traces.load_wf(ROOT)
    pid = os.getpid()
    presence_dir.mkdir()
    (presence_dir / "r.json").write_text(json.dumps({
        "slug": "other-slug", "repo_root": str(root / "alpha"), "pid": pid, "process": "pytest",
        "start_ticks": wf.proc_stat(pid)[2], "recorded_at": "2026-11-16T10:00:00+01:00"}))
    row = next(r for r in features(port)["open"] if r["slug"] == "other-slug")
    assert row["agent"]["pid"] == pid and row["agent"]["process"] == "pytest"
    assert row["signals"] == []


def test_feature_detail_is_200_from_the_last_snapshot(running):
    """Witness: the detail of the claim row `other-slug` (id from /api/features) carries the
    Next session text of its handoff (repo `ACME/alpha` joined to origin `acme/alpha`) and the
    row's own fields; the id of a history row works too. Distinguishes answering from the last
    snapshot with the handoff read from disk from an empty stub."""
    port, _ = running
    data = features(port)
    row = next(r for r in data["open"] if r["slug"] == "other-slug")
    resp, body = request(port, "GET", f"/api/features/{row['id']}")
    assert resp.status == 200 and resp.getheader("Content-Type").startswith("application/json")
    detail = json.loads(body)
    assert detail["handoff_next"] == NEXT_TEXT
    assert detail["id"] == row["id"] and detail["slug"] == "other-slug"
    assert detail["reports"] == [] and [d["kind"] for d in detail["documents"]] == ["handoff"]
    assert any(t["kind"] == "handoff" for t in detail["traces"])


def test_feature_detail_lists_a_repoless_matrix_and_503s_when_the_store_cannot_be_read(stored):
    """Spec 25-AC4. Witness: the stored matrix `other-slug-mutation-1` (a matrix has no repo; slug
    known in one repo) is listed, the review report `other-slug-review` of repo someone/else is
    not - distinguishes slug-only attribution from the old drop and from listing every prefixed
    name. A store that cannot be read is a 503, not an empty list."""
    put_matrix(stored.store, "other-slug-mutation-1", ["M1"])
    put_review(stored.store, "someone/else", "other-slug", "other-slug-review", ["I9"])
    assert [r["name"] for r in detail_of(stored.port)["reports"]] == ["other-slug-mutation-1.jsonl"]
    row = next(r for r in features(stored.port)["open"] if r["slug"] == "other-slug")
    stored.store.write_bytes(b"this is not an sqlite database" * 20)
    resp, body = request(stored.port, "GET", f"/api/features/{row['id']}")
    assert resp.status == 503 and json.loads(body)["error"]


def test_feature_detail_is_404_for_an_unknown_id_and_before_any_snapshot(running):
    port, _ = running
    resp, _ = request(port, "GET", "/api/features/000000000000")     # no snapshot built yet
    assert resp.status == 404
    row = features(port)["open"][0]
    assert request(port, "GET", f"/api/features/{row['id']}")[0].status == 200
    for path in ("/api/features/000000000000", "/api/features/", "/api/features/x/y"):
        resp, body = request(port, "GET", path)
        assert resp.status == 404 and "error" in json.loads(body)


def test_feature_detail_is_503_when_a_handoff_vanished_after_the_snapshot(running, workflow_dir):
    port, _ = running
    row = next(r for r in features(port)["open"] if r["slug"] == "other-slug")
    (workflow_dir / "handoffs" / "handoff-other-slug-20261116.md").unlink()
    resp, body = request(port, "GET", f"/api/features/{row['id']}")
    assert resp.status == 503 and json.loads(body)["error"]


def test_a_failed_scan_is_503_with_an_error(tmp_path):
    """The scan itself failing (no wf.py in the workflow dir) is 503 {error},
    not a dropped connection and not an empty snapshot."""
    empty = tmp_path / "no-wf"
    empty.mkdir()
    httpd = server.build_server(0, (tmp_path,), empty, APP / "static")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp, body = request(httpd.server_address[1], "GET", "/api/features")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    assert resp.status == 503
    assert json.loads(body)["error"]


def test_a_failed_scan_still_carries_server_timing(tmp_path):
    """Witness: the snapshot raising (no wf.py in the workflow dir) - distinguishes 'the header
    is added only on the 200' from 'the header is on the 503 too'."""
    empty = tmp_path / "no-wf"
    empty.mkdir()
    httpd = server.build_server(0, (tmp_path,), empty, APP / "static")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp, _ = request(httpd.server_address[1], "GET", "/api/features")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    assert resp.status == 503
    assert re.fullmatch(r"scan;dur=\d+", resp.getheader("Server-Timing") or "")


def test_every_request_writes_one_timed_line_and_no_stdlib_line(tmp_path, workflow_dir, presence_dir, capsys):
    """Witness: GET / (200), a 404 and a 405 - three statuses, so a line with a fixed status
    cannot pass; and no line keeps the stdlib shape `"GET / HTTP/1.1" 200 -`."""
    root, _ = populated_roots(tmp_path)
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        asked = [("GET", "/", 200), ("GET", "/nope", 404), ("PUT", "/api/features", 405)]
        for method, path, _status in asked:
            request(port, method, path)
    finally:
        httpd.shutdown()          # returns once the request in flight has been logged
        httpd.server_close()
        thread.join(5)
    lines = capsys.readouterr().err.splitlines()
    assert not any(re.search(r'"[A-Z]+ \S+ HTTP/1\.\d" \d+', line) for line in lines), lines
    for method, path, status in asked:
        mine = [ln for ln in lines if re.search(rf"\] {method} {re.escape(path)} \d+ ", ln)]
        assert len(mine) == 1, (method, path, lines)
        assert re.search(rf"\] {method} {re.escape(path)} {status} \d+ ms$", mine[0]), mine[0]


def test_a_connection_without_a_request_line_writes_no_log_line(tmp_path, workflow_dir, presence_dir, capsys):
    """Witness: a socket opened and closed without a byte, then one GET / - the stdlib records
    no status for the first, so stderr holds exactly the GET / line and nothing with None or a
    `-` method."""
    root, _ = populated_roots(tmp_path)
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
        request(port, "GET", "/")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
    assert len(lines) == 1, lines
    assert re.search(r"\] GET / 200 \d+ ms$", lines[0]), lines[0]
    assert not any("None" in ln or re.search(r"\] - ", ln) for ln in lines), lines


def test_features_answer_carries_server_timing_header(tmp_path, workflow_dir, presence_dir):
    root, _ = populated_roots(tmp_path)
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        features, _ = request(httpd.server_address[1], "GET", "/api/features")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    assert re.fullmatch(r"scan;dur=\d+", features.getheader("Server-Timing") or "")


def test_the_page_starts_with_the_loading_block(running):
    """Witness: the served HTML - the loading block is markup inside #view (visible before any
    script runs) and the timings footer follows <main>."""
    port, _ = running
    _, body = request(port, "GET", "/")
    html = body.decode()
    view = html[html.index('id="view"'):html.index("</main>")]
    assert 'class="loading"' in view and "Loading the features…" in view
    assert html.index("</main>") < html.index('<footer id="timings"')


def test_server_binds_loopback_only(running):
    port, _ = running
    for addr in ("127.0.0.1",):
        assert socket.create_connection((addr, port), timeout=5)


def test_building_on_a_taken_port_raises_oserror(tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(OSError):
            server.build_server(taken.getsockname()[1], (tmp_path,), ROOT, APP / "static")


# --- invariant: nothing under a repo or the workflow dir is written ---------------

def tree_state(root: Path) -> set[tuple[str, int]]:
    return {(str(p), p.lstat().st_mtime_ns) for p in [root, *root.rglob("*")]}


def status(tree: Path) -> str:
    """Porcelain status that itself writes nothing (a plain `git status` refreshes the index)."""
    return git(tree, "--no-optional-locks", "status", "--porcelain")


def test_scan_and_actions_write_nothing(documented):
    """Witness several scans of a root holding a dirty worktree, a claim and
    an undelivered spec, then the 204 action (terminal) and every document read with the recorder, comparing `git status --porcelain` and the (path,
    mtime) set before and after - distinguishes 'read-only git calls' from a
    scan that refreshes the index (`git status` without --no-optional-locks) or
    writes any cache, and an action that writes anything of its own. The scan must have
    produced rows and the actions must have reached the launcher, or a no-op passes."""
    ns = documented
    root = ns.root
    (root / "alpha-feat" / "untracked.txt").write_text("dirty\n")
    repos = [root / "alpha", root / "alpha-feat"]
    map_install(ns.workflow_dir)       # an install the workflow map can read
    watched = (root, ns.workflow_dir, ns.presence_dir)
    before = ({r: status(r) for r in repos}, [tree_state(w) for w in watched if w.exists()])
    for _ in range(3):
        resp, body = request(ns.port, "GET", "/api/features")
        assert resp.status == 200
    assert json.loads(body)["open"], "the scan listed nothing - a no-op would pass"
    rows = known(ns.port)
    assert post(ns.port, asks(ns, "terminal", rows["feat"]["id"])).status == 204
    assert len(ns.calls) == 1
    names = listed(ns.port)          # documents: the listings, every read
    assert names, "the document listing was empty - a no-op would pass"
    for slug in ("feat", "other-slug"):     # spec, handoff and report documents of a row are read too
        detail = json.loads(request(ns.port, "GET", f"/api/features/{rows[slug]['id']}")[1])
        assert {"spec", "handoff", "report"} & {d["kind"] for d in detail["documents"]}
        for doc in detail["documents"]:
            assert get_document(ns.port, doc["id"])[0].status == 200
    assert get_document(ns.port, names["docs/adr/0001-first.md"]["id"])[0].status == 200
    for slug in ("feat", "other-slug"):     # the phases' artefacts of a row are read too
        assert workflow_of(ns.port, rows[slug]["id"])[0].status == 200
    assert len(ns.calls) == 1
    after = ({r: status(r) for r in repos}, [tree_state(w) for w in watched if w.exists()])
    assert after == before


def test_the_scan_writes_no_bytecode_under_the_roots(tmp_path):
    """Witness: a copy of the real wf.py + audit.py placed INSIDE a discovery
    root, scanned several times over a repo with an undelivered spec - distinguishes
    'loading wf.py/audit.py leaves no `__pycache__`' from the default import
    machinery writing bytecode next to the sources."""
    root, _ = populated_roots(tmp_path)
    workflow = root / "workflow"
    workflow.mkdir()
    for name in ("wf.py", "audit.py"):
        (workflow / name).write_text((ROOT / name).read_text())
    before = {str(p) for p in [root, *root.rglob("*")]}
    httpd = server.build_server(0, (root,), workflow, APP / "static")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(3):
            resp, body = request(httpd.server_address[1], "GET", "/api/features")
            assert resp.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    assert json.loads(body)["open"], "the scan listed nothing - a no-op would pass"
    assert {str(p) for p in [root, *root.rglob("*")]} - before == set()



def test_a_scan_without_the_server_writes_no_bytecode_under_the_roots(tmp_path, workflow_dir, monkeypatch):
    """witness the CLI path (65-S1.9): `traces.snapshot` with no server, so no process-wide flag,
    bytecode writing on, and a `derive` that imports a module by path from the workflow dir, as
    `wf._audit` loads audit.py - distinguishes 'the scan's own `no_bytecode()` covers a derive run
    in its thread' from relying on the server's flag."""
    probe = workflow_dir / "lazy_probe.py"
    probe.write_text("VALUE = 1\n")
    real = traces.load_wf(ROOT)
    derived: list[str] = []

    def derive(repo_root, feature, store=None):
        found = importlib.util.spec_from_file_location("lazy_probe_cli", probe)
        found.loader.exec_module(importlib.util.module_from_spec(found))
        derived.append(feature)
        return real.derive(repo_root, feature, store)

    monkeypatch.setattr(traces, "load_wf", lambda workflow_dir: SimpleNamespace(
        derive=derive, parse_front_matter=real.parse_front_matter,
        read_presence=real.read_presence, default_presence_dir=real.default_presence_dir))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)   # a server built by another test set it
    root, _ = populated_roots(tmp_path)
    traces.snapshot((root,), workflow_dir, datetime.now().astimezone())
    assert derived, "no derive ran - a no-op would pass"
    assert not (workflow_dir / "__pycache__").exists()

# --- desk CLI ---------------------------------------------------------------

def test_serve_exits_2_when_the_port_is_taken(capsys, tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert desk.main(["serve", "--port", str(port), "--state-dir", str(tmp_path / "st")]) == 2
    assert f"port {port} in use" in capsys.readouterr().err


class FakeHttpd:
    def serve_forever(self):
        raise KeyboardInterrupt

    def warm_up(self):
        pass

    def server_close(self):
        self.closed = True


def test_serve_defaults_and_ctrl_c_exits_0(monkeypatch):
    """Witness: no flags - distinguishes the documented defaults (8765, $HOME,
    the parent of app/) from any other value; Ctrl-C is a clean exit 0."""
    seen = {}
    fake = FakeHttpd()

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen.update(port=port, roots=roots, workflow_dir=workflow_dir, static_dir=static_dir)
        return fake

    monkeypatch.setattr(desk.server, "build_server", build)
    monkeypatch.setenv("HOME", "/tmp/somewhere-home")
    assert desk.main(["serve"]) == 0
    assert seen == {"port": 8765, "roots": (Path("/tmp/somewhere-home"),),
                    "workflow_dir": ROOT, "static_dir": APP / "static"}
    assert fake.closed


def test_serve_takes_port_roots_and_workflow_dir(monkeypatch, tmp_path):
    seen = {}

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen.update(port=port, roots=roots, workflow_dir=workflow_dir, remote_host=remote_host)
        return FakeHttpd()

    monkeypatch.setattr(desk.server, "build_server", build)
    a, b, w = tmp_path / "a", tmp_path / "b", tmp_path / "w"
    code = desk.main(["serve", "--port", "9", "--root", str(a), "--root", str(b),
                      "--workflow-dir", str(w)])
    assert code == 0
    assert seen == {"port": 9, "roots": (a, b), "workflow_dir": w, "remote_host": None}


def test_serve_hands_the_remote_host_to_the_server(monkeypatch, tmp_path):
    """witness `serve --remote-host H` - distinguishes a flag that reaches build_server
    from one parsed and dropped."""
    seen = {}

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen["remote_host"] = remote_host
        return FakeHttpd()

    monkeypatch.setattr(desk.server, "build_server", build)
    assert desk.main(["serve", "--remote-host", REMOTE]) == 0
    assert seen == {"remote_host": REMOTE}


def test_serve_hands_the_skills_dir_to_the_server(monkeypatch, tmp_path):
    """Witness `--skills-dir D`, then `AGENT_SKILLS_DIR=E` alone, then neither (HOME fixed) -
    distinguish the flag over the variable over `~/.agents/skills` (wf.py's own default, read in
    the CLI) from a default fixed in code or a flag parsed and dropped."""
    seen = []

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen.append(skills_dir)
        return FakeHttpd()

    monkeypatch.setattr(desk.server, "build_server", build)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_SKILLS_DIR", str(tmp_path / "env"))
    assert desk.main(["serve", "--skills-dir", str(tmp_path / "flag")]) == 0
    assert desk.main(["serve"]) == 0
    monkeypatch.delenv("AGENT_SKILLS_DIR")
    assert desk.main(["serve"]) == 0
    assert seen == [tmp_path / "flag", tmp_path / "env", tmp_path / "home" / ".agents" / "skills"]


def test_serve_hands_the_jira_url_and_the_distro_to_the_server(monkeypatch):
    """Witness `serve --jira-url U` with `WSL_DISTRO_NAME=Ubuntu-Test`, then no flag and no
    variable - distinguish a flag and an environment read that reach build_server from ones
    parsed and dropped, and `distro=""` / `jira_url=None` as the absent values."""
    seen = []

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen.append({"jira_url": jira_url, "distro": distro})
        return FakeHttpd()

    monkeypatch.setattr(desk.server, "build_server", build)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-Test")
    assert desk.main(["serve", "--jira-url", "https://j.example"]) == 0
    monkeypatch.delenv("WSL_DISTRO_NAME")
    assert desk.main(["serve"]) == 0
    assert seen == [{"jira_url": "https://j.example", "distro": "Ubuntu-Test"},
                    {"jira_url": None, "distro": ""}]


def test_the_server_reuses_phases_across_refreshes_in_memory_only(tmp_path, workflow_dir, presence_dir):
    """witness three GETs of /api/features over a git install and an unchanged root (the jobs of
    the first one finished before the second), then a restart - distinguishes 'one cache per
    server, filled by the jobs, empty on a new server' from no cache (empty after the jobs) or a
    cache shared across servers (full on restart)."""
    git(workflow_dir, "init", "-q", "-b", "master")
    git(workflow_dir, "add", "-A")
    git(workflow_dir, "commit", "-q", "-m", "install")
    root, _ = populated_roots(tmp_path)
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        request(port, "GET", "/api/features")
        assert httpd.phase_jobs.wait_idle(60)
        bodies = [request(port, "GET", "/api/features")[1] for _ in range(2)]
        assert len(httpd.phase_cache) == 1
        assert json.loads(bodies[0])["open"] == json.loads(bodies[1])["open"]
        assert {r["slug"]: r["phase"]["kind"] for r in json.loads(bodies[1])["open"]}["feat"] == "derived"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    assert server.build_server(0, (root,), workflow_dir, APP / "static").phase_cache == {}


# --- #65: the table before its phases ---------------------------------------

def gated_derive(monkeypatch, open_gate: bool = False,
                 failing: frozenset[str] = frozenset()) -> SimpleNamespace:
    """Make the server's scans load a wf whose derive blocks until `gate` is set (it gives up
    after 5 s so a broken server fails instead of hanging; `open_gate` sets it at once);
    `started` is set when one begins and `calls` lists the feature of every derive, which raises
    for the features in `failing`."""
    real = traces.load_wf(ROOT)
    gate, started = threading.Event(), threading.Event()
    calls: list[str] = []
    if open_gate:
        gate.set()

    def derive(repo_root, feature, store=None):
        calls.append(feature)
        started.set()
        gate.wait(5)
        if feature in failing:
            raise RuntimeError(f"cannot derive {feature}")
        return real.derive(repo_root, feature, store)

    wrapped = SimpleNamespace(derive=derive, parse_front_matter=real.parse_front_matter,
                              read_presence=real.read_presence,
                              default_presence_dir=real.default_presence_dir)
    monkeypatch.setattr(traces, "load_wf", lambda workflow_dir: wrapped)
    return SimpleNamespace(gate=gate, started=started, calls=calls)


@pytest.fixture
def gated(tmp_path: Path, workflow_dir: Path, presence_dir: Path, monkeypatch):
    """A live server over `running`'s root and a git install, with `derive` gated (`gated.gate`)."""
    git(workflow_dir, "init", "-q", "-b", "master")
    git(workflow_dir, "add", "-A")
    git(workflow_dir, "commit", "-q", "-m", "install")
    root, _ = populated_roots(tmp_path)
    ns = gated_derive(monkeypatch)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder())
    ns.httpd, ns.port = httpd, httpd.server_address[1]
    yield ns
    ns.gate.set()
    stop(httpd, thread)


def test_the_table_answers_before_its_phases(gated):
    """witness a server whose `derive` blocks on an event - distinguishes 'the rows at once, the
    phase `computing` and the row in Open, then derived once the job ended' from an answer that
    waits for the phase (the first answer would be derived, after the gate's 5 s)."""
    first = features(gated.port)
    feat = next(r for r in first["open"] if r["slug"] == "feat")
    assert feat["phase"] == {"kind": "computing", "phase": None, "verdicts": [], "refreshing": False}
    assert not any(r["slug"] == "feat" for r in first["history"])
    gated.gate.set()
    assert gated.httpd.phase_jobs.wait_idle(60)
    later = next(r for r in features(gated.port)["open"] if r["slug"] == "feat")
    assert (later["phase"]["kind"], later["phase"]["phase"]) == ("derived", 0)


def test_the_page_answers_while_a_scan_runs(gated, monkeypatch):
    """witness `GET /` and a repo's documents asked within 2 s while the request thread of
    `/api/features` is blocked inside the scan (the tree fingerprint it takes waits on a gate) -
    distinguishes a server that handles requests in parallel from one that serves one at a time
    (the page would time out behind the table request); once the gate opens the table answers."""
    features(gated.port)   # a first table, so the repo's documents can be listed
    real_fingerprint = traces.tree_fingerprint
    inside, release = threading.Event(), threading.Event()

    def blocked_fingerprint(tree):
        inside.set()
        assert release.wait(10)
        return real_fingerprint(tree)

    monkeypatch.setattr(traces, "tree_fingerprint", blocked_fingerprint)
    table: list[int] = []
    scan = threading.Thread(target=lambda: table.append(request(gated.port, "GET", "/api/features")[0].status),
                            daemon=True)
    scan.start()
    try:
        assert inside.wait(5)
        assert request(gated.port, "GET", "/", timeout=2)[0].status == 200
        assert request(gated.port, "GET", "/api/documents?repo=acme/alpha", timeout=2)[0].status == 200
        assert table == [] and scan.is_alive()
    finally:
        release.set()
    scan.join(10)
    assert table == [200]


def test_a_phase_job_writes_no_bytecode(tmp_path, workflow_dir, presence_dir, monkeypatch):
    """witness a `derive` that imports a module by path from the workflow dir, run by a phase job
    (a pool thread, outside any scan) on a server started with bytecode writing on - distinguishes
    'the server process never writes bytecode, so the job leaves no `__pycache__`' from a flag
    that only the synchronous scan sets. The test restores `sys.dont_write_bytecode` itself, which
    the server leaves set for its life."""
    git(workflow_dir, "init", "-q", "-b", "master")
    git(workflow_dir, "add", "-A")
    git(workflow_dir, "commit", "-q", "-m", "install")
    probe = workflow_dir / "lazy_probe.py"
    probe.write_text("VALUE = 1\n")
    real = traces.load_wf(ROOT)

    scan_answered = threading.Event()   # the job imports only once the scan has returned

    def derive(repo_root, feature, store=None):
        assert scan_answered.wait(10)
        found = importlib.util.spec_from_file_location("lazy_probe_desk", probe)
        found.loader.exec_module(importlib.util.module_from_spec(found))
        return real.derive(repo_root, feature, store)

    monkeypatch.setattr(traces, "load_wf", lambda workflow_dir: SimpleNamespace(
        derive=derive, parse_front_matter=real.parse_front_matter,
        read_presence=real.read_presence, default_presence_dir=real.default_presence_dir))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)   # restored by monkeypatch
    root, _ = populated_roots(tmp_path)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder())
    try:
        features(httpd.server_address[1])
        scan_answered.set()
        assert httpd.phase_jobs.wait_idle(60)
        assert httpd.phase_cache, "no job ran - the probe was never imported"
    finally:
        scan_answered.set()
        stop(httpd, thread)
    assert not (workflow_dir / "__pycache__").exists()


# --- #66: the phase cache file and the warm-up -------------------------------

def commit_install(workflow_dir: Path) -> None:
    """Make the workflow dir a git install, so its phases are cached (they need its fingerprint)."""
    git(workflow_dir, "init", "-q", "-b", "master")
    git(workflow_dir, "add", "-A")
    git(workflow_dir, "commit", "-q", "-m", "install")


def add_spec_repo(root: Path, name: str, slug: str) -> Path:
    """A repo `name` under `root` with one committed undelivered spec `slug`."""
    primary = make_repo(root, name, f"https://host/scm/acme/{name}.git")
    (primary / "docs" / "specs").mkdir(parents=True)
    (primary / "docs" / "specs" / f"{slug}.md").write_text(spec_text(slug))
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "spec")
    return primary


def feature_rows(port: int) -> dict[str, dict]:
    """The `/api/features` rows (open and history) by slug."""
    answer = features(port)
    return {r["slug"]: r for r in (*answer["open"], *answer["history"])}


def test_a_restart_with_nothing_changed_is_warm(tmp_path, workflow_dir, presence_dir, monkeypatch):
    """witness a server with a cache file that ran its warm-up and closed, then a second server
    on the same file over the same unchanged trees - distinguishes 'the first table has the phase
    from the file, derive never called' from a cold start (a `computing` row, one derive)."""
    commit_install(workflow_dir)
    root, _ = populated_roots(tmp_path)
    cache_file = tmp_path / "state" / "phase-cache.json"
    calls = gated_derive(monkeypatch, open_gate=True).calls
    first, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        first.warm_up()
    finally:
        stop(first, thread)
    assert calls == ["feat"] and cache_file.exists()
    calls.clear()
    second, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        row = feature_rows(second.server_address[1])["feat"]
    finally:
        stop(second, thread)
    assert (row["phase"]["kind"], row["phase"]["phase"]) == ("derived", 0)
    assert calls == []


def test_a_cache_file_entry_is_used_only_with_its_fingerprint(tmp_path, workflow_dir, presence_dir,
                                                              monkeypatch):
    """witness two repos with a spec each, a warm-up saved, then the spec of one edited (in each
    of its trees) while the server is down - distinguishes 'the edited row answers its last known phase
    `refreshing` and is derived once, the untouched row comes from the file' from a file entry trusted without its
    fingerprint (the edited row cached, no derive) or a file ignored (two derives)."""
    commit_install(workflow_dir)
    root, primary = populated_roots(tmp_path)
    add_spec_repo(root, "beta", "other")
    cache_file = tmp_path / "state" / "phase-cache.json"
    calls = gated_derive(monkeypatch, open_gate=True).calls
    first, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        first.warm_up()
    finally:
        stop(first, thread)
    assert sorted(calls) == ["feat", "other"]
    for tree in (primary, root / "alpha-feat"):
        with (tree / "docs" / "specs" / "feat.md").open("a") as spec:
            spec.write("\nedited while the server was down\n")
    calls.clear()
    second, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        rows = feature_rows(second.server_address[1])
        assert (rows["feat"]["phase"]["kind"], rows["feat"]["phase"]["refreshing"]) == ("derived", True)
        assert (rows["other"]["phase"]["kind"], rows["other"]["phase"]["refreshing"]) == ("derived", False)
        assert second.phase_jobs.wait_idle(60)
    finally:
        stop(second, thread)
    assert calls == ["feat"]


def test_the_cache_file_keeps_decision_13(tmp_path, workflow_dir, presence_dir, monkeypatch):
    """witness a warm-up over a feature that derives, one whose derive raises, one whose tree has
    no fingerprint, and a file that already holds a key no scan uses - distinguishes a file that
    holds only the good feature's entry (no failure, no fingerprint-less tree, nothing outside
    the last scan) from one that writes the memory cache without those rules."""
    commit_install(workflow_dir)
    root, _ = populated_roots(tmp_path)
    add_spec_repo(root, "beta", "boom")
    add_spec_repo(root, "gamma", "nofp")
    cache_file = tmp_path / "state" / "phase-cache.json"
    stale = (Path("/gone"), "old")
    traces.save_phase_cache(cache_file, {stale: (("h", ()), (("h", ()), None),
                                                 traces.PhaseView("derived", 0, ()))})
    gated_derive(monkeypatch, open_gate=True, failing=frozenset({"boom"}))
    real_fingerprint = traces.tree_fingerprint
    monkeypatch.setattr(traces, "tree_fingerprint",
                        lambda tree: None if Path(tree).name == "gamma" else real_fingerprint(tree))
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        assert stale in httpd.phase_cache
        httpd.warm_up()
    finally:
        stop(httpd, thread)
    assert {slug for _, slug in traces.load_phase_cache(cache_file)} == {"feat"}


def test_a_key_no_scan_uses_leaves_the_cache_file_with_nothing_else_changed(
        tmp_path, workflow_dir, presence_dir, monkeypatch):
    """witness a file holding every live key with current fingerprints plus one key no scan uses,
    then a warm-up that derives nothing - distinguishes 'the pruned key is a change, so the file
    is rewritten without it' from a prune that is not counted (the file keeps the key until some
    other feature happens to derive)."""
    commit_install(workflow_dir)
    root, _ = populated_roots(tmp_path)
    cache_file = tmp_path / "state" / "phase-cache.json"
    calls = gated_derive(monkeypatch, open_gate=True).calls
    first, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        first.warm_up()
    finally:
        stop(first, thread)
    saved = traces.load_phase_cache(cache_file)
    stale = (Path("/gone"), "old")
    traces.save_phase_cache(cache_file, {**saved, stale: next(iter(saved.values()))})
    assert stale in traces.load_phase_cache(cache_file) and calls == ["feat"]
    calls.clear()
    second, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        second.warm_up()
    finally:
        stop(second, thread)
    assert calls == []
    assert set(traces.load_phase_cache(cache_file)) == set(saved)


def test_the_cache_file_is_written_only_when_the_cache_changed(tmp_path, workflow_dir,
                                                               presence_dir, monkeypatch):
    """witness a table request while the first job is gated, then one after it ended, two more, and
    the close - distinguishes 'one write for the change, none for a request or a close that finds
    the cache as saved' from a write at every request (4 writes) or none until the close (1, late)."""
    commit_install(workflow_dir)
    root, _ = populated_roots(tmp_path)
    cache_file = tmp_path / "state" / "phase-cache.json"
    ns = gated_derive(monkeypatch)
    writes: list[int] = []
    real_save = traces.save_phase_cache

    def recording_save(path, cache):
        writes.append(len(cache))
        real_save(path, cache)

    monkeypatch.setattr(traces, "save_phase_cache", recording_save)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        port = httpd.server_address[1]
        features(port)
        assert ns.started.wait(10) and writes == []
        ns.gate.set()
        assert httpd.phase_jobs.wait_idle(60)
        for _ in range(3):
            features(port)
        assert writes == [1]
    finally:
        ns.gate.set()
        stop(httpd, thread)
    assert writes == [1]


def test_jobs_that_finish_between_requests_are_saved_at_close(tmp_path, workflow_dir, presence_dir,
                                                              monkeypatch):
    """witness one table request, the job finishing after it, no further request, then the close -
    distinguishes 'the close saves what finished' from a file that exists only after a later
    request (absent before the close, present after)."""
    commit_install(workflow_dir)
    root, _ = populated_roots(tmp_path)
    cache_file = tmp_path / "state" / "phase-cache.json"
    ns = gated_derive(monkeypatch)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    try:
        features(httpd.server_address[1])
        assert ns.started.wait(10)
        ns.gate.set()
        assert httpd.phase_jobs.wait_idle(60)
        assert not cache_file.exists()
    finally:
        ns.gate.set()
        stop(httpd, thread)
    assert {slug for _, slug in traces.load_phase_cache(cache_file)} == {"feat"}


def test_a_failing_save_only_logs(tmp_path, workflow_dir, presence_dir, monkeypatch, capsys):
    """witness a cache file whose directory cannot be created (a regular file stands in its
    place), three table requests after a job finished, then a spec edit and two more - distinguishes
    'every table answers 200 with the phase from memory, stderr has one line per change of the
    cache (the attempt counts)' from a save error that becomes a 503 or one retried, and logged,
    at every request."""
    commit_install(workflow_dir)
    root, primary = populated_roots(tmp_path)
    (tmp_path / "blocker").write_text("a file, not a directory\n")
    gated_derive(monkeypatch, open_gate=True)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(),
                               cache_file=tmp_path / "blocker" / "phase-cache.json")
    marker = "desk: cannot save the phase cache:"
    try:
        port = httpd.server_address[1]
        features(port)
        assert httpd.phase_jobs.wait_idle(60)
        row = feature_rows(port)["feat"]
        feature_rows(port)
        feature_rows(port)
        logged_once = capsys.readouterr().err.count(marker)
        for tree in (primary, root / "alpha-feat"):
            with (tree / "docs" / "specs" / "feat.md").open("a") as spec:
                spec.write("\nedited, so the cache changes\n")
        features(port)
        assert httpd.phase_jobs.wait_idle(60)
        feature_rows(port)
        feature_rows(port)
        logged_again = capsys.readouterr().err.count(marker)
    finally:
        stop(httpd, thread)
    assert row["phase"]["kind"] == "derived"
    assert (logged_once, logged_again) == (1, 1)


@pytest.mark.parametrize("wf_source", [None, 'raise RuntimeError("broken install")\n'],
                         ids=["no wf.py (OSError)", "wf.py raising at import (not an OSError)"])
def test_a_failing_warm_up_only_logs(tmp_path, presence_dir, capsys, wf_source):
    """witness a workflow dir with no wf.py, and one whose wf.py raises a RuntimeError at import -
    distinguishes 'warm_up returns after one stderr line and the page still answers, whatever the
    exception' from a warm-up that catches only OSError or raises into its thread's caller."""
    root, _ = populated_roots(tmp_path)
    install = tmp_path / "broken-install"
    install.mkdir()
    if wf_source is not None:
        (install / "wf.py").write_text(wf_source)
    httpd, thread = serve_over(root, install, presence_dir, Recorder(),
                               cache_file=tmp_path / "state" / "phase-cache.json")
    try:
        httpd.warm_up()
        assert request(httpd.server_address[1], "GET", "/")[0].status == 200
    finally:
        stop(httpd, thread)
    assert "desk: warm-up failed:" in capsys.readouterr().err


def test_a_table_request_during_the_warm_up_does_not_wait_and_shares_its_jobs(
        tmp_path, workflow_dir, presence_dir, monkeypatch):
    """witness a warm-up running in a thread with its derive gated, a table request in the
    meantime, then the gate opened - distinguishes 'the request answers at once with `computing`,
    derive ran once for both, and the warm-up ends with the file written' from a request that
    waits for the warm-up (it would time out) or a second derive of the same key."""
    commit_install(workflow_dir)
    root, _ = populated_roots(tmp_path)
    cache_file = tmp_path / "state" / "phase-cache.json"
    ns = gated_derive(monkeypatch)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), cache_file=cache_file)
    warm = threading.Thread(target=httpd.warm_up, daemon=True)
    try:
        warm.start()
        assert ns.started.wait(10)
        row = feature_rows(httpd.server_address[1])["feat"]
        assert row["phase"]["kind"] == "computing"
        assert warm.is_alive() and not cache_file.exists()
        ns.gate.set()
        warm.join(30)
        assert not warm.is_alive()
    finally:
        ns.gate.set()
        stop(httpd, thread)
    assert ns.calls == ["feat"]
    assert {slug for _, slug in traces.load_phase_cache(cache_file)} == {"feat"}


# --- remote host (#37) ------------------------------------------------------

REMOTE = "desk.example.ts.net:8443"


def served(tmp_path: Path, workflow_dir: Path, remote_host: str | None):
    root, _ = populated_roots(tmp_path)
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", None, remote_host)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def test_remote_host_is_accepted_only_when_named(tmp_path, workflow_dir):
    """Witness `desk.example.ts.net:8443` with and without the flag, beside
    `other.example.ts.net:8443` and `desk.example.ts.net:9443` -
    distinguishes an exact opt-in name from always-open, any-ts.net or any-port."""
    near_misses = ("other.example.ts.net:8443", "desk.example.ts.net:9443",
                   "desk.example.ts.net")
    plain, t1 = served(tmp_path / "a", workflow_dir, None)
    flagged, t2 = served(tmp_path / "b", workflow_dir, REMOTE)
    try:
        port = plain.server_address[1]
        assert request(port, "GET", "/api/features", host=REMOTE)[0].status == 403
        port = flagged.server_address[1]
        for path in ("/", "/api/features"):
            assert request(port, "GET", path, host=REMOTE)[0].status == 200
            assert request(port, "GET", path)[0].status == 200
            assert request(port, "GET", path, host=f"localhost:{port}")[0].status == 200
        for host in near_misses:
            assert request(port, "GET", "/api/features", host=host)[0].status == 403
        assert request(port, "PUT", "/api/features", host=near_misses[0])[0].status == 403
        assert request(port, "PUT", "/api/features", host=REMOTE)[0].status == 405
        assert server.host_allowed(REMOTE, port, REMOTE)
        assert not server.host_allowed(REMOTE, port)
    finally:
        for httpd, thread in ((plain, t1), (flagged, t2)):
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)


# --- open actions (#26) -----------------------------------------------------

DISTRO = "Ubuntu-24.04"


class Recorder:
    """The injected launcher: collects argvs, opens nothing; `error` makes it fail like a missing tool."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.error: OSError | None = None

    def __call__(self, argv: list[str]) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(argv)


def serve_over(root: Path, workflow_dir: Path, presence_dir: Path | None, recorder: Recorder, **kw):
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir,
                                launcher=recorder, **kw)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def stop(httpd, thread) -> None:
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


@pytest.fixture
def acting(tmp_path: Path, workflow_dir: Path, presence_dir: Path):
    """A live server with a recorder over `running`'s root plus a handoff of a repo that is not on
    disk (its row has no where.path), a store (`ns.store`) holding the review report
    `other-slug-review` of the row, and two decoy files in reports/ that nothing lists."""
    root, _ = populated_roots(tmp_path)
    (workflow_dir / "handoffs" / "handoff-gone-slug-20261116.md").write_text(
        "# Handoff gone-slug\n\n- **Repo**: nobody/elsewhere\n\n## Done\n\n- x\n\n"
        "## Next session\n\n- nothing\n\n## Ground truth\n\n- ok\n")
    (workflow_dir / "reports").mkdir()
    (workflow_dir / "reports" / "other-slug-file.jsonl").write_text('{"repo": "acme/alpha"}\n')
    (workflow_dir / "reports" / "other-slug-notes.md").write_text("notes\n")
    store = tmp_path / "state" / "reports.sqlite"
    put_review(store, "acme/alpha", "other-slug", "other-slug-review", ["R1", "R2"])
    recorder = Recorder()
    httpd, thread = serve_over(root, workflow_dir, presence_dir, recorder, distro=DISTRO, store=store)
    yield SimpleNamespace(port=httpd.server_address[1], root=root, httpd=httpd, recorder=recorder,
                          calls=recorder.calls, workflow_dir=workflow_dir,
                          presence_dir=presence_dir, store=store)
    stop(httpd, thread)


def post(port: int, payload=None, host: str | None = None, origin: str | None = "own",
         content_type: str | None = "application/json", raw: bytes | None = None):
    """POST /api/open; `origin="own"` is the page's own origin for the Host used."""
    host = host or f"127.0.0.1:{port}"
    headers = {"Host": host}
    if origin is not None:
        headers["Origin"] = f"http://{host}" if origin == "own" else origin
    if content_type is not None:
        headers["Content-Type"] = content_type
    body = raw if raw is not None else json.dumps(payload).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", "/api/open", body=body, headers=headers)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp


def asks(ns, action: str, target: str, token: str | None = None) -> dict:
    return {"action": action, "id": target, "token": ns.httpd.token if token is None else token}


def known(port: int) -> dict[str, dict]:
    """Slug -> wire row of a fresh snapshot (this also makes it the server's last snapshot)."""
    body = json.loads(request(port, "GET", "/api/features")[1])
    return {r["slug"]: r for r in [*body["open"], *body["history"]]}


def trace_of(row: dict, kind: str) -> dict:
    return next(t for t in row["traces"] if t["kind"] == kind)


def test_actions_refused_without_own_origin_and_token(acting):
    """Witness Origin `http://localhost:<port>` on `Host: 127.0.0.1:<port>` - distinguishes
    'Origin equals http://<Host>' from 'Origin names some allowed host'; token `""` (the
    marker's value) and a token one character off - distinguish a compared token from an
    absent or constant-empty one. A good request first, so the refusals are not vacuous."""
    ns = acting
    row = known(ns.port)["feat"]
    good = asks(ns, "terminal", row["id"])
    assert post(ns.port, good).status == 204 and len(ns.calls) == 1
    ns.calls.clear()
    flipped = ns.httpd.token[:-1] + ("A" if ns.httpd.token[-1] != "A" else "B")
    cases = [
        ("no Origin", {"payload": good, "origin": None}),
        ("foreign Origin", {"payload": good, "origin": "http://evil.example"}),
        ("other name of this server", {"payload": good, "origin": f"http://localhost:{ns.port}"}),
        ("Origin with a suffix", {"payload": good, "origin": f"http://127.0.0.1:{ns.port}.evil.example"}),
        ("Origin with another scheme", {"payload": good, "origin": f"https://127.0.0.1:{ns.port}"}),
        ("empty token", {"payload": asks(ns, "terminal", row["id"], token="")}),
        ("wrong token", {"payload": asks(ns, "terminal", row["id"], token=flipped)}),
        ("token plus suffix", {"payload": asks(ns, "terminal", row["id"],
                                                  token=ns.httpd.token + "x")}),
    ]
    for label, kwargs in cases:
        assert post(ns.port, **kwargs).status == 403, label
    assert ns.calls == []
    # the 403 for a wrong token comes before the 404 and 422 of the id
    assert post(ns.port, asks(ns, "terminal", "0" * 12, token="")).status == 403
    assert post(ns.port, asks(ns, "nonsense", row["id"], token="")).status == 403
    assert post(ns.port, good, host="evil.example:1", origin="http://evil.example:1").status == 403
    assert ns.calls == []


def test_remote_host_is_read_only(tmp_path, workflow_dir):
    """Witness the page for `Host: H` next to the loopback page, and a POST carrying `Host: H`,
    `Origin: http://H` and the loopback token - distinguishes 'no token in the remote page and
    403 for the remote Host' from a server that only hides the token (a remote POST with a
    stolen token passes) or only refuses the POST. Origin, type and body are junk on purpose:
    the remote Host is refused first (Decision 15)."""
    root, _ = populated_roots(tmp_path)
    recorder = Recorder()
    httpd, thread = serve_over(root, workflow_dir, None, recorder, remote_host=REMOTE,
                               distro=DISTRO)
    try:
        port = httpd.server_address[1]
        row = known(port)["feat"]
        marker = '<meta name="wf-token" content="">'
        remote_page = request(port, "GET", "/", host=REMOTE)[1].decode()
        local_page = request(port, "GET", "/")[1].decode()
        named_page = request(port, "GET", "/", host=f"localhost:{port}")[1].decode()
        assert marker in remote_page and httpd.token not in remote_page
        assert marker not in local_page and f'content="{httpd.token}"' in local_page
        assert f'content="{httpd.token}"' in named_page
        good = {"action": "terminal", "id": row["id"], "token": httpd.token}
        assert post(port, good, host=REMOTE).status == 403
        assert post(port, good, host=REMOTE, origin=None).status == 403
        assert post(port, good, host=REMOTE, content_type="text/plain").status == 403
        assert post(port, host=REMOTE, raw=b"not json").status == 403
        assert post(port, good).status == 204        # the same request on loopback goes through
        assert len(recorder.calls) == 1
    finally:
        stop(httpd, thread)


def test_action_targets_only_known_ids(acting, tmp_path, workflow_dir, presence_dir):
    """Witness a request with an extra `path` field and an id that is itself a path - distinguish
    'the argv path is the snapshot's own' from one taken from the request; a fresh server asked
    with ids it never listed - distinguishes 'last snapshot' from 'recompute on demand'."""
    ns = acting
    rows = known(ns.port)
    feat, other = rows["feat"], rows["other-slug"]
    # the ids are valid on a server that has not listed them yet only after its first snapshot
    fresh_calls = Recorder()
    root2 = ns.root
    fresh, thread = serve_over(root2, workflow_dir, presence_dir, fresh_calls, distro=DISTRO)
    try:
        ask = {"action": "terminal", "id": feat["id"], "token": fresh.token}
        assert post(fresh.server_address[1], ask).status == 404
        assert fresh_calls.calls == []
    finally:
        stop(fresh, thread)
    # unknown ids: 404, whatever they look like
    # an id is lower-case hex: the upper-cased form of a known id is unknown, but only when the id has a letter
    flipped = [i.upper() for i in (feat["id"], other["id"]) if i.upper() != i]
    assert flipped, "the premise: some known id has a hex letter"
    for target in ("0" * 12, "", "/etc", ns.root.as_posix(), feat["where"]["path"],
                   *flipped, feat["id"] + "0"):
        assert post(ns.port, asks(ns, "terminal", target)).status == 404, target
        assert post(ns.port, asks(ns, "editor", target)).status == 404, target
    # a path in the body is refused, not used
    smuggled = {**asks(ns, "terminal", other["id"]), "path": "/etc"}
    assert post(ns.port, smuggled).status == 400
    assert ns.calls == []
    # the one path that reaches the launcher is the snapshot's own
    assert other["where"]["path"] == str(ns.root / "alpha")
    assert post(ns.port, asks(ns, "terminal", other["id"])).status == 204
    assert ns.calls == [["wt.exe", "-w", "new", "wsl.exe", "-d", DISTRO, "--cd",
                         str(ns.root / "alpha")]]


def test_open_answers_each_status(acting):
    """Witness `text/plain` with junk, a 5000-byte id, `{"action":"editor","id":<row id>}` and `terminal` on a spec trace id - distinguish
    each status from its neighbours and from 400 for everything; a recorder that raises OSError is
    503, and 204 only after the argv was handed to the launcher."""
    ns = acting
    rows = known(ns.port)
    feat, other, gone = rows["feat"], rows["other-slug"], rows["gone-slug"]
    assert gone["where"]["path"] is None
    spec, handoff = trace_of(feat, "spec"), trace_of(other, "handoff")
    detail = json.loads(request(ns.port, "GET", f"/api/features/{other['id']}")[1])
    report = detail["reports"][0]
    assert report["name"] == "other-slug-review.jsonl"
    document = detail["documents"][0]
    good = asks(ns, "terminal", feat["id"])
    # 415: not application/json (before the body is read)
    for content_type in (None, "text/plain", "application/x-www-form-urlencoded", "application/jsonx"):
        assert post(ns.port, good, content_type=content_type).status == 415, content_type
    assert post(ns.port, raw=b"junk", content_type="text/plain").status == 415   # 415 before 400
    # 400: body not a JSON object with exactly the three string fields
    for label, raw in (
            ("invalid json", b"{nope"), ("empty", b""), ("a list", b"[]"), ("null", b"null"),
            ("not utf-8", b'{"action": "\xff"}'),
            ("no token", json.dumps({"action": "terminal", "id": feat["id"]}).encode()),
            ("no id", json.dumps({"action": "terminal", "token": ns.httpd.token}).encode()),
            ("int id", json.dumps({"action": "terminal", "id": 5, "token": ns.httpd.token}).encode()),
            ("null action", json.dumps({"action": None, "id": feat["id"], "token": ns.httpd.token}).encode()),
            ("over 4096 bytes", json.dumps(asks(ns, "terminal", "a" * 5000)).encode())):
        assert post(ns.port, raw=raw).status == 400, label
    wrong = {**asks(ns, "terminal", feat["id"], token="x"), "extra": "1"}
    assert post(ns.port, wrong).status == 400                                  # 400 before 403
    # 404: the id is not a row (a trace, a report, a document)
    for label, ask in (
            ("terminal on a handoff", asks(ns, "terminal", handoff["id"])),
            ("terminal on a spec", asks(ns, "terminal", spec["id"])),
            ("terminal on a report", asks(ns, "terminal", report["id"])),
            ("terminal on a document", asks(ns, "terminal", document["id"]))):
        assert post(ns.port, ask).status == 404, label
    # 422: the action does not fit the row
    for label, ask in (
            ("editor on a row", asks(ns, "editor", feat["id"])),
            ("terminal on a row without path", asks(ns, "terminal", gone["id"])),
            ("unknown action", asks(ns, "delete", feat["id"]))):
        assert post(ns.port, ask).status == 422, label
    assert post(ns.port, asks(ns, "delete", "0" * 12)).status == 404           # 404 before 422
    assert ns.calls == []
    # 503: the tool could not be started
    ns.recorder.error = FileNotFoundError("wt.exe")
    assert post(ns.port, good).status == 503
    ns.recorder.error = None
    assert ns.calls == []
    # 204: the command was started
    resp = post(ns.port, good)
    assert resp.status == 204 and resp.getheader("Cache-Control") == "no-store"
    assert len(ns.calls) == 1
    assert post(ns.port, good, content_type="application/json; charset=utf-8").status == 204


def test_action_argv_is_the_windows_command():
    """Witness a row whose where.path (`/wt/f-26`) differs from the path of every one of its traces,
    and distro `""` next to `Ubuntu-24.04` - distinguish 'the row's where.path' from a trace
    path, and a dropped `-d` from `-d ""`."""
    where = traces.Where("worktree", "/wt/f-26", "feature/f", 0, 0, None)
    members = (traces.Trace("claim", "o/r", "f", "/repo/.git/agentic-sessions/f", None, 0.0, ""),
               traces.Trace("spec", "o/r", "f", "/repo/docs/specs/f.md", "feature/f", 0.0, ""),
               traces.Trace("worktree", "o/r", "f", "/wt/f-26/..", "feature/f", 0.0, ""))
    row = traces.Row("a" * 12, "o/r", "f", 0.0, traces.PhaseView("no-spec", None, ()), where,
                     None, None, (), members, None)
    snap = traces.Snapshot("2026-11-16T00:00:00+00:00", False, (), (row,), ())
    assert server.action_argv("terminal", row.id, snap, "Ubuntu-24.04") == [
        "wt.exe", "-w", "new", "wsl.exe", "-d", "Ubuntu-24.04", "--cd", "/wt/f-26"]
    assert server.action_argv("terminal", row.id, snap, "") == [
        "wt.exe", "-w", "new", "wsl.exe", "--cd", "/wt/f-26"]
    for refused, status in ((("editor", row.id), 422), (("delete", row.id), 422),
                            (("terminal", traces.trace_id(members[1])), 404),
                            (("editor", traces.trace_id(members[1])), 404),
                            (("terminal", "f" * 12), 404)):
        with pytest.raises(server.ActionRefused) as error:
            server.action_argv(*refused, snap, "D")
        assert error.value.status == status, refused
    with pytest.raises(server.ActionRefused) as error:     # no snapshot yet: nothing is known
        server.action_argv("terminal", row.id, None, "D")
    assert error.value.status == 404
    nowhere = replace(row, where=traces.Where("none", None, None, None, None, None))
    with pytest.raises(server.ActionRefused) as error:
        server.action_argv("terminal", row.id, replace(snap, open=(nowhere,)), "D")
    assert error.value.status == 422


def test_features_rows_carry_links_built_with_the_servers_jira_url(tmp_path, workflow_dir):
    """Witness a server started with `jira_url` and a spec `tracker: EXAMPLE-5166` - distinguishes a
    server that hands its `jira_url` to the scan (a ticket href) from one that drops it (None);
    the tracker text is on the wire either way, and the origin URL is on no row."""
    root, primary = populated_roots(tmp_path)
    spec = primary / "docs" / "specs" / "feat.md"
    spec.write_text(spec.read_text().replace("tracker: #1", "tracker: EXAMPLE-5166"))
    git(primary, "commit", "-qam", "tracker")
    git(root / "alpha-feat", "merge", "-q", "master")
    for jira, ticket in (("https://j.example/", "https://j.example/browse/EXAMPLE-5166"), (None, None)):
        httpd, thread = serve_over(root, workflow_dir, None, Recorder(), jira_url=jira)
        try:
            links = known(httpd.server_address[1])["feat"]["links"]
        finally:
            stop(httpd, thread)
        assert links["tracker"] == "EXAMPLE-5166" and links["ticket"] == ticket
        assert links["pr"] == ("https://host/projects/ACME/repos/alpha/pull-requests"
                               "?at=refs%2Fheads%2Ffeature%2Ffeat")


def test_the_detail_lists_spec_handoffs_and_reports_as_documents(acting):
    """Witness `other-slug` with two handoffs (20261116 and 20261110), a stored review report, a
    stored matrix and a stored manifest, and two files in reports/ (a `.jsonl` and a `.md`);
    `feat` with a spec - distinguish a newest-first order from the name order, the content type of
    each report suffix, a catalog that holds exactly what the detail listed (the id of a stored
    report never listed, and of the files in reports/, are 404) and a `reports` list that carries
    the catalog ids."""
    ns = acting
    handoffs = ns.workflow_dir / "handoffs"
    (handoffs / "handoff-other-slug-20261110.md").write_text(
        "# Handoff other-slug\n\n- **Repo**: ACME/alpha\n\n## Done\n\n- x\n")
    put_matrix(ns.store, "other-slug-matrix", ["M1", "M2"])
    put_manifest(ns.store, "other-slug-manifest", '{"mutants": []}')
    rows = known(ns.port)
    never_listed = catalog.doc_id("report", f"{ns.store}#matrix/other-slug-matrix")
    assert get_document(ns.port, never_listed)[0].status == 404      # same item, not listed yet
    other = json.loads(request(ns.port, "GET", f"/api/features/{rows['other-slug']['id']}")[1])
    docs = {d["name"]: d for d in other["documents"]}
    assert [d["name"] for d in other["documents"] if d["kind"] == "handoff"] == [
        "handoff-other-slug-20261116.md", "handoff-other-slug-20261110.md"]
    assert {n: d["kind"] for n, d in docs.items()} == {
        "handoff-other-slug-20261116.md": "handoff", "handoff-other-slug-20261110.md": "handoff",
        "other-slug-review.jsonl": "report", "other-slug-matrix.jsonl": "report",
        "other-slug-manifest.json": "report"}
    assert [r["id"] for r in other["reports"]] == [docs[r["name"]]["id"] for r in other["reports"]]
    assert {r["name"] for r in other["reports"]} == {
        "other-slug-review.jsonl", "other-slug-matrix.jsonl", "other-slug-manifest.json"}
    resp, body = get_document(ns.port, docs["handoff-other-slug-20261116.md"]["id"])
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/markdown; charset=utf-8"
    assert body == Path(docs["handoff-other-slug-20261116.md"]["path"]).read_bytes()
    for name, content_type in (("other-slug-review.jsonl", "application/x-ndjson; charset=utf-8"),
                               ("other-slug-matrix.jsonl", "application/x-ndjson; charset=utf-8"),
                               ("other-slug-manifest.json", "application/json; charset=utf-8")):
        resp, _ = get_document(ns.port, docs[name]["id"])
        assert resp.status == 200 and resp.getheader("Content-Type") == content_type, name
    for decoy in ("other-slug-file.jsonl", "other-slug-notes.md"):
        assert get_document(ns.port, catalog.doc_id("report", str(ns.workflow_dir / "reports" / decoy)))[0].status == 404
    feat = json.loads(request(ns.port, "GET", f"/api/features/{rows['feat']['id']}")[1])
    spec = next(d for d in feat["documents"] if d["kind"] == "spec")
    assert spec["path"] == trace_of(rows["feat"], "spec")["path"] and spec["name"] == spec["path"]
    resp, body = get_document(ns.port, spec["id"])
    assert resp.getheader("Content-Type") == "text/markdown; charset=utf-8"
    assert body == Path(spec["path"]).read_bytes()


def test_editor_is_gone(acting):
    """Witness `editor` on a real row id (a row that `terminal` opens with 204), `terminal` on
    the spec trace id and on a document id - distinguish a removed action (422, no launcher call)
    from one still wired, and a row-only `terminal` from one that also takes traces or documents."""
    ns = acting
    rows = known(ns.port)
    feat = rows["feat"]
    detail = json.loads(request(ns.port, "GET", f"/api/features/{rows['other-slug']['id']}")[1])
    assert post(ns.port, asks(ns, "editor", feat["id"])).status == 422
    assert post(ns.port, asks(ns, "terminal", trace_of(feat, "spec")["id"])).status == 404
    assert post(ns.port, asks(ns, "terminal", detail["documents"][0]["id"])).status == 404
    assert ns.calls == []
    assert post(ns.port, asks(ns, "terminal", feat["id"])).status == 204 and len(ns.calls) == 1


def test_app_js_uses_the_ahead_label_and_the_base_note_line_of_panel_js(running):
    """Witness the served script: the node tests cover the logic, this proves the page calls it."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    assert "WfPanel.aheadLabel(" in script and "WfPanel.baseNoteLine(" in script
    assert "`${w.ahead} ahead`" not in script


def test_page_has_no_editor_button(running):
    """Witness the served script and page - distinguish a page that still posts `editor` or says
    'editor' on a button from the window-only page."""
    port, _ = running
    page = request(port, "GET", "/")[1].decode()
    script = request(port, "GET", "/static/app.js")[1].decode()
    for text in (page, script):
        assert '"editor"' not in text and "'editor'" not in text
        assert "in editor" not in text.lower() and "open in vs code" not in text.lower()


def test_page_says_what_the_workflow_tab_shows(running):
    """Witness the served script (no JS runner here; the real browser smoke covers behaviour) -
    distinguishes a page that still shows an unmarked phase, an open rules strip and no legend from
    the fixes of #48 that survive #86: the selected-phase mark and title, a closed `<details>` for the
    general rules, and a legend naming every rule level. The chooser's banner, the `HERE` marker, the
    verdict half of the legend and the grey map are gone (Decision 2 of #86): with no `?feature` there
    is no overlay."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    css = request(port, "GET", "/static/app.css")[1].decode()
    for text in ("aria-current", "click a box to see another phase",
                 "general rules", "\u25b8", 'el("details"', 'el("summary"'):
        assert text in script, text
    for word in ("structure", "script", "prompt"):
        assert f"legendItem(\"{word}\"" in script, word
    for gone in ("no spec \u2014 nothing to lay on the map", "nodata", 'legendItem("HERE"', 'legendItem("PASS"',
                 'legendItem("FAIL"', 'legendItem("TODO"', 'legendItem("SKIP"'):
        assert gone not in script, gone
    assert ".node.sel" in css
    assert ".node.nodata" not in css and ".nodata-msg" not in css


def test_panel_js_is_served_as_javascript_and_loaded_before_app_js(running):
    """Witness: `GET /static/panel.js` and the page's script tags in order - distinguish a missing
    STATIC entry (404), a wrong media type, and a page that loads app.js first (WfPanel undefined)."""
    port, _ = running
    resp, body = request(port, "GET", "/static/panel.js")
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert "WfPanel" in body.decode()
    html = request(port, "GET", "/")[1].decode()
    assert html.index("/static/panel.js") < html.index("/static/app.js")
    lowered = html.lower()
    assert "<style" not in lowered and " style=" not in lowered
    assert all(" src=" in s.split(">")[0] for s in lowered.split("<script")[1:])


def test_theme_js_is_served_as_javascript_and_runs_before_the_stylesheet(running):
    """Witness: `GET /static/theme.js` and the page's `<head>` - distinguish a missing STATIC entry (404), a
    wrong media type, a `defer`/`async` script (the first paint would be in the wrong theme), a script after the
    stylesheet, a script in `<body>`, and an inline script (the CSP forbids it)."""
    port, _ = running
    resp, body = request(port, "GET", "/static/theme.js")
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert "WfTheme" in body.decode()
    html = request(port, "GET", "/")[1].decode()
    head = html[html.index("<head>"):html.index("</head>")]
    tag = '<script src="/static/theme.js"></script>'
    assert tag in head, "theme.js is not a plain blocking script in <head>"
    assert "defer" not in tag and "async" not in tag
    assert head.index(tag) < head.index('<link rel="stylesheet"')
    assert all(" src=" in s.split(">")[0] for s in html.lower().split("<script")[1:])


def test_the_window_header_has_the_theme_switch(running):
    """Witness the page markup: one `#theme-switch` button inside `<header class="wf-top">` (Decision 6), a
    real button that submits nothing, naming itself for assistive technology and starting un-pressed."""
    port, _ = running
    html = request(port, "GET", "/")[1].decode()
    header = html[html.index('<header class="wf-top">'):html.index("</header>")]
    assert header.count('id="theme-switch"') == 1
    tag = header[header.rindex("<button", 0, header.index('id="theme-switch"')):].split(">")[0]
    assert 'type="button"' in tag and "aria-label=" in tag and 'aria-pressed="false"' in tag
    assert html.count('id="theme-switch"') == 1


def test_the_page_has_the_panel_container_outside_the_view(running):
    """Witness the page markup: `#wfpanel` is an aside, hidden at first, present, and not between the
    opening and closing tag of `<section id="view">` (render() empties the view at every refresh); both
    live in `#body`."""
    port, _ = running
    html = request(port, "GET", "/")[1].decode()
    start = html.index('<section id="view"')
    end = html.index("</section>", start)
    assert '<aside id="wfpanel"' in html
    assert 'id="wfpanel"' not in html[start:end]
    panel_tag = html[html.index('<aside id="wfpanel"'):].split(">")[0]
    assert "hidden" in panel_tag
    body = html[html.index('id="body"'):]
    assert body.index('id="view"') < body.index('id="wfpanel"') < body.index("</main>")


def test_the_workflow_tab_has_no_feature_selector(running):
    """Witness the served script: no chooser strings anywhere, and the tab's own request
    (`loadWorkflow`) carries no `feature` query, while the panel's request does."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    for gone in ("feature on the map", "chooseFeature", "wfFeature", "featureChooser", "featurePanel"):
        assert gone not in script, gone
    tab_request = script[script.index("async function loadWorkflow("):]
    tab_request = tab_request[:tab_request.index("\n}\n")]
    assert 'fetch("/api/workflow"' in tab_request and "feature" not in tab_request
    panel_request = script[script.index("async function loadPanelWorkflow()"):]
    assert "/api/workflow?feature=" in panel_request[:panel_request.index("\n}\n")]


# --- documents and diagrams (#28) -------------------------------------------

MODEL_TAIL = """
## Model

```mermaid
flowchart LR
  A --> B
```

### States

Text under a subsection.

## Other section

### Not the model

secret
"""
MOCKUP = "<!doctype html><title>m</title><script>1</script><p>mockup</p>"


def write_documents(root: Path) -> SimpleNamespace:
    """Documents in the worktree of `feat` (its row's tree) and in the primary of acme/alpha."""
    primary, tree = root / "alpha", root / "alpha-feat"

    def put(base: Path, rel: str, text: str) -> Path:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    spec = put(tree, "docs/specs/feat.md", spec_text("feat") + MODEL_TAIL)
    put(tree, "docs/models/feat.md", "# model of feat\n")
    put(tree, "docs/design/feat.md", "# design of feat\n")
    mockup = put(tree, ".scratch/product-review/feat/index.html", MOCKUP)
    for prefix_only in ("docs/design/feat-old.md", "docs/models/feat-25.md",
                        ".scratch/product-review/feat-2/x.html"):
        put(tree, prefix_only, "not feat\n")
    adr = put(primary, "docs/adr/0001-first.md", "# adr one\n")
    put(primary, "docs/adr/notes.txt", "not an adr\n")
    design = put(primary, "docs/design/zzz.md", "# design zzz\n")
    return SimpleNamespace(primary=primary, tree=tree, spec=spec, mockup=mockup, adr=adr, design=design)


@pytest.fixture
def documented(acting):
    acting.docs = write_documents(acting.root)
    return acting


def documents_of(port: int, repo: str = "acme/alpha", host: str | None = None):
    resp, body = request(port, "GET", f"/api/documents?repo={repo}", host=host)
    return resp, json.loads(body)


def get_document(port: int, doc_id: str, host: str | None = None):
    return request(port, "GET", f"/api/documents/{doc_id}", host=host)


def listed(port: int) -> dict[str, dict]:
    """Name -> wire document of the repo listing (this also fills the server's catalog)."""
    resp, body = documents_of(port)
    assert resp.status == 200
    return {d["name"]: d for d in body["documents"]}


def test_vendor_serves_only_manifest_names(running):
    """Witness `/vendor/MANIFEST.md` and `/vendor/mermaid.LICENSE` (real files of the directory),
    `/vendor/../server.py` and `/vendor/x.js` - distinguish 'the names of the manifest' from 'any
    file under vendor_dir' and from a path built from the request."""
    port, _ = running
    for name in ("mermaid.min.js", "marked.umd.js"):
        resp, body = request(port, "GET", f"/vendor/{name}")
        assert resp.status == 200 and resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
        assert body == (APP / "vendor" / name).read_bytes()
        assert resp.getheader("Cache-Control") == "no-cache"
    for path in ("/vendor/MANIFEST.md", "/vendor/mermaid.LICENSE", "/vendor/../server.py",
                 "/vendor/x.js", "/vendor/", "/vendor", "/vendor/..%2Fserver.py"):
        assert request(port, "GET", path)[0].status == 404, path
    assert request(port, "GET", "/vendor/mermaid.min.js", host="evil.example:1")[0].status == 403


def test_vendor_is_404_for_a_manifest_that_is_not_utf8(tmp_path, workflow_dir, presence_dir):
    """Witness a vendor directory whose MANIFEST.md holds bytes that are not UTF-8 - distinguish a
    404 (the same as an unreadable manifest) from an unhandled UnicodeDecodeError (dropped connection)."""
    root, _ = populated_roots(tmp_path)
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "MANIFEST.md").write_bytes(b"| a.js | 1 | u | " + b"0" * 64 + b" |\n\xff\xfe\n")
    (vendor / "a.js").write_text("1")
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), vendor_dir=vendor)
    try:
        assert request(httpd.server_address[1], "GET", "/vendor/a.js")[0].status == 404
    finally:
        stop(httpd, thread)


def test_documents_only_from_the_catalog(documented):
    """Witness the id of the primary's README (a real file no listing returns), the id of a listed
    adr asked BEFORE its listing then after it, and an id built from another path - distinguish
    'ids a listing returned' from 'any file of the repo' / 'any well-formed id'; no snapshot, an
    unknown repo and a missing `repo` are 404."""
    ns, docs = documented, documented.docs
    adr_id = catalog.doc_id("adr", str(docs.adr))
    assert documents_of(ns.port)[0].status == 404                  # no snapshot yet
    known(ns.port)
    assert documents_of(ns.port, "nobody/nothing")[0].status == 404
    assert request(ns.port, "GET", "/api/documents")[0].status == 404
    assert request(ns.port, "GET", "/api/documents?repo=")[0].status == 404
    assert get_document(ns.port, adr_id)[0].status == 404          # a real file, not listed yet
    readme_id = catalog.doc_id("design", str(docs.primary / "README.md"))
    other_path = catalog.doc_id("adr", str(docs.adr) + ".other")
    assert get_document(ns.port, readme_id)[0].status == 404
    by_name = listed(ns.port)
    assert by_name["docs/adr/0001-first.md"]["id"] == adr_id
    resp, body = get_document(ns.port, adr_id)
    assert resp.status == 200 and body == b"# adr one\n"
    assert get_document(ns.port, readme_id)[0].status == 404
    assert get_document(ns.port, other_path)[0].status == 404
    assert get_document(ns.port, "../../etc/passwd")[0].status == 404
    assert request(ns.port, "GET", "/api/documents/")[0].status == 404


def test_the_repo_listing_has_every_kind_sorted_and_a_repo_without_a_primary_is_empty(documented):
    """Witness the case-different repo `ACME/Alpha` and `nobody/elsewhere` (a repo of the snapshot
    known only from a handoff) - distinguish a case-insensitive identity and 'in the snapshot' from
    'primary found'; an empty listing from a 404."""
    ns = documented
    known(ns.port)
    resp, body = documents_of(ns.port, "ACME/Alpha")
    assert resp.status == 200 and body["repo"] == "ACME/Alpha"
    assert [(d["kind"], d["name"]) for d in body["documents"]] == [
        ("adr", "docs/adr/0001-first.md"), ("design", "docs/design/zzz.md")]
    assert set(body["documents"][0]) == {"id", "kind", "name", "path"}
    resp, body = documents_of(ns.port, "nobody/elsewhere")
    assert resp.status == 200 and body["documents"] == []


def test_the_catalog_keeps_the_ids_of_earlier_listings(documented):
    """Witness a repo listing followed by the detail of another feature, then by a second listing -
    distinguish a catalog that remembers every id from one holding only the last listing."""
    ns = documented
    row = known(ns.port)["feat"]
    adr_id = listed(ns.port)["docs/adr/0001-first.md"]["id"]
    assert request(ns.port, "GET", f"/api/features/{row['id']}")[0].status == 200
    assert get_document(ns.port, adr_id)[0].status == 200
    ns.docs.adr.write_text("# adr one, edited\n")
    listed(ns.port)
    assert get_document(ns.port, adr_id)[1] == b"# adr one, edited\n"


ROW_KINDS = ("spec", "handoff", "report")   # the row's own files (#46), not the #28 feature documents


def test_feature_detail_lists_its_documents_by_exact_slug(documented):
    """Witness `feat-old.md`, `feat-25.md` and `feat-2/x.html` beside slug `feat` - distinguish an
    exact slug from a prefix; the detail's ids open (the detail fills the catalog)."""
    ns, docs = documented, documented.docs
    row = known(ns.port)["feat"]
    resp, body = request(ns.port, "GET", f"/api/features/{row['id']}")
    detail = json.loads(body)
    feature_docs = [d for d in detail["documents"] if d["kind"] not in ROW_KINDS]
    assert sorted((d["kind"], d["name"]) for d in feature_docs) == [
        ("design", "docs/design/feat.md"), ("mockup", ".scratch/product-review/feat/index.html"),
        ("model", "docs/models/feat.md"), ("spec-model", "docs/specs/feat.md#Model")]
    by_kind = {d["kind"]: d for d in detail["documents"]}
    assert Path(by_kind["spec-model"]["path"]).resolve() == docs.spec.resolve()
    resp, body = get_document(ns.port, by_kind["spec-model"]["id"])
    model = body.decode()
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/markdown; charset=utf-8"
    assert model.startswith("## Model\n") and "### States" in model and "```mermaid" in model
    assert "Other section" not in model and "secret" not in model
    assert get_document(ns.port, by_kind["design"]["id"])[1] == b"# design of feat\n"
    other = known(ns.port)["other-slug"]          # a row with no tree: no documents
    own = json.loads(request(ns.port, "GET", f"/api/features/{other['id']}")[1])["documents"]
    assert [d for d in own if d["kind"] not in ROW_KINDS] == []


def test_mockups_are_sandboxed(documented):
    """Witness the header value compared whole - `sandbox` exactly - distinguishes a bare sandbox
    from `sandbox allow-scripts` (a substring or startswith check passes both); a markdown document
    answers `text/markdown` and carries no sandbox header."""
    ns, docs = documented, documented.docs
    row = known(ns.port)["feat"]
    detail = json.loads(request(ns.port, "GET", f"/api/features/{row['id']}")[1])
    mockup = next(d for d in detail["documents"] if d["kind"] == "mockup")
    resp, body = get_document(ns.port, mockup["id"])
    assert resp.status == 200 and body == MOCKUP.encode()
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    assert resp.getheader("Content-Security-Policy") == "sandbox"
    assert resp.getheader("Cache-Control") == "no-store"
    by_name = listed(ns.port)
    resp, _ = get_document(ns.port, by_name["docs/adr/0001-first.md"]["id"])
    assert resp.getheader("Content-Type") == "text/markdown; charset=utf-8"
    assert resp.getheader("Content-Security-Policy") is None
    assert docs.mockup.is_file()


def test_a_vanished_document_is_503(documented):
    """Witness a listed file removed before its GET, and a spec whose `## Model` was removed -
    distinguish 503 'cannot be read now' from 404 'not in the catalog' and from a dropped socket."""
    ns, docs = documented, documented.docs
    known(ns.port)
    by_name = listed(ns.port)
    docs.adr.unlink()
    resp, body = get_document(ns.port, by_name["docs/adr/0001-first.md"]["id"])
    assert resp.status == 503 and "error" in json.loads(body)
    row = known(ns.port)["feat"]
    detail = json.loads(request(ns.port, "GET", f"/api/features/{row['id']}")[1])
    model = next(d for d in detail["documents"] if d["kind"] == "spec-model")
    docs.spec.write_text(spec_text("feat"))
    assert get_document(ns.port, model["id"])[0].status == 503
    assert get_document(ns.port, by_name["docs/design/zzz.md"]["id"])[0].status == 200


def test_page_csp_admits_inline_styles_only(running):
    """Witness the whole header compared to the literal of the spec - distinguishes
    `style-src 'self' 'unsafe-inline'` alone from a looser `default-src 'unsafe-inline'`, a
    `script-src` addition or a missing `frame-src`."""
    port, _ = running
    resp, _ = request(port, "GET", "/")
    expected = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; connect-src 'self'; form-action 'none'"
    assert resp.getheader("Content-Security-Policy") == expected == server.CSP


def test_the_page_loads_no_vendored_script(running):
    """Witness the `GET /` body: no vendored script tag, `theme.js`, `panel.js` then `app.js` loaded, no inline script."""
    port, _ = running
    html = request(port, "GET", "/")[1].decode()
    assert re.findall(r'<script src="([^"]+)"', html) == ["/static/theme.js", "/static/panel.js", "/static/app.js"]
    assert html.count("<script") == 3
    assert 'id="tab-documents"' in html


def test_vendored_files_are_revalidated(running):
    """A vendored file answers 200 with its manifest sha as `ETag` and `no-cache`; the same tag, a weak
    tag, `*` or a list holding it answer 304 with no body; another tag answers 200 again; and
    `/api/features` keeps `no-store`, so a handler that drops it everywhere cannot pass."""
    port, _ = running
    sha = catalog.vendor_files(APP / "vendor")["mermaid.min.js"]
    tag = f'"{sha}"'
    path = "/vendor/mermaid.min.js"
    resp, body = request(port, "GET", path)
    assert resp.status == 200 and body == (APP / "vendor" / "mermaid.min.js").read_bytes()
    assert resp.getheader("ETag") == tag
    assert resp.getheader("Cache-Control") == "no-cache"
    for match in (tag, f"W/{tag}", "*", f'"other", {tag}'):
        resp, body = request(port, "GET", path, headers={"If-None-Match": match})
        assert resp.status == 304 and body == b"", match
        assert resp.getheader("ETag") == tag
        assert resp.getheader("Cache-Control") == "no-cache"
    resp, body = request(port, "GET", path, headers={"If-None-Match": '"other"'})
    assert resp.status == 200 and len(body) > 0
    assert request(port, "GET", "/api/features")[0].getheader("Cache-Control") == "no-store"


def test_etag_matches_is_true_only_for_the_tag_a_list_holding_it_a_weak_tag_or_star():
    """`None`, the empty string, another tag and a longer tag are False; the tag, a list holding it, its weak form
    and `*` are True."""
    tag = '"abc"'
    for header in (None, "", '"other"', '"abcd"'):
        assert server.etag_matches(header, tag) is False
    for header in (tag, f'"x", {tag}', f"W/{tag}", "*", f' "x" ,W/{tag} '):
        assert server.etag_matches(header, tag) is True


def test_documents_are_readable_from_the_remote_host(tmp_path, workflow_dir):
    """Witness `Host: H` for the listing, a document and a vendored file next to a near-miss host -
    distinguish 'read-only from the phone' (documents readable) from refusing the remote host."""
    root, _ = populated_roots(tmp_path)
    docs = write_documents(root)
    httpd, thread = serve_over(root, workflow_dir, None, Recorder(), remote_host=REMOTE)
    try:
        port = httpd.server_address[1]
        request(port, "GET", "/api/features")
        resp, body = documents_of(port, host=REMOTE)
        assert resp.status == 200 and body["documents"]
        doc_id = body["documents"][0]["id"]
        resp, _ = get_document(port, doc_id, host=REMOTE)
        assert resp.status == 200
        assert request(port, "GET", "/vendor/marked.umd.js", host=REMOTE)[0].status == 200
        assert get_document(port, doc_id, host="other.example.ts.net:8443")[0].status == 403
        assert docs.adr.is_file()
    finally:
        stop(httpd, thread)


# --- the workflow map (#29) -------------------------------------------------

OVERLAY_SPEC = """\
---
feature: feat
repo: owner/feat
branch: feature/feat
tracker: #1
---

## Phase plan

| Phase | Decision | Reason |
|---|---|---|
| 0 · Start | run | worktree |
| 1 · Design & alignment | skip | tiny fix |
| 2 · Product review | run | a UI |
| 3 · System architecture | run | laws |
| 4 · Tickets | run | one |
| 5 · Program design | run | code |
| 6 · Implement-loop | run | code |

Approved: 2026-11-16, owner in chat — "Approvo" (plan as above).

## Product review

Mockup: `docs/nothing-here.html`
"""


def map_install(workflow_dir: Path) -> Path:
    """Complete the `workflow_dir` fixture into an install the map can read: the real gen-flow.py
    linked, the real prose copied, and a stub `LESSONS.md` (the real one is private and not part of
    an exported tree)."""
    (workflow_dir / "gen-flow.py").symlink_to(ROOT / "gen-flow.py")
    (workflow_dir / "guards").symlink_to(ROOT / "guards")
    for name in ("WORKFLOW.md", "INVARIANTS.md", "REVIEW-CRITERIA.md", "SESSION-ISOLATION.md"):
        shutil.copy(ROOT / name, workflow_dir / name)
    (workflow_dir / "LESSONS.md").write_text("# Lessons\n\nstub\n")
    shutil.copytree(ROOT / "phases", workflow_dir / "phases")
    return workflow_dir


def map_skills(tmp_path: Path) -> Path:
    """A skills store of its own: every skill the pipeline names, one line each."""
    store = tmp_path / "skills"
    for name in ("grill-with-docs", "domain-modeling", "to-spec", "uml-modeling", "product-review",
                 "system-architecture", "modular-monolith", "to-tickets", "program-design",
                 "implement-loop", "code-review", "ponytail-lean"):
        (store / name).mkdir(parents=True)
        (store / name / "SKILL.md").write_text(f"# skill {name}\n")
    return store


def workflow_of(port: int, feature: str | None = None, host: str | None = None):
    path = "/api/workflow" + (f"?feature={feature}" if feature is not None else "")
    resp, body = request(port, "GET", path, host=host)
    return resp, json.loads(body)


@pytest.fixture
def mapped(tmp_path: Path, workflow_dir: Path, presence_dir: Path):
    """A live server with a recorder over a root whose `feat` is at phase 2, an install the map can
    read and a skills store of its own."""
    root, _ = populated_roots(tmp_path)
    (root / "alpha-feat" / "docs" / "specs" / "feat.md").write_text(OVERLAY_SPEC)
    skills = map_skills(tmp_path)
    recorder = Recorder()
    httpd, thread = serve_over(root, map_install(workflow_dir), presence_dir, recorder, skills_dir=skills)
    yield SimpleNamespace(port=httpd.server_address[1], root=root, httpd=httpd, calls=recorder.calls,
                          workflow_dir=workflow_dir, skills=skills)
    stop(httpd, thread)


def test_overlay_comes_from_the_last_snapshot(mapped):
    """Witness `feat` (phase 1 skipped by the plan with a reason, phase 2 failing, phases 3-6 not
    walked) and a claim row without a spec - distinguish `here` = the row's derived phase from
    'the first phase that is not a pass' (which would mark the skipped 1), a skipped phase that
    shows its plan reason, and a phase with no verdict (null) from a made-up status; the claim row
    has no verdict, so nothing is marked. Unknown id and a request before any snapshot are 404."""
    port = mapped.port
    assert workflow_of(port, "0123456789ab")[0].status == 404       # before any snapshot
    resp, plain = workflow_of(port)
    assert resp.status == 200
    assert [p["overlay"] for p in plain["phases"]] == [None] * 7
    known(port)                                       # the install is fingerprinted: phases compute in jobs
    assert mapped.httpd.phase_jobs.wait_idle(60)
    rows = known(port)
    feat = rows["feat"]
    assert feat["phase"]["phase"] == 2
    resp, body = workflow_of(port, feat["id"])
    assert resp.status == 200
    overlays = [p["overlay"] for p in body["phases"]]
    verdicts = {v["phase"]: v for v in feat["phase"]["verdicts"]}
    assert overlays[0] == {"status": "pass", "problems": [], "reason": "", "here": False}
    assert overlays[1] == {"status": "skip", "problems": [], "reason": "tiny fix", "here": False}
    assert overlays[2] == {"status": "fail", "problems": verdicts[2]["problems"], "reason": "",
                           "here": True}
    assert any("nothing-here.html" in line for line in overlays[2]["problems"])
    assert overlays[3:] == [None] * 4
    resp, claim = workflow_of(port, rows["other-slug"]["id"])
    assert resp.status == 200 and [p["overlay"] for p in claim["phases"]] == [None] * 7
    assert workflow_of(port, "0123456789ab")[0].status == 404       # not in the snapshot


def test_workflow_documents_enter_the_catalog(mapped):
    """Witness the file of phase 2 and of a skill, asked before and after the listing, a
    transversal file, and a skill of the store that no phase names - distinguish 'the catalog is what a listing returned' (404, then 200) from
    reading by path; a skill id opens its own SKILL.md."""
    port = mapped.port
    phase_file = mapped.workflow_dir / "phases" / "2-product-review.md"
    skill_file = mapped.skills / "product-review" / "SKILL.md"
    before = catalog.doc_id("workflow", str(phase_file))
    assert get_document(port, before)[0].status == 404
    resp, body = workflow_of(port)
    assert resp.status == 200
    phase2 = body["phases"][2]
    by_name = {d["name"]: d for d in phase2["documents"]}
    assert by_name["phases/2-product-review.md"] == {
        "id": before, "kind": "workflow", "name": "phases/2-product-review.md", "path": str(phase_file)}
    assert by_name["product-review"]["kind"] == "skill"
    assert phase2["rules_file"] == "phases/2-product-review.md"
    assert phase2["skills"] == ["product-review"]
    resp, text = get_document(port, before)
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/markdown; charset=utf-8"
    assert text == phase_file.read_bytes()
    assert get_document(port, by_name["product-review"]["id"])[1] == skill_file.read_bytes()
    # SESSION-ISOLATION.md, not LESSONS.md: the kit's WORKFLOW.md has no row for the private lessons file
    isolation = next(t for t in body["transversal"] if t["file"] == "SESSION-ISOLATION.md")
    assert isolation["document"]["kind"] == "workflow"
    assert get_document(port, isolation["document"]["id"])[1] == (
        mapped.workflow_dir / "SESSION-ISOLATION.md").read_bytes()
    (mapped.skills / "stray").mkdir()
    (mapped.skills / "stray" / "SKILL.md").write_text("# not in the pipeline\n")
    never = catalog.doc_id("skill", str(mapped.skills / "stray" / "SKILL.md"))
    assert get_document(port, never)[0].status == 404
    assert mapped.calls == []


def test_workflow_is_503_when_the_install_cannot_be_read(tmp_path, workflow_dir, presence_dir):
    """Witness a workflow dir with no gen-flow.py, then one with gen-flow.py but no WORKFLOW.md -
    distinguish a read failure answered 503 (each cause) from a dropped socket or an empty map."""
    root, _ = populated_roots(tmp_path)
    httpd, thread = serve_over(root, workflow_dir, presence_dir, Recorder(), skills_dir=map_skills(tmp_path))
    try:
        port = httpd.server_address[1]
        resp, body = workflow_of(port)
        assert resp.status == 503 and "error" in body
        map_install(workflow_dir)
        assert workflow_of(port)[0].status == 200
        (workflow_dir / "WORKFLOW.md").unlink()
        assert workflow_of(port)[0].status == 503
    finally:
        stop(httpd, thread)


def test_a_server_without_a_skills_dir_has_no_skill_document_and_never_reads_home(
        tmp_path, monkeypatch, workflow_dir, presence_dir):
    """Witness a HOME holding `.agents/skills/<every pipeline skill>/SKILL.md` and a server built
    without `skills_dir` - distinguish 'no skills store' (no `skill` document, the names still
    listed) from a server-side default of `~/.agents/skills`."""
    home = tmp_path / "home"
    shutil.copytree(map_skills(tmp_path), home / ".agents" / "skills")
    monkeypatch.setenv("HOME", str(home))
    root, _ = populated_roots(tmp_path)
    httpd, thread = serve_over(root, map_install(workflow_dir), presence_dir, Recorder())
    try:
        resp, body = workflow_of(httpd.server_address[1])
        assert resp.status == 200
        phases = body["phases"]
        assert [d for p in phases for d in p["documents"] if d["kind"] == "skill"] == []
        assert any(p["skills"] for p in phases)
    finally:
        stop(httpd, thread)


def test_workflow_is_readable_from_the_remote_host_and_the_page_has_its_tab(tmp_path, workflow_dir, presence_dir):
    """Witness `Host: H` on the map and the page's tab order - distinguish 'read-only from the
    phone' (the map readable) from refusing the remote host, and a tab between History and
    Documents from one appended elsewhere."""
    root, _ = populated_roots(tmp_path)
    httpd, thread = serve_over(root, map_install(workflow_dir), presence_dir, Recorder(),
                               remote_host=REMOTE, skills_dir=map_skills(tmp_path))
    try:
        port = httpd.server_address[1]
        assert workflow_of(port, host=REMOTE)[0].status == 200
        assert workflow_of(port, host="other.example.ts.net:8443")[0].status == 403
        html = request(port, "GET", "/")[1].decode()
        assert re.findall(r'id="tab-(\w+)"', html) == ["open", "history", "workflow", "documents"]
    finally:
        stop(httpd, thread)


# --- report-store #56: the window reads reports from the store ----------------

report_store = load("report_store", ROOT / "report_store.py")
harness = load("harness_fixture_for_app", ROOT / "tests" / "test_harness.py")
STORE_NOW = datetime(2026, 11, 17, 12, 0)


def review_row(item: str, repo: str = "ACME/alpha") -> dict:
    """A coverage row (no commit quote needed) of the shape `report add` accepts."""
    return {"repo": repo, "criterion": "a", "item_id": item, "verdict": "pass", "severity": "fatal",
            "reviewer": "spec", "actor": "spec-1", "claim": "coverage"}


def put_review(store: Path, repo: str, feature: str, report: str, items: list[str]) -> None:
    conn = report_store.open_store(store)
    report_store.add_review_rows(conn, [review_row(i, repo) for i in items], feature, report,
                                 store.parent, STORE_NOW)
    conn.close()


def put_matrix(store: Path, name: str, ids: list[str]) -> None:
    conn = report_store.open_store(store)
    report_store.add_matrix(conn, name, [{"id": i, "kind": "mutant", "outcome": "pinned"}
                                         for i in ids], STORE_NOW)
    conn.close()


def put_manifest(store: Path, name: str, text: str) -> None:
    # by SQL: a valid prover manifest is long and this test only needs a stored one
    conn = report_store.open_store(store)
    with conn:
        conn.execute("INSERT INTO mutation_manifest (name, recorded_at, manifest_json) VALUES (?,?,?)",
                     (name, STORE_NOW.isoformat(), text))
    conn.close()


@pytest.fixture
def stored(tmp_path: Path, workflow_dir: Path, presence_dir: Path):
    """A live server whose store (under tmp_path, not created yet) is `ns.store`."""
    root, _ = populated_roots(tmp_path)
    store = tmp_path / "state" / "reports.sqlite"
    httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir, store=store)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(port=httpd.server_address[1], root=root, store=store,
                          workflow_dir=workflow_dir, httpd=httpd)
    stop(httpd, thread)


def detail_of(port: int, slug: str = "other-slug") -> dict:
    row = next(r for r in features(port)["open"] if r["slug"] == slug)
    resp, body = request(port, "GET", f"/api/features/{row['id']}")
    assert resp.status == 200
    return json.loads(body)


def test_a_row_lists_its_stored_review_reports(stored):
    """witness the same report name `other-slug-spec` stored for repo someone/else - distinguishes
    attribution by the row's (repo case-folded, feature) from attribution by report name alone;
    the row's repo is stored as ACME/alpha against the row's acme/alpha, so a case-sensitive
    match would list nothing."""
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-spec", ["I1", "I2"])
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-standards", ["I1"])
    put_review(stored.store, "someone/else", "other-slug", "other-slug-spec", ["I9"])
    put_review(stored.store, "ACME/alpha", "feat", "feat-spec", ["I1"])
    detail = detail_of(stored.port)
    assert sorted(r["name"] for r in detail["reports"]) == [
        "other-slug-spec.jsonl", "other-slug-standards.jsonl"]
    assert all(r["id"] for r in detail["reports"])
    assert {d["name"] for d in detail["documents"] if d["kind"] == "report"} == {
        r["name"] for r in detail["reports"]}


def test_a_stored_report_opens_with_the_same_wire_shape(stored):
    """witness a review report of two rows, a matrix of two rows and a manifest - distinguish
    JSONL (one object per line, the rows as stored) from one JSON array, and a manifest served
    as ndjson from the content type of its suffix."""
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-spec", ["I1", "I2"])
    put_matrix(stored.store, "other-slug-mutation-1", ["M1", "M2"])
    put_manifest(stored.store, "other-slug-mutation-manifest-1", '{"mutants": []}')
    docs = {d["name"]: d for d in detail_of(stored.port)["documents"] if d["kind"] == "report"}
    assert sorted(docs) == ["other-slug-mutation-1.jsonl", "other-slug-mutation-manifest-1.json",
                            "other-slug-spec.jsonl"]
    resp, body = get_document(stored.port, docs["other-slug-spec.jsonl"]["id"])
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "application/x-ndjson; charset=utf-8"
    assert [json.loads(line) for line in body.decode().splitlines()] == [
        review_row("I1"), review_row("I2")]
    resp, body = get_document(stored.port, docs["other-slug-mutation-1.jsonl"]["id"])
    assert resp.getheader("Content-Type") == "application/x-ndjson; charset=utf-8"
    assert [json.loads(line)["id"] for line in body.decode().splitlines()] == ["M1", "M2"]
    resp, body = get_document(stored.port, docs["other-slug-mutation-manifest-1.json"]["id"])
    assert resp.getheader("Content-Type") == "application/json; charset=utf-8"
    assert json.loads(body) == {"mutants": []}


def test_a_report_file_is_not_listed_and_the_store_is_the_only_source(stored):
    """witness `other-slug-spec.jsonl` as a file (one row, item FILE) and in the store (two
    rows), and `other-slug-extra.jsonl` as a file only - distinguishes 'the store is the only
    source' from listing the file too, or the file winning for the shared name."""
    reports = stored.workflow_dir / "reports"
    reports.mkdir()
    for name in ("other-slug-spec", "other-slug-extra"):
        (reports / f"{name}.jsonl").write_text(json.dumps({**review_row("FILE"), "claim": "coverage"}) + "\n")
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-spec", ["S1", "S2"])
    detail = detail_of(stored.port)
    assert [r["name"] for r in detail["reports"]] == ["other-slug-spec.jsonl"]
    assert [d["name"] for d in detail["documents"] if d["kind"] == "report"] == ["other-slug-spec.jsonl"]
    _resp, body = get_document(stored.port, detail["reports"][0]["id"])
    assert [json.loads(line)["item_id"] for line in body.decode().splitlines()] == ["S1", "S2"]


def test_a_vanished_store_is_a_503_for_its_documents(stored):
    """witness the store removed after the listing - distinguishes a 503 like a vanished file
    from a 200 with an empty body or a dropped socket."""
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-spec", ["I1"])
    report = detail_of(stored.port)["reports"][0]
    assert get_document(stored.port, report["id"])[0].status == 200
    stored.store.unlink()
    resp, body = get_document(stored.port, report["id"])
    assert resp.status == 503 and json.loads(body)["error"]


def test_a_corrupt_store_is_a_503_for_its_documents(stored):
    """witness the store file overwritten with non-SQLite bytes after the listing - distinguishes a
    503 with a JSON error from a sqlite3.Error escaping the handler and dropping the connection."""
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-spec", ["I1"])
    report = detail_of(stored.port)["reports"][0]
    stored.store.write_bytes(b"this is not an sqlite database" * 20)
    resp, body = get_document(stored.port, report["id"])
    assert resp.status == 503 and json.loads(body)["error"]


def test_the_window_never_creates_or_writes_the_store(stored):
    """witness a store path that does not exist, then one with a report: after a full run of
    the endpoints the first still does not exist and the second keeps its (mtime, size), and the
    repos' porcelain is unchanged - distinguishes read-only `open_existing` from `open_store`
    (which creates the file and its directory) or any write."""
    root = stored.root
    repos = [root / "alpha", root / "alpha-feat"]

    def run_everything() -> None:
        assert request(stored.port, "GET", "/api/features")[0].status == 200
        for slug in ("feat", "other-slug"):
            detail = detail_of(stored.port, slug)
            for doc in detail["documents"]:
                get_document(stored.port, doc["id"])
        listed(stored.port)

    before = {r: status(r) for r in repos}
    run_everything()
    assert not stored.store.exists() and not stored.store.parent.exists()
    put_review(stored.store, "ACME/alpha", "other-slug", "other-slug-spec", ["I1"])
    seen = stored.store.stat()
    run_everything()
    after = stored.store.stat()
    assert (after.st_mtime_ns, after.st_size) == (seen.st_mtime_ns, seen.st_size)
    assert {r: status(r) for r in repos} == before
    assert detail_of(stored.port)["reports"], "the store was not read - a no-op would pass"


DELIVERED_WITH_STORE = (harness.SPEC.replace("| 6 · Implement-loop | skip | docs only |",
                                             "| 6 · Implement-loop | run | — |")
                        + "\n## Delivery\n\n"
                        + "".join(f"- {ref} — PR: https://example.com/pr/1 · Model: n/a — docs only · "
                                  "Mutation: n/a — docs only\n"
                                  for ref in ("#9", "https://example.com/owner/demo/issues/2")))


def test_the_window_phase_reads_the_store(tmp_path, workflow_dir, presence_dir):
    """witness a spec whose phase 6 passes only with the store's brief and review rows - with the
    server's store the row is `delivered`, with store=None it is phase 6 - distinguishes a
    `wf.derive` that gets the store from one called without it."""
    root = tmp_path / "roots"
    primary = make_repo(root, "alpha", "https://host/scm/acme/alpha.git")
    spec = primary / "docs" / "specs" / "demo.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(DELIVERED_WITH_STORE)
    mockup = primary / "docs" / "mockups" / "demo.md"
    mockup.parent.mkdir(parents=True)
    mockup.write_text("mockup\n")
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "spec")
    store = tmp_path / "state" / "reports.sqlite"
    put_review(store, "owner/demo", "demo", "demo-spec", ["I1", "I2"])
    conn = report_store.open_store(store)
    report_store.add_brief_items(conn, "owner/demo", "demo", ["I1", "I2"], STORE_NOW)
    conn.close()
    phases = {}
    for label, path in (("with", store), ("without", None)):
        httpd = server.build_server(0, (root,), workflow_dir, APP / "static", presence_dir, store=path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            features(httpd.server_address[1])         # first answer: `computing`, a job derives it
            assert httpd.phase_jobs.wait_idle(60)
            rows = features(httpd.server_address[1])
            row = next(r for r in (*rows["open"], *rows["history"]) if r["slug"] == "demo")
            phases[label] = (row["phase"]["kind"], row["phase"]["phase"])
        finally:
            stop(httpd, thread)
    assert phases == {"with": ("delivered", None), "without": ("derived", 6)}


def test_serve_hands_the_default_store_path_to_the_server(monkeypatch, tmp_path):
    """witness `AGENT_REPORT_STORE=E`, then `XDG_STATE_HOME=X` alone, then neither (HOME fixed) -
    distinguishes `report_store.default_store_path(os.environ, Path.home())` (the variable over the
    XDG state dir over `~/.local/state`) from a path fixed in code or not handed down; no flag
    exists for it."""
    seen = []

    def build(port, roots, workflow_dir, static_dir, remote_host=None, jira_url=None, distro="",
              skills_dir=None, store=None, cache_file=None):
        seen.append(store)
        return FakeHttpd()

    monkeypatch.setattr(desk.server, "build_server", build)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_REPORT_STORE", str(tmp_path / "env.sqlite"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert desk.main(["serve"]) == 0
    monkeypatch.delenv("AGENT_REPORT_STORE")
    assert desk.main(["serve"]) == 0
    monkeypatch.delenv("XDG_STATE_HOME")
    assert desk.main(["serve"]) == 0
    assert seen == [tmp_path / "env.sqlite", tmp_path / "xdg" / "agentic-dev-kit" / "reports.sqlite",
                    tmp_path / "home" / ".local" / "state" / "agentic-dev-kit" / "reports.sqlite"]


# --- #85: each phase lists its artefacts -------------------------------------------

def test_every_artefact_of_a_phase_opens_through_the_document_endpoint(
        tmp_path, workflow_dir, presence_dir):
    """witness a row with a spec (with Model), a model, a design, a mockup, a handoff and a stored
    report - distinguishes artefacts listed through `_listed` (every id opens) from ids that never
    entered the catalog; the kinds must all be present or the loop passes vacuously."""
    root, _ = populated_roots(tmp_path)
    write_documents(root)
    (workflow_dir / "handoffs" / "handoff-feat-20261116.md").write_text(
        "# Handoff feat\n\n- **Repo**: ACME/alpha\n\n## Done\n\n- x\n\n"
        "## Next session\n\n- go\n\n## Ground truth\n\n- ok\n")
    store = tmp_path / "state" / "reports.sqlite"
    put_review(store, "ACME/alpha", "feat", "feat-spec", ["I1"])
    httpd, thread = serve_over(root, map_install(workflow_dir), presence_dir, Recorder(), store=store)
    try:
        port = httpd.server_address[1]
        resp, body = workflow_of(port, known(port)["feat"]["id"])
        assert resp.status == 200
        artefacts = [a for p in body["phases"] for a in p["artefacts"]]
        assert {a["kind"] for a in artefacts} == {"spec", "spec-model", "model", "design",
                                                  "mockup", "handoff", "report"}
        for artefact in artefacts:
            assert get_document(port, artefact["id"])[0].status == 200
    finally:
        stop(httpd, thread)


def test_the_generic_workflow_has_no_artefacts(mapped):
    """witness the generic map asked AFTER a feature's map filled the catalog - distinguishes an
    empty `artefacts` from one drawn from the documents the server already knows."""
    assert workflow_of(mapped.port, known(mapped.port)["other-slug"]["id"])[1]["phases"][6]["artefacts"]
    resp, body = workflow_of(mapped.port)
    assert resp.status == 200 and body["phases"]
    assert all(p["artefacts"] == [] for p in body["phases"])


def test_a_feature_with_no_tree_has_no_artefacts_and_a_200(mapped):
    """witness the claim row `other-slug` (no worktree, one handoff) - distinguishes an empty
    feature catalog from a 404/503; only its own handoff, in phase 6, is listed."""
    resp, body = workflow_of(mapped.port, known(mapped.port)["other-slug"]["id"])
    assert resp.status == 200
    assert {p["n"]: [a["kind"] for a in p["artefacts"]] for p in body["phases"]} == {
        n: (["handoff"] if n == 6 else []) for n in range(7)}


def unreadable_spec_row(mapped):
    """The `feat` row (spec, handoff) after the snapshot, its spec replaced by a directory: the spec
    exists but `read_text` raises an OSError whatever the uid. Returns the row id."""
    (mapped.workflow_dir / "handoffs" / "handoff-feat-20261116.md").write_text(
        "# Handoff feat\n\n- **Repo**: ACME/alpha\n\n## Done\n\n- x\n\n"
        "## Next session\n\n- go\n\n## Ground truth\n\n- ok\n")
    row_id = known(mapped.port)["feat"]["id"]
    spec = mapped.root / "alpha-feat" / "docs" / "specs" / "feat.md"
    spec.unlink()
    spec.mkdir()
    return row_id


def test_a_feature_whose_spec_cannot_be_read_still_answers_200_with_its_own_documents(mapped):
    """witness `feat` with its spec unreadable after the snapshot and a handoff of its own -
    distinguishes the 200 of invariant 4 (no feature artefacts, own documents kept) from a 503."""
    resp, body = workflow_of(mapped.port, unreadable_spec_row(mapped))
    assert resp.status == 200
    kinds = {p["n"]: [a["kind"] for a in p["artefacts"]] for p in body["phases"]}
    assert "handoff" in kinds[6]
    assert not {"spec-model", "model", "design", "mockup"} & {k for ks in kinds.values() for k in ks}


def test_the_detail_of_a_row_whose_spec_cannot_be_read_stays_503(mapped):
    """The twin: `/api/features/{id}` of the same row keeps its 503 (the decision of #85 changes
    only the workflow map)."""
    row_id = unreadable_spec_row(mapped)
    resp, body = request(mapped.port, "GET", f"/api/features/{row_id}")
    assert resp.status == 503 and json.loads(body)["error"]


def test_the_page_has_no_detail_aside_any_more(running):
    """Witness the served script and style: the old detail aside (D2) and the one tone table of the panel
    (it lives in panel.js, app.js keeps no copy)."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    for gone in ("detailPanel", "loadDetail", "clearDetail", "state.detail", "state.selected", "cannot load the detail", "TONE_OF", "skipk"):
        assert gone not in script, gone


# --- #93: the group "Other worktrees" ----------------------------------------------------------

def test_terminal_action_accepts_the_id_of_an_other_worktree(acting):
    """Witness: a worktree `release-0.0.23` added to the populated root - its row is an entry of
    `other_worktrees`, and `terminal` on its id answers 204 with the entry's own path; an id of
    no row stays 404. Distinguishes an `action_argv` that reads only open and history (404)."""
    ns = acting
    tree = git_worktree(ns.root / "alpha", "release-0.0.23")
    body = json.loads(request(ns.port, "GET", "/api/features")[1])
    assert [r["slug"] for r in body["other_worktrees"]] == ["release-0.0.23"]
    entry = body["other_worktrees"][0]
    assert entry["id"] not in {r["id"] for r in [*body["open"], *body["history"]]}
    assert post(ns.port, asks(ns, "terminal", entry["id"])).status == 204
    assert ns.calls == [["wt.exe", "-w", "new", "wsl.exe", "-d", DISTRO, "--cd", str(tree)]]
    assert post(ns.port, asks(ns, "terminal", "0" * 12)).status == 404


def git_worktree(primary: Path, name: str) -> Path:
    path = primary.parent / f"{primary.name}-{name}"
    git(primary, "worktree", "add", "-q", "-b", name, str(path))
    return path


def function_body(script: str, name: str) -> str:
    """The text of `function <name>(...) { ... }` at top level of app.js (up to the next top-level `}`)."""
    start = script.index(f"function {name}(")
    return script[start:script.index("\n}\n", start)]


def test_served_app_js_draws_the_other_worktrees_group_without_feature_actions(running):
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    assert "WfPanel.otherRows(" in script and "WfPanel.otherGroupLabel(" in script
    for name in ("otherGroup", "otherRowNode"):
        body = function_body(script, name)
        for feature_only in ("actionsBar", "featureCell", "copyButton"):
            assert feature_only not in body, f"{name}: {feature_only}"


def test_served_app_js_puts_a_new_table_above_the_other_worktrees_group(running):
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    body = function_body(script, "syncTable")
    assert 'details.other' in body and "group.before(wrap)" in body


# --- #105 window-updates: a version on the three answers the page polls ----------------------------

ETAG_FORM = re.compile(r'"[0-9a-f]{32}"')


def settled(port: int, path: str, host: str | None = None, tries: int = 40):
    """(etag, the 304 answer) once a request carrying the last tag is answered 304: the phases of the
    fixture are derived by background jobs, so the first answers may still change under the client."""
    resp, _ = request(port, "GET", path, host=host)
    assert resp.getheader("ETag"), f"the 200 of {path} carries no ETag"
    for _ in range(tries):
        etag = resp.getheader("ETag")
        resp, data = request(port, "GET", path, host=host, headers={"If-None-Match": etag or '"none"'})
        if resp.status == 304:
            return etag, resp, data
        time.sleep(0.25)
    raise AssertionError(f"the content of {path} never settled: last status {resp.status}")


def test_features_answers_200_with_a_strong_etag_and_no_store(running):
    port, _ = running
    resp, body = request(port, "GET", "/api/features")
    assert resp.status == 200
    assert ETAG_FORM.fullmatch(resp.getheader("ETag") or ""), resp.getheader("ETag")
    assert resp.getheader("Cache-Control") == "no-store"
    assert set(json.loads(body)) == {"generated_at", "partial", "errors", "open", "history", "other_worktrees"}


def test_features_with_the_current_etag_answers_304_with_no_body_and_keeps_server_timing(running):
    port, _ = running
    etag, resp, data = settled(port, "/api/features")
    assert resp.status == 304 and data == b""
    assert resp.getheader("ETag") == etag
    assert resp.getheader("Content-Length") in (None, "0")
    assert re.fullmatch(r"scan;dur=\d+", resp.getheader("Server-Timing") or "")
    assert resp.getheader("Cache-Control") == "no-store"


def test_the_features_etag_ignores_generated_at(running):
    port, _ = running
    etag, _, _ = settled(port, "/api/features")
    first, first_body = request(port, "GET", "/api/features")
    time.sleep(1.1)
    second, second_body = request(port, "GET", "/api/features")
    assert json.loads(first_body)["generated_at"] != json.loads(second_body)["generated_at"]
    assert first.getheader("ETag") == second.getheader("ETag") == etag


def test_the_features_etag_changes_when_a_row_changes(running):
    port, root = running
    etag, _, _ = settled(port, "/api/features")
    (root / "alpha-feat" / "untracked.txt").write_text("a file the tree did not have\n")
    resp, body = request(port, "GET", "/api/features", headers={"If-None-Match": etag})
    assert resp.status == 200 and body
    assert ETAG_FORM.fullmatch(resp.getheader("ETag") or "") and resp.getheader("ETag") != etag


def test_if_none_match_star_a_weak_tag_and_a_list_are_honoured_and_a_stale_tag_gets_200(running):
    port, _ = running
    etag, _, _ = settled(port, "/api/features")
    for header in ("*", f"W/{etag}", f'"stale", {etag}'):
        resp, data = request(port, "GET", "/api/features", headers={"If-None-Match": header})
        assert (resp.status, data) == (304, b""), header
    resp, data = request(port, "GET", "/api/features", headers={"If-None-Match": '"0123456789abcdef0123456789abcdef"'})
    assert resp.status == 200 and data


def test_a_304_of_features_still_refreshes_the_last_snapshot(acting):
    port = acting.port
    row = features(port)["open"][0]
    acting.httpd.last_snapshot = None
    resp, _ = request(port, "GET", "/api/features", headers={"If-None-Match": "*"})
    assert resp.status == 304
    assert request(port, "GET", f"/api/features/{row['id']}")[0].status == 200


def test_a_failed_scan_is_503_even_with_if_none_match_star(tmp_path):
    empty = tmp_path / "no-wf"
    empty.mkdir()
    httpd = server.build_server(0, (tmp_path,), empty, APP / "static")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp, _ = request(httpd.server_address[1], "GET", "/api/features", headers={"If-None-Match": "*"})
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
    assert resp.status == 503 and resp.getheader("ETag") is None


def test_workflow_has_an_etag_and_a_304(mapped):
    etag, resp, data = settled(mapped.port, "/api/workflow")
    assert ETAG_FORM.fullmatch(etag or "") and resp.status == 304 and data == b""
    assert resp.getheader("ETag") == etag


def test_workflow_etag_changes_when_the_install_changes(mapped):
    etag, _, _ = settled(mapped.port, "/api/workflow")
    (mapped.workflow_dir / "LESSONS.md").unlink()
    resp, body = request(mapped.port, "GET", "/api/workflow", headers={"If-None-Match": etag})
    assert resp.status == 200 and body
    assert ETAG_FORM.fullmatch(resp.getheader("ETag") or "") and resp.getheader("ETag") != etag


def test_workflow_feature_unknown_is_404_with_no_etag(mapped):
    request(mapped.port, "GET", "/api/features")
    resp, _ = request(mapped.port, "GET", "/api/workflow?feature=0123456789ab",
                      headers={"If-None-Match": "*"})
    assert resp.status == 404 and resp.getheader("ETag") is None


def test_documents_has_an_etag_per_repo_and_a_304(documented):
    port = documented.port
    features(port)
    etag, resp, data = settled(port, "/api/documents?repo=acme/alpha")
    assert ETAG_FORM.fullmatch(etag or "") and resp.status == 304 and data == b""
    other = request(port, "GET", "/api/documents?repo=nobody/elsewhere")
    assert other[0].status == 200 and other[0].getheader("ETag") != etag
    again = request(port, "GET", "/api/documents?repo=nobody/elsewhere", headers={"If-None-Match": etag})
    assert again[0].status == 200


def test_the_documents_tag_of_two_repos_with_the_same_list_differs(acting):
    """Witness two repos known only from a handoff (no primary, so both list no document): the same
    list under two repos must not share a tag, or a 304 of one repo would answer for the other."""
    (acting.workflow_dir / "handoffs" / "handoff-third-slug-20261116.md").write_text(
        "# Handoff third-slug\n\n- **Repo**: nobody/nowhere\n\n## Done\n\n- x\n\n"
        "## Next session\n\n- nothing\n\n## Ground truth\n\n- ok\n")
    features(acting.port)
    first, first_body = documents_of(acting.port, "nobody/elsewhere")
    second, second_body = documents_of(acting.port, "nobody/nowhere")
    assert first.status == second.status == 200
    assert first_body["documents"] == second_body["documents"] == []
    assert first.getheader("ETag") != second.getheader("ETag")


def test_documents_304_leaves_the_catalog_as_a_200_does(documented):
    port = documented.port
    features(port)
    etag, _, _ = settled(port, "/api/documents?repo=acme/alpha")
    ids = [d["id"] for d in documents_of(port)[1]["documents"]]
    assert ids
    documented.httpd.documents.clear()
    resp, data = request(port, "GET", "/api/documents?repo=acme/alpha", headers={"If-None-Match": etag})
    assert (resp.status, data) == (304, b"")
    assert get_document(port, ids[0])[0].status == 200


def test_documents_unknown_repo_is_404_with_no_etag(documented):
    features(documented.port)
    resp, _ = request(documented.port, "GET", "/api/documents?repo=nobody/nothing",
                      headers={"If-None-Match": "*"})
    assert resp.status == 404 and resp.getheader("ETag") is None


def test_content_etag_ignores_the_ignored_key_and_the_key_order_and_changes_with_content():
    base = {"generated_at": "t1", "rows": [1, 2], "n": 1}
    tag = server.content_etag(base, ignore=("generated_at",))
    assert ETAG_FORM.fullmatch(tag)
    assert server.content_etag({"generated_at": "t2", "n": 1, "rows": [1, 2]}, ignore=("generated_at",)) == tag
    assert server.content_etag({**base, "n": 2}, ignore=("generated_at",)) != tag
    assert server.content_etag({**base, "rows": [2, 1]}, ignore=("generated_at",)) != tag
    assert server.content_etag(base) != tag


def test_served_app_js_sends_the_tag_and_keeps_the_view(running):
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    for used in ("WfPanel.conditionalHeaders(", "WfPanel.answerKind(", "WfPanel.planRows(", "WfPanel.drawFigure("):
        assert used in script, used
    assert "replaceChildren" not in function_body(script, "syncTable")


def test_served_app_js_polls_fast_through_pollfast(running):
    """witness the served script - distinguishes `schedule` deciding the fast poll through
    `WfPanel.pollFast` (refreshing rows included) from its old inline computing-only test."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    body = function_body(script, "schedule")
    assert "WfPanel.pollFast(" in body and '"computing"' not in body


# --- #100 (gate-sections): phase.sections on the wire, a section served by its id -------------------

def sections_on_the_wire(port: int, row_id: str) -> dict[int, list[dict]]:
    resp, body = workflow_of(port, row_id)
    assert resp.status == 200
    return {p["n"]: p.get("sections") or [] for p in body["phases"]}


def test_workflow_of_a_feature_carries_the_sections_of_each_phase_and_phase_5_none(mapped):
    """witness `feat` (spec with `## Phase plan` and `## Product review` only): phase 0 and 2 list
    their heading present, phase 4 lists `Tickets` missing, phase 5 none - distinguishes the per-phase
    contract from one flat list, and a missing heading (listed) from an absent key."""
    sections = sections_on_the_wire(mapped.port, known(mapped.port)["feat"]["id"])
    shown = {n: [(s["heading"], s["state"]) for s in found] for n, found in sections.items()}
    assert shown[0] == [("Phase plan", "present")]
    assert shown[2] == [("Product review", "present")]
    assert shown[4] == [("Tickets", "missing")]
    assert shown[5] == []
    assert shown[6] == [("Delivery", "missing")]
    assert sections[0], "phase 0 lists no section"
    present = sections[0][0]
    assert present["source"] == "docs/specs/feat.md"
    assert present["id"] and len(present["rev"]) == 12 and present["note"] is None


def test_a_section_is_served_by_its_id_as_markdown(mapped):
    sections = sections_on_the_wire(mapped.port, known(mapped.port)["feat"]["id"])
    assert sections[2], "phase 2 lists no section"
    review = sections[2][0]
    resp, body = get_document(mapped.port, review["id"])
    assert resp.status == 200 and resp.getheader("Content-Type").startswith("text/markdown")
    assert body.decode() == "\nMockup: `docs/nothing-here.html`"


def test_a_model_section_is_served_by_its_id_with_the_body_the_model_parser_cuts(mapped):
    """witness a model whose heading is `## flow` (lower case) at phase 6: the id listed for `Flow`
    answers 200 with exactly that body - distinguishes the model parser (lower-cased headings) from
    the exact-case spec parser, which finds no `Flow` there and would answer 503."""
    model = mapped.root / "alpha-feat" / "docs" / "models" / "feat.md"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text("## Elements\ne\n\n## flow\nstart\n### Happy path\n| b |\n\n## Model changes\nz\n")
    sections = sections_on_the_wire(mapped.port, known(mapped.port)["feat"]["id"])
    flow = next((s for s in sections[6] if s["heading"] == "Flow"), None)
    assert flow is not None and flow["state"] == "present", sections[6]
    resp, body = get_document(mapped.port, flow["id"])
    assert resp.status == 200
    assert body.decode() == "start\n### Happy path\n| b |\n"


def test_a_section_whose_heading_left_the_file_is_503_with_the_reason(mapped):
    sections = sections_on_the_wire(mapped.port, known(mapped.port)["feat"]["id"])
    assert sections[2], "phase 2 lists no section"
    review = sections[2][0]
    spec = mapped.root / "alpha-feat" / "docs" / "specs" / "feat.md"
    spec.write_text(spec.read_text().replace("## Product review", "## Review"))
    resp, body = get_document(mapped.port, review["id"])
    assert resp.status == 503 and "Product review" in json.loads(body)["error"]


def test_the_workflow_etag_changes_when_a_section_body_changes(mapped):
    """witness the same section edited in place (same size, other bytes) - distinguishes a tag that
    follows `rev` from one that follows the set of documents (#105)."""
    row_id = known(mapped.port)["feat"]["id"]
    spec = mapped.root / "alpha-feat" / "docs" / "specs" / "feat.md"
    before = request(mapped.port, "GET", f"/api/workflow?feature={row_id}")[0].getheader("ETag")
    assert before
    spec.write_text(spec.read_text().replace("nothing-here", "nothing-were"))
    after = request(mapped.port, "GET", f"/api/workflow?feature={row_id}")[0].getheader("ETag")
    assert after and after != before


def test_workflow_without_a_feature_has_no_sections_key(mapped):
    resp, body = workflow_of(mapped.port)
    assert resp.status == 200
    assert all("sections" not in p for p in body["phases"])


def test_sections_of_an_unreadable_spec_do_not_fail_the_map(mapped):
    """witness `feat` with its spec replaced by a directory: 200, and every section of the spec is
    `unreadable` with the error, none is dropped into a 503."""
    resp, body = workflow_of(mapped.port, unreadable_spec_row(mapped))
    assert resp.status == 200
    tickets = next(p for p in body["phases"] if p["n"] == 4).get("sections")
    assert [(s["heading"], s["state"], bool(s["note"]), s["id"]) for s in tickets or []] == [
        ("Tickets", "unreadable", True, None)]


def test_with_two_copies_of_a_spec_the_sections_come_from_the_one_in_the_rows_own_tree(mapped):
    """witness `feat` with a spec in the primary checkout and another in the row's worktree (the
    plan row of phase 0 says `worktree` only in the latter): the primary's spec is listed first, yet
    the phase 0 section is the worktree's - distinguishes the spec of the row's own tree (the one the
    gates read) from the first spec listed."""
    row_id = known(mapped.port)["feat"]["id"]
    phase0 = next(p for p in workflow_of(mapped.port, row_id)[1]["phases"] if p["n"] == 0)
    first, *_ = phase0["artefacts"]
    assert first["name"].endswith("alpha/docs/specs/feat.md"), "the premise: the primary's copy comes first"
    plan = sections_on_the_wire(mapped.port, row_id)[0][0]
    assert plan["source"] == "docs/specs/feat.md"
    _, body = get_document(mapped.port, plan["id"])
    assert "| 0 · Start | run | worktree |" in body.decode()


def test_a_section_is_cut_with_the_parsers_of_the_server_that_listed_it(mapped):
    """witness a workflow dir whose `wf.py` cuts `Product review` as `STUB` (a copy with one more
    function), listed by the server and then asked for by its id - distinguishes a body cut with the
    parsers the server lists with from one cut with the install that holds catalog.py."""
    wf_copy = mapped.workflow_dir / "wf.py"
    real = wf_copy.resolve().read_text()
    wf_copy.unlink()
    wf_copy.write_text(real + '''

_real_parse_sections = parse_sections


def parse_sections(text):
    found = _real_parse_sections(text)
    if "Product review" in found:
        found["Product review"] = "STUB"
    return found
''')
    sections = sections_on_the_wire(mapped.port, known(mapped.port)["feat"]["id"])
    assert sections[2], "phase 2 lists no section"
    _, body = get_document(mapped.port, sections[2][0]["id"])
    assert body.decode() == "STUB"


def test_the_parsers_of_the_sections_are_loaded_once_per_server(mapped, monkeypatch):
    """witness three workflow requests and three section bodies - distinguishes parsers loaded once
    per server from `wf.py` and `check_model.py` executed again on every request."""
    row_id = known(mapped.port)["feat"]["id"]
    loads = []
    real = server.catalog.load_check_model
    monkeypatch.setattr(server.catalog, "load_check_model", lambda wdir: loads.append(wdir) or real(wdir))
    for _ in range(3):
        sections = sections_on_the_wire(mapped.port, row_id)
        assert get_document(mapped.port, sections[2][0]["id"])[0].status == 200
    assert len(loads) <= 1, loads


# --- #127 (design-diagrams): diagrams and lengths on the workflow answer -------------------------------

DIAGRAM_FEATURE_SPEC = OVERLAY_SPEC + (
    "\n## Model\n\n### Flow\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n"
    "```python\n## Not a heading\n```\n\n"
    "### Sequence\n\n```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n\n"
    "## System Architecture\n\n```mermaid\nflowchart TD\n  S --> T\n```\n")


def phases_of(port: int, row_id: str) -> dict[int, dict]:
    resp, body = workflow_of(port, row_id)
    assert resp.status == 200
    return {p["n"]: p for p in body["phases"]}


def write_diagram_spec(mapped, text: str = DIAGRAM_FEATURE_SPEC) -> str:
    (mapped.root / "alpha-feat" / "docs" / "specs" / "feat.md").write_text(text)
    return known(mapped.port)["feat"]["id"]


def test_workflow_phases_carry_diagrams_and_lines(mapped):
    """witness `feat` with a `## Model` of two blocks under two headings (and a python fence holding a
    `## ` line) plus a block under `## System Architecture` - distinguishes the per-phase contract (phase 1:
    the Model blocks; phase 3: also the architecture's; `doc` a listed id that opens; `lines` on each
    section and artefact) from a flat or missing key."""
    row_id = write_diagram_spec(mapped)
    phases = phases_of(mapped.port, row_id)
    assert "diagrams" in phases[1] and "diagrams" in phases[3], "the answer carries no `diagrams`"
    one, three = phases[1]["diagrams"], phases[3]["diagrams"]
    assert [d["title"] for d in one] == ["Model › Flow", "Model › Sequence"]
    assert [d["title"] for d in three] == ["Model › Flow", "Model › Sequence", "System Architecture"]
    assert set(one[0]) == {"title", "source", "doc", "generated", "mermaid"}
    assert one[0]["source"] == "docs/specs/feat.md § Model" and one[0]["generated"] is None
    assert one[0]["mermaid"] == "flowchart LR\n  A --> B"
    resp, body = get_document(mapped.port, one[0]["doc"])
    assert resp.status == 200 and b"## Model" in body
    assert one[0]["doc"] in [a["id"] for a in phases[1]["artefacts"]]
    spec_lines = len(DIAGRAM_FEATURE_SPEC.splitlines())
    assert spec_lines in [a["lines"] for a in phases[0]["artefacts"]]   # the primary's copy has its own length
    model = next(s for s in phases[3]["sections"] if s["heading"] == "Model")
    assert isinstance(model["lines"], int) and model["lines"] > 8
    missing = next(s for s in phases[4]["sections"] if s["heading"] == "Tickets")
    assert missing["state"] == "missing" and missing["lines"] is None


def test_other_phases_carry_no_diagrams_or_invariants(mapped):
    """witness `feat` with Mermaid blocks in its spec - distinguishes `diagrams: []` for phases 0, 2, 4
    (and 5, which has no design file here) from a diagram on every phase; and a map asked without a feature
    keeps no `diagrams` key. (`invariants` belongs to #129: nothing is asserted about it here.)"""
    row_id = write_diagram_spec(mapped)
    phases = phases_of(mapped.port, row_id)
    assert [phases[n].get("diagrams") for n in (0, 2, 4, 5)] == [[], [], [], []]
    assert phases[6]["diagrams"] == []   # no model file: nothing to draw
    assert all("diagrams" not in p and "invariants" not in p for p in workflow_of(mapped.port)[1]["phases"])
    assert [phases[n]["invariants"] for n in range(7)] == [None] * 7   # a spec with no `### System invariants`
    with_table = write_diagram_spec(mapped, DIAGRAM_FEATURE_SPEC + INVARIANT_SPEC_TAIL)
    shown = phases_of(mapped.port, with_table)
    assert [n for n in range(7) if shown[n]["invariants"] is not None] == [3, 6]
    assert [shown[n]["diagrams"] == [] for n in (0, 2, 4, 5)] == [True] * 4


def test_workflow_phase_five_carries_the_generated_diagrams(mapped):
    """witness `feat` with a design that holds a json design block and one hand-written block - distinguishes
    phase 5 carrying the two generated diagrams first (`generated` set, no `doc`, a Mermaid source naming the
    design's nodes) and the hand-written one after (its `doc` a listed id that opens), from an answer whose
    phase 5 is still `[]`."""
    design = {"data_flow": {"nodes": [{"id": "m.f", "kind": "add"}], "edges": [{"from": "m.f", "to": "m.g"}]},
              "call_stack": {"name": "root", "children": [{"name": "m.f", "children": []}]}}
    (mapped.root / "alpha-feat" / "docs" / "design").mkdir(parents=True, exist_ok=True)
    (mapped.root / "alpha-feat" / "docs" / "design" / "feat.md").write_text(
        "## Order\n\n```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n\n```json design\n"
        + json.dumps(design) + "\n```\n")
    row_id = write_diagram_spec(mapped)
    found = phases_of(mapped.port, row_id)[5]["diagrams"]
    assert [(d["title"], d["generated"], d["doc"] is None) for d in found] == [
        ("Data flow (generated)", "data_flow", True), ("Call stack (generated)", "call_stack", True),
        ("Order", None, False)]
    assert 'n0["m.f"]' in found[0]["mermaid"] and 'n1["m.g"]' in found[0]["mermaid"]
    assert found[0]["source"] == "docs/design/feat.md § json design"
    resp, body = get_document(mapped.port, found[2]["doc"])
    assert resp.status == 200 and b"sequenceDiagram" in body


def test_workflow_with_diagrams_writes_nothing_and_versions_new_matrices(inv):
    """witness the answer asked twice, a diagram edited in place (same size, other bytes) and then a matrix
    landing in the store - distinguishes a read-only answer (tree and store bytes unchanged) whose ETag follows
    the diagram sources and the matrices (200 with a new tag, then 304 on the same tag) from one that ignores
    them or writes."""
    row_id = write_diagram_spec(inv, DIAGRAM_FEATURE_SPEC + INVARIANT_SPEC_TAIL)
    spec = inv.root / "alpha-feat" / "docs" / "specs" / "feat.md"
    tree = [(p, p.stat().st_mtime_ns) for p in sorted(inv.root.rglob("*")) if p.is_file()]
    stored = (inv.store.read_bytes(), inv.store.stat().st_mtime_ns)
    resp, _ = request(inv.port, "GET", f"/api/workflow?feature={row_id}")
    first = resp.getheader("ETag")
    assert first
    assert request(inv.port, "GET", f"/api/workflow?feature={row_id}",
                   headers={"If-None-Match": first})[0].status == 304
    assert [(p, p.stat().st_mtime_ns) for p in sorted(inv.root.rglob("*")) if p.is_file()] == tree
    assert (inv.store.read_bytes(), inv.store.stat().st_mtime_ns) == stored
    spec.write_text(spec.read_text().replace("A --> B", "A --> C"))
    resp, body = request(inv.port, "GET", f"/api/workflow?feature={row_id}",
                         headers={"If-None-Match": first})
    assert resp.status == 200 and resp.getheader("ETag") != first
    assert "A --> C" in json.loads(body)["phases"][1]["diagrams"][0]["mermaid"]
    second = resp.getheader("ETag")
    put_mutants(inv.store, "m-new", [("tests/test_a.py::test_one", "pinned")])
    resp, body = request(inv.port, "GET", f"/api/workflow?feature={row_id}", headers={"If-None-Match": second})
    assert resp.status == 200 and resp.getheader("ETag") != second, "a matrix that landed did not change the version"
    assert json.loads(body)["phases"][3]["invariants"]["rows"][0]["status"] == "pinned"


# --- ticket 129 (design-diagrams): the invariants of phases 3 and 6 with a live status ----------------

INVARIANT_SPEC_TAIL = """
### System invariants

| Invariant (law) | Negation → test | Verified in |
|---|---|---|
| The law with a pinned test | a negation | `tests/test_a.py::test_one` (to write) |
| The law of an absent test | a negation | `tests/test_a.py::test_gone` |
| The law of a script | a negation | `scripts/check_x.py` |
| The law of an unmutated test | a negation | `tests/test_a.py::test_two` |
"""


def put_mutants(store: Path, name: str, rows: list[tuple[str, str]], file: str = "app/x.py") -> None:
    """A stored matrix whose mutants each mutate `file` and target the given test, with the outcome given."""
    conn = report_store.open_store(store)
    report_store.add_matrix(conn, name, [
        {"id": f"M{i}", "kind": "mutant", "outcome": outcome, "test": test,
         "diff": f"--- a/{file}\n+++ b/{file}\n@@\n-a\n+b\n"} for i, (test, outcome) in enumerate(rows)],
        datetime.now())
    conn.close()


@pytest.fixture
def inv(tmp_path: Path, workflow_dir: Path, presence_dir: Path):
    """`mapped` with a store of its own (`ns.store`, already created) and, in the row's worktree, a tests file
    with `test_one` and `test_two` and the file `app/x.py` the stored mutants mutate."""
    root, _ = populated_roots(tmp_path)
    tree = root / "alpha-feat"
    (tree / "docs" / "specs" / "feat.md").write_text(OVERLAY_SPEC + INVARIANT_SPEC_TAIL)
    (tree / "tests").mkdir()
    (tree / "tests" / "test_a.py").write_text("def test_one(): pass\ndef test_two(): pass\n")
    (tree / "app").mkdir()
    (tree / "app" / "x.py").write_text("x = 1\n")
    store = tmp_path / "state" / "reports.sqlite"
    report_store.open_store(store).close()
    recorder = Recorder()
    httpd, thread = serve_over(root, map_install(workflow_dir), presence_dir, recorder,
                               skills_dir=map_skills(tmp_path), store=store)
    yield SimpleNamespace(port=httpd.server_address[1], root=root, httpd=httpd, store=store, tree=tree)
    stop(httpd, thread)


def invariant_rows(inv, n: int = 3) -> list[dict]:
    return phases_of(inv.port, known(inv.port)["feat"]["id"])[n]["invariants"]["rows"]


def test_workflow_invariants_follow_the_store(inv):
    """witness a spec with four laws (a pinned-to-be test, an absent test, a script, a test nobody mutates),
    asked before any matrix, after a pinned one, after a newer blind one, and with the store replaced by a file
    that is not a database - distinguishes statuses read from the live store at each request (never-mutated,
    test-missing, not-checkable first; then pinned; then the newest matrix's blind) with the contract's keys,
    the same table in phase 6, and a store that cannot be read answering 503 - from a table fixed at the first read."""
    row_id = known(inv.port)["feat"]["id"]
    answer = phases_of(inv.port, row_id)
    table = answer[3]["invariants"]
    assert set(table) == {"source", "readable", "tree", "why", "rows"} and table["readable"] is True
    assert table["tree"] == str(inv.tree) and table["why"] is None
    assert table["source"] == "docs/specs/feat.md"
    assert set(table["rows"][0]) == {"n", "law", "negation", "verified", "tests", "status", "evidence"}
    assert [r["status"] for r in table["rows"]] == ["never-mutated", "test-missing", "not-checkable", "never-mutated"]
    assert table["rows"][0]["tests"] == ["tests/test_a.py::test_one"] and table["rows"][2]["tests"] == []
    assert table["rows"][0]["evidence"] == [{"test": "tests/test_a.py::test_one", "matrix": None, "mutant": None,
                                             "outcome": None, "at": None}]
    assert answer[6]["invariants"] == table
    put_mutants(inv.store, "m-old", [("tests/test_a.py::test_one", "pinned")])
    rows = invariant_rows(inv)
    assert [r["status"] for r in rows][:2] == ["pinned", "test-missing"]
    assert (rows[0]["evidence"][0]["matrix"], rows[0]["evidence"][0]["outcome"]) == ("m-old", "pinned")
    put_mutants(inv.store, "m-foreign", [("tests/test_a.py::test_two", "pinned")], file="not/in/this/tree.py")
    assert invariant_rows(inv)[3]["status"] == "never-mutated"
    put_mutants(inv.store, "m-new", [("tests/test_a.py::test_one[x]", "blind-test")])
    seen = inv.store.stat()   # the stamp is (mtime_ns, size): move it on explicitly, whatever the clock grain
    os.utime(inv.store, ns=(seen.st_atime_ns, seen.st_mtime_ns + 1_000_000_000))
    rows = invariant_rows(inv)
    assert rows[0]["status"] == "blind" and rows[0]["evidence"][0]["matrix"] == "m-new"
    inv.store.write_bytes(b"this is not an sqlite database" * 20)
    assert workflow_of(inv.port, row_id)[0].status == 503


def test_an_unreadable_invariants_table_is_served_raw(inv):
    """witness a spec whose invariants table is `| Module | Owns | May not |` - distinguishes `readable: false`
    with `raw` and `why` and no `rows` key on the wire from a dropped table."""
    (inv.tree / "docs" / "specs" / "feat.md").write_text(
        OVERLAY_SPEC + "\n### System invariants\n\n| Module | Owns | May not |\n|---|---|---|\n| a | b | c |\n")
    table = phases_of(inv.port, known(inv.port)["feat"]["id"])[3]["invariants"]
    assert set(table) == {"source", "readable", "raw", "why"} and table["readable"] is False
    assert table["raw"].splitlines()[0] == "| Module | Owns | May not |" and "negation" in table["why"]


def test_mutant_rows_are_read_once_per_store_stamp(inv, monkeypatch):
    """witness four workflow requests (each of which asks for phases 3 and 6), a matrix landing, and two more
    requests - distinguishes one read of the store per store change (1 call for the four, 2 after the matrix,
    still 2 after the next two) from a read per poll or per phase."""
    row_id = known(inv.port)["feat"]["id"]
    reads = []
    real = server.catalog.mutant_rows
    monkeypatch.setattr(server.catalog, "mutant_rows", lambda store: reads.append(store) or real(store))
    for _ in range(4):
        assert workflow_of(inv.port, row_id)[0].status == 200
    assert len(reads) == 1, reads
    put_mutants(inv.store, "m-new", [("tests/test_a.py::test_one", "pinned")])
    for _ in range(2):
        assert workflow_of(inv.port, row_id)[0].status == 200
    assert len(reads) == 2, reads
    assert reads == [inv.store, inv.store]


def test_served_app_js_keeps_folds_out_of_the_art_key(running):
    """witness the served scripts - distinguishes folds that toggle through `WfPanel.foldToggled` on
    `panel.openFolds` and are painted with `<details>` (no redraw of the stage) from a stage key that
    holds the open flag."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    panel = request(port, "GET", "/static/panel.js")[1].decode()
    assert "WfPanel.foldToggled(" in script and "openFolds" in script
    assert 'el("details"' in function_body(script, "foldsBlock")
    start = panel.index("function stageKeys(")
    keys = panel[start:panel.index("\n  }\n", start)]
    assert "s.folds" in keys, "stageKeys does not key the folds"
    assert not [line for line in keys.splitlines() if "folds" in line and "open" in line]


def motion_gates(script: str) -> None:
    """The assertions of the motion witness, on the text of app.js (so they can be run on any version of it)."""
    assert "WfPanel.motionOf(" in script and "WfPanel.MOTION_MS" in script
    rows = function_body(script, "runRowUpdates")
    assert rows.index('motion().rows !== "transition"') < rows.index("document.startViewTransition(")
    assert "plan.remove.length" in rows and "plan.draw.length" in rows and "plan.move.length" in rows
    assert 'motion().draw !== "in"' in function_body(script, "drawIn")
    zoom = function_body(script, "zoomFrom")
    assert "reducedMotion()" in zoom and "motion()" not in zoom, "the zoom has no motion check of its own"
    paint = function_body(script, "paintArtefacts")
    assert paint.index('motion().stage === "fade"') < paint.index("snapshotOf(f.art)") < paint.index("f.art.replaceChildren(...parts)") \
        < paint.index("crossFadeStage(f.art, leaving)"), "the stage is not copied, then replaced at once, then faded"
    assert ".animate(" not in paint and "startViewTransition" not in paint
    fade = function_body(script, "crossFadeStage")
    assert fade.count(".animate(") == 2 and "leaving.remove()" in fade, "the old stage does not fade out over the new one"
    assert "attachShadow" in function_body(script, "snapshotOf"), "the copy of the old stage is reachable by the page's selectors"
    assert script.count("startViewTransition(") == 1 and script.count(".animate(") == 3


def test_served_app_js_runs_each_motion_only_where_motion_of_allows_it(running):
    """witness the served script (ticket 130) - distinguishes a page whose view transition (rows), draw-in and zoom are each gated (`motionOf`, a missing API or reduced motion start nothing, an
    empty plan starts no transition, the zoom has a check of its own) and whose stage is a cross-fade through a
    copy of the old stage (the DOM stays synchronous) from one that calls `startViewTransition` or `.animate(` unconditionally or fades only the new
    stage in."""
    port, _ = running
    motion_gates(request(port, "GET", "/static/app.js")[1].decode())


def test_served_app_js_names_and_clears_the_cap_on_waiting_for_the_rows(running):
    """witness the served script (ticket 130) - distinguishes a refresh that waits for the rows' update through
    the named `UPDATE_WAIT_MS` and clears its timer when the update wins, and a page whose idle timer draws nothing
    once the data are there (a 304 draws nothing, #105), from a literal 500 whose timer lingers or a timer that
    redraws the Documents tab 5 s after the load."""
    port, _ = running
    script = request(port, "GET", "/static/app.js")[1].decode()
    wait = function_body(script, "untilDrawn")
    assert "UPDATE_WAIT_MS" in wait and "clearTimeout(timer)" in wait and "Promise.race(" in wait
    assert re.search(r"const UPDATE_WAIT_MS = 500;", script)
    assert "await untilDrawn(render())" in function_body(script, "refresh") and "setTimeout(done, 500)" not in script
    assert "setTimeout(() => { if (state.data === null) render(); }, SLOW_MS)" in script


def test_a_row_without_a_worktree_takes_the_repo_of_its_spec(inv, monkeypatch):
    """witness `feat` after its worktree was removed (where.path null) with the invariants spec, the tests file
    and the mutated file in the primary checkout, and a pinned matrix - distinguishes `invariants.tree` naming the
    primary and the statuses read from its files (pinned, never-mutated, test-missing) from a table joined against
    no tree, where every test would be test-missing."""
    primary = inv.root / "alpha"
    subprocess.run(["git", "-C", str(primary), "worktree", "remove", "--force", str(inv.tree)], check=True)
    (primary / "tests").mkdir()
    (primary / "tests" / "test_a.py").write_text("def test_one(): pass\ndef test_two(): pass\n")
    (primary / "app").mkdir()
    (primary / "app" / "x.py").write_text("x = 1\n")
    (primary / "docs" / "specs" / "feat.md").write_text(OVERLAY_SPEC + INVARIANT_SPEC_TAIL)
    put_mutants(inv.store, "m-one", [("tests/test_a.py::test_one", "pinned")])
    monkeypatch.setattr(server.traces, "located", lambda *a, **k: server.traces.Where(kind="none", path=None, branch=None, dirty=None, ahead=None, base=None))
    row = known(inv.port)["feat"]
    assert row["where"]["path"] is None, "the premise: the row has no worktree"
    table = phases_of(inv.port, row["id"])[3]["invariants"]
    assert table["tree"] == str(primary.resolve()) and table["why"] is None
    assert [r["status"] for r in table["rows"]] == ["pinned", "test-missing", "not-checkable", "never-mutated"]


def test_a_phase_without_a_spec_does_not_read_the_store(inv, monkeypatch):
    """witness the claim row `other-slug`, which has no spec, asked twice - distinguishes an answer that never reads
    the matrices when no spec can carry invariants (`invariants` null in phases 3 and 6, no `mutant_rows` call, so a
    store it does not need cannot turn it into a 503) from one that reads them for every phase 3 and 6."""
    row_id = known(inv.port)["other-slug"]["id"]
    reads = []
    real = server.catalog.mutant_rows
    monkeypatch.setattr(server.catalog, "mutant_rows", lambda store: reads.append(store) or real(store))
    for _ in range(2):
        answer, body = workflow_of(inv.port, row_id)
        assert answer.status == 200, body
        assert all(p["invariants"] is None for p in body["phases"] if p["n"] in (3, 6))
    assert reads == []
