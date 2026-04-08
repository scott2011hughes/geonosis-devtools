Run `pwd` to get the current working directory. Find the most recently modified plan file in `$(pwd)/.claude/planning/` and read its full contents. Then invoke the `acme_coding_agent` subagent with the plan content as the feature request, prefixed with:

"The following plan has been approved by the user. Implement it exactly as described — do not re-negotiate the plan with the coder, proceed directly to PHASE 2 (strategy detection):"

Pass the full plan text after that prefix.
