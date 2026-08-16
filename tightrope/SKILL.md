---
name: tightrope
description: Gate code changes against the tightrope between ponytail minimalism and engineering soundness — tension check, test, lint, PR gate.
disable-model-invocation: true
---

# tightrope

`tightrope` is a local gate that validates code changes walk the tightrope
between **ponytail minimalism** and **sound engineering discipline** — the
code is neither overengineered nor underengineered. It drives a pipeline
(tension check → test → lint → PR gate) before changes reach upstream.

The **tightrope** is the Pareto frontier: the point where making the code more
minimal would sacrifice engineering quality, and making it more engineered
would add unnecessary complexity. The agent finds this point by running two
authoritative tools — `/ponytail-review` for minimalism, and `/code-review`
(Pocock's two-axis review) for engineering soundness — then interprets both
reports and decides what to escalate.

When the user invokes `/tightrope`, report the outcome at the end. If the user
asks for something specific, translate that into the matching flag — for
example, "skip lint" becomes `--skip=lint`, "lean toward minimalism" becomes
`--lean ponytail`.

## Two ways to invoke

`/tightrope` works in two modes, depending on whether the user hands you a
task along with the command:

- **Validate-only** — bare `/tightrope` (optionally with flags like
  `--skip lint` or `--lean ponytail`). The user's code changes are already
  committed; validate them and report the outcome.
- **Task-first** — `/tightrope <task>`, e.g.
  `/tightrope add a --json flag to the status command`. First carry out the
  task yourself, then validate through the pipeline:
  1. **Check scope.** Inspect `git status` before you change or commit
     anything. Preserve unrelated pre-existing uncommitted changes; commit
     only what belongs to the user's task.
  2. **Do the work.** Make the changes the task describes, then **commit them
     on a feature branch**. If the user is on the repository's default branch,
     create a feature branch first.
  3. **Then validate**, passing the user's task as your `--intent`.

Everything below — preconditions, the tension check, the pipeline stages —
applies the same way once the work is committed on a feature branch.

## Preconditions

Before the pipeline runs, verify:

- The work must be **committed** on a branch. The gate validates committed
  history, not uncommitted changes.
- You must be on a **feature branch**, not the repository's default branch.
- **Ponytail must be installed.** `ponytail-review` must be a recognized skill
  (from github.com/DietrichGebert/ponytail — e.g. in `.agents/skills/`). If
  `/ponytail-review` isn't a recognized command, install it before proceeding.

## Flags

Pass flags on the invoke line:

| Flag | Effect |
|---|---|
| `--lean ponytail` | Resolve tensions in favor of minimalism: when ponytail says "remove it" and engineering says "add it", trust ponytail. |
| `--lean engineering` | Resolve tensions in favor of soundness: when engineering says "add it" and ponytail says "remove it", trust engineering. |
| `--skip <stage>` | Skip one or more pipeline stages (e.g. `--skip lint` or `--skip test,lint`). |
| `--intent "<text>"` | What the user set out to accomplish — see below. |

When neither lean flag is set, the agent resolves non-conflicting findings
automatically and escalates only `tension` findings.

## Intent is required

When you start a run you must pass `--intent`: **what the user set out to
accomplish** — the goal or request behind this work, in their terms. This is
not a description of the diff or the files you changed; it is the objective
the change is meant to achieve. You know it from the conversation, so pass it
directly.

Err on the side of completeness, not brevity. The tension check uses
`--intent` to tell a deliberate design choice apart from a mistake. Capture
the nuance: the user's goal, the specific decisions and tradeoffs they made
along the way, any constraints or approaches they ruled in or out, and
anything they explicitly asked for that might otherwise look surprising in
the diff.

## Pipeline stages

The pipeline runs in order. Each stage blocks before the next begins.

### 1. Tension check

Run two authoritative tools in parallel, then interpret their output:

**Axis A — Ponytail minimalism.** Run `/ponytail-review` against the current
diff. It returns a structured delete-list with tags: `delete`, `stdlib`,
`native`, `yagni`, `shrink`, and a net lines-removable count. If the report
says "Lean already. Ship.", axis A finds nothing to flag.

**Axis B — Engineering soundness.** Run `/code-review`'s two-axis
methodology (Standards + Spec) against the same diff. It checks documented
coding standards and Fowler code smells, plus whether the change faithfully
implements the originating spec or intent.

#### Agent interpretation

Both axes are **criteria families** (`~/agentic-workflow/WORKFLOW.md` →
`## Review criteria family`). Every finding must quote **evidence** — a
verbatim excerpt from the diff — and carry a **severity** (`fatal` /
`advisory`). A finding without evidence is not accepted. Order the
criteria by severity before deciding: `fatal` findings are escalated,
never auto-resolved.

Read both reports and classify every finding into one of three buckets:

- **Resolved automatically** — clear-cut findings that match the user's
  `--lean` preference, or findings where one axis finds nothing while the
  other's findings are minor (single-line lint noise, trivial stdlib
  replacements). Apply the fix or note it and move on.
- **Escalated to user** — findings that need human judgment:
  - A `tension` between the two axes on the same concern (ponytail says
    "remove this abstraction" and engineering says "this needs tests" —
    the user decides which way to lean).
  - A finding that contradicts the user's explicit `--lean` preference.
  - A finding severe enough that auto-resolving it would be presumptuous
    (e.g. removing a guard at a trust boundary).
- **Noted as advisory** — genuine but low-priority findings that should be
  recorded but don't block the pipeline (e.g. "this could be one line"
  suggestions that don't affect correctness).

The decision process is:

1. If both reports find nothing or only minor advisory items → pass.
2. If one report has findings and the other is clean → the agent resolves
   non-severe findings automatically. If a finding is severe (e.g. data-loss
   risk, security hole), escalate it.
3. If both reports flag the same code in opposite directions → **tension**.
   Present both sides to the user and ask how to resolve.

**Completion criterion**: both reports are read, every finding is classified
into one of the three buckets, every accepted finding carries verified
evidence, and all actionable non-escalated findings are resolved.

### 2. Test

Run the project's tests. For now only **Python** is supported.

The agent determines the right test command from the project's conventions
(`pytest`, `unittest`, `tox`, etc.). The user can express scope preference:

- Default: run **unit tests only**.
- `--test-scope integration` — also run integration tests.
- `--test-scope all` — run unit, integration, and e2e tests.

The test step is **deterministic**: the same project with the same scope
produces the same command. The agent reads `pyproject.toml`, `tox.ini`,
`setup.cfg`, or `pytest.ini` to find the canonical test configuration, then
runs it.

**Completion criterion**: all tests in the selected scope pass. If tests
fail, report the failures and stop — do not proceed to lint until the user
fixes them and re-invokes.

### 3. Lint

Run the project's linter. The agent reads `pyproject.toml` or `ruff.toml`
(prefer ruff), `setup.cfg` (flake8), or `.pylintrc` to find the canonical
linting configuration, then runs it.

**Completion criterion**: lint passes with zero errors. If the linter finds
issues, report them and stop — do not proceed to the PR gate until they are
resolved.

### 4. PR gate

Create a PR from the feature branch and monitor it through CI and merge.

Push the branch, create a PR with the `--intent` text as the description
(plus the pipeline outcome summary), then wait for CI to pass. Do not merge
yourself — tell the user the PR is ready and ask them to review and merge it.
Report the PR link.

**Completion criterion**: CI is green and the PR is handed off to the user
for review. Do not wait for the merge.

## Validate and decide

The agent only stops to ask when something genuinely needs your judgment.
Most findings are resolved automatically; you only see escalations.

When the agent escalates, it presents the finding with both sides of the
conflict and asks for your decision:

> **Tension**: ponytail says "remove the Pydantic model — three fields,
> one caller, use a dict" — engineering says "this is a public API boundary,
> the model documents the schema for consumers"
>
> How should this land? (approve ponytail / approve engineering / skip)

Your options:

| You say | Meaning |
|---|---|
| "approve ponytail" | Accept the ponytail finding. Remove or reject the flagged code. |
| "approve engineering" | Accept the engineering finding. Keep or add the flagged code. |
| "fix \<detail\>" | Apply a specific fix and re-check. |
| "skip" | Leave the finding unresolved and move to the next stage. |

### Automatic resolution with `--lean`

When `--lean ponytail` is active, the agent resolves tensions in favor of
removing code without asking: ponytail-violation findings are applied,
engineering-gap findings on the same concern are noted as advisory.

When `--lean engineering` is active, the agent resolves tensions in favor of
adding code without asking: engineering-gap findings are applied,
ponytail-violation findings on the same concern are noted as advisory.

When both axes flag the same code independently (not in conflict), each
finding is still resolved on its own merit — `--lean` only breaks ties
between the two axes on the same concern.

If the agent finds nothing to escalate, it reports that the tension check
passed and moves directly to the next stage without stopping.

## Outcome

The pipeline produces one of these outcomes:

| Outcome | Meaning |
|---|---|
| `checks-passed` | Tension check, tests, and lint all passed. PR is open and CI is green. Hand off to the user for merge review. |
| `passed` | Everything passed and the PR was merged or closed. |
| `failed` | A stage failed. Report what failed and what the user needs to fix. |
| `cancelled` | The user aborted the run. |

On `checks-passed` or `passed`, summarize what happened: the tension findings
(how many on each axis, which were resolved automatically and which needed
the user), test results, lint results, and the PR link.

On `failed`, do not leave the user without direction — explain what failed,
what to fix, and suggest the next `/tightrope` command. Fix the issue, commit
on the same feature branch, then run `/tightrope` again.
