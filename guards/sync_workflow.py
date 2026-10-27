#!/usr/bin/env python3
"""sync_workflow — keep the kit's WORKFLOW.md a real file derived from the canonical one.

    python3 guards/sync_workflow.py [CANONICAL]           # rewrite WORKFLOW.md
    python3 guards/sync_workflow.py [CANONICAL] --check   # exit 1 if it drifted

CANONICAL defaults to ../agentic-workflow/WORKFLOW.md. The kit used to
symlink it, which clones of the public repo received dangling. The copy
differs from the canonical in two ways only:
  1. `~/agentic-workflow/` paths become `~/agentic-dev-kit/` (the files exist here);
  2. the lines that cite files kept only in the private workflow repo
     (LESSONS.md, BACKLOG.md, gen-flow.py) are dropped or reworded.
Stdlib only.
"""
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
TARGET = KIT / "WORKFLOW.md"
DEFAULT_CANONICAL = KIT.parent / "agentic-workflow" / "WORKFLOW.md"
PRIVATE_ONLY = ("LESSONS.md", "gen-flow.py")


def derive(canonical_text: str) -> str:
    kept = [line for line in canonical_text.splitlines(keepends=True)
            if not any(name in line for name in PRIVATE_ONLY)]
    text = "".join(kept)
    text = re.sub(r";? those rows are the candidates for B-004/B-007 in\n`BACKLOG.md`\.", ".", text)
    text = text.replace("~/agentic-workflow/", "~/agentic-dev-kit/")
    leftover = re.findall(r"LESSONS\.md|BACKLOG\.md|gen-flow\.py|agentic-workflow", text)
    if leftover:
        raise SystemExit(f"sync_workflow: private reference left in the copy: {sorted(set(leftover))}")
    return text


def main(argv: list[str]) -> int:
    check = "--check" in argv
    paths = [a for a in argv if not a.startswith("--")]
    canonical = Path(paths[0]) if paths else DEFAULT_CANONICAL
    if not canonical.is_file():
        print(f"sync_workflow: canonical not found: {canonical}", file=sys.stderr)
        return 2
    wanted = derive(canonical.read_text(encoding="utf-8"))
    if check:
        if TARGET.is_symlink() or not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != wanted:
            print("sync_workflow: WORKFLOW.md drifted from the canonical; run sync_workflow.py", file=sys.stderr)
            return 1
        print("sync_workflow: WORKFLOW.md matches the canonical")
        return 0
    if TARGET.is_symlink():
        TARGET.unlink()
    TARGET.write_text(wanted, encoding="utf-8")
    print(f"sync_workflow: wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
