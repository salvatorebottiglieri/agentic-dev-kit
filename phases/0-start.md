# Phase 0. Start

> Part of the agentic workflow. Index and reading order: `../WORKFLOW.md`. Written on 2026-11-16 (D-053, #12); `wf.py next` prints it at phase 0.

Phase 0 cannot be skipped. It isolates the work, then decides which phases run.

## Isolation

`wf.py next` checks it on every run whose derived phase is not delivered: the
repo root must be a linked worktree, or `<git-common-dir>/agentic-sessions/<feature>`
must exist (a claim, see `SESSION-ISOLATION.md`); otherwise it exits 1 with
`not isolated: …`. Isolate first, then run `next` again.

## Output: `docs/specs/<feature>.md`

The spec file is the source for phases 0–4 and carries the plan from its first
line. It starts with front matter:

```yaml
---
feature: <slug>
repo: <owner/name>
branch: <branch>
tracker: <URL, #N or ticket key>   # may wait until phase 1
---
```

### `## Phase plan`

One row per phase 1–6, decision `run` or `skip`, a reason for every `skip`, and
an `Approved:` line: the date, then the owner's answer in chat, quoted.

```markdown
## Phase plan

| Phase | Decision | Reason |
|---|---|---|
| 1 · Design & alignment | run | — |
| 2 · Product review | skip | CLI only, no UI |
| 3 · System architecture | run | — |
| 4 · Tickets | run | — |
| 5 · Program design | run | — |
| 6 · Implement-loop | run | — |

Approved: 2026-11-16, owner in chat — "<the owner's answer, verbatim>"
```

### `## Plan amendments` (optional)

Any later change to the plan is a row, with its own date and quoted owner answer.

```markdown
## Plan amendments

| # | Change | Reason | Approved |
|---|---|---|---|
| 1 | phase 3 run | the feature adds stored data | 2026-11-17, owner in chat — "<answer>" |
```

`Change` is `phase <N> <run|skip>`.

## Other sections and rows

Section headings by phase (names only): 1 `## Problem Statement`, `## Solution`,
`## User Stories`, `## Premises`, `## Model` (with `### Lifecycle inventory`);
2 `## Product review`; 3 `## System Architecture` (with `### System invariants`);
4 `## Tickets`; 6 `## Delivery`.

The ticket references of a `## Tickets` item are those before its first ` — `
(the whole line when there is none). A Delivery row reads, one per ticket:

```markdown
- <refs> — PR: <URL> · Model: <path | n/a — reason> · Mutation: <path | n/a — reason>
```

`Model:` and `Mutation:` may be `n/a`, and only as `n/a — <reason>`.

Whether an output is valid is decided by `python3 ~/agentic-dev-kit/wf.py next <feature>`; its FAIL lines name what is missing. This file does not restate the checks.
