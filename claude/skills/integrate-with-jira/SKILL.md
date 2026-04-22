---
name: integrate-with-jira
description: Jira integration — fetch tickets, list open issues, comment, transition status, take ownership, and create tickets from a PRD. Credentials read from $JIRA_URL, $JIRA_EMAIL, $JIRA_API_TOKEN — never hardcoded.
---

# Jira Integration

All operations read credentials from environment variables. Never hardcode tokens or URLs.

| Variable | Description |
|----------|-------------|
| `$JIRA_URL` | Jira instance base URL (e.g. `https://yourorg.atlassian.net`) |
| `$JIRA_EMAIL` | Atlassian account email |
| `$JIRA_API_TOKEN` | API token from id.atlassian.com |

Verify they are set before running any commands:
```bash
echo "$JIRA_URL" && echo "$JIRA_EMAIL" && echo "${JIRA_API_TOKEN:0:4}…"
```

---

## Operations

### Fetch a ticket

```bash
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$JIRA_URL/rest/api/3/issue/PROJ-123" | jq '{
    key: .key,
    summary: .fields.summary,
    status: .fields.status.name,
    assignee: .fields.assignee.displayName,
    description: .fields.description
  }'
```

### List open issues (JQL)

```bash
curl -s -G -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  --data-urlencode "jql=project = PROJ AND status != Done ORDER BY created DESC" \
  --data-urlencode "fields=key,summary,status,assignee" \
  "$JIRA_URL/rest/api/3/search" | jq '.issues[] | {key: .key, summary: .fields.summary.text, status: .fields.status.name}'
```

### Assign a ticket to yourself

```bash
# Get your account ID first
ACCOUNT_ID=$(curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  "$JIRA_URL/rest/api/3/myself" | jq -r '.accountId')

curl -s -X PUT -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"accountId\": \"$ACCOUNT_ID\"}" \
  "$JIRA_URL/rest/api/3/issue/PROJ-123/assignee"
```

### Add a comment

```bash
curl -s -X POST -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "body": {
      "version": 1, "type": "doc",
      "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Your comment here"}]}]
    }
  }' \
  "$JIRA_URL/rest/api/3/issue/PROJ-123/comment"
```

### Transition a ticket

```bash
# Step 1 — list available transitions
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  "$JIRA_URL/rest/api/3/issue/PROJ-123/transitions" | jq '.transitions[] | {id, name: .name}'

# Step 2 — execute (replace TRANSITION_ID)
curl -s -X POST -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transition": {"id": "TRANSITION_ID"}}' \
  "$JIRA_URL/rest/api/3/issue/PROJ-123/transitions"
```

### Fetch comments

```bash
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  "$JIRA_URL/rest/api/3/issue/PROJ-123?fields=comment" | \
  jq '.fields.comment.comments[] | {author: .author.displayName, created: .created[:10], body: .body}'
```

---

## Publishing a task breakdown as Jira tickets

This skill handles the mechanical creation — it does **not** own the decomposition.

If you have a PRD and need to break it into tickets:
1. Run **`prd-to-tasks`** first — it produces the slice breakdown and sizes each slice
2. Come back here to create the tickets once the breakdown is approved

If a PRD looks epic-sized (multiple independent features, cross-cutting concerns, more than ~5 slices) say so and suggest running `prd-to-tasks` before proceeding.

### Creating tickets from an approved breakdown

Create in dependency order (blockers first) so you can reference real issue keys in "Blocks" links. Use the body from the `prd-to-tasks` output — do not re-derive the decomposition here.

```bash
# Create a story
curl -s -X POST -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "fields": {
      "project": {"key": "PROJ"},
      "summary": "slice title",
      "issuetype": {"name": "Story"},
      "description": {
        "version": 1, "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "slice description"}]}]
      }
    }
  }' \
  "$JIRA_URL/rest/api/3/issue" | jq '{key: .key, url: ("'"$JIRA_URL"'/browse/" + .key)}'
```

```bash
# Link as "is blocked by"
curl -s -X POST -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": {"name": "Blocks"},
    "inwardIssue": {"key": "PROJ-BLOCKER"},
    "outwardIssue": {"key": "PROJ-NEW"}
  }' \
  "$JIRA_URL/rest/api/3/issueLink"
```

---

## MCP alternative

If `mcp-atlassian` is configured, these tools are available instead of curl:

| Tool | Purpose |
|------|---------|
| `jira_get_issue` | Fetch issue by key |
| `jira_search` | JQL search |
| `jira_create_issue` | Create story/task/bug |
| `jira_update_issue` | Update fields |
| `jira_transition_issue` | Change status |
| `jira_add_comment` | Add comment |
| `jira_create_issue_link` | Link issues |

Always call `jira_get_transitions` before transitioning — IDs vary per project workflow.

---

## Dev workflow comment templates

**Starting work:**
```
Starting implementation.
Branch: feat/PROJ-123-slug
```

**PR created:**
```
PR: https://github.com/org/repo/pull/NNN
```

**Work complete:**
```
Implementation complete. PR merged. All tests passing.
```

---

## Security

- Credentials live in shell env only — never in source code or skill files
- Rotate `$JIRA_API_TOKEN` immediately if exposed in git history
- Use least-privilege tokens scoped to required projects
