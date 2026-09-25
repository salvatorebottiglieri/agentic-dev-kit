# agentic-dev-kit

[![version](https://img.shields.io/github/v/tag/salvatorebottiglieri/agentic-dev-kit?label=version&sort=semver)](CHANGELOG.md)
[![license: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An opinionated, phase-by-phase workflow for building software with an AI
coding agent — from a raw idea to a merged PR — plus the custom skills and the
audit gate that enforce it.

The method is **agent-agnostic**: `WORKFLOW.md` is plain process, readable by
any agent. The skills ship in the portable *agent-skills* (`SKILL.md`) format,
which Claude Code loads natively and other agents can adopt.

`WORKFLOW.md` is the entry point: it defines the pipeline every session
follows (design → product review → system architecture → tickets → program
design → implement-loop) and the review criteria family that gates a merge.

## What's in here

| Path | What it is |
|---|---|
| `WORKFLOW.md` | **Start here.** The operational spec — the phases, in order, and the rules. |
| `INVARIANTS.md` / `INVARIANTS-METHOD.md` | The workflow invariants (I1–I6) the audit enforces, and the theory behind them. |
| `RATIONALE.md` | The *why* of every load-bearing rule. |
| `skills/` | The four custom skills the workflow invokes (`implement-loop`, `product-review`, `system-architecture`, `program-design`). |
| `audit.py` + `tests/` | The session-end gate: verifies the invariants against the tracker. |
| `handoffs/` | Where your own session handoffs land (starts empty). |

## Two layers — mind the prerequisite

This kit is a **layer on top of** [Matt Pocock's skills](https://github.com/mattpocock/skills)
(MIT). The backbone flow it orchestrates — `grill-with-docs`, `to-spec`,
`to-tickets`, `implement`, `tdd`, `code-review`, `ponytail-review`,
`domain-modeling`, `ask-matt` — lives there, not here. This repo adds the
orchestration doc (`WORKFLOW.md`), the invariant gate (`audit.py`), and four
skills that slot into the flow.

**Install the base layer first**, then this one:

```bash
# 1. base layer — Matt Pocock's skills (see that repo's install steps)
#    https://github.com/mattpocock/skills

# 2. this kit
git clone https://github.com/salvatorebottiglieri/agentic-dev-kit.git ~/agentic-dev-kit
cd ~/agentic-dev-kit
./install.sh
```

`install.sh` symlinks the four skills from `skills/` into your agent's skills
directory and leaves the docs and `audit.py` in the clone. It defaults to
`~/.claude/skills` (Claude Code); point it elsewhere for any other agent:

```bash
AGENT_SKILLS_DIR=~/.agents/skills ./install.sh
```

It never overwrites a skill you already have without asking.

## Using it

Have your coding agent read `~/agentic-dev-kit/WORKFLOW.md` at the start of any
non-trivial coding work — the usual way is a rule in the agent's global or
project instructions file (`CLAUDE.md`, `AGENTS.md`, or your agent's
equivalent). Then follow its phases. A typo fix, a one-line config change, or a
question about code does not need the pipeline.

## Caveats

- **`audit.py` is GitHub-only** (it reads issues/PRs via the `gh` CLI). On
  GitLab, Bitbucket, or a local tracker the session-end gate is manual — the
  handoff is still the auditable artifact.
- The skills use the `SKILL.md` + `skill://` invocation convention. Agents
  that don't support it can still follow `WORKFLOW.md` and read each
  `skills/<name>/SKILL.md` as a plain instruction file.
- The custom skills marked *WIP* in their own `SKILL.md`
  (`product-review`, `system-architecture`, `program-design`) are first
  versions, not yet battle-tested.

## Versioning

Released under [Semantic Versioning](https://semver.org/); see
[`CHANGELOG.md`](CHANGELOG.md) and the git tags. Pin a specific version by
checking out its tag (e.g. `git checkout v0.1.0`).

## License

MIT — see `LICENSE`. The base-layer skills are Matt Pocock's, under his repo's
MIT license — not redistributed here; install them from the upstream.
