# sb-skills

My agent skills collection.

## Skills

### `implement-loop`

Process a batch of work items: implement each via a TDD subagent, review via a reviewer subagent, fix until clean, then create a PR. Issue-tracker-agnostic.

```
/skill:implement-loop
```

### `tightrope` — DEPRECATED (2026-08-30)

> **Deprecated.** Folded into `/implement-loop`: the ponytail-vs-engineering
> tension is now its **third review axis**, and the test / lint / PR gate is
> its **pre-push gate**. The skill folder stays as a historical record
> (`disable-model-invocation: true`), it is no longer installed and no longer
> documented as a command.
>
> If you were reaching for `/tightrope`, run `/implement-loop` on the same
> items instead. The ponytail axis is `ponytail-review`; the engineering axes
> are `code-review` (Standards + Spec); the criteria-family taxonomy lives in
> `~/agentic-workflow/WORKFLOW.md` phase 6. The rule change is recorded in
> `RATIONALE.md` D-010 (folded into the review) and D-011 (the gate moves into
> implement-loop).


### `product-review` ⚠️ WIP

> **Work in progress.** First version, not yet battle-tested.
>
> Produce a visual HTML review of a PRD — problem statement, success criteria,
> and wireframe mockup — to align on what the user sees before designing
> architecture. Designed to be used between `/to-spec` and `/system-architecture`.

```
/product-review <prd-number>
```

### `system-architecture` ⚠️ WIP

> **Work in progress.** First version, not yet battle-tested.
>
> Design system architecture for a PRD: produce Mermaid sequence/state
> diagrams, endpoint contracts, and data models, then append them to the
> PRD on the tracker. Designed to be used between `/to-spec` and `/to-tickets`.

```
/system-architecture <prd-number>
```

### `program-design` ⚠️ WIP

> **Work in progress.** First version, not yet battle-tested.
>
> Produce program design artifacts (call-stack tree, file-tree diff, type
> signatures) for a ticket and append them to the ticket on the tracker.
> Designed to be used between `/to-tickets` and `implement-loop`.

```
/program-design <ticket-number>
```

## Install

### omp

omp reads skills from `~/.agents/skills/`. Copy the skill folders (`tightrope`
excluded — deprecated):

```bash
cp -r ~/sb-skills/implement-loop ~/sb-skills/product-review \
      ~/sb-skills/program-design ~/sb-skills/system-architecture \
      ~/.agents/skills/
```

Or symlink them instead, so the repo stays the single source of truth:

```bash
ln -s ~/sb-skills/implement-loop ~/sb-skills/product-review \
      ~/sb-skills/program-design ~/sb-skills/system-architecture \
      ~/.agents/skills/
```

A deprecated skill that is still installed is still discoverable: delete
`~/.agents/skills/tightrope` if it is there from an earlier install.

### Claude Code

Symlink the skills you want into `.claude/skills/`:

```bash
# In your project or globally
mkdir -p ~/.claude/skills
ln -s ~/sb-skills/* ~/.claude/skills/
```

Or reference them in `CLAUDE.md` / `CLAUDE_GLOBAL.md`:

```markdown
See skills in ~/sb-skills/ for reusable workflows.
```
