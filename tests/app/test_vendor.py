"""Tests for app/vendor/: the pinned renderers match their manifest (spec invariant)."""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR = ROOT / "app" / "vendor"


def load(name: str, path: Path):
    """Import a file by path (the app has no package); first loader wins."""
    if name in sys.modules:
        return sys.modules[name]
    found = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(found)
    sys.modules[name] = module
    found.loader.exec_module(module)
    return module


catalog = load("catalog", ROOT / "app" / "catalog.py")


def test_vendored_files_match_the_manifest():
    """Witness the two pinned hashes of Decision 21, literally - distinguish a manifest that is
    recomputed from the files (always green) from one that pins the approved bytes; the `.js`
    files listed on disk against the manifest names distinguish an unlisted file from a listed one."""
    manifest = catalog.vendor_files(VENDOR)
    assert manifest == {
        "mermaid.min.js": "336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3",
        "marked.umd.js": "bb95d4c15f1289f78486eaacebf3bb18688475ed58a040ef762ed49337090542"}
    for name, digest in manifest.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
    assert {p.name for p in VENDOR.glob("*.js")} == set(manifest)


def test_the_manifest_records_version_and_source_of_every_file():
    """Witness the row of each file - distinguishes a row carrying name, version, source URL and
    SHA-256 from a bare name/hash pair."""
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in (VENDOR / "MANIFEST.md").read_text().splitlines()
            if re.match(r"\|\s*\S+\.js\s*\|", line)]
    assert {r[0]: (r[1], r[2].startswith("https://registry.npmjs.org/")) for r in rows} == {
        "mermaid.min.js": ("12.3.0", True), "marked.umd.js": ("18.2.1", True)}
    assert all(len(r) == 4 and re.fullmatch(r"[0-9a-f]{64}", r[3]) for r in rows)
    assert (VENDOR / "mermaid.LICENSE").is_file() and (VENDOR / "marked.LICENSE").is_file()
