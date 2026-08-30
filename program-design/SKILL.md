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


### 4. Append to the ticket

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

Omit sections that are empty — don't produce "N/A" placeholders.

### 5. Stop for review

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
| **implement-loop** | Passes the full ticket body to the implement subagent. If `## Program Design` is present, the signatures there are contract — the subagent implements exactly those interfaces. |
| **code-review** | The spec-axis reviewer compares the diff against the ticket body, now including the signatures. _"Signature says `createResource(...)`, but the implementation uses `save()` and returns `any`."_ |
| `codebase-design` | Provides vocabulary (deep module, seam). Program design is the *concrete* application: "the seam is this signature." |
| `domain-modeling` | Program design uses `CONTEXT.md` vocabulary. If a term is ambiguous, call `domain-modeling`. |
| `to-tickets` | Produces tickets. Program design takes one ticket and adds signatures to it. |
| `prototype` | If the shape isn't clear yet, prototype first, then program-design the result. |
| `improve-codebase-architecture` | If an architectural concern emerges ("this function is in the wrong module"), escalate it — don't resolve it here. |

