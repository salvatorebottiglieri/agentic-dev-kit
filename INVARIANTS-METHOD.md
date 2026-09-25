# Invariants Method — theory and process

> How to define a system's invariants and use them to make tests effective.
> Method distilled from a real stress campaign and integrated into the
> workflow: the `## System Invariants` section produced by
> system-architecture and consumed by implement-loop.
>
> Date: 2026-08-05 · Applicable to any project.

---

## 1. What an invariant is

An **invariant** is a property that must hold in *every* reachable state of
the system — not in one example, but in all. Formally: if $S$ is the set of
states and $P$ a property,

$$\forall s \in S: P(s)$$

The difference from the "classic" test is **universal quantification**: the
test does not verify *one* case ("derive this node and check it is
auto-verified"), but *all* nodes present ("for every node with depth>0
there exists an outgoing `derived_from` edge").

**Example test vs invariant**

| | Example test | Invariant |
|---|---|---|
| Verifies | one scenario | a law |
| Quantification | none (one case) | universal (all states) |
| Covers | what you wrote | the whole state space |
| Fails when | that scenario breaks | the law breaks anywhere |

A passing scenario says nothing about the scenarios not written; a law, if
true, covers everything.

### The four categories

| Category | Example | Where it lives |
|---|---|---|
| **Structural** (data) | "every node with depth>0 has a derived_from edge" | DB constraints + tests |
| **Transition** (state machines) | "draft→auto-verified only when checks pass" | CHECK constraint + tests |
| **Idempotence** (commands) | "render(render(x)) = render(x)" | byte-identical tests |
| **Boundary** (seams) | "the fetcher never stores junk as node content" | contract tests |

---

## 2. Why the bugs lived at the boundaries (seam theory)

All 4 bugs found in the example-project campaign — every one — lived at the
**boundaries** of the system, not in its core:

- **Plugin seam** (agent): whoever writes a plugin can return anything. The
  caller assumed `deriv.prose` without checking its type → unhandled
  traceback on an out-of-schema response.
- **Network seam** (fetcher): hostile HTML, JS-only pages, redirect
  wrappers. The fetcher confused *payload* with *content* (an 11MB JS
  bundle stored as a node's text).
- **Triplicated logic**: the confidence cascade existed in 3 copies
  (synthesize + backfill + contradiction propagation), one correct and two
  with different bugs. Duplicated domain logic is a *latent bug by
  construction*: whoever copies the logic copies the bugs.

**Heuristic rule**: ~90% of a new feature's bugs live at its boundaries. If
your tests only cover the happy internal path, you are testing the part
that works.

---

## 3. The process in 7 steps

### Step 0 — The laws before the code

Invariants are **derived from the spec/ADR** before writing the feature. For
each feature ask:

> "What must be **always** true, in every reachable state?"

If you cannot phrase the answer in one sentence, you do not understand the
feature. Derivation rules:

- Touches the data model → structural invariants.
- Introduces states → state machine and legal transitions.
- Introduces a command → idempotence and non-destructiveness.
- Touches a boundary (network, plugin, filesystem) → the contract.

### Step 1 — Formalize: write the negation

Each law becomes a predicate, and for each one write **the negation** — the
negation becomes the query/assert of the test:

| Law | Negation → query |
|---|---|
| "Every node above L0 has provenance" | `SELECT … WHERE depth>0 AND NOT EXISTS(edge)` |
| "Depth = max(parent)+1" | `GROUP BY child HAVING depth != MAX(parent.depth)+1` |
| "No dangling targets" | `SELECT e.to WHERE NOT EXISTS(node)` |
| "Render is idempotent" | byte-identical diff of two runs |

Careful: formalization is an act of design, not transcription. Depth is
*max* of the parents, not of each single parent — the per-edge version
looks right and breaks on the first synthesis with mixed-depth parents.

### Step 2 — Quantify universally

The invariant test **enumerates all states**, it does not sample:

```python
# ❌ example test
def test_derivation_has_provenance():
    node = derive_one()
    assert node.has_provenance()

# ✅ invariant
def test_every_node_has_provenance(store):
    build_campus(store)                      # setup: minimal complete graph
    for node_id, depth in q(store, "SELECT id, depth FROM node"):
        if depth <= 0: continue
        assert has_outgoing_provenance(node_id)
```

The `for` over all nodes is the universal quantification. The setup builds a
*rich* state (2 L0 → derive → synthesize = 6 nodes, 6 edges) so the
quantification has something to iterate.

### Step 3 — The killer: vacuously green tests

A test whose query is wrong and selects 0 rows **passes without verifying
anything** — worse than a red one: it gives false confidence. Defenses:

1. **Assert the setup**: `assert by_child, "no provenance edges found"` —
   an invariant over an empty system is meaningless.
2. **Mutation testing**: after writing the invariant, *deliberately break
   the code* (invert a condition, swap `from_node` for `to_node`) and check
   the test goes red. If it does not, the test verifies nothing. Once, at
   write time.
3. **Corpus audit**: run the same queries against real data — the proof
   that the invariant is not only consistent with the code, but true on
   real data.

### Step 4 — Separate model from intent

Two questions, two suites:

- **Invariants**: "does the system lie to itself?" → internal consistency.
  They must be **green from day one** (they pin existing contracts) and
  **refactor-proof** (they test behavior, not implementation).
- **Stress/adversarial**: "does the system do what it must?" → intent.
  These **are born red** (they are the campaign findings) and turn green
  with the fix.

Mixing the two loses both: a red invariant is a bug, a green stress test is
a non-finding.

### Step 5 — Defend the boundaries

For each **seam**, test the contract from the caller's side:

- **Plugin** → a fake that returns garbage, one that throws, one *flaky*
  (fails N times then succeeds) for retry. Contract: "the caller never
  crashes, whatever the plugin returns".
- **Network/fetcher** → hostile HTML, JS-only pages, redirects. Contract:
  "junk never becomes content".
- **CLI** → exit codes, JSON on stdout/stderr, idempotence.

### Step 6 — Anchor them to real data

When you find a bug, the first thing is to **freeze the input that triggers
it** as a fixture, then write the fix. A real fixture is stronger than a
synthetic one: it is *proof the bug exists*, not an approximation of it.

### Step 7 — Place and link

Each invariant test has:

- **A canonical home** (no scattered tests).
- **A reference to its law** in the docstring (`ADR-0004`, `rule D2`,
  issue #112) — the test says *what* it protects and *why*.
- **A place in CI** that runs the whole directory.

---

## 4. Consistency vs intent (the subtlest distinction)

**Invariants** verify the model's internal consistency; **bugs** are
violations of *intent*, not of the model. Storing 11MB of JS as content is
*perfectly consistent* with the data structure (an extracted node with a
content_path to a file) — yet wrong relative to what the system *should*
do.

A model can be consistent and wrong at the same time. Invariants tell you
"the system does not lie to itself"; adversarial tests tell you "the system
does what it must". That is why the campaign had **two axes**: invariants
alone would have left the 4 bugs buried — the corpus was "clean" with
respect to the laws but polluted with respect to intent.

---

## 5. Reusable checklist for every feature

1. [ ] Have I phrased the laws as sentences ("it must always be true
       that…") **before** writing the code?
2. [ ] Does every law have its negation turned into a query/predicate?
3. [ ] Do the invariants quantify over **all** states (or the richest
       buildable setup)?
4. [ ] Have I **asserted the setup** (no quantification over empty sets)?
5. [ ] Have I **mutation-tested** (broken the code → test red) at least
       once per invariant?
6. [ ] Have I separated invariants (green from day one, refactor-proof)
       from stress (red at birth, then fixed)?
7. [ ] Have I tested the **boundaries** (plugin, network, CLI) with hostile
       input + failure injection?
8. [ ] Do the found bugs have a **fossil fixture** from real data?
9. [ ] Does every test cite its law (ADR/rule/issue) and live in its
       canonical home?

---

## 6. Integration into the workflow

Two layers (details in `INVARIANTS.md` and `WORKFLOW.md`):

1. **Per-feature** — system-architecture appends `## System Invariants` to
   the PRD (law → negation → where verified format, anti-vacuity rule);
   implement-loop writes their tests first (red → green); the Spec review
   verifies coverage.
2. **Transversal** — the workflow itself is subject to invariants (I1–I6 in
   `INVARIANTS.md`), verified by `audit.py` in any project, with mutation
   tests guaranteeing every check can fail.
