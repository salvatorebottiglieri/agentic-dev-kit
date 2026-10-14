# Session isolation — one writer per working tree

> Part of the agentic workflow. Index and reading order: `WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

A git working tree is per repo, and it is what two agentic sessions fight
over. Observed failure (2026-09-19, `a-data-pipeline`): a second
session could not change branch without moving the first session's working
tree, `git stash` — repo-global, shared by every worktree — was cycled
between the two, and a commit landed on the other session's feature branch
(`handoffs/handoff-<feature>-20260919.md`, deviations 1 and
4).

**At most one writer per working tree.** The default is a worktree per
session: a session's working tree is part of its launch context, decided
before it writes. The primary tree is available only to a session that has
checked that nobody else is there — and claimed it (below).

**Isolation is entered on evidence, not on assumption.** On entering a repo,
before the first write, the session reads three observable signals:

- the primary working tree is dirty with work the session did not produce;
- the repo has a worktree other than the primary tree;
- a **live claim** exists (below).

Any one of them → the repo is shared, and the session works in its own
worktree: `git worktree add ../<repo>-<feature> <base>`, a sibling directory
named after the session's `<feature>` slug (the same slug as its handoff),
never inside the repo (a nested worktree is untracked content in the
session's own diff). None of them → it may work in the primary tree, and it
then **claims** it.

**Claims.** Deciding to work in the primary tree is followed by writing
`<git-common-dir>/agentic-sessions/<feature>` — `git rev-parse
--git-common-dir` is the same directory for every worktree of the repo, so
every session sees it — holding the slug and the start time, and by removing
it at session end (part of the session-end gate, see `WORKFLOW.md` § General rules). Reason:
cleanliness is not exclusivity — a session that has committed and pushed is
clean while still working, so "clean, no other worktree" is not proof that
nobody else is there. A claim left behind by a session that died is treated
as live: a stale claim costs a worktree, a missed collision costs a commit.

**Cleanup at session end — the tree a session opens, it closes.** Worktrees
accumulate: observed 2026-09-21 on `a-shared-lib`, nine of them
(`handoff-<feature>-20260921.md`, deviation 3), every one a false
"the repo is shared" signal for every later session, which then isolates for
nothing. **Two species, and the rule covers both:**

- *workflow worktrees* — `../<repo>-<feature>`, created by this rule;
- *harness worktrees* — `<repo>/.claude/worktrees/agent-<hash>` on branches
  `worktree-agent-<hash>`, created by the agent harness, **nested inside the
  repo** in violation of the rule above and therefore never cleaned by it.
  These are the ones that pile up.

**Removal is on evidence, never on assumption**, in this order:

1. **The session's own worktree is always dealt with.** Tree clean *and*
   `git merge-base --is-ancestor HEAD <base>` → `git worktree remove` plus
   `git branch -D` of its branch. Otherwise it stays, and the handoff names it
   and why.
2. **Another session's worktree is removed only on all three:** no live claim
   for it, tree clean, and HEAD reachable from the base branch. Five of the
   nine of 2026-09-21 were in this case; the other four were not, which is
   what the next point is for.
3. **Dirty, or carrying a commit the base cannot reach → never silently
   dropped.** Archive first (`git format-patch` for the commits, `git diff` for
   the dirty tree), then remove, and the handoff says where the patch went and
   what the evidence for disposability was. "Its content shipped under another
   commit" is a claim to *verify* — grep the base branch for the change — not
   to assume from a rebase.
4. Finish with `git worktree prune`, and remove `.claude/worktrees/` when it is
   left empty — an empty directory still reads as a worktree parent to the next
   session scanning by path.

The asymmetry with the claim rule is deliberate and runs the other way: for
*entering*, a stale claim is treated as live (cheap to be wrong); for
*removing*, evidence is required on every one of the three conditions (a lost
commit is not cheap).

**Never `git stash` to coordinate with another session.** `refs/stash` is
shared by every worktree of a repo and invisible to the other session, so
your parked work reads to them as "residuo di una sessione precedente" — and
theirs to you. Park work by committing it to your own branch.

**Handoff.** The repo-state line names the working tree the session ran in —
primary tree (with its claim) or the worktree path — so the isolation is
auditable after the fact (I5). Deliberately not an `audit.py` check: no
tracker artifact records whether a session *should* have isolated, so a check
would be unfalsifiable (see `INVARIANTS.md`, "Not auditable").
