---
name: product-review
description: >
  Produce a visual HTML review of a PRD — problem statement, success criteria,
  and HTML wireframe mockup — to align on what the user will see before
  designing architecture or writing code. Use after /to-spec and before
  /system-architecture.
disable-model-invocation: true
---

# Product Review

Bridge the gap between **product requirements** (text in a PRD) and
**architecture decisions** (how services communicate). Produce a **visual
spec** — a self-contained HTML page showing the problem, the success criteria,
and wireframe mockups of the UI — so everyone agrees on what the user sees and
interacts with before any code is written.

`$ARGUMENTS` is the PRD issue to review. Pass a number, URL, or path.

The issue tracker should have been provided to you — run
`/setup-matt-pocock-skills` if not.

## Process

### 1. Gather context

- **Read the PRD.** Fetch it from the tracker. Read the full body: problem
  statement, user stories, solution, implementation decisions, testing
  decisions, out of scope.
- **Read existing UI patterns.** If the project is a web app, explore existing
  pages, components, and layouts. Look at `CONTEXT.md` for domain vocabulary
  and UI conventions. Check for existing design system components, CSS
  variables, or layout patterns — your mockup should match the project's
  visual language.
- **Read existing mockups.** Check if mockups or wireframes already exist in a
  `docs/` directory, a Figma link in the PRD, or a `mockups/` folder. If they
  exist, reference them and show only the diff.

_Done when: you have read the PRD body, surveyed existing UI patterns, and
identified whether mockups already exist._

### 2. Classify the PRD

Not every PRD needs a visual mockup. Before producing artifacts, classify:

| If the PRD… | Produce |
|---|---|
| Adds new UI (page, dialog, form, nav) | Full HTML: problem card + criteria + mockup |
| Modifies existing UI (new field, changed layout) | Mockup showing the delta (before/after or overlay) |
| Is API-only, data-only, or backend only | Skip mockup — produce only problem + criteria card |
| Changes user-facing behavior without new UI (copy, permissions, flow) | Problem card + criteria + user flow description as prose |

### 3. Produce the HTML page

Produce a single self-contained HTML file at
`.scratch/product-review/<feature-name>/index.html`. Inline all styles — no
external CSS, no JS, no framework. The file renders in any browser.

The page has three sections. Only the mockup section can be omitted (see §2).

#### Section A: Problem statement card

A prominent card at the top. Render the PRD's problem statement as a concise,
readable block. Use the project's brand colors if you can infer them (check
CSS variables in the codebase, or use a neutral palette).

```html
<div class="card problem">
  <h2>Problem</h2>
  <p>As a field agent, I currently cannot update incident reports after
  submission. New evidence collected on-site must be appended via email to
  dispatch, causing delays and data-entry errors.</p>
</div>
```

#### Section B: Success criteria checklist

Render each acceptance criterion as a checkbox list. Use the PRD's testing
decisions and user stories to derive the list.

```html
<div class="card criteria">
  <h2>Success Criteria</h2>
  <ul class="checklist">
    <li><input type="checkbox" disabled checked> Agent can edit report within 24h of submission</li>
    <li><input type="checkbox" disabled> Edits are logged as revision history</li>
    <li><input type="checkbox" disabled checked> Manager receives notification on edit</li>
  </ul>
</div>
```

**Rules:**
- Derive criteria from user stories, testing decisions, and acceptance criteria
  in the PRD. If the PRD doesn't have explicit criteria, synthesise them from
  the stories and solution.
- **Cover every distinct persona** in the user stories — at least one
  criterion per persona, phrased so it can fail ("an admin can X" is
  checkable; "users love X" is not).
- **Cover the edge-case states** — empty, error, loading, long content,
  small viewport. A criterion that cannot fail is decoration.
- Mark any that are clearly already met by existing functionality as `checked`.
- Group related criteria visually with indentation or subtitles.

#### Section C: Wireframe mockup

An HTML+CSS representation of the UI. This is **not** a screenshot or an image
— it's semantic HTML styled to look like a wireframe.

**Rules:**
- Use only HTML + inline CSS. No images, no SVGs, no JavaScript.
- Grey-scale wireframe style (different shades of grey for containers, borders,
  text blocks, using `#e0e0e0`, `#f5f5f5`, `#333`, etc.) — unless the project
  has clear brand colors, in which case use them for emphasis.
- Lorum ipsum or `[placeholder text]` for content the PRD doesn't specify.
- **No AI-look defaults**: avoid the generic AI-generated pattern set —
  default purple/indigo gradient heroes, centered glassmorphism cards,
  near-identical card rows, filler copy. Derive the palette and layout from
  the project's brand (CSS variables) or, when the project has none, from a
  reference site's design system; state which you used.
- Show realistic structure: real header, real nav items, real button labels
  from the PRD.
- If the change affects multiple screens/states (e.g., empty state, error
  state, confirmation), show each in a separate mockup panel with a label.
- Use `border: 2px dashed #999` for wireframe-style containers.
- The mockup should fill ~60-80% of the page width; center it.

```html
<div class="mockup">
  <h3>Screen: Edit Incident Report</h3>
  <div class="wireframe">
    <div class="wf-header">
      <div class="wf-logo">[Logo]</div>
      <div class="wf-nav">[Dashboard] [Reports] [Logout]</div>
    </div>
    <div class="wf-body">
      <div class="wf-form">
        <div class="wf-field"><label>Report ID</label><div class="wf-input">INC-2024-0421</div></div>
        <div class="wf-field"><label>Description *</label><div class="wf-textarea">[Editable text area with existing content]</div></div>
        <div class="wf-field"><label>Attachments</label><div class="wf-input">[Upload new evidence...]</div></div>
        <div class="wf-actions">
          <button class="wf-btn-primary">Save Edits</button>
          <button class="wf-btn-secondary">Cancel</button>
        </div>
      </div>
    </div>
  </div>
</div>
```

**When to adapt fidelity:**
| Situation | Style |
|---|---|
| This is a new greenfield feature | Detailed layout with fields, buttons, states |
| The PRD says "follow pattern of X" | Show the existing pattern, highlight the new piece |
| Only one new field on an existing page | Show the existing page with the new field highlighted (colored border or annotation) |
| Mobile / responsive concern | Show mobile viewport mockup (narrow container, 375px simulated width) |

**Multi-screen flows:** if the feature spans multiple screens (wizard,
onboarding), show them left-to-right as a sequence with arrow separators.

### 4. Write the file

Write the complete HTML to `.scratch/product-review/<feature-name>/index.html`.

Create the directory if it doesn't exist.

The filename should match the feature name from the PRD title or ticket label
— kebab-case, lowercase.

Tell the user where the file is:

> "Product review ready at `.scratch/product-review/<feature-name>/index.html`
> — open it in a browser to see the mockup."
>
> "Would you like to modify anything or proceed with system-architecture?"

### 5. Iterate on feedback

If the user requests changes, update the HTML and overwrite the file. Only
when they approve does the PRD move to `system-architecture`.

## How this feeds other skills

| Skill | Effect |
|---|---|
| **system-architecture** | The mockup shows *what* the user sees. Architecture designs *how* services deliver it. Together they cover front and back. |
| **to-tickets** | Each mockup screen/story maps to a ticket. "The edit-report screen" becomes a ticket with the mockup as visual reference. |
| **implement-loop** | The implement subagent sees the PRD body. If the mockup exists, the visual spec is in `.scratch/` for reference. |
| `to-spec` | Produces the PRD text. Product review renders it visually — same source, different medium. |
| `domain-modeling` | If UI vocabulary is ambiguous during mockup (e.g., "what do we call this component?"), call `domain-modeling`. |

## When to skip

| Type | Reason |
|---|---|
| **API-only / backend-only** | No UI to mock up |
| **Bug fix (UI)** | The fix is visual but the existing UI is unchanged in layout |
| **Mechanical refactor** | No UI change |
| **Follows existing pattern exactly** | Mockup would be identical to existing pages — skip and note it |
| **Data-only feature** | New field on model, no user-facing change |
