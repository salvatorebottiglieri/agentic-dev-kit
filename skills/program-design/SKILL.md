---
name: program-design
description: >
  Produce program design artifacts (call-stack tree, file-tree diff, type
  signatures) for a ticket and append them to the issue on the tracker.
  Use between /to-tickets and implement-loop to give the implement and review
  subagents a concrete contract before they write code. Runs near-always —
  skip only when there is no shape to design (one-shot throwaway, pure
  config/data, mechanical rename/move).
disable-model-invocation: true
---

# Program Design

Bridge the gap between **architecture** (how services talk, what the schema
looks like) and **implementation** (the actual code). Most code written by
agents has the right architecture but wrong *shape* — wrong function names,
wrong parameters, wrong file boundaries. Program design fixes the shape
before an agent writes a line.

`$ARGUMENTS` is the ticket/issue to design. Pass a number, URL, or path.

The issue tracker should have been provided to you — run
`/setup-matt-pocock-skills` if not.

## Process

### 1. Gather context

- **Read the ticket.** Fetch it from the tracker. Read the full body, comments,
  acceptance criteria, and blocking edges.
- **Read the terrain.** Explore the files and modules the ticket touches. Read
  `CONTEXT.md` for domain vocabulary. Read relevant ADRs in `docs/adr/`. Read
  existing function signatures, types, and test patterns in the area.
- **Classify the change.** Determine what kind of work this is (see §3). This
  decides which artifacts are worth producing.

_Done when: you have read the ticket body, the relevant CONTEXT.md terms,
and any ADRs touching the area, and you can classify the change._

### 2. Produce artifacts

For each artifact type that fits the change, produce a draft. Do **not** write
code or pseudocode for the implementation body — only the **shape**. Every
artifact encodes the **shape** of the change: what calls what (call-stack tree),
where files live (file-tree diff), and what interfaces the code must satisfy
(type signatures).

#### Call-stack tree

Produce this when the change introduces new orchestration or modifies control
flow (new route handler, new service method, new branch in an existing function).

Show the call stack with diff syntax:

```diff
 entrypoint
   authenticate
+    handleCreateResource
+      validateInput(input)
+      resourceRepo.insert(input)
+      notifySubscribers(resourceId)
   sendResponse
```

 + = new  ~ = modified  - = removed

Include only the functions that are part of **this** change. Existing functions
that don't change are reference context, not part of the diff.

If the change is purely data or config (add a field, toggle a flag), skip this
section entirely.

#### File-tree diff

Produce this when the change creates or modifies files.

```diff
 src
 └── resource
+    ├── resource-client.ts       # NEW — wraps API contract calls
+    ├── resource-client.test.ts  # NEW — request/response mapping
~    └── resource-route.ts        # MODIFIED — wires create action
```

 + = created  ~ = modified  - = removed

Each new or modified file gets a one-line rationale. Test files follow the
project's naming convention.

If the change doesn't add or remove files (only edits inside existing files),
omit the tree and note the files modified in prose.

#### Type signatures

Produce this when the change introduces new functions, modifies existing
signatures, or defines new data types.

Use the project's language (TypeScript, Python, Go, etc.):

```ts
// New
interface ResourceInput {
  name: string
  sourceUrl: string
  tags: string[]
}

function createResource(input: ResourceInput): Promise<Resource>

// Modified
// resource-route.ts — PUT /resources/:id now accepts `tags`
function handleCreateResource(req: Request): Promise<Response>
```

**Rules:**
- Every function in the call-stack tree MUST have a signature here.
- Names use the domain vocabulary from `CONTEXT.md`.
- If a function is called but not modified, reference its existing location
  ("vedi signature in src/resource/resource-service.ts") — don't repeat it.
- Types are concrete, not generic. `Promise<Resource>`, not `Promise<T>`.
- If the change adds or modifies zero functions (config only, copy tweak),
  skip this section.

#### Premises (B-025 voce 2, D-033)

The design's premises are claims the design makes about the problem or
the system — things that, if false, would invalidate the design but that
are not directly provable from the call-stack tree or the type
signatures. Examples: "user count grows linearly", "TLS termination at
ingress", "the team's CI is green within 10 minutes".

For each non-trivial design, list the premises and **falsify them**: each
premise carries a `{claim, verdict, evidence}` triple. Verdicts are
`falsified` (proved false by an existing artifact — a test, a benchmark,
a spec section) or `confirmed` (the design asserts it; the evidence
pointer cites where the confirmation lives). `untested` is a **violation**
in `audit.py check_premises_falsified` — a design that did not falsify
its own premises is a design that has not done its job. If a premise
cannot be falsified, mark it `[non verificabile — opinione]` and document
why in the evidence.

```
- **Premise**: user count grows linearly.
  - verdict: falsified
  - evidence: docs/specs/scale.md:42 (PoC: 10k users, flat latency)
```

Premises are not optional for designs that introduce behavior the
team has not seen before. A refactor that only touches existing call
graphs can skip them.

#### Load-bearing decisions (D-043)

A choice that changes agent behaviour, run cost, or an external contract
(CLI argv, config keys, wire format, file layout) is a **load-bearing
choice**. For each one, add a `## Decision` section to the design document
(`docs/design/<feature>.md`) and to the appended `## Program Design` section,
before you publish:

```markdown
## Decision — <short title>

- **Choice**: <what the design does>
- **Alternatives rejected**: <alternative> — <why rejected>; ...
- **Consequences**: <agent behaviour, run cost, external contract impact>
- **Owner sign-off**: <link to the comment where the owner approved it>
```

One section per load-bearing choice; the heading starts with `## Decision`.
A design that makes such a choice without the section is incomplete: the Spec
axis raises it as `fatal`, quoting the design text as evidence. If the design
has no load-bearing choice, no `## Decision` is needed.

#### External fixtures (B-022 / D-044)

A test fixture that stands in for a system the repo does not own (an API
response, a file produced by another tool, a schema of another service)
declares its provenance. Add an `## External fixtures` section to the design
listing the systems and the manifest path (default
`tests/fixtures/PROVENANCE.md`), with one row per external fixture:

```markdown
| fixture | source | reference | sanitized / why |
|---|---|---|---|
| `tests/fixtures/external/jira.json` | captured | Jira dev, 2026-09-03 | removed e-mail |
| `tests/fixtures/external/stripe.json` | contract | docs/contracts/stripe.md | n/a |
| `tests/fixtures/external/sast.xml` | synthetic | none available | real report under NDA |
```

`source` is `captured` (a real sample, sanitized), `contract` (a named contract
document), or `synthetic` (no real sample, with the reason). Run
`python3 guards/check_fixtures.py <manifest> .` (add `--scope <dir>` to prove
no file was forgotten). The Spec axis raises a missing row, or a `synthetic`
source without a reason, as `fatal` when a contract test uses the fixture. If
the design has no external fixtures, say so in one line.

### 3. When to skip (classify the change)

Program design runs **near-always**. Skip only when there is no *shape* to
design — the change introduces or modifies zero functions, types, or control
flow. If it matches any of these, skip program design, append a one-line note
to the ticket, and stop:

| Type | Example | Reason |
|---|---|---|
| **One-shot throwaway** | One-off script, scratch data migration | No reusable shape to design |
| **Pure config/data** | "Add a static field with no new function" | Introduces zero signatures |
| **Mechanical rename/move** | "Rename `UserService` → `AccountService`" (no logic change) | LSP rename is enough; no new shape |

Anything that introduces or modifies a function, type, or control flow —
including **bug fixes** and **"trivial" features** — gets program design.
A bug fix that only tweaks two lines of logic inside an existing function
can still skip, but only if it changes no signature and no control flow.
When in doubt, do it: it costs minutes and prevents hours of rework.


### 4. Write the design document on the branch

The design lives in **`docs/design/<feature>.md`**, committed on the branch —
not only in the ticket. It is the prose above plus a fenced `json design` block
that is the machine part:

```json design
{
  "feature": "…",
  "call_stack": { "name": "…", "children": [ { "name": "…", "note": "…", "children": [] } ] },
  "file_tree": [ { "kind": "add", "path": "src/…", "note": "…" } ],
  "signatures": [ { "module": "src/…py", "language": "python", "code": "def f(a, b=1) -> T: …" } ],
  "data_flow": {
    "nodes": [ { "id": "mod.f", "module": "src/mod.py", "kind": "add" } ],
    "edges": [ { "from": "mod.f", "to": "other.g" } ]
  },
  "contracts": [
    {
      "site": "mod.f",
      "callee": "other.g",
      "exits": [
        { "kind": "returns", "type": "T", "handled_at": "src/mod.py:42" },
        { "kind": "absent", "type": "None", "handled_at": "why it cannot happen" },
        { "kind": "raises", "type": "ValueError", "handled_at": "src/mod.py:50" },
        { "kind": "effect", "type": "none", "handled_at": null }
      ]
    }
  ]
}
```

Then run the checker in **full** mode; it must exit 0 before you publish:

```bash
python3 guards/check_design.py docs/design/<feature>.md .
```

- All four exit kinds are mandatory: a contract without `raises` is silent, not
  a claim that nothing raises. Every dispatchable exit (`returns`, `absent`,
  `raises`) needs a real `handled_at`; `effect` may be `null`.
- A contract that calls outside the repo (free-text `call`, or a ghost node
  whose module is not a repo file) declares `residual`: what happens to the
  exits it did not enumerate.
- At pre-push and in CI the same tool runs `--gate` (signatures + coherence +
  exits + frontier). It does **not** check the design's `state`
  (`add`/`mod`/`keep`): that is true only while the code does not exist.

### 5. Append to the ticket

Append a `## Program Design` section to the issue body on the tracker.

**How** depends on the tracker configured in `/setup-matt-pocock-skills`:

- **GitHub:** Read the current body with `gh issue view <number> --json body`,
  extract the body text, append the section, write back with
  `gh issue edit <number> --body "<full-body>"`.
- **Local files:** Read the file, find the end of the content (before
  `## Comments` if it exists), insert the section there, write back.
- **GitLab / Linear:** Follow the tracker's convention for updating issue
  descriptions (see the tracker doc from setup).

The appended section uses this structure:

```markdown
## Program Design

_CLASSIFY: <change type>_

### Call-stack tree

```diff
...
```

### File-tree diff

```diff
...
```

### Type signatures

```ts
...
```
```

If the design makes a load-bearing choice, append one `## Decision` section
per choice (choice, rejected alternatives, consequences, owner sign-off) after
the Program Design section; see §2. A design with such a choice and without
the section is incomplete.

If the design has external fixtures, add an `## External fixtures` section
naming the systems and the manifest path, and the provenance rows; see §2.

Omit sections that are empty — don't produce "N/A" placeholders.

### 6. Stop for review

Do **not** proceed to implementation. Report what was produced and ask:

> "The program design for this ticket is ready. The signatures are the
> contract for implementation. Would you like to modify anything or proceed
> with implement-loop?"

If the user corrects anything, incorporate the fix and re-append the section
(overwrite the previous one). Only when they approve does the ticket move to
`ready-for-agent` (or the implement-loop picks it up).

## How this shapes other skills

| Skill | Effect |
|---|---|
| **implement-loop** | Passes the full ticket body and the design file `docs/design/<feature>.md` to the implement subagent. The signatures there are contract — the subagent implements exactly those interfaces — and the declared `exits` are its test list. |
| **code-review** | The spec-axis reviewer compares the diff against the ticket body, now including the signatures. _"Signature says `createResource(...)`, but the implementation uses `save()` and returns `any`."_ |
| `codebase-design` | Provides vocabulary (deep module, seam). Program design is the *concrete* application: "the seam is this signature." |
| `domain-modeling` | Program design uses `CONTEXT.md` vocabulary. If a term is ambiguous, call `domain-modeling`. |
| `to-tickets` | Produces tickets. Program design takes one ticket and adds signatures to it. |
| `prototype` | If the shape isn't clear yet, prototype first, then program-design the result. |
| `improve-codebase-architecture` | If an architectural concern emerges ("this function is in the wrong module"), escalate it — don't resolve it here. |

