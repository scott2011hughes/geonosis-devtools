# Changelog

## v0.0.1 — 2026-04-18

### Agent improvements
- **inspector**: Added `VAULT IMPORT RULE` — blocks `shared.resources.*` module-level imports that kill pytest collection at the `config.py` vault read; includes mandatory self-check before outputting any test file
- **intake**: Added `mcp__jira__get_jira_issue` to tools list; updated source routing table to use MCP for Jira issues instead of unavailable CLI commands (`jira`, `gh`, `glab`)

### Multi-agent README
- Replaced mermaid flowchart with plain ASCII diagram (renders everywhere)
- Fixed commit message format (removed erroneous backtick wrapping)
- Updated orchestrator role description to "spawns coder + QA"

### Hooks
- `stop-hook.sh` / `vscode-diff.sh`: stripped trailing spaces from blank lines, added proper trailing newlines
- `vscode-diff.sh`: fixed continuation line indentation (4 → 2 spaces)

### Skills
- **playwright-cli**: Removed `--raw` output section; moved `--extension` flag from `attach` to `open` parameters
