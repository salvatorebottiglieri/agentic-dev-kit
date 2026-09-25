#!/usr/bin/env bash
# install.sh — link the custom skills of agentic-dev-kit into your agent.
#
# Symlinks each skill in skills/ into the directory your coding agent loads
# skills from. Defaults to ~/.claude/skills (Claude Code); override with
# AGENT_SKILLS_DIR for any other agent, e.g.:
#     AGENT_SKILLS_DIR=~/.agents/skills ./install.sh
# Idempotent: an existing correct link is left alone; anything else is only
# replaced after you confirm. The docs and audit.py stay in this clone.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="${AGENT_SKILLS_DIR:-$HOME/.claude/skills}"
SKILLS=(implement-loop product-review system-architecture program-design)

mkdir -p "$SKILLS_DIR"
echo "Installing agentic-dev-kit skills into: $SKILLS_DIR"

for skill in "${SKILLS[@]}"; do
  src="$KIT_DIR/skills/$skill"
  dst="$SKILLS_DIR/$skill"

  if [[ ! -d "$src" ]]; then
    echo "  ! $skill: source folder missing in kit, skipping"
    continue
  fi

  # already the right link → nothing to do
  if [[ -L "$dst" && "$(readlink -f "$dst")" == "$(readlink -f "$src")" ]]; then
    echo "  = $skill: already linked"
    continue
  fi

  # something else is there → ask before touching it
  if [[ -e "$dst" || -L "$dst" ]]; then
    read -r -p "  ? $skill exists at $dst — replace it? [y/N] " ans
    if [[ "${ans:-N}" != [yY] ]]; then
      echo "  - $skill: left as-is"
      continue
    fi
    rm -rf "$dst"
  fi

  ln -s "$src" "$dst"
  echo "  + $skill: linked"
done

echo
echo "Done. Next:"
echo "  1. Install the base layer if you haven't: https://github.com/mattpocock/skills"
echo "  2. Have your coding agent read $KIT_DIR/WORKFLOW.md at the start of"
echo "     non-trivial work (e.g. from its global/project instructions file)."
