---
name: tightrope
description: >
  DEPRECATED — folded into implement-loop. The ponytail-vs-engineering
  tension is now the third review axis of /implement-loop, and the
  test/lint/PR gate is implement-loop's pre-push gate. See
  ~/agentic-workflow/WORKFLOW.md phase 6.
disable-model-invocation: true
---

# tightrope — deprecated

`/tightrope` is deprecated (2026-08-30). Its two jobs moved into
`/implement-loop`:

- **Tension check** (ponytail minimalism vs engineering soundness) → the
  third review axis of implement-loop step 2, resolved by the user's weight:
  `balanced` (default, tensions escalate to the human) or
  `--lean ponytail` / `--lean engineering`.
- **Test / lint / PR gate** → implement-loop's pre-push gate (steps 6–7):
  canonical full-suite test + lint, then a PR held open until CI is green.

If you invoked `/tightrope`, run `/implement-loop` on the same items instead.
The ponytail axis is `/ponytail-review`; the engineering axes are
`/code-review` (Standards + Spec). The criteria family taxonomy lives in
`~/agentic-workflow/WORKFLOW.md` → `## Review criteria family`.
