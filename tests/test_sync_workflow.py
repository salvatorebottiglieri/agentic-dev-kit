"""WORKFLOW.md in the kit is a real file and matches what sync_workflow derives."""
import importlib.util
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sync_workflow", KIT / "guards" / "sync_workflow.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

CANONICAL = KIT.parent / "agentic-workflow" / "WORKFLOW.md"


def test_workflow_md_is_a_real_file_not_a_symlink():
    assert not (KIT / "WORKFLOW.md").is_symlink()


def test_workflow_md_has_no_reference_to_private_only_files():
    text = (KIT / "WORKFLOW.md").read_text(encoding="utf-8")
    for private in ("LESSONS.md", "BACKLOG.md", "gen-flow.py", "agentic-workflow"):
        assert private not in text


def test_derive_rewrites_paths_and_drops_private_rows():
    src = "see ~/agentic-workflow/audit.py\n| `LESSONS.md` | x |\nkeep\n"
    assert sync.derive(src) == "see ~/agentic-dev-kit/audit.py\nkeep\n"


def test_derive_refuses_a_leftover_private_reference():
    with pytest.raises(SystemExit):
        sync.derive("candidates in `BACKLOG.md`\n")


@pytest.mark.skipif(not CANONICAL.is_file(), reason="canonical workflow repo not next to the kit")
def test_workflow_md_matches_canonical_when_available():
    assert (KIT / "WORKFLOW.md").read_text(encoding="utf-8") == sync.derive(CANONICAL.read_text(encoding="utf-8"))
