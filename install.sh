#!/usr/bin/env bash
# install.sh — install geonosis-devtools into a target repo's .claude/ directory
#
# Usage:
#   ./install.sh <path-to-target-repo> [--link]
#
#   Default: copies files — fully self-contained, no dependency on this repo.
#   --link:  symlinks instead — updates to geonosis-devtools propagate automatically.
#            Use this only if you own both repos and want live updates.
#
# Safe to re-run — overwrites existing files, skips existing directories.

set -euo pipefail

DEVTOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$DEVTOOLS_DIR/claude"

# --------------------------------------------------------------------------
# Args
# --------------------------------------------------------------------------

USE_LINKS=false
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --link) USE_LINKS=true ;;
    *)      TARGET="$arg" ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 <path-to-target-repo> [--link]"
  exit 1
fi

TARGET="$(cd "$TARGET" && pwd)"
REPO_NAME="$(basename "$TARGET")"
CLAUDE_DIR="$TARGET/.claude"

echo ""
echo "geonosis-devtools installer"
echo "  source:  $DEVTOOLS_DIR"
echo "  target:  $TARGET"
echo "  mode:    $( $USE_LINKS && echo 'symlink (--link)' || echo 'copy (default)' )"
echo ""

# --------------------------------------------------------------------------
# Install helper — copy or symlink a source into dest
#   directories: skip if dest exists (mkdir -p is safe)
#   files:       always overwrite (enables re-run as update)
#   --link mode: keeps old skip-if-exists behavior (symlinks are already live)
# --------------------------------------------------------------------------

install_item() {
  local src="$1" dest="$2" label="$3"
  if $USE_LINKS; then
    if [[ -L "$dest" || -e "$dest" ]]; then
      echo "  [skip]    $label — already linked"
      return
    fi
    ln -s "$src" "$dest"
    echo "  [link]    $label"
    return
  fi
  if [[ -d "$src" ]]; then
    local existed=false
    [[ -d "$dest" ]] && existed=true
    mkdir -p "$dest"
    cp -rf "$src/." "$dest/"
    $existed && echo "  [update]  $label" || echo "  [copy]    $label"
  else
    local existed=false
    [[ -e "$dest" ]] && existed=true
    cp "$src" "$dest"
    $existed && echo "  [update]  $label" || echo "  [copy]    $label"
  fi
}

# --------------------------------------------------------------------------
# .claude root
# --------------------------------------------------------------------------

mkdir -p "$CLAUDE_DIR"

# --------------------------------------------------------------------------
# agents, commands, hooks, skills — whole directories
# --------------------------------------------------------------------------

for dir in agents commands hooks skills; do
  install_item "$SRC/$dir" "$CLAUDE_DIR/$dir" ".claude/$dir"
done

# --------------------------------------------------------------------------
# factory/ — always a real directory so logs/ stays in the target repo
# --------------------------------------------------------------------------

mkdir -p "$CLAUDE_DIR/factory/logs"

for file in orchestrator.py builder.md inspector.md eec.template.json factory_config.json README.md; do
  src_file="$SRC/factory/$file"
  [[ -f "$src_file" ]] || continue
  install_item "$src_file" "$CLAUDE_DIR/factory/$file" ".claude/factory/$file"
done

# --------------------------------------------------------------------------
# EEC bootstrap — always a copy; this file is project-specific
# --------------------------------------------------------------------------

EEC_FILE="$TARGET/${REPO_NAME}_eec.json"
if [[ -f "$EEC_FILE" ]]; then
  echo "  [skip]    ${REPO_NAME}_eec.json — already exists"
else
  cp "$SRC/factory/eec.template.json" "$EEC_FILE"
  sed -i "s/\"repo\": \"\"/\"repo\": \"$REPO_NAME\"/" "$EEC_FILE"
  echo "  [create]  ${REPO_NAME}_eec.json"
fi

# --------------------------------------------------------------------------
# Done
# --------------------------------------------------------------------------

echo ""
echo "Done. Next steps:"
echo ""
echo "  1. Edit ${REPO_NAME}_eec.json — fill in maturity, paths, imports,"
echo "     canonical_entry_points. Set maturity='established' once your"
echo "     test directories exist."
echo ""
echo "  2. Install the factory Python dependency (if not already installed):"
echo "     pip install anthropic"
echo ""
echo "  3. Run the factory against a plan file:"
echo "     python3 $CLAUDE_DIR/factory/orchestrator.py --plan path/to/IP_feature.prd.md"
echo ""
