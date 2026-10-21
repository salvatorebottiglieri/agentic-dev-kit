# Enforcement — the fitness function per stack

The only file of this skill that names a stack. Each stack fixes five
things the rest of the skill uses by concept:

- **Layout** — where a module's published interface and internals live.
- **Boundary contract** (law 2), **cycle contract** (law 4), **shared-kernel
  contract** (law 6) — run by the canonical CI command.
- **Exception list** — where frozen violations are listed for the ratchet of
  [MODULARIZE.md](MODULARIZE.md).
- **Dependency-map tool** — draws the current graph (MODULARIZE step 1).

The other laws are checked as the last section says.

## Python — import-linter

| Concept | Python |
|---|---|
| Layout | see below |
| Contracts | `import-linter>=2.6` (`protected` arrived in 2.5, `acyclic_siblings` in 2.6), `lint-imports` in the same CI job as the tests |
| Exception list | `ignore_imports` of each contract |
| Dependency-map tool | `lint-imports` with the `acyclic_siblings` contract lists the cycles; `grimp` or `pydeps` draw the graph |

Layout:

```text
src/<app>/
  shared/            # technical kernel only (law 6)
  <module>/
    __init__.py      # empty — the published interface lives in api.py
    api.py           # published interface: functions, DTOs, event types
    internal/        # domain, persistence, adapters — private to the module
```

Contracts in `pyproject.toml` (`<app>` = `shop`, modules `orders`,
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

| Stack | Layout | Contracts (laws 2, 4) | Exception list | Dependency-map tool |
|---|---|---|---|---|
| Java / Kotlin + Spring | module = direct sub-package of the app package; its top-level types are the published interface, its sub-packages the internals | Spring Modulith `ApplicationModules.of(App.class).verify()` in a test | — | Spring Modulith `Documenter` |
| JVM without Spring | `<module>.api` / `<module>.internal` packages | ArchUnit: rules per module on `..internal..`; `slices().matching(...).should().beFreeOfCycles()` | ArchUnit `FreezingArchRule` | `jdeps` |
| TypeScript / JavaScript | `<module>/index.ts` published, `<module>/internal/` private | dependency-cruiser `forbidden` rules into `<module>/internal`; `no-circular` | dependency-cruiser baseline (`depcruise-baseline`, `--ignore-known`) | dependency-cruiser `--output-type dot` |
| .NET | one project or namespace per module, `Internal` namespace private | NetArchTest or ArchUnitNET namespace rules; ArchUnitNET slice cycle rules | — | — |

The shared-kernel contract (law 6) is a forbidden-dependency rule of the same
tool. A `—` is a cell not yet verified: fill it from the tool's docs for the
version in the repo before relying on it, as every other non-Python cell. For
a stack outside the table, laws 2, 4 and 6 join the list below.

## Laws without a dependency contract

Their `### System invariants` row names the review-time check in **Verified
in** and carries the marker `[prompt — no mechanical check]`:

- **Law 1** — each module name is a `CONTEXT.md` term (review).
- **Law 3** — dependency rules do not see data access. The mechanical form is
  one storage namespace per module (DB schema, database, or collection
  prefix) with per-module credentials granted only on it; when the repo has
  it, law 3 drops the marker and cites the grants.
- **Law 5** — each event in the module map states its transaction semantics
  (review).
- **Law 7** — tests depend only on published interfaces (review), except in
  Python, where the `protected` contracts enforce it once `tests` is in
  `root_packages`.
