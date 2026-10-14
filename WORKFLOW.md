# Agentic Workflow — Operational Spec

> Canonical reference for the agentic workflow. Every session follows this.
> Edit these files to change session behavior — no system-prompt changes needed.
> **This file is the index**: read it at session start, then read only the
> file for the phase you are in. The files it points to are normative; the
> tables below are pointers, not a summary that overrides them.

Skills are installed where your agent loads them (Claude Code:
`~/.claude/skills/`) and are read via `skill://<name>`, which means **read
the file, don't invoke it**: the phase skills are user-invocation only
(in Claude Code: `disable-model-invocation: true`), so read the skill's
`SKILL.md` before executing that phase — never reconstruct a skill
from memory.

The tracker is the one configured for the repo (GitHub, GitLab, Linear,
or a local tracker — `/setup-matt-pocock-skills` configures it).

## Pipeline

For any non-trivial feature, proceed through the phases in order. Each phase
is a skill; read it via `skill://<name>` before executing it.
The phase file holds the phase's own rules.

| # | Phase | Skill(s) | Rules |
|---|---|---|---|
| 1 | Design & alignment | `grill-with-docs`, `domain-modeling`, `to-spec` | `phases/1-design-and-alignment.md` |
| 2 | Product review (only when the feature touches UI) | `product-review` | `phases/2-product-review.md` |
| 3 | System architecture (only when non-trivial) | `system-architecture` | `phases/3-system-architecture.md` |
| 4 | Tickets | `to-tickets` | below |
| 5 | Program design (near-always) | `program-design` | `phases/5-program-design.md` |
| 6 | Implement-loop | `implement-loop`, `code-review`, `ponytail-review` | `phases/6-implement-loop.md` |

Transversal files:

| File | Read when |
|---|---|
| `REVIEW-CRITERIA.md` | phase 6 — the main agent pastes the family into every reviewer task |
| `SESSION-ISOLATION.md` | on entering a repo, before the first write; and at session end (cleanup) |

### 4. Tickets

`skill://to-tickets` — splits the spec into tracer-bullet tickets with
blocking edges (native tracker links, or one file per ticket locally).

## Where each rule is enforced

Each load-bearing rule is held at one of three levels: **structure** (the
harness or the type system makes the violation impossible), **script** (a
check that runs and can fail — `audit.py`, CI, a test), **prompt** (text an
agent is asked to follow). A prompt-only rule holds only as long as the agent
reads and obeys it; those rows are the candidates for a scripted or
structural check.

| Rule | Where | Enforced by |
|---|---|---|
| Phases in order; read the skill before the phase | this file | prompt |
| Falsify the premises before publishing the spec | phase 1 | prompt |
| Phase-2 decision always stated in the session | phase 2 | prompt |
| Skipped phase annotated on the ticket (I3) | General rules | script (`audit.py` skip_annotations, warning) + prompt |
| System Invariants each with a test that can fail | phase 3 | prompt (executed by the mutation prover) |
| Program-design signatures are contract (I2) | phase 5 | prompt (judgment: Spec axis) |
| Main agent never edits code; fresh context per subagent; verbatim diffs | phase 6 | prompt |
| Review axes read-only; mutation prover only in a scratch worktree | `REVIEW-CRITERIA.md` | prompt |
| Evidence quote matches the diff verbatim (criterion 4) | `REVIEW-CRITERIA.md` | prompt |
| Every brief item answered, counted by the orchestrator (criterion 7) | `REVIEW-CRITERIA.md` | prompt |
| Zero actionable findings to exit the fix loop (I6) | phase 6 | prompt (judgment) |
| Pre-push gate: canonical test + lint | phase 6 | prompt |
| PR merged only after CI is green (I1) | phase 6 | script (`audit.py` ci_before_merge, after the fact) |
| Every configured quality gate runs in CI (I8) | `INVARIANTS.md` | script (`audit.py` configured_gates_run_in_ci) |
| PR links the issue (I4) | `INVARIANTS.md` | script (`audit.py` pr_links_issue) |
| At most one writer per working tree; claims | `SESSION-ISOLATION.md` | prompt (not auditable, see `INVARIANTS.md`) |
| Worktree cleanup at session end | `SESSION-ISOLATION.md` | prompt |
| Rule change carries a RATIONALE.md entry | General rules | prompt (audit check is a candidate in RATIONALE.md) |
| Session ends with the audit run | General rules | prompt |
| A handoff per session (I5) | General rules | script (`audit.py` recent_handoff) |
| Skipped tests listed in the commit by a tool, not reported by the agent | `README.md` ("Pre-commit gate") | script (`guards/gate.py`, git pre-commit hook, agent-independent) |
| Follow-ups in a handoff carry a tracker reference (I9) | `INVARIANTS.md` | script (`audit.py` followups_tracked) |
| A handoff names the command behind a result (I10) | `INVARIANTS.md` | script (`audit.py` handoffs_quote_commands, weak) |

## General rules

- Unsure which skill to use? Read `skill://ask-matt` (router).
- Skipped phases are never silent: the decision is stated in the session
  ("Phase N skipped — <reason>") and annotated on the ticket/spec:
  "Skipped — <reason>".
- Custom skills (implement-loop, product-review, program-design,
  system-architecture) live in `~/agentic-dev-kit/skills/` (repo:
  github.com/salvatorebottiglieri/agentic-dev-kit); installed copies in your
  agent's skills dir (e.g. `~/.claude/skills/`). `tightrope` is deprecated
  (folded into phase 6).
- Reference docs: `~/agentic-dev-kit/INVARIANTS.md` (workflow invariants I1–I6),
  `~/agentic-dev-kit/INVARIANTS-METHOD.md` (theory and process),
  `~/agentic-dev-kit/RATIONALE.md` (the why of every load-bearing rule).
- **Rule changes carry a rationale**: any change to a load-bearing rule in
  this file adds or updates an entry in `~/agentic-dev-kit/RATIONALE.md`
  (same session, citing the rule location). A rule without a rationale
  entry is an orphan.
- **Every session ends with the transversal gate** — including exploration-only
  sessions: (1) run `python ~/agentic-dev-kit/audit.py --repo <owner/repo>`
  and report the outcome in the final message; (2) write and git-commit the
  handoff `~/agentic-dev-kit/handoffs/handoff-<feature>-<YYYYMMDD>.md` (I5);
  (3) release the session claim, if the session claimed a primary tree
  (`<git-common-dir>/agentic-sessions/<feature>` — see `SESSION-ISOLATION.md`);
  (4) **clean up worktrees** per `SESSION-ISOLATION.md` § "Cleanup at session end":
  the session's own always, others only on evidence, anything dirty or
  unreachable archived and reported instead of dropped.
  The audit only sees tracker artifacts (issues, PRs, handoffs): work done
  outside the pipeline — local design docs, direct commits, exploration — is
  invisible to it; the handoff is what makes it visible and auditable.
- **"Session" means the whole conversation, not a turn.** The gate runs
  **once, at the end**, never mid-session: a handoff written after a single
  exchange documents a session that has not happened yet, and the next turn
  makes it wrong. While the work continues, the gate has not come due.

## Workflow invariants (transversal)

The workflow itself is subject to invariants, valid in **every** project:
`~/agentic-dev-kit/INVARIANTS.md` (I1–I6: CI-before-merge, signatures-as-
contract with documented deviations, skip annotations, PR links issue,
handoff per session in `~/agentic-dev-kit/handoffs/`, zero-findings exit).
Verified by `~/agentic-dev-kit/audit.py` — **mandatory
at the end of every session** (see General rules) and pre-merge in any repo:

```
python ~/agentic-dev-kit/audit.py --repo <owner/repo> [--since YYYY-MM-DD]
```

Every session ends with a handoff in `~/agentic-dev-kit/handoffs/`
(`handoff-<feature>-<YYYYMMDD>.md`), git-versioned, listing: the repo it
concerns (`- **Repo**: owner/name` — it is what lets the audit scope I9 to the
project), repo state, delivered work, verification, deviations (with where
they are documented), next session, ground truth. The audit's mutation tests
(`~/agentic-dev-kit/tests/test_audit.py`) guarantee every check can fail.
