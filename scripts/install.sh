#!/usr/bin/env bash
# Set up PMI for the current user (idempotent):
#   1. install Python dependencies with uv      3. link `pmi` into ~/.local/bin
#   2. create the data folder (data/)           4. link the Claude Code skills into ~/.claude/skills
# Options: --no-skills  --no-semantic (keyword search only)  --with-model (download the embedding model)
set -euo pipefail
ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${PMI_BIN_DIR:-$HOME/.local/bin}"
SKILLS_DIR="${PMI_SKILLS_DIR:-$HOME/.claude/skills}"
SKILLS=(pmi-notion pmi-linear pmi-slack)
WITH_SKILLS=1
WITH_MODEL=0
SEMANTIC=1
EXTRA=(--extra semantic)

usage() { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; }
for arg in "$@"; do
  case "$arg" in
    --no-skills) WITH_SKILLS=0 ;;
    --no-semantic) EXTRA=(); SEMANTIC=0; export PMI_NO_SEMANTIC=1 ;;
    --with-model) WITH_MODEL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }
link() {  # link TARGET DEST: create or refresh a symlink, never replace a real file or folder
  local target="$1" dest="$2"
  if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$target" ]; then echo "  ✓ $dest (already linked)"; return; fi
    ln -sfn "$target" "$dest" && echo "  ↻ $dest → $target (re-pointed)"
  elif [ -e "$dest" ]; then
    echo "  ! $dest exists and is not a symlink — left untouched" >&2
  else
    ln -s "$target" "$dest" && echo "  + $dest → $target"
  fi
}

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

step "Installing dependencies (uv sync ${EXTRA[*]:-})"
(cd "$ROOT" && uv sync --frozen ${EXTRA[@]+"${EXTRA[@]}"})

step "Creating the data folder"
"$ROOT/pmi" init

step "Linking the pmi command into $BIN_DIR"
mkdir -p "$BIN_DIR"
link "$ROOT/pmi" "$BIN_DIR/pmi"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  ! $BIN_DIR is not on your PATH. Add this to your shell profile:" >&2
     echo "      export PATH=\"$BIN_DIR:\$PATH\"" >&2 ;;
esac

if [ "$WITH_SKILLS" = 1 ]; then
  step "Linking Claude Code skills into $SKILLS_DIR"
  mkdir -p "$SKILLS_DIR"
  for s in "${SKILLS[@]}"; do link "$ROOT/integrations/claude-code/skills/$s" "$SKILLS_DIR/$s"; done
fi

if [ "$WITH_MODEL" = 1 ]; then
  step "Downloading the local embedding model (~220 MB)"
  "$ROOT/pmi" model download
fi

cat <<NEXT

PMI is ready. Data lives in $ROOT/data (never committed).

Connect Claude Code (all projects):
    claude mcp add --scope user pmi -- "$ROOT/pmi" mcp
Other MCP clients use the same command: "$ROOT/pmi" with args ["mcp"]  (see README).

Try it:
    pmi import "$ROOT/examples/project-a" --extract
    pmi context "为什么 Project A 不采用方案 B"
NEXT
if [ "$SEMANTIC" = 0 ]; then
  echo
  echo "Keyword-only install: add  export PMI_NO_SEMANTIC=1  to your shell profile (and pass"
  echo "-e PMI_NO_SEMANTIC=1 to 'claude mcp add'), otherwise pmi installs the semantic extra on first run."
fi
