#!/bin/bash
# Opens a VS Code diff (HEAD vs working file) after Claude edits a file.
# Receives Claude hook JSON on stdin.
 
FILE=$(jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0
 
REPO=$(git -C "$(dirname "$FILE")" rev-parse --show-toplevel 2>/dev/null)
REL=$(realpath --relative-to="$REPO" "$FILE" 2>/dev/null)
 
# Use the most recently installed VS Code server binary (survives updates)
CODE=$(ls -t /home/hughsc2/.vscode-server/cli/servers/Stable-*/server/bin/remote-cli/code 2>/dev/null | head -1)
[ -z "$CODE" ] && exit 0
 
HASH=$(echo "$FILE" | md5sum | cut -c1-8)
TMPFILE="/tmp/claude_diff_${HASH}_$(basename "$FILE")"
 
git -C "$REPO" show "HEAD:$REL" > "$TMPFILE" 2>/dev/null \
    && "$CODE" --no-wait --diff "$TMPFILE" "$FILE" 2>/dev/null \
    || "$CODE" --no-wait "$FILE" 2>/dev/null
 
exit 0
 