# agentic-dev-kit

> **Generated repository.** This kit is exported from the canonical workflow
> source by `sync_kit.py`. Do not edit it by hand; changes go to the canonical
> `kit/` source and are exported.

[![version](https://img.shields.io/github/v/tag/salvatorebottiglieri/agentic-dev-kit?label=version&sort=semver)](CHANGELOG.md)
[![license: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An opinionated, phase-by-phase workflow for building software with an AI
coding agent — from a raw idea to a merged PR — plus the custom skills and the
audit gate that enforce it.

The method is **agent-agnostic**: `WORKFLOW.md` is plain process, readable by
any agent. The skills ship in the portable *agent-skills* (`SKILL.md`) format,
which Claude Code loads natively and other agents can adopt. `WORKFLOW.md` is
the entry point: it defines the pipeline every session follows (design → product review → system architecture → tickets → program
design → implement-loop) and the review criteria family that gates a merge.

## What's in here

| Path | What it is |
|---|---|
| `WORKFLOW.md` | **Start here.** The operational spec — the phases, in order, and the rules. |
| `INVARIANTS.md` / `INVARIANTS-METHOD.md` | The workflow invariants (I1–I6) the audit enforces, and the theory behind them. |
| `RATIONALE.md` | The *why* of every load-bearing rule. |
| `skills/` | The custom skills the workflow invokes (`implement-loop`, `product-review`, `system-architecture`, `modular-monolith`, `program-design`, `uml-modeling`). |
| `docs/GATES.md` | Gate configuration, `final` mode, bypasses and known limits. |
| `wf.py` + `report_store.py` | The phase harness (`wf.py next <feature>` prints and gates each phase) and the report store. |
| `app/` | The desktop window: every feature, its phase and its documents. See `app/README.md`. |
| `gen-flow.py` + `FLOW.md` | The map of the method, generated from `WORKFLOW.md` and checked by `gen-flow.py --check`. |
| `audit.py` + `tests/` | The session-end gate: verifies the invariants against the tracker. |
| `handoffs/` | Where your own session handoffs land (starts empty). |

## Two layers — mind the prerequisite

This kit is a **layer on top of** [Matt Pocock's skills](https://github.com/mattpocock/skills)
(MIT). The backbone flow it orchestrates — `grill-with-docs`, `to-spec`,
`to-tickets`, `implement`, `tdd`, `code-review`, `ponytail-lean`,
`domain-modeling`, `ask-matt` — lives there, not here. This repo adds the
orchestration doc (`WORKFLOW.md`), the invariant gate (`audit.py`), and the custom
skills that slot into the flow.

**Install the base layer first**, then this one:

```bash
# 1. base layer — Matt Pocock's skills (see that repo's install steps)
#    https://github.com/mattpocock/skills

# 2. this kit
git clone https://github.com/salvatorebottiglieri/agentic-dev-kit.git ~/agentic-dev-kit
cd ~/agentic-dev-kit
AGENT_SKILLS_DIR=~/.agents/skills ./install.sh
```

`install.sh` symlinks the custom skills from `skills/` into the agent-neutral
store `~/.agents/skills`, where `wf.py` looks for them (`$AGENT_SKILLS_DIR`
overrides it for both). Without that variable it links into `~/.claude/skills`,
which `wf.py` does not read. It never overwrites a skill you already have
without asking. Claude Code reads its own directory, so point it
into the store:

```bash
mkdir -p ~/.claude/skills; for s in ~/.agents/skills/*; do [ -e ~/.claude/skills/${s##*/} ] || ln -s ../../.agents/skills/${s##*/} ~/.claude/skills/${s##*/}; done
```

## Using it

Have your coding agent read `~/agentic-dev-kit/WORKFLOW.md` at the start of any
non-trivial coding work: a rule in its instructions file (`CLAUDE.md`,
`AGENTS.md` or equivalent). A typo fix, a one-line config change, or a question
about code does not need the pipeline.

## The harness and the window

`python3 ~/agentic-dev-kit/wf.py next <feature>` prints and gates each phase
(`end`, `report` and `post-spec` close a session, store review reports and post
the spec to tickets). Its state lives in `~/.local/state/agentic-dev-kit`, or
`$XDG_STATE_HOME/agentic-dev-kit`. A phase skill that is not installed prints as
`MISSING SKILL <name>`: install it into `$AGENT_SKILLS_DIR`, else
`~/.agents/skills`. `python3 ~/agentic-dev-kit/app/desk.py serve` plus any
browser shows the window. Only the server and a browser are needed; the Edge and
terminal buttons, `gh` (for `wf.py end` and `post-spec`), `--remote-host` and
`--jira-url` are optional: see [`app/README.md`](app/README.md). `LESSONS.md` is
optional and not shipped: put your own next to `WORKFLOW.md` and phase 0 prints it.

The kit's own tests: `python3 -m pytest -q` from the clone (`node` is optional,
for the panel tests). The mutation prover, `guards/mutation_prover.py`, is run as
`phases/6-implement-loop.md` says.

## Model

`uml-modeling` produces the per-feature model in `docs/models/<feature>.md`: a
flow, the interaction sequences, and a state machine for every entity whose
lifecycle moves in time. Its `## Elements` table ties every diagram ID to code,
and `check_model.py --gate` verifies that the skeleton still exists (Python
built in; other languages pass `--facts`). Default-on, skippable with a reason
(`Model: n/a — <reason>` in the PR).

## Gates

`install.sh --gate <repo>` installs two repository-local git hooks, with no
global config: a **pre-commit** gate that computes skipped tests and stale
pins into `.workflow/gate-report.md`, and a **pre-push** gate over
`docs/design/` and, when present, `docs/models/`. Configuration, `final` mode,
bypasses and known limits: [`docs/GATES.md`](docs/GATES.md).

## Caveats

- **`audit.py` is GitHub-only** (it reads issues/PRs via the `gh` CLI). On
  GitLab, Bitbucket, or a local tracker the session-end gate is manual — the
  handoff is still the auditable artifact.
- The skills use the `SKILL.md` + `skill://` invocation convention. Agents
  that don't support it can still follow `WORKFLOW.md` and read each
  `skills/<name>/SKILL.md` as a plain instruction file.
- The custom skills marked *WIP* in their own `SKILL.md`
  (`product-review`, `system-architecture`, `program-design`, `uml-modeling`) are
  first versions, not yet battle-tested.

## Versioning

Released under [Semantic Versioning](https://semver.org/); see
[`CHANGELOG.md`](CHANGELOG.md) and the git tags. Pin a specific version by
checking out its tag (e.g. `git checkout v0.6.0`).

## License

MIT — see `LICENSE`. Third-party code (the vendored Mermaid and marked bundles):
[`NOTICE`](NOTICE). The base-layer skills are Matt Pocock's, under his repo's MIT
license, not redistributed here.