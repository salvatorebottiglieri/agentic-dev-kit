# Review criteria family

> Part of the agentic workflow. Index and reading order: `WORKFLOW.md`. Moved verbatim from WORKFLOW.md on 2026-10-09 (R-001); only cross-file references were rewritten.

Every review and every pre-merge gate is a **family of small, orthogonal
criteria** — never a single vague judgement. A monolithic reviewer — one
prompt, several fuzzy questions, one boolean verdict — leaves too much room
for maneuver. Each criterion has:

1. **A defined scope** — one disjoint class of defects it is responsible
   for. Semantic overlap between criteria is resolved by assignment,
   written down in the families below — never by the reviewer.
2. **A falsifiable question per item** — each finding answers one
   question ("does this hunk violate a documented standard?") with a
   verdict, never a vibe.
3. **Pre-sliced evidence** — the code/repo supplies the candidate items
   (hunks, acceptance criteria, invariants); the reviewer gets a narrow
   brief, not the whole artifact.
4. **A structured, evidence-based finding** — verdict + evidence quote
   (what, where, the supporting excerpt). **No evidence = the finding is
   not accepted**, and the evidence is verified: the quote must match the
   file/diff verbatim. **Two species of evidence, and the claim decides
   which**: a claim *about the code* is carried by a verbatim quote; a
   claim *about test coverage* ("this is not pinned", "this test would
   catch a violation") is carried by an **execution** — the mutation and
   the suite's response — because a test that does not discriminate reads
   exactly like one that does (see criterion 7 and the Spec axis's
   invariant coverage).
5. **Severity** — every finding is `fatal` (blocks) or `advisory`
   (recorded, does not block). The gate keeps a pass-with-warning path:
   an all-or-nothing gate cries wolf and gets ignored.
6. **Dependency order** — criteria that consume another criterion's
   output run after it (Spec's invariant-coverage consumes the ticket's
   `## System Invariants`). The family is a DAG, not a flat fan-out;
   independent criteria run in parallel.
7. **Total coverage of the brief** — the report carries **one line per
   item of the pre-sliced brief** (every standard/ADR named, every
   acceptance criterion, every invariant), each with an explicit verdict,
   `pass` and `n/a — <reason>` included. **Silence is not a verdict**: in a
   free-form finding list, "conforms", "not applicable" and "never read"
   are the same output — which is how a reviewer drops an item that was in
   its own brief. The orchestrator then checks coverage **mechanically** —
   every brief item appears in the report, or the report is rejected and
   the reviewer re-spawned. This is counting, not judging: it is the only
   check a coordinator that does not read the code can make reliably.

The concrete families, **owned by this document** — criteria changes land
here first, then propagate to the skills, which reference this section
(the main agent pastes the family into reviewer tasks; reviewers have no
other access to it):

- **code-review — Standards axis**: the brief names the standards sources
  (repo standards files, the ADRs the ticket cites) item by item, and the
  report answers **every one of them** (criterion 7). (a) documented repo
  standards — cite standard file + rule → `fatal` on breach; (b) the Fowler smell baseline
  (12 smells, each a labelled judgement call; a documented repo standard
  overrides the baseline) → `advisory`; (c) the anti-AI-look baseline —
  UI diffs only: generic AI-generated design patterns (default
  purple/indigo gradients, centered glassmorphism cards, near-identical
  card rows, filler copy) → `advisory`; a documented design standard or
  per-project design tokens (e.g. `design.md`) overrides it. Skip anything
  tooling enforces.
- **code-review — Spec axis**: (a) acceptance criteria — per `- [ ]`
  item: pass / fail / missing; pass only if the code demonstrably
  satisfies it (a passing test with wrong logic is not a pass) →
  `fatal` on fail/missing; (b) scope creep — behaviour not asked for →
  `advisory`; (c) wrong implementation — implemented but incorrect →
  `fatal`; (d) invariant coverage — per law of `## System Invariants`, **one of
  three verdicts, never a binary**: `pinned` (a mutation of the
  implementing line was executed and the test went red — the mutation diff
  is the evidence), `blind-test` (a test exists and exercises the line, but
  the mutation stayed green: the test does not discriminate), `no-test` (no
  test touches the law). `blind-test` and `no-test` are both `fatal` and
  are **distinct findings** — collapsing them misreports the weight of the
  gap, and "no coverage" said of a blind test is a false claim about a real
  problem. The verdict is produced by the mutation prover, never by reading
  the diff. Dependency: (d) consumes the spec's invariants section and the
  prover's mutation matrix; (e) design-contract conformance — per
  `contracts[]` of `docs/design/<feature>.md`: the gate proves the declaration
  is present and the signatures hold (`guards/check_design.py --gate` exits
  0); this criterion judges its **content** — is each exit's `handled_at` the
  right place, and is an external call's `residual` the right policy? →
  `advisory` on a declared-but-wrong place; a signature divergence is already
  `--gate` exit ≠ 0 → `fatal`. Dependency: (e) consumes the design file and
  the gate's result; (f) load-bearing design choices — a choice that changes
  agent behaviour, run cost, or an external contract (CLI argv, config keys,
  wire format, file layout) must appear in the design as a `## Decision`
  section with the choice, the rejected alternatives, the consequences, and a
  link to the owner's sign-off comment. A design that makes such a choice
  without it is incomplete → `fatal`, quoting the design text that makes the
  choice as evidence. The brief names the design file and this criterion.
  Dependency: (f) consumes the design file and runs after the design gate,
  not instead of it. (g) external fixture provenance — for each new/changed
  fixture that stands in for a system the repo does not own, the manifest
  named by the design carries a row with `source` (`captured` / `contract` /
  `synthetic`), `reference`, and `sanitized`/`why`; a missing row, or
  `synthetic` without a reason, is `fatal` when a contract test uses the
  fixture and `advisory` otherwise. `guards/check_fixtures.py` proves the
  row's shape; this criterion judges whether the fixture is external and
  whether the source is the right one. The brief names the manifest path and
  this criterion. Dependency: (g) consumes the design's `## External
  fixtures` section and the fixture diff.
- **ponytail axis** — the delete-list (`skill://ponytail-review`):
  `delete` / `stdlib` / `native` / `yagni` / `shrink`, with a net
  lines-removable count. Every finding quotes the hunk verbatim →
  `advisory` (minimalism never blocks a correct feature alone). Scope:
  only "could be simpler/shorter"; correctness, security and performance
  are out of scope (routed to the engineering axes). A minimal smoke test
  or `assert` is the ponytail floor, never flagged for deletion.
- **mutation prover** — the executed half of anti-vacuity. The **agent**
  chooses the targets and the plausible-wrong alternatives; the **script**
  `guards/mutation_prover.py` executes them, so the mechanical part no longer
  depends on an LLM remembering to revert a mutant or on an editable venv
  pointing at the session tree. Scope: **only the lines implementing a
  declared law or a `fatal` acceptance criterion** — a handful of mutants per
  ticket, not whole-diff mutation testing. For each target the agent writes a
  manifest row with one plausible-wrong alternative (normalise instead of pass
  through, swap a sibling column, coalesce a default, flip a boundary); the
  script applies it in a clean worktree, sets `PYTHONPATH` on that worktree,
  runs the **control first** (green/no-test → matrix `inconclusive`, exit 1),
  then the targeted suite, and emits a JSONL matrix `pinned` / `blind-test` /
  `no-test` / `error` with each diff. **Green mutant = the test is blind** →
  `fatal`, and the finding ships the mutation diff, so the claim is
  reproducible instead of trusted. The script removes every worktree; mutmut
  is optional and consultative, never a gate. **No other role applies
  mutants** (B-007): the implementer and the fixer never mutate, their brief
  contains no mutation verb or mutation recipe, and a mutant in the session's
  worktree is a process violation, not evidence. The coordinator inspects
  `git status` after every fixer round, before committing or launching the
  prover. Dependency: runs once after the three axes have closed, not on every
  fix-loop iteration.

- **tension resolution** — where the ponytail axis and the engineering
  axes (Standards + Spec) flag the same code in opposite directions
  (e.g. ponytail "remove this abstraction" vs engineering "this needs
  tests at a public boundary"). The user decides the weight: balanced
  (default — every real tension escalates to the human with both sides
  stated) or lean (`--lean ponytail` / `--lean engineering`, auto-resolving
  the tension in that direction). `fatal` engineering findings are
  escalated regardless of lean; ambiguous tensions are escalated, never
  guessed. Dependency: runs after all three axes and consumes their
  findings; ordered by severity before any decision.

New criteria enter the registry only for a real, observed defect class —
the family is curated, not grown by habit.

## Forma intermedia strutturata dei report

> D-025 — emesso 2026-10-15 da B-004. La prosa dei report reviewer
> "detonava" prima dell'ultimo passo: il coordinatore doveva ricostruirne
> la struttura, e le regole "verbatim quote" (criterio 4) e "ogni item del
> brief" (criterio 7) diventavano giudizi, non verifiche. Lo stesso errore
> dell'SQL tenuto in una stringa. Soluzione: ogni reviewer emette **righe
> con schema fisso** in un file `reports/<feature>-<axis>.jsonl`, e il
> markdown per l'umano viene renderizzato da quello.

Ogni reviewer emette **una riga JSON per finding**, newline-delimited
(JSONL). Lo schema canonico, **owned by this document**:

| campo | tipo | obbligatorio | significato |
|---|---|---|---|
| `criterion` | string | sì | riferimento al criterio di `REVIEW-CRITERIA.md` ("4", "7", "I6", "4a", …) |
| `claim` | enum | no, default `code` | `code` (claim sul codice, richiede `quote` verbatim) o `coverage` (claim di copertura, prova = mutation matrix, niente `quote`) |
| `premise` | object | no | `{claim, verdict, evidence}` per falsificare le premesse del design (B-025 voce 2). `claim` = testo della premessa, `verdict` = `confirmed` / `falsified` / `untested`, `evidence` = puntatore (`file:riga`, sezione spec, …). Una premessa `untested` senza `evidence` è una violazione. |
| `item_id` | string | sì | id dell'item del brief a cui il finding risponde ("ADR-0013", "AC-2", "I-impl-line-12", "1.2", …) |
| `verdict` | enum | sì | `pass` / `fail` / `n/a — motivo` / `actionable` / `pinned` / `blind-test` / `no-test` |
| `severity` | enum | sì | `fatal` / `advisory` |
| `file` | string | cond. | path relativo al repo del file del finding |
| `line` | int | cond. | numero di riga (1-indexed) nel file |
| `quote` | string | cond. | citazione **verbatim** del diff o del file (richiesta per `criterion=4`; non ammessa per claim di copertura — vedi nota (b) sotto) |
| `commit` | string | cond. | SHA del commit a cui `quote` si riferisce (per la verifica meccanica) |
| `reviewer` | enum | sì | `standards` / `spec` / `ponytail` / `mutation` |
| `repo` | string | sì | `owner/name` del repo di prodotto: `gather_reviewer_reports` usa questo campo per non giudicare i report di un altro progetto |
| `actor` | string | sì | il sub-agente che ha emesso la riga (es. `spec-1`, `mutation-a`): è il dato che D-031 userà per l'isolamento; un report senza `actor` è una violazione di formato |
| `status` | enum | no, default `open` | `open` / `resolved` / `fixed` / `deferred`: solo `open` + `severity=fatal` + `verdict=actionable` è una violazione I6 |

**Brief manifest.** Prima di lanciare i reviewer, l'orchestratore scrive
`reports/<feature>-brief.json` con la lista numerata degli item del brief:

```json
{"repo": "owner/name", "feature": "<feature>", "items": ["1", "2", "3"]}
```

`check_brief_coverage` confronta gli `item_id` del report con questa lista:
un report per una feature senza brief manifest è una violazione, e un brief
senza report è una violazione (silence is not a verdict). Gli item sono
stringhe, gli stessi id che il reviewer mette in `item_id`.

**Regole di emissione** (vincolanti per il reviewer, verificate dai check in `audit.py`):

1. **Una riga per ogni item del brief**, anche quando il verdict è `pass`
   o `n/a — motivo`. Il silenzio non è un verdict (criterio 7). Ogni riga
   porta il `repo` del prodotto e l'`actor` che l'ha emessa; la stessa feature
   usa lo stesso `repo` in tutte le righe.
2. **`quote` per i claim sul codice** (criterio 4, claim (a)). Il testo
   deve matchare il diff di `commit` **byte per byte**; la verifica è in
   `audit.py check_reviewer_evidence_verbatim` (voce 8 di B-025).
3. **Claim di copertura = execution, non quote** (criterio 4, claim (b)).
   Per "il test discriminava" non si quota il diff: si quota la **matrice
   mutante→esito** del mutation prover (campo `quote` omesso o lasciato a
   un puntatore al file della matrice). Una `quote` assente per un finding
   di copertura non è una violazione.
4. **`severity=fatal` non accompagnato da fix = violazione I6**. Lo stato
   è letto in `audit.py check_i6_zero_actionable` (voce 10 di B-025):
   un finding `verdict=actionable` + `severity=fatal` non risolto (cioè
   senza un commit successivo che lo chiude) fa uscire il check con
   violazione.
5. **Il file `reports/*.jsonl` è append-only**. Riscritture o
   cancellazioni sono una violazione di processo: la storia è la prova.
   Il raccoglitore `gather_reviewer_reports()` legge i file di `reports/` e
   le copie archiviate in `handoffs/*-reports/`, filtra per `repo
   <owner/name>` e concatena; ogni riga porta il timestamp di emissione dal
   filesystem. I report precedenti all'introduzione di `repo` non hanno
   questo campo e vengono ignorati: l'audit non può attribuirli a un progetto
   e non deve giudicarli contro il repo sbagliato.

**Vista markdown per l'umano**: il coordinatore renderizza un report
leggibile dal JSONL a chiusura della review (skill `code-review`, fase 6).
La prosa è generata, non scritta: la fonte unica è la riga strutturata,
quindi la regola "una fonte sola per fatto" (B-023) si applica anche qui.
