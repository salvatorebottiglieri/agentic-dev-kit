"""Loopback HTTP server of the workflow window.

Serves the page, two static files, the two vendored renderers, `GET /api/features`,
`GET /api/features/{id}`, `GET /api/documents` (+ `/{id}`, only ids a listing returned) and
`POST /api/open` (opens a terminal, the only action; it writes nothing); every other
request is 404, every other method 405, every Host that is not loopback or the --remote-host 403,
every response `no-store` but `/vendor/*` (`no-cache` + ETag). Threaded (#65): a request never waits for another; the phases are derived by background jobs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

if "traces" not in sys.modules:  # run as a script or loaded by path: siblings are importable
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog
import traces

# Mermaid colours its diagrams with <style> elements (Decision 19); scripts stay 'self' only.
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; connect-src 'self'; form-action 'none'"
MAX_DRAINED_BODY = 1 << 20
MAX_OPEN_BODY = 4096
TOKEN_MARKER = '<meta name="wf-token" content="">'
OPEN_FIELDS = {"action", "id", "token"}

# The only files under /static/, by name (no path is ever built from a request).
STATIC = {
    "/static/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/static/panel.js": ("panel.js", "text/javascript; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
}


def host_allowed(host: str | None, port: int, remote_host: str | None = None) -> bool:
    """Exactly `127.0.0.1:<port>`, `localhost:<port>` or, when given, `remote_host` (DNS rebinding guard)."""
    allowed = (f"127.0.0.1:{port}", f"localhost:{port}")
    return host in allowed or (remote_host is not None and host == remote_host)


def launch(argv: list[str]) -> None:
    """Start `argv` detached (no shell, no wait); raises OSError when it cannot be started."""
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def etag_matches(header: str | None, etag: str) -> bool:
    """`If-None-Match` against `etag`: `*`, or one listed tag (a `W/` prefix ignored) equal to it."""
    if header is None:
        return False
    entries = [entry.strip().removeprefix("W/") for entry in header.split(",")]
    return "*" in entries or etag in entries


def content_etag(payload: dict, ignore: tuple[str, ...] = ()) -> str:
    """The version of an answer (#105): a strong tag from the payload without the top-level keys in
    `ignore`; the order of the producer's keys never changes it."""
    kept = {key: value for key, value in payload.items() if key not in ignore}
    digest = hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest()
    return f'"{digest[:32]}"'


class ActionRefused(Exception):
    """`status` 404: the id is not known; 422: the action does not fit what the id addresses."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def action_argv(action: str, target_id: str, snap: traces.Snapshot | None, distro: str) -> list[str]:
    """The command of an action, from what the LAST snapshot holds (Decision 16, 26): `terminal`
    on a row id (of open, history or the other worktrees); an id that is not a row is 404, any other action (or a row with no tree) 422.
    The path is always the snapshot's own."""
    rows = (*snap.open, *snap.history, *snap.other) if snap else ()
    row = next((r for r in rows if r.id == target_id), None)
    if row is None:
        raise ActionRefused(404)
    if action == "terminal" and row.where.path is not None:
        return ["wt.exe", "-w", "new", "wsl.exe", *(["-d", distro] if distro else []),
                "--cd", row.where.path]
    raise ActionRefused(422)


class DeskServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer carrying what the handler reads.

    `last_snapshot` and `documents` are shared by the request threads with no lock: they rely on
    one operation each being atomic (one attribute assignment, one `dict.update`)."""

    daemon_threads = True   # a request thread never keeps the process alive

    def __init__(self, port: int, roots: tuple[Path, ...], workflow_dir: Path,
                 static_dir: Path, presence_dir: Path | None = None,
                 remote_host: str | None = None, jira_url: str | None = None,
                 distro: str = "",
                 launcher: Callable[[list[str]], None] = launch,
                 vendor_dir: Path | None = None,
                 skills_dir: Path | None = None,
                 store: Path | None = None,
                 cache_file: Path | None = None) -> None:
        # Nothing is written under a repo or the workflow dir (only the phase cache file, in the
        # state dir): no `__pycache__` next to a module imported by path. Request threads and
        # phase jobs import concurrently, so a per-import save/restore of this process-wide flag
        # would interleave; it stays set for the server's life instead.
        sys.dont_write_bytecode = True
        self.store = store   # the report store, read only; None: no store
        self.remote_host = remote_host
        self.jira_url = jira_url
        self.distro = distro
        self.launcher = launcher
        self.token = secrets.token_urlsafe(32)   # memory only: new on every start (Decision 18)
        self.presence_dir = presence_dir
        self.cache_file = cache_file   # the phase cache file (#66); None: memory only
        # One per server (Decision 13); with a file, it starts from what the file holds.
        self.phase_cache: traces.PhaseCache = (
            traces.load_phase_cache(cache_file) if cache_file is not None else {})
        self._save_lock = threading.Lock()
        self._saved_generation = 0   # the jobs' generation at the last save attempt
        self.last_snapshot: traces.Snapshot | None = None   # what /api/features/{id} answers from
        self.roots = roots
        self.workflow_dir = workflow_dir
        self.static_dir = static_dir
        self.vendor_dir = vendor_dir or static_dir.parent / "vendor"
        self.skills_dir = skills_dir   # None: no skills store (desk.py resolves the default)
        self.documents: dict[str, catalog.Document] = {}   # every id a listing returned (Decision 20)
        self._parsers: tuple[ModuleType, ModuleType] | None = None   # see `parsers`
        self._mutants: tuple[tuple[int, int] | None, tuple[catalog.MutantRow, ...]] | None = None   # see `mutant_rows`
        self._mutants_lock = threading.Lock()
        self.phase_jobs = traces.PhaseJobs(workers=min(8, os.cpu_count() or 1))
        super().__init__(("127.0.0.1", port), DeskHandler)

    def parsers(self) -> tuple[ModuleType, ModuleType]:
        """`(wf, check_model)` of the workflow dir, loaded on first use and kept for the server's
        life: the sections are listed and cut with the same two modules. A load that raises is not kept."""
        if self._parsers is None:
            self._parsers = (traces.load_wf(self.workflow_dir), catalog.load_check_model(self.workflow_dir))
        return self._parsers

    def mutant_rows(self) -> tuple[catalog.MutantRow, ...]:
        """The store's counting mutant rows (`catalog.mutant_rows`), read again only when the store file changed
        (`traces.store_stamp`), under one lock: a poll does not read the store. A `sqlite3.Error` propagates and
        nothing is kept, so the next request reads again."""
        with self._mutants_lock:
            stamp = traces.store_stamp(self.store)
            if self._mutants is None or self._mutants[0] != stamp:
                self._mutants = (stamp, catalog.mutant_rows(self.store))
            return self._mutants[1]

    def scan(self) -> traces.Snapshot:
        """One snapshot over the server's roots, phases from its jobs; it becomes the
        `last_snapshot` and the phase cache file is saved when the cache changed."""
        snap = traces.snapshot(self.roots, self.workflow_dir, datetime.now().astimezone(),
                               self.presence_dir, self.phase_cache, self.jira_url, self.store,
                               jobs=self.phase_jobs)
        self.last_snapshot = snap
        self.save_cache()
        return snap

    def save_cache(self) -> None:
        """Write the phase cache file if the jobs changed the cache since the last save attempt. Nothing
        without a `cache_file`; a failed write is one stderr line and never reaches a request."""
        if self.cache_file is None:
            return
        with self._save_lock:
            generation, cache = self.phase_jobs.cache_copy(self.phase_cache)
            if generation == self._saved_generation:
                return
            try:
                traces.save_phase_cache(self.cache_file, cache)
            except OSError as error:
                print(f"desk: cannot save the phase cache: {error}", file=sys.stderr, flush=True)
            # The attempt counts, failed or not: the next try waits for the next change.
            self._saved_generation = generation

    def warm_up(self) -> None:
        """Scan, wait for the jobs that scan started, save the cache (story 11: any failure is
        one stderr line, the server keeps answering)."""
        try:
            self.scan()
            self.phase_jobs.wait_idle()
            self.save_cache()
        except Exception as error:  # noqa: BLE001 - a broken repo must not stop the window
            print(f"desk: warm-up failed: {type(error).__name__}: {error}",
                  file=sys.stderr, flush=True)

    def server_close(self) -> None:
        self.phase_jobs.close()
        self.save_cache()
        super().server_close()


class DeskHandler(BaseHTTPRequestHandler):
    server: DeskServer
    status: int | str | None = None   # the status of the request being handled, set by log_request
    cache_control: str   # set by handle_one_request for every request; only `_vendor` changes it

    def handle_one_request(self) -> None:
        """The stdlib's, timed: one line `<method> <path> <status> <ms> ms` when a status was sent."""
        self.status = None
        self.cache_control = "no-store"
        started = time.monotonic()
        super().handle_one_request()
        if self.status is not None:   # no request line, or a drop before any answer: no line
            self.log_message("%s %s %s %d ms", getattr(self, "command", None) or "-",
                             getattr(self, "path", "-"), self.status,
                             round((time.monotonic() - started) * 1000))

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        self.status = code   # the timed line of handle_one_request replaces the stdlib's

    def end_headers(self) -> None:
        # Here, not in each reply, so error pages built by the stdlib carry it too.
        self.send_header("Cache-Control", self.cache_control)
        super().end_headers()

    def _reply(self, status: int, body: bytes, content_type: str,
               extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, extra: dict[str, str] | None = None) -> None:
        self._reply(status, json.dumps(payload).encode(), "application/json", extra)

    def _versioned(self, payload: dict, ignore: tuple[str, ...] = (),
                   extra: dict[str, str] | None = None) -> None:
        """The reply of an answer that has a version (#105): 304 with no body when `If-None-Match`
        names the tag, else 200 with the body; both carry `ETag` and `extra`."""
        etag = content_etag(payload, ignore)
        headers = {**(extra or {}), "ETag": etag}
        if etag_matches(self.headers.get("If-None-Match"), etag):
            self.send_response(304)   # no body, so no Content-Type or Content-Length either
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
        else:
            self._reply(200, json.dumps(payload).encode(), "application/json", headers)

    def _guarded(self) -> bool:
        """False after answering 403 when the Host is neither loopback nor the --remote-host."""
        port = self.server.server_address[1]
        if host_allowed(self.headers.get("Host"), port, self.server.remote_host):
            return True
        self._json(403, {"error": "forbidden host"})
        return False

    def do_GET(self) -> None:
        if not self._guarded():
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._page()
        elif path in STATIC:
            self._file(*STATIC[path])
        elif path == "/api/features":
            self._features()
        elif path.startswith("/api/features/"):
            self._detail(path.removeprefix("/api/features/"))
        elif path == "/api/workflow":
            self._workflow(parse_qs(urlsplit(self.path).query).get("feature"))
        elif path == "/api/documents":
            self._repo_documents(parse_qs(urlsplit(self.path).query).get("repo", [""])[0])
        elif path.startswith("/api/documents/"):
            self._document(path.removeprefix("/api/documents/"))
        elif path.startswith("/vendor/"):
            self._vendor(path.removeprefix("/vendor/"))
        else:
            self._json(404, {"error": "not found"})

    def _page(self) -> None:
        """index.html; the action token goes in only for a loopback Host (read-only from the phone)."""
        try:
            page = (self.server.static_dir / "index.html").read_text(encoding="utf-8")
        except OSError:
            self._json(404, {"error": "not found"})
            return
        if host_allowed(self.headers.get("Host"), self.server.server_address[1]):
            page = page.replace(TOKEN_MARKER,
                               f'<meta name="wf-token" content="{self.server.token}">')
        self._reply(200, page.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": CSP})

    def _file(self, name: str, content_type: str, extra: dict[str, str] | None = None) -> None:
        try:
            body = (self.server.static_dir / name).read_bytes()
        except OSError:
            self._json(404, {"error": "not found"})
            return
        self._reply(200, body, content_type, extra)

    def _features(self) -> None:
        started = time.monotonic()
        try:
            snap = self.server.scan()
            payload = traces.to_json(snap)
        except Exception as error:  # noqa: BLE001 - any scan failure is a 503 answer, not a dropped socket
            self._json(503, {"error": f"{type(error).__name__}: {error}"},
                       {"Server-Timing": scan_timing(started)})
            return
        self._versioned(payload, ignore=("generated_at",), extra={"Server-Timing": scan_timing(started)})

    def _detail(self, row_id: str) -> None:
        """One row of the LAST snapshot with its handoff text and reports; 404 if unknown."""
        snap = self.server.last_snapshot
        rows = (*snap.open, *snap.history) if snap else ()
        row = next((r for r in rows if r.id == row_id), None)
        if row is None:
            self._json(404, {"error": "not found"})
            return
        try:
            kept, own, documents = self._row_catalog(row, rows)
            detail = traces.feature_detail(row, kept)
            detail["documents"] = self._listed(documents)
            detail["reports"] = [{"id": d.id, "name": d.name} for d in own if d.kind == "report"]
            body = json.dumps(detail).encode()
        except Exception as error:  # noqa: BLE001 - a vanished file or an unreadable store is a 503 answer, not a dropped socket
            self._json(503, {"error": f"{type(error).__name__}: {error}"})
            return
        self._reply(200, body, "application/json")

    def _row_catalog(self, row: traces.Row, rows: Sequence[traces.Row], spec_optional: bool = False) -> tuple[
            list[tuple[str, tuple[str, ...]]], list[catalog.Document], list[catalog.Document]]:
        """(stored reports, the row's own documents, all its documents) - what the detail and the
        workflow map list for a row. Raises OSError or a store error, which the callers answer 503;
        with `spec_optional` a spec that cannot be read only drops the feature documents."""
        repos_of = traces.slug_repos(rows)
        kept = traces.stored_reports(self.server.store, row.slug, row.repo, repos_of)
        tree = Path(row.where.path) if row.where.path is not None else None
        own = catalog.row_documents(row, kept, self.server.store)
        try:
            feature = catalog.feature_documents(row.slug, tree)
        except OSError:
            if not spec_optional:
                raise
            feature = []
        return kept, own, [*feature, *own]

    def _workflow(self, feature: list[str] | None) -> None:
        """The workflow map; with `?feature=<row id>` each phase also carries the overlay of that row
        of the LAST snapshot (404 for an id not in it); nothing is derived here. A spec that cannot be
        read only drops the feature's artefacts (200, invariant 4); an unreadable install or store is a 503.
        With a feature each phase also carries `sections` (#100): the gate sections of its documents, and
        (#127) `diagrams`, the Mermaid blocks the stage draws, and `lines`, the length of each section and artefact."""
        row = None
        if feature is not None:
            snap = self.server.last_snapshot
            rows = (*snap.open, *snap.history) if snap else ()
            row = next((r for r in rows if r.id == feature[0]), None)
            if row is None:
                self._json(404, {"error": "not found"})
                return
        try:
            wdir = self.server.workflow_dir
            documents = self._row_catalog(row, rows, spec_optional=True)[2] if row else []
            wf, check_model = self.server.parsers()
            built = catalog.workflow_map(wdir, self.server.skills_dir, wf, catalog.load_genflow(wdir))
            tree = Path(row.where.path) if row and row.where.path is not None else None
            # the spec of the row's own tree is the one the gates read: it comes first for the sections
            sourced = sorted(documents, key=lambda d: not (d.kind == "spec" and tree is not None
                                                           and Path(d.path).is_relative_to(tree)))
            for phase in built["phases"]:
                phase["documents"] = self._listed(phase["documents"])
                phase["overlay"] = catalog.overlay(row.phase, phase["n"]) if row else None
                artefacts = catalog.phase_artefacts(phase["n"], documents)
                phase["artefacts"] = self._listed(artefacts)
                if row:
                    for item, doc in zip(phase["artefacts"], artefacts):
                        item["lines"] = catalog.text_lines(doc)
                    found = catalog.phase_sections(phase["n"], sourced, wf, check_model)
                    self._listed([s.doc for s in found if s.doc])
                    phase["sections"] = [{"heading": s.heading, "source": s.source, "state": s.state,
                                          "id": s.doc.id if s.doc else None, "rev": s.rev, "note": s.note,
                                          "lines": catalog.text_lines(s.doc, (wf, check_model)) if s.doc else None}
                                         for s in found]
                    drawn = catalog.phase_diagrams(phase["n"], sourced)
                    self._listed([d.doc for d in drawn if d.doc])
                    phase["diagrams"] = [{"title": d.title, "source": d.source, "doc": d.doc.id if d.doc else None,
                                          "generated": d.generated, "mermaid": d.mermaid} for d in drawn]
                    # (#129) the invariants with a status: the store is read only for the phases that show them
                    phase["invariants"] = self._invariants(phase["n"], sourced, tree)
            for item in built["transversal"]:
                item["document"] = self._listed([item["document"]])[0] if item["document"] else None
        except Exception as error:  # noqa: BLE001 - an unreadable install is a 503 answer, not a dropped socket
            self._json(503, {"error": f"{type(error).__name__}: {error}"})
            return
        self._versioned(built)

    def _invariants(self, n: int, sourced: list[catalog.Document], tree: Path | None) -> dict | None:
        """The wire form of `catalog.phase_invariants` (the spec's endpoint contract), or None. The one phase check
        stays here because `mutant_rows()` below is an argument: without it every phase would read the store, and so does
        the spec check: a phase with no spec has no invariants and must not 503 on a store it does not need."""
        if n not in (3, 6) or not any(d.kind == "spec" for d in sourced):
            return None
        table = catalog.phase_invariants(n, sourced, tree, self.server.mutant_rows())
        if table is None:
            return None
        if not table.readable:
            return {"source": table.source, "readable": False, "raw": table.raw or "", "why": table.why or ""}
        return {"source": table.source, "readable": True, "tree": table.tree, "why": table.why,
                "rows": [{"n": r.n, "law": r.law, "negation": r.negation, "verified": r.verified,
                          "tests": list(r.tests), "status": r.status, "evidence": list(r.evidence)}
                         for r in table.rows]}

    def _listed(self, docs: list[catalog.Document]) -> list[dict]:
        """The wire form of a listing; its ids enter the catalog (the only way in, Decision 20)."""
        self.server.documents.update({d.id: d for d in docs})
        return [{k: v for k, v in asdict(d).items() if k not in ("stored", "section")} for d in docs]

    def _repo_documents(self, repo: str) -> None:
        """The documents of a repo of the LAST snapshot (404 otherwise); a repo known only from a
        handoff has no primary and lists none."""
        snap = self.server.last_snapshot
        wanted = repo.casefold()
        if snap is None or not repo:
            self._json(404, {"error": "not found"})
            return
        primary = next((path for identity, path in snap.primaries if identity.casefold() == wanted), None)
        in_rows = any((r.repo or "").casefold() == wanted for r in (*snap.open, *snap.history))
        if primary is None and not in_rows:
            self._json(404, {"error": "not found"})
            return
        try:
            docs = self._listed(catalog.repo_documents(Path(primary))) if primary else []
        except OSError as error:
            self._json(503, {"error": f"{type(error).__name__}: {error}"})
            return
        self._versioned({"repo": repo, "documents": docs})

    def _document(self, doc_id: str) -> None:
        """A document of the catalog: markdown, or a mockup under `Content-Security-Policy: sandbox`."""
        doc = self.server.documents.get(doc_id)
        if doc is None:
            self._json(404, {"error": "not found"})
            return
        try:
            body = catalog.document_body(doc, self.server.store, self.server.parsers() if doc.section else None)
        except OSError as error:
            self._json(503, {"error": f"cannot read the document: {error}"})
            return
        if doc.kind == "mockup":
            self._reply(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": "sandbox"})
        elif doc.kind == "report":
            self._reply(200, body, catalog.CONTENT_TYPES[Path(doc.name).suffix] + "; charset=utf-8")
        else:
            self._reply(200, body, "text/markdown; charset=utf-8")

    def _vendor(self, name: str) -> None:
        """A renderer named in the manifest; no path is ever built from the request."""
        try:
            sha = catalog.vendor_files(self.server.vendor_dir).get(name)
            if sha is not None:
                body = (self.server.vendor_dir / name).read_bytes()
            else:
                self._json(404, {"error": "not found"})
                return
        except (OSError, ValueError):   # unreadable, or not UTF-8, manifest
            self._json(404, {"error": "not found"})
            return
        self.cache_control = "no-cache"   # revalidated by the manifest hash, never stale
        etag = f'"{sha}"'
        if etag_matches(self.headers.get("If-None-Match"), etag):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self._reply(200, body, "text/javascript; charset=utf-8", {"ETag": etag})

    def do_POST(self) -> None:
        if urlsplit(self.path).path == "/api/open":
            self._open()
        else:
            self._refuse()

    def _open(self) -> None:
        """POST /api/open, the checks in the order of Decision 18; 204 when the command started."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        raw = self.rfile.read(min(max(length, 0), MAX_DRAINED_BODY))   # unread bodies reset the socket
        port = self.server.server_address[1]
        host = self.headers.get("Host")
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not host_allowed(host, port, self.server.remote_host):
            self._json(403, {"error": "forbidden host"})
        elif not host_allowed(host, port):
            self._json(403, {"error": "read-only from this host"})
        elif self.headers.get("Origin") != f"http://{host}":
            self._json(403, {"error": "forbidden origin"})
        elif content_type != "application/json":
            self._json(415, {"error": "application/json only"})
        elif (ask := open_request(raw, length)) is None:
            self._json(400, {"error": "expected exactly the string fields action, id and token"})
        elif not hmac.compare_digest(ask["token"].encode(), self.server.token.encode()):
            self._json(403, {"error": "forbidden token"})
        else:
            self._launch(ask)

    def _launch(self, ask: dict[str, str]) -> None:
        try:
            argv = action_argv(ask["action"], ask["id"], self.server.last_snapshot,
                               self.server.distro)
        except ActionRefused as refused:
            self._json(refused.status, {"error": "not found" if refused.status == 404
                                        else "action not valid for that id"})
            return
        try:
            self.server.launcher(argv)
        except OSError as error:
            self._json(503, {"error": f"cannot start {argv[0]}: {error}"})
            return
        self.send_response(204)
        self.end_headers()

    def __getattr__(self, name: str) -> Callable[[], None]:
        # Every do_<METHOD> but GET (HEAD included) answers 405.
        if not name.startswith("do_"):
            raise AttributeError(name)
        return self._refuse

    def _refuse(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0  # not a length: nothing to drain, the answer is still 405
        self.rfile.read(min(max(length, 0), MAX_DRAINED_BODY))  # unread bodies reset the socket
        if self._guarded():
            self._reply(405, json.dumps({"error": "method not allowed"}).encode(),
                        "application/json", {"Allow": "GET"})


def scan_timing(started: float) -> str:
    """`Server-Timing` value for a scan begun at monotonic `started`: whole milliseconds."""
    return f"scan;dur={round((time.monotonic() - started) * 1000)}"


def open_request(raw: bytes, length: int) -> dict[str, str] | None:
    """The body of POST /api/open when it is a JSON object with exactly the string fields
    action, id and token and is at most 4096 bytes; else None."""
    if not 0 <= length <= MAX_OPEN_BODY:
        return None
    try:
        body = json.loads(raw)
    except ValueError:   # not JSON, or not UTF-8
        return None
    if (not isinstance(body, dict) or set(body) != OPEN_FIELDS
            or not all(isinstance(v, str) for v in body.values())):
        return None
    return body


def build_server(port: int, roots: tuple[Path, ...], workflow_dir: Path,
                 static_dir: Path, presence_dir: Path | None = None,
                 remote_host: str | None = None, jira_url: str | None = None,
                 distro: str = "",
                 launcher: Callable[[list[str]], None] = launch,
                 vendor_dir: Path | None = None,
                 skills_dir: Path | None = None,
                 store: Path | None = None,
                 cache_file: Path | None = None) -> ThreadingHTTPServer:
    """Bound to 127.0.0.1, not serving; raises OSError when the port is taken. With a
    `cache_file` the phase cache is loaded from it and saved back (#66)."""
    return DeskServer(port, roots, workflow_dir, static_dir, presence_dir, remote_host,
                      jira_url, distro, launcher, vendor_dir, skills_dir, store, cache_file)
