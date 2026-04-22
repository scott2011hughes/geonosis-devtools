---
name: integrate-with-gh
description: GitHub integration — list issues, view PRs, create issues, comment, close, and manage work items. Use when interacting with GitHub issues or pull requests. For converting a PRD into issues, run prd-to-tasks first to get the breakdown, then use this skill to create them.
---

# GitHub Integration

All operations use the `gh` CLI, which reads credentials from `$GH_TOKEN` or your active `gh auth login` session. Never hardcode tokens.

Verify auth before bulk operations: `gh auth status`

---

## Operations

### List issues

```bash
gh issue list --state open --limit 50
gh issue list --state open --label "bug"
gh issue list --assignee "@me"
```

### View an issue

```bash
gh issue view <number>
gh issue view <number> --comments
```

### Create an issue

```bash
gh issue create --title "title" --body "body"
```

### Close an issue

```bash
gh issue close <number>
gh issue close <number> --comment "Closing — implemented in PR #<pr>"
```

### Comment on an issue or PR

```bash
gh issue comment <number> --body "your comment"
gh pr comment <number> --body "your comment"
```

### Create a PR

```bash
gh pr create --title "feat: short title" --body "$(cat <<'EOF'
## Summary
- bullet

## Test plan
- [ ] item

🤖 Generated with Claude Code
EOF
)"
```

### View PR status / diff

```bash
gh pr view <number>
gh pr diff <number>
gh pr checks <number>
```

---

## Publishing a task breakdown as GitHub issues

This skill handles the mechanical creation — it does **not** own the decomposition.

If you have a PRD and need to break it into issues:
1. Run **`prd-to-tasks`** first — it produces the slice breakdown and sizes each slice
2. Come back here to create the issues once the breakdown is approved

If a PRD looks epic-sized (multiple independent features, cross-cutting concerns, more than ~5 slices) say so and suggest running `prd-to-tasks` before proceeding.

### Creating issues from an approved breakdown

Create in dependency order (blockers first) so you can reference real issue numbers. Use the body structure the breakdown already provides — do not re-derive or re-template it here.

```bash
gh issue create \
  --title "<slice title>" \
  --body "<body from prd-to-tasks output>"
```

Do NOT close or modify the parent PRD issue.

---

## Security

- Credentials come from `$GH_TOKEN` or `gh auth login` — never hardcode
- Confirm before closing or modifying issues in shared repos
