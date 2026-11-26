# Vendored renderers

The one third-party exception of the window (spec, "Rendering"): copied unchanged from the npm
tarballs, served offline by `/vendor/{name}`. `tests/app/test_vendor.py` recomputes every SHA-256.
Updating a renderer is a new row and a new hash.

| name | version | source URL | SHA-256 |
|---|---|---|---|
| mermaid.min.js | 12.3.0 | https://registry.npmjs.org/mermaid/-/mermaid-12.3.0.tgz `package/dist/mermaid.min.js` | 336df803d87a38b70a01e736e666c8f7601d2ce42f5e47c8fafe5d721074b9a3 |
| marked.umd.js | 18.2.1 | https://registry.npmjs.org/marked/-/marked-18.2.1.tgz `package/lib/marked.umd.js` | bb95d4c15f1289f78486eaacebf3bb18688475ed58a040ef762ed49337090542 |

Licenses: `mermaid.LICENSE`, `marked.LICENSE` (copied from the same tarballs).
