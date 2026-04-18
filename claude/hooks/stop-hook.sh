#!/bin/bash
# Runs when Claude stops responding.
# 1. Reminds about uncommitted changes.
# 2. Auto-pushes if there are commits ahead of remote.

# Uncommitted changes reminder
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo '{"systemMessage":"Uncommitted changes pending — remember to commit."}'
fi

# Auto-push if on a named branch with commits ahead of remote
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -n "$BRANCH" ] && [ "$BRANCH" != "HEAD" ]; then
    git push origin "$BRANCH" >/dev/null 2>&1 || true
fi

exit 0
