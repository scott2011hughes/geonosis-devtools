---
name: worklog
description: Use when the user asks for a daily worklog, work summary, or "what did I do today". Collects git commits across all repos under /opt/apps and Jira activity for today, synthesizes into a narrative summary of meaningful work, and saves to ~/worklogs/.
---

# Daily Worklog

Summarize today's meaningful work from git history and Jira, then save to `~/worklogs/YYYY-MM-DD.md`.

## Step 1 — Collect Git Activity

Find all git repos under /opt/apps:
```bash
find /opt/apps -maxdepth 2 -name ".git" -type d 2>/dev/null | sed 's|/.git$||' | sort
```

For each repo found, get today's commits and changed files:
```bash
git -C <repo> log --since="midnight" --oneline --no-merges 2>/dev/null
git -C <repo> diff --stat $(git -C <repo> log --since="midnight" --format="%H" 2>/dev/null | tail -1)..HEAD 2>/dev/null
```

## Step 2 — Collect Jira Activity

Use the `jira` MCP server to find issues touched today:
```
assignee = currentUser() AND updated >= startOfDay() ORDER BY updated DESC
```

Note any status transitions or comments added today.

## Step 3 — Apply the 15-Minute Heuristic

**Include as a substantial work item:**
- Any topic with 2+ commits
- A single commit touching 4+ files
- Any Jira ticket with a status transition or substantive comment today

**Collapse or skip:**
- Single commits touching 1-2 files for minor fixes
- Commits whose message contains: typo, formatting, lint, whitespace, revert

**Group related commits** into one narrative item — don't list individual commit hashes.

## Step 4 — Write the Summary

Format (markdown):

```
# Worklog — YYYY-MM-DD

## <Repo Name>
- **<Work Item>** — <1-2 sentences: what was done and why>

## Jira Activity
- **<TICKET-KEY> — <Title>**: <what happened — transition, comments, outcome>

---
*Generated from git log + Jira*
```

**Voice:** first-person past tense, outcome-focused ("Refactored the sync stage to..."), not task-list style ("Worked on sync stage").

## Step 5 — Save and Display

1. Create `~/worklogs/` if it doesn't exist
2. Save to `~/worklogs/YYYY-MM-DD.md`
3. Display the full content
4. Tell the user the file path

## Notes

- Only include work from midnight to now
- If a repo has no commits today, skip it entirely — don't mention it
- If there's no activity anywhere, say so explicitly
- Never include credentials, tokens, or internal hostnames in the worklog

<!-- FLAG: Jira integration needs to be adapted for EMR/STP issue tracker — TBD whether Jira, Linear, or Patrick's Kibana tool. May need a slimmer/less chatty version for a small 3-person team vs corporate CYA use case. -->
