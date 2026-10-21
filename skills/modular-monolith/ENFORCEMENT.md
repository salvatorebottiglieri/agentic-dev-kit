# Enforcement — the fitness function per stack

Requirement on every stack: one contract each for laws 2, 4 and 6, run by the
canonical CI command. The other laws are checked as the last section says.

## Python — import-linter

Requires `import-linter>=2.6` (the `protected` contract arrived in 2.5,
`acyclic_siblings` in 2.6). Add it to the dev dependencies and run
`lint-imports` in the same CI job as the tests.

Contracts in `pyproject.toml`, for the layout of
[GREENFIELD.md](GREENFIELD.md) step 4 (`<app>` = `shop`, modules `orders`,
`billing`, `notifications`):

```toml
[tool.importlinter]
root_package = "shop"

# Law 2 — one contract per module: only the module itself reaches its internals.
[[tool.importlinter.contracts]]
name = "billing internals are private to billing"
type = "protected"
protected_modules = ["shop.billing.internal"]
allowed_importers = ["shop.billing"]
broken_contract_guidance = "Import from shop.billing.api instead."

# Law 4 — no cycles between modules.
[[tool.importlinter.contracts]]
name = "modules form a DAG"
type = "acyclic_siblings"
ancestors = ["shop"]
depth = 0

# Law 6 — the shared kernel imports no module.
[[tool.importlinter.contracts]]
name = "shared kernel imports no module"
type = "forbidden"
source_modules = ["shop.shared"]
forbidden_modules = ["shop.orders", "shop.billing", "shop.notifications"]
```

Notes that change the outcome:

- Write one `protected` contract **per module**. A single
  `protected_modules = ["shop.*.internal"]` with `allowed_importers =
  ["shop.*"]` lets every module into every other's internals, because each
  importer matches `shop.*`.
- `depth = 0` checks the modules against each other; raise it to also forbid
  cycles inside a module.
- `ignore_imports` is the ratchet of [MODULARIZE.md](MODULARIZE.md): with the
  default `unmatched_ignore_imports_alerting = "error"`, an entry whose import
  is gone fails the check, so a fixed violation must also leave the list.
- Law 7 for free: make `tests/` a package and set
  `root_packages = ["shop", "tests"]`. The same `protected` contracts then go
  red when any test imports a module's internals.
- `protected` guards `internal/` only: a file next to `api.py` is importable by
  every module. Keep `api.py` as the only code outside `internal/`.
- A new module means a new `protected` contract and a new entry in the
  `forbidden_modules` of the shared kernel — part of the FEATURE branch diff.

## Other stacks

| Stack | Tool | Boundary (law 2) | Cycles (law 4) |
|---|---|---|---|
| Java / Kotlin + Spring | Spring Modulith | `ApplicationModules.of(App.class).verify()` in a test; internals live in sub-packages of the module package | same `verify()` |
| JVM without Spring | ArchUnit | `classes().that().resideInAPackage("..internal..")` rules per module | `slices().matching(...).should().beFreeOfCycles()` |
| TypeScript / JavaScript | dependency-cruiser | `forbidden` rules on paths into `<module>/internal` | `no-circular` rule |
| .NET | NetArchTest or ArchUnitNET | namespace dependency rules per module | ArchUnitNET slice cycle rules |

Confirm each rule against the tool's docs for the version in the repo. For a
stack outside the table, laws 2, 4 and 6 join the list below.

## Laws without an import contract

Their `### System invariants` row names the review-time check in **Verified
in** and carries the marker `[prompt — no mechanical check]`:

- **Law 1** — each module name is a `CONTEXT.md` term (review).
- **Law 3** — import rules do not see SQL. The mechanical form is one DB
  schema per module, with a DB role per module granted only on its own
  schema; when the repo has it, law 3 drops the marker and cites the grants.
- **Law 5** — each event in the module map states its transaction semantics
  (review).
- **Law 7** — tests import only `api.py` (review), except in Python, where
  the `protected` contracts enforce it once `tests` is in `root_packages`.
