# Gates: pre-commit + pre-push

Detailed reference for the kit's two repository-local hooks. The README keeps the overview.


`install.sh --gate <repo>` points the repository-local `core.hooksPath` at the
kit's `guards/hooks`, which ships two hooks. No global config is touched and
the install is idempotent. Both require Python >= 3.11 (`tomllib`).

- **pre-commit** — computes facts the review needs and stages them into the
  commit itself, so they do not depend on what the agent chooses to report.
- **pre-push** — runs the DesignDoc gate over `docs/design/*.md` at push time
  (signatures against the real code, coherence, the four exit kinds, the
  external frontier). A repository without `docs/design/` has nothing to
  check and the hook exits 0.

Install both into a repository's top level:

```bash
~/agentic-dev-kit/install.sh --gate /path/to/repo
```

If `core.hooksPath` is already set to something else (e.g. husky's `.husky`),
the install refuses and changes nothing: unset it first if the gate should
replace it. Hooks in `.git/hooks/` stop running once `core.hooksPath` is set;
the install names any it finds.

### Pre-commit gate

Configure it in that repository's `.workflow/gate.toml`:

```toml
[skips]
command = ["python3", "-m", "pytest", "-q"]   # argv, not a shell string
# junit_option = "--junitxml={path}"          # default; {path} is a temp file

[pins]
artifact = "my-svc"                  # matched as my-svc:<version> (optional "v" before the version)
base = "main"                        # any git revision: branch, tag or sha
files = ["../infra/deploy-*.sh", "deploy/**/*.yml"]   # globs from the repo top level; may leave the repo
# version_file = "pyproject.toml"   # default; [project].version is read
```

On every `git commit` the gate runs `command` plus the JUnit option (with
every `GIT_*` variable removed from its environment), and writes
`.workflow/gate-report.md` into the same commit: each skipped test as
`<classname>::<name>` with its skip reason, sorted, no timestamps — `none`
when nothing is skipped, `not configured` when `[skips]` is absent. Skips are
read from the JUnit report only, never from console output. Deselected tests
are not listed: JUnit does not contain them.

With `[pins]`, the gate reads `[project].version` of `version_file` at `base`
and in the index being committed (staged content, not the working tree). If
they are equal the `## Stale pins` section says `not applicable: version
unchanged (<version>)` and the deploy files are not read. Otherwise it scans
the deploy files (read from the filesystem, so they may be untracked or
outside the repo) and lists every `<artifact>:<version>` whose version differs
from the new one, as `` `<path>:<line>` pins <artifact>:<version> `` under
`version <old> → <new>:`, sorted by path and line — or `version <old> → <new>:
none`. The comparison is with `base`, not the previous commit: every later
commit on the branch keeps listing the stale pins until they are updated.
Name boundary: the artifact must be followed by `:` and preceded by the start
of the line or a character other than a letter, digit, `.`, `_` or `-`, so
`registry/my-svc:0.0.3` matches but `other-my-svc:0.0.3` and
`my-svc-extra:0.0.3` do not. `not configured` when `[pins]` is absent.

It **blocks the commit only when a configured check cannot run**: Python
older than 3.11, missing, unreadable (or non-UTF-8) `.workflow/gate.toml`,
invalid TOML or wrong types, a test command that cannot start, a JUnit report
that is missing, unreadable or not parsable, a `base` that cannot be resolved,
a version file missing at `base` or in the index (or not UTF-8, not valid
TOML, or without a `[project].version` string), and, only when the version
changed, a deploy glob that matches no file or a deploy file that cannot be
read (or is not UTF-8), or a gate report that cannot be written or staged. The reason is on stderr. Failing tests do not block — that
is the canonical test gate's job, not this one's.

Known limitation: with `git commit <paths>` the report lands in the commit,
but the main index keeps the previous report afterwards (`git status` shows
`.workflow/gate-report.md` modified) until the next commit.

Known bypasses: `git commit --no-verify`, changing `core.hooksPath`, editing
the gate. The `final` mode detects them after the fact.

#### Final mode

Run it on the final working tree — by a person before review, in CI, or by a
bench outside git — after the last commit of a session:

```bash
python3 ~/agentic-dev-kit/guards/gate.py final [/path/to/repo]   # default: the cwd
```

The path may be any directory inside the repo: `final` verifies the repo's top
level (unlike `install`, which refuses a subdirectory).

It recomputes the report at `HEAD` (the same checks as the hook; versioned
files such as `version_file` are read at `HEAD`, deploy files and the test run
from the filesystem) and compares it with the committed one. It prints one
line per problem on stderr as `gate: final: <problem>`, in this order:

- `not a git repository` — the path is not inside a git work tree (reported
  alone).
- `uncommitted tracked changes` — staged or unstaged; `final` verifies the
  committed state. Untracked files are ignored (e.g. a PR description file).
- `no report committed` — `HEAD` has no `.workflow/gate-report.md` (commits
  made before the install, or with `--no-verify`).
- `report stale` — the committed report differs from the recompute; a
  unified diff committed → recomputed is indented below the line.
- `configured check cannot run` — the reason, as the hook would block (with
  the test command's console output below it, if any).
- `hook path not pointing at the gate` — the repo-local `core.hooksPath` is
  unset or resolves (relative paths from the top level) elsewhere than this
  kit's `guards/hooks`.
- `gate files modified in the kit` — `git status` of the kit checkout lists
  changes under `guards/` (edits or new files).
- `cannot verify gate integrity` — the kit is not a git checkout of its own
  (a copy inside another repo does not count).

Exit 0, with no output, only when the committed report is present and current
and the hook intact; 1 otherwise; 2 on bad arguments.

### DesignDoc gate (pre-push)

A repository that keeps its phase-5 designs in `docs/design/*.md` gets a
second gate. The design file is prose plus a fenced `json design` block with
the machine part — call stack, file tree, signatures, data flow, and one
contract per call with its exits. `guards/check_design.py` checks it
(stdlib only, no browser):

```bash
python3 guards/check_design.py docs/design/<feature>.md .          # full: state included
python3 guards/check_design.py docs/design/<feature>.md . --gate   # pre-push/CI: no state
```

- **signatures** — every function the document declares is compared with the
  real code: `compatible` / `shortened` / `proposed` / `new`, and
  `incompatible` (a required parameter omitted) exits non-zero.
- **coherence** — contract sites, nodes and edges may not contradict each
  other; signatures live in one place.
- **exits** — all four kinds (`returns`, `absent`, `raises`, `effect`) must be
  declared, and every dispatchable one (`returns`, `absent`, `raises`) needs a
  real `handled_at`. A contract without `raises` is silent, which is
  indistinguishable from a forgotten exit.
- **frontier** — a contract that calls outside the repo (free-text `call`, or a
  ghost node) must declare `residual`: what happens to the exits it did not
  enumerate. A library's exception set is not readable with `ast`, so the check
  verifies the declared residual, not the library.
- **state** — `add` / `mod` / `keep` is verified against the code. This one is
  **not** checked by `--gate`: it is true only while the code does not exist,
  so at pre-push it would be stale by construction. The full mode runs at the
  end of phase 5.

`guards/hooks/pre-push` runs `--gate` over every `docs/design/*.md` (and
`*.json`) in the repository at push time; it is installed by the same
`core.hooksPath` as the pre-commit gate (`./install.sh --gate <repo>`). A
repository without `docs/design/` has nothing to check.
