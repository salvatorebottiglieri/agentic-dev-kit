"""The report store: one SQLite file of reviewer evidence, append-only.

Stdlib only. Every function takes its path explicitly; only the CLI entry
points (wf.main, audit.main, desk.py serve, and mutation_prover.main for
--name/--manifest-name only) read the environment and pass the path down.
The evidence checks are the audit's own (loaded by path); the manifest check is the
prover's `validate_manifest` (guards/mutation_prover.py, also loaded by path). These are
the only two modules loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import subprocess
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

STORE_ENV = "AGENT_REPORT_STORE"
STATE_NAME = "agentic-dev-kit"   # store dir under $XDG_STATE_HOME; same literal in wf.py and app/desk.py; sync_kit.py rewrites it to "agentic-dev-kit" in the public kit (Decision 1)
AUDIT_PATH = Path(__file__).resolve().parent / "audit.py"
PROVER_PATH = Path(__file__).resolve().parent / "guards" / "mutation_prover.py"
TAIL_LINES = 30
OUTCOMES = ("red", "pinned", "blind-test", "no-test", "error", "inconclusive")
MATRIX_KINDS = ("control", "mutant")

_KEYS = {"review_row": ("seq",), "brief_item": ("repo", "feature", "item_id"),
         "mutation_manifest": ("name",), "mutation_result": ("name", "seq"),
         "import_source": ("path", "line")}
_TABLES = tuple(_KEYS)
SCHEMA = """
CREATE TABLE IF NOT EXISTS review_row (
  seq          INTEGER PRIMARY KEY,
  recorded_at  TEXT NOT NULL,
  repo         TEXT NOT NULL,
  feature      TEXT NOT NULL,
  report       TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  criterion    TEXT NOT NULL,
  verdict      TEXT NOT NULL,
  severity     TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'open',
  actor        TEXT NOT NULL,
  row_json     TEXT NOT NULL,
  historical   INTEGER NOT NULL DEFAULT 0,
  why_historical TEXT,
  CHECK (severity IN ('fatal', 'advisory') OR historical = 1)
);
CREATE INDEX IF NOT EXISTS review_row_repo_feature ON review_row (repo, feature);
CREATE TABLE IF NOT EXISTS brief_item (repo TEXT NOT NULL, feature TEXT NOT NULL, item_id TEXT NOT NULL,
                         recorded_at TEXT NOT NULL, PRIMARY KEY (repo, feature, item_id));
CREATE TABLE IF NOT EXISTS mutation_manifest (name TEXT PRIMARY KEY, recorded_at TEXT NOT NULL,
                         manifest_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mutation_result (
  name TEXT NOT NULL, seq INTEGER NOT NULL, recorded_at TEXT NOT NULL,
  mutant_id TEXT NOT NULL, kind TEXT NOT NULL, invariant TEXT, test TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('red', 'pinned', 'blind-test', 'no-test', 'error', 'inconclusive')),
  exit_code INTEGER, diff TEXT, command TEXT, note TEXT,
  output_tail TEXT,
  historical INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (name, seq)
);
CREATE TABLE IF NOT EXISTS import_source (path TEXT NOT NULL, line INTEGER NOT NULL, kind TEXT NOT NULL,
                         ref TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (path, line));
""" + "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'append-only'); END;\n"
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'append-only'); END;\n"
    for t in _TABLES
) + "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_replace BEFORE INSERT ON {t} "
    f"WHEN EXISTS (SELECT 1 FROM {t} WHERE {' AND '.join(f'{c} IS NEW.{c}' for c in key)}) "
    f"BEGIN SELECT RAISE(ABORT, 'append-only'); END;\n"
    for t, key in _KEYS.items()
)

VERDICTS = ("pass", "fail", "missing", "actionable", "pinned", "blind-test", "no-test")
SEVERITIES = ("fatal", "advisory")
REVIEWERS = ("standards", "spec", "ponytail", "mutation")
CLAIMS = ("code", "coverage")
STATUSES = ("open", "resolved", "fixed", "deferred")
REQUIRED = ("criterion", "item_id", "verdict", "severity", "reviewer", "repo", "actor")
REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # the grammar of wf.SLUG_RE


class BatchRejected(Exception):
    """A batch with at least one bad row; ``reasons`` has one line per problem."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def default_store_path(env: Mapping[str, str], home: Path) -> Path:
    if env.get(STORE_ENV):
        return Path(env[STORE_ENV])
    state = env.get("XDG_STATE_HOME")
    base = Path(state) if state else home / ".local" / "state"
    return base / STATE_NAME / "reports.sqlite"


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(SCHEMA)
    return conn


def open_existing(path: Path | None) -> sqlite3.Connection | None:
    """A connection to the store at ``path``; ``None`` when there is no such file.

    The one "a missing store is no store" rule: reading never creates a store."""
    if path is None or not path.is_file():
        return None
    return open_store(path)


def schema_problems(row: object) -> list[str]:
    """The REVIEW-CRITERIA vocabulary for a new row; [] when it fits."""
    if not isinstance(row, dict):
        return ["not a JSON object"]
    problems = []
    for key in REQUIRED:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            problems.append(f"{key} is missing or not a non-empty string")
    verdict = row.get("verdict")
    if isinstance(verdict, str) and verdict and verdict not in VERDICTS and not (
            verdict.startswith("n/a — ") and verdict[len("n/a — "):].strip()):
        problems.append(f"verdict {verdict!r} is not in the vocabulary")
    for key, allowed in (("severity", SEVERITIES), ("reviewer", REVIEWERS)):
        value = row.get(key)
        if isinstance(value, str) and value and value not in allowed:
            problems.append(f"{key} {value!r} is not one of {', '.join(allowed)}")
    repo = row.get("repo")
    if isinstance(repo, str) and repo and not REPO_RE.match(repo):
        problems.append(f"repo {repo!r} is not owner/name")
    for key, allowed in (("claim", CLAIMS), ("status", STATUSES)):
        if key in row and row[key] not in allowed:
            problems.append(f"{key} {row[key]!r} is not one of {', '.join(allowed)}")
    if "line" in row and (not isinstance(row["line"], int) or isinstance(row["line"], bool)):
        problems.append("line is not an integer")
    return problems


def commit_diffs(repo_root: Path, commits: Iterable[str]) -> dict[str, str]:
    """``git show`` per distinct commit; a commit git does not know is left out."""
    diffs: dict[str, str] = {}
    for commit in dict.fromkeys(commits):
        done = subprocess.run(["git", "-C", str(repo_root), "show", commit],
                              capture_output=True, text=True, check=False)
        if done.returncode == 0:
            diffs[commit] = done.stdout
    return diffs


def _audit():
    found = importlib.util.spec_from_file_location("report_store_audit", AUDIT_PATH)
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def add_review_rows(conn: sqlite3.Connection, rows: list[dict], feature: str, report: str,
                    repo_root: Path, now: datetime, raw: list[str] | None = None) -> int:
    """Validate the whole batch, then append it in one transaction.

    ``raw`` is the text of each row as given (one per row); row_json keeps it verbatim."""
    audit = _audit()
    diffs = commit_diffs(repo_root, (r["commit"] for r in rows if isinstance(r, dict)
                                     and r.get("claim", "code") == "code"
                                     and isinstance(r.get("commit"), str)))
    reasons: list[str] = []
    first_repo = next((r.get("repo") for r in rows if isinstance(r, dict)), None)
    for k, r in enumerate(rows, start=1):
        problems = schema_problems(r)
        if not problems:
            if r["repo"] != first_repo:
                problems.append(f"repo {r['repo']!r} differs from the batch's first row")
            problems += audit.check_reviewer_evidence_verbatim([r], diffs)
            problems += audit.check_report_actor([r])
        reasons += [f"row {k}: {p}" for p in problems]
    if reasons:
        raise BatchRejected(reasons)
    stamp = now.isoformat()
    texts = raw if raw is not None else [json.dumps(r, ensure_ascii=False) for r in rows]
    with conn:
        conn.executemany(
            "INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion,"
            " verdict, severity, status, actor, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(stamp, r["repo"], feature, report, r["item_id"], r["criterion"], r["verdict"],
              r["severity"], r.get("status", "open"), r["actor"], text) for r, text in zip(rows, texts)])
    return len(rows)


def review_rows(conn: sqlite3.Connection, repo: str | None = None, feature: str | None = None,
                report: str | None = None) -> list[dict]:
    where, args = [], []
    for column, value in (("repo", repo), ("feature", feature), ("report", report)):
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    sql = "SELECT row_json, feature, report FROM review_row"
    if where:
        sql += " WHERE " + " AND ".join(where)
    found = conn.execute(sql + " ORDER BY seq", args).fetchall()
    return [{**json.loads(text), "feature": f, "report": rep} for text, f, rep in found]


def add_brief_items(conn: sqlite3.Connection, repo: str, feature: str, items: list[str],
                    now: datetime) -> int:
    """Validate the brief, then append the items not yet stored for (repo, feature).

    Items already stored and repeats inside ``items`` are ignored; returns the number appended."""
    reasons = []
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        reasons.append(f"repo {repo!r} is not owner/name")
    if not isinstance(feature, str) or not SLUG_RE.match(feature):
        reasons.append(f"feature {feature!r} is not a valid slug")
    if not items:
        reasons.append("the brief has no item")
    reasons += [f"item {k}: not a non-empty string" for k, item in enumerate(items, start=1)
                if not isinstance(item, str) or not item]
    if reasons:
        raise BatchRejected(reasons)
    stamp = now.isoformat()
    with conn:
        known = {item for (item,) in conn.execute(
            "SELECT item_id FROM brief_item WHERE repo = ? AND feature = ?", (repo, feature))}
        new = [item for item in dict.fromkeys(items) if item not in known]
        conn.executemany("INSERT INTO brief_item (repo, feature, item_id, recorded_at) VALUES (?,?,?,?)",
                         [(repo, feature, item, stamp) for item in new])
    return len(new)


def brief_items(conn: sqlite3.Connection, repo: str) -> dict[str, list[str]]:
    """feature -> its brief items, in the order they were appended."""
    briefs: dict[str, list[str]] = {}
    for feature, item in conn.execute(
            "SELECT feature, item_id FROM brief_item WHERE repo = ? ORDER BY rowid", (repo,)):
        briefs.setdefault(feature, []).append(item)
    return briefs


def review_records(conn: sqlite3.Connection, repo: str) -> list[tuple[dict, str, str]]:
    """(row as given, feature, report) per stored row of ``repo``, in ``seq`` order."""
    found = conn.execute("SELECT row_json, feature, report FROM review_row WHERE repo = ? ORDER BY seq",
                         (repo,)).fetchall()
    return [(json.loads(text), feature, report) for text, feature, report in found]


def feature_dates(conn: sqlite3.Connection, repo: str) -> dict[str, str]:
    """feature -> its most recent ``recorded_at`` over its review rows and its brief items."""
    found = conn.execute(
        "SELECT feature, MAX(recorded_at) FROM ("
        " SELECT feature, recorded_at FROM review_row WHERE repo = ?"
        " UNION ALL SELECT feature, recorded_at FROM brief_item WHERE repo = ?) GROUP BY feature",
        (repo, repo)).fetchall()
    return dict(found)


def _prover():
    found = importlib.util.spec_from_file_location("report_store_prover", PROVER_PATH)
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module


def _name_problems(conn: sqlite3.Connection, table: str, name: str) -> list[str]:
    if not isinstance(name, str) or not SLUG_RE.match(name):
        return [f"name {name!r} is not a valid slug"]
    if conn.execute(f"SELECT 1 FROM {table} WHERE name = ?", (name,)).fetchone():
        return [f"{name} is already stored"]
    return []


def add_manifest(conn: sqlite3.Connection, name: str, manifest: object, now: datetime) -> None:
    """Append the manifest under ``name``: a bad name, a name already stored or a manifest the
    prover rejects raises ``BatchRejected``, one reason per problem, and nothing is written."""
    reasons = _name_problems(conn, "mutation_manifest", name)
    reasons += _prover().validate_manifest(manifest)
    if reasons:
        raise BatchRejected(reasons)
    with conn:
        conn.execute("INSERT INTO mutation_manifest (name, recorded_at, manifest_json) VALUES (?,?,?)",
                     (name, now.isoformat(), json.dumps(manifest, ensure_ascii=False)))


def manifest(conn: sqlite3.Connection, name: str) -> dict | None:
    found = conn.execute("SELECT manifest_json FROM mutation_manifest WHERE name = ?", (name,)).fetchone()
    return None if found is None else json.loads(found[0])


def _output_tail(row: dict) -> str:
    lines: list[str] = []
    for key in ("stdout", "stderr", "error"):
        text = row.get(key)
        if isinstance(text, str):
            lines += text.splitlines()
    return "\n".join(lines[-TAIL_LINES:])


def add_matrix(conn: sqlite3.Connection, name: str, rows: list[dict], now: datetime) -> int:
    """Append the matrix under ``name`` in one transaction; ``seq`` is the row's position.

    ``output_tail`` is the last 30 lines of the row's stdout, stderr and error, in that order."""
    reasons = _name_problems(conn, "mutation_result", name)
    if not rows:
        reasons.append("the matrix has no row")
    for k, r in enumerate(rows, start=1):
        if not isinstance(r, dict):
            reasons.append(f"row {k}: not a JSON object")
            continue
        if not isinstance(r.get("id"), str) or not r["id"]:
            reasons.append(f"row {k}: id is missing or not a non-empty string")
        if r.get("kind") not in MATRIX_KINDS:
            reasons.append(f"row {k}: kind {r.get('kind')!r} is not one of {', '.join(MATRIX_KINDS)}")
        if r.get("outcome") not in OUTCOMES:
            reasons.append(f"row {k}: outcome {r.get('outcome')!r} is not one of {', '.join(OUTCOMES)}")
    if reasons:
        raise BatchRejected(reasons)
    stamp = now.isoformat()
    with conn:
        conn.executemany(
            "INSERT INTO mutation_result (name, seq, recorded_at, mutant_id, kind, invariant, test,"
            " outcome, exit_code, diff, command, note, output_tail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(name, k, stamp, r["id"], r["kind"], r.get("invariant"), r.get("test"), r["outcome"],
              r.get("exit_code"), r.get("diff"),
              json.dumps(r["command"]) if "command" in r else None, r.get("note"), _output_tail(r))
             for k, r in enumerate(rows)])
    return len(rows)


def matrix_rows(conn: sqlite3.Connection, name: str) -> list[dict] | None:
    """The stored rows in ``seq`` order, or ``None`` when no matrix has that name."""
    found = conn.execute(
        "SELECT mutant_id, kind, invariant, test, outcome, exit_code, diff, command, note, output_tail"
        " FROM mutation_result WHERE name = ? ORDER BY seq", (name,)).fetchall()
    if not found:
        return None
    keys = ("id", "kind", "invariant", "test", "outcome", "exit_code", "diff", "command", "note", "output_tail")
    rows = [dict(zip(keys, values)) for values in found]
    for r in rows:
        r["command"] = None if r["command"] is None else json.loads(r["command"])
    return rows


def review_report_names(conn: sqlite3.Connection, repo: str, feature: str) -> list[str]:
    """The distinct report names of (repo, feature), ``repo`` compared case-folded, in the
    ``seq`` order of each name's first row."""
    found = conn.execute("SELECT repo, report FROM review_row WHERE feature = ? ORDER BY seq",
                         (feature,)).fetchall()
    return list(dict.fromkeys(report for stored, report in found if stored.casefold() == repo.casefold()))


def review_report_text(conn: sqlite3.Connection, repo: str, feature: str, report: str) -> str | None:
    """The report's ``row_json`` lines verbatim, one per line with a final newline; ``None`` when none.
    ``repo`` is compared case-folded, as in ``review_report_names``."""
    found = conn.execute("SELECT repo, row_json FROM review_row WHERE feature = ? AND report = ?"
                         " ORDER BY seq", (feature, report)).fetchall()
    lines = [text for stored, text in found if stored.casefold() == repo.casefold()]
    return "".join(f"{text}\n" for text in lines) if lines else None


def manifest_text(conn: sqlite3.Connection, name: str) -> str | None:
    """The stored ``manifest_json`` verbatim, or ``None``."""
    found = conn.execute("SELECT manifest_json FROM mutation_manifest WHERE name = ?", (name,)).fetchone()
    return None if found is None else found[0]


def brief_text(conn: sqlite3.Connection, repo: str, feature: str) -> str | None:
    """The stored brief of (repo, feature) as its file would have been: ``{"repo", "feature",
    "items"}`` on one line, the items in append order; ``None`` when there is none. ``repo`` is
    compared case-folded, as in ``review_report_text``."""
    found = conn.execute("SELECT repo, item_id FROM brief_item WHERE feature = ? ORDER BY rowid",
                         (feature,)).fetchall()
    own = [(stored, item) for stored, item in found if stored.casefold() == repo.casefold()]
    if not own:
        return None
    return json.dumps({"repo": own[0][0], "feature": feature, "items": [item for _, item in own]},
                      ensure_ascii=False)


def matrix_names(conn: sqlite3.Connection) -> list[str]:
    """The sorted names of the stored matrices."""
    return [name for (name,) in conn.execute("SELECT DISTINCT name FROM mutation_result ORDER BY name")]


def manifest_names(conn: sqlite3.Connection) -> list[str]:
    """The sorted names of the stored manifests."""
    return [name for (name,) in conn.execute("SELECT name FROM mutation_manifest ORDER BY name")]


class ImportSummary(NamedTuple):
    files: int
    rows: int
    historical: int
    skipped: int


def _jsonl_rows(path: Path, text: str) -> list[tuple[int, str, dict]]:
    """(physical line number, the line, the row) per non-blank line; ``BatchRejected`` with one
    reason for the file's first bad line."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise BatchRejected([f"{path}: line {number} is not JSON"]) from None
        if not isinstance(row, dict):
            raise BatchRejected([f"{path}: line {number} is not a JSON object"])
        found.append((number, line, row))
    return found


def evidence_kind(path: Path, text: str) -> str:
    """``review``, ``matrix``, ``brief`` or ``manifest``, from the content; else ``BatchRejected``."""
    if path.suffix == ".jsonl":
        rows = [row for _, _, row in _jsonl_rows(path, text)]
        if any("outcome" in r and "kind" in r for r in rows):
            return "matrix"
        if any("item_id" in r or "criterion" in r or ("item" in r and "verdict" in r) for r in rows):
            return "review"
    elif path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            raise BatchRejected([f"{path}: not JSON"]) from None
        if isinstance(data, dict) and "items" in data:
            return "brief"
        if isinstance(data, dict) and ("control" in data or "mutants" in data):
            return "manifest"
    raise BatchRejected([f"{path}: not a review report, matrix, brief or manifest"])


def alias(conn: sqlite3.Connection, path: str) -> str | None:
    """The stored name of an imported matrix or manifest path, or ``None``."""
    found = conn.execute("SELECT ref FROM import_source WHERE path = ? AND kind IN ('matrix', 'manifest')"
                         " ORDER BY line LIMIT 1", (path,)).fetchone()
    return None if found is None else found[0]


def _import_names(conn: sqlite3.Connection, paths: list[str], kind: str) -> dict[str, str]:
    """path -> stored name for the new files of ``kind``: the stem, or ``<parent folder>.<stem>``
    when another path of the kind has that stem (in ``paths`` or already imported) or a stored
    name equals it."""
    table = "mutation_result" if kind == "matrix" else "mutation_manifest"
    known = {Path(p).stem for (p,) in conn.execute("SELECT DISTINCT path FROM import_source WHERE kind = ?", (kind,))}
    known |= {n for (n,) in conn.execute(f"SELECT DISTINCT name FROM {table}")}
    names = {}
    for path in paths:
        stem = Path(path).stem
        twin = stem in known or any(other != path and Path(other).stem == stem for other in paths)
        names[path] = f"{Path(path).parent.name}.{stem}" if twin else stem
    return names


def _expand(root: Path, given: Path, reasons: list[str]) -> list[Path]:
    full = (root / given).resolve()
    if not full.is_relative_to(root):
        reasons.append(f"{given}: outside the workflow dir")
    elif full.is_dir():
        dirs = [full, *sorted(d for d in full.glob("*-reports") if d.is_dir())]
        return [f for d in dirs for f in sorted(d.iterdir()) if f.is_file() and f.suffix in (".json", ".jsonl")]
    elif full.is_file():
        return [full]
    else:
        reasons.append(f"{given}: no such file or directory")
    return []


def _text(row: dict, key: str, default: str = "") -> str:
    value = row.get(key)
    return value if isinstance(value, str) else default


def read_evidence(workflow_dir: Path, paths: list[Path]) -> list[tuple[str, str, str]]:
    """(path relative to ``workflow_dir``, kind, text) per evidence file of ``paths``, in order;
    ``BatchRejected`` with one reason per path that is outside, missing, unreadable or unclassifiable.
    It needs no store."""
    root = Path(workflow_dir).resolve()
    reasons: list[str] = []
    files = list(dict.fromkeys(f for given in paths for f in _expand(root, Path(given), reasons)))
    parsed: list[tuple[str, str, str]] = []
    for full in files:
        rel = full.relative_to(root).as_posix()
        try:
            text = full.read_text(encoding="utf-8")
            parsed.append((rel, evidence_kind(Path(rel), text), text))
        except BatchRejected as err:
            reasons += err.reasons
        except (OSError, UnicodeDecodeError) as err:
            reasons.append(f"{rel}: unreadable ({err})")
    if reasons:
        raise BatchRejected(reasons)
    return parsed


def _import_plan(conn: sqlite3.Connection, parsed: list[tuple[str, str, str]]) -> list[tuple]:
    """(kind, rel, name, text, brief data or None) in write order, briefs first; ``BatchRejected``
    for a name that is not a slug."""
    refs = dict(conn.execute("SELECT path, ref FROM import_source"))
    new_names: dict[str, dict[str, str]] = {}
    for kind in ("matrix", "manifest"):
        fresh = [rel for rel, k, _ in parsed if k == kind and rel not in refs]
        new_names[kind] = _import_names(conn, fresh, kind) if fresh else {}
    plan: list[tuple] = []
    reasons: list[str] = []
    for rel, kind, text in sorted(parsed, key=lambda f: f[1] != "brief"):
        data = json.loads(text) if kind == "brief" else None
        name = refs.get(rel) or new_names.get(kind, {}).get(rel)
        if name is None:
            name = Path(rel).stem
            if kind == "brief":
                name = _text(data, "feature") or name.removesuffix("-brief")
        if kind != "review" and not SLUG_RE.match(name):
            reasons.append(f"{rel}: name {name!r} is not a valid slug")
        plan.append((kind, rel, name, text, data))
    if reasons:
        raise BatchRejected(reasons)
    return plan


def _import_manifest(conn: sqlite3.Connection, rel: str, name: str, text: str, stamp: str) -> int:
    problems = _name_problems(conn, "mutation_manifest", name)
    if problems:
        raise BatchRejected([f"{rel}: {p}" for p in problems])
    conn.execute("INSERT INTO mutation_manifest (name, recorded_at, manifest_json) VALUES (?,?,?)",
                 (name, stamp, text))
    return 1


def _import_brief(conn: sqlite3.Connection, rel: str, name: str, data: dict, stamp: str) -> int:
    items = data["items"]
    if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
        raise BatchRejected([f"{rel}: items must be a list of non-empty strings"])
    repo = _text(data, "repo")
    held = {i for (i,) in conn.execute("SELECT item_id FROM brief_item WHERE repo = ? AND feature = ?", (repo, name))}
    new = [i for i in dict.fromkeys(items) if i not in held]
    conn.executemany("INSERT INTO brief_item (repo, feature, item_id, recorded_at) VALUES (?,?,?,?)",
                     [(repo, name, i, stamp) for i in new])
    return len(new)


def _import_matrix(conn: sqlite3.Connection, rel: str, name: str, text: str, known: set,
                   stamp: str) -> tuple[int, int, int]:
    """(rows, historical, skipped) of one matrix file."""
    seq = conn.execute("SELECT COALESCE(MAX(seq) + 1, 0) FROM mutation_result WHERE name = ?", (name,)).fetchone()[0]
    rows = historical = skipped = 0
    for number, _, row in _jsonl_rows(Path(rel), text):
        if (rel, number) in known:
            skipped += 1
            continue
        historical += _insert_matrix_row(conn, rel, name, seq, row, stamp)
        seq += 1
        conn.execute("INSERT INTO import_source VALUES (?,?,?,?,?)", (rel, number, "matrix", name, stamp))
        rows += 1
    return rows, historical, skipped


def _import_reviews(conn: sqlite3.Connection, rel: str, name: str, text: str, known: set,
                    features: dict[str, frozenset[str]], stamp: str) -> tuple[int, int, int]:
    """(rows, historical, skipped) of one review report; ``features`` caches each repo's brief items."""
    audit = _audit()
    rows = historical = skipped = 0
    for number, line, row in _jsonl_rows(Path(rel), text):
        if (rel, number) in known:
            skipped += 1
            continue
        repo = _text(row, "repo")
        if repo not in features:
            features[repo] = frozenset(brief_items(conn, repo))
        historical += _insert_review_row(conn, audit, Path(rel), name, features[repo], row, line, stamp)
        conn.execute("INSERT INTO import_source VALUES (?,?,?,?,?)", (rel, number, "review", name, stamp))
        rows += 1
    return rows, historical, skipped


def import_files(conn: sqlite3.Connection, workflow_dir: Path, paths: list[Path],
                 now: datetime) -> ImportSummary:
    """Import the evidence files (or the evidence files of the directories) under ``workflow_dir``
    in one transaction; a file that does not parse or classify rejects the whole import.

    A ``(path, line)`` already in ``import_source`` is skipped, so a second run imports only what
    is new; ``row_json`` and ``manifest_json`` are the file's text verbatim."""
    plan = _import_plan(conn, read_evidence(workflow_dir, paths))
    known = set(conn.execute("SELECT path, line FROM import_source"))
    stamp = now.isoformat()
    files = rows = historical = skipped = 0
    features: dict[str, frozenset[str]] = {}
    with conn:
        for kind, rel, name, text, data in plan:
            if kind in ("brief", "manifest"):
                if (rel, 0) in known:
                    skipped += 1
                    continue
                added = (_import_brief(conn, rel, name, data, stamp) if kind == "brief"
                         else _import_manifest(conn, rel, name, text, stamp))
                conn.execute("INSERT INTO import_source VALUES (?,?,?,?,?)", (rel, 0, kind, name, stamp))
                done = (added, 0, 0)
                touched = True
            else:
                done = (_import_matrix(conn, rel, name, text, known, stamp) if kind == "matrix"
                        else _import_reviews(conn, rel, name, text, known, features, stamp))
                touched = done[0] > 0
            rows += done[0]
            historical += done[1]
            skipped += done[2]
            files += touched
    return ImportSummary(files, rows, historical, skipped)


def _insert_review_row(conn: sqlite3.Connection, audit, path: Path, report: str, features: frozenset[str],
                       row: dict, line: str, stamp: str) -> int:
    repo = _text(row, "repo")
    problems = [p for p in schema_problems(row) if not p.startswith("repo is missing")]
    if not problems:
        problems = audit.check_report_actor([row])
    why = "; ".join((["no repo"] if not repo else []) + problems)
    historical = int(bool(why))
    feature, _ = audit._feature_axis(path, features)
    conn.execute(
        "INSERT INTO review_row (recorded_at, repo, feature, report, item_id, criterion, verdict, severity,"
        " status, actor, row_json, historical, why_historical) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (stamp, repo, feature, report, _text(row, "item_id"), _text(row, "criterion"), _text(row, "verdict"),
         _text(row, "severity"), _text(row, "status", "open"), _text(row, "actor"), line,
         historical, why or None))
    return historical


def _insert_matrix_row(conn: sqlite3.Connection, rel: str, name: str, seq: int, row: dict, stamp: str) -> int:
    if row.get("outcome") not in OUTCOMES:
        raise BatchRejected([f"{rel}: outcome {row.get('outcome')!r} is not one of {', '.join(OUTCOMES)}"])
    historical = int(row.get("kind") not in MATRIX_KINDS or not _text(row, "id"))
    conn.execute(
        "INSERT INTO mutation_result (name, seq, recorded_at, mutant_id, kind, invariant, test, outcome,"
        " exit_code, diff, command, note, output_tail, historical) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, seq, stamp, _text(row, "id"), _text(row, "kind"), row.get("invariant"), row.get("test"),
         row["outcome"], row.get("exit_code"), row.get("diff"),
         json.dumps(row["command"]) if "command" in row else None, row.get("note"),
         _output_tail(row), historical))
    return historical
