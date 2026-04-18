# Changelog

## v0.0.2 — 2026-04-18

### Factory — delivery report on process exit
- Orchestrator now writes a full plain-text delivery report to `/tmp/factory-{pid}.txt` on every exit — success, failure, crash, interrupt, or SIGTERM — overwriting the brief status message so the caller's `watch cat` watcher picks it up without tailing logs
- Report includes: verdict (PASSED / FAILED / INCOMPLETE), scope, iterations used, files written, tests introduced, git diff --stat, full path to run.log, and QA tail on failure
- Report is also persisted to `{log_dir}/delivery_report.txt` for later reference
- No ANSI or box-drawing characters — safe for all terminal viewers

### Factory — imperative commit subjects
- Commit messages no longer start with builder self-talk ("I will add...", "This will implement...")
- `normalize_commit_subject()` strips first-person and future-tense preamble from `PLAN_AGREED` before writing the git commit subject line, producing conventional imperative phrases ("add retry logic to the webhook handler")

### Factory — EEC layer (shipped in v0.0.2 batch)
- Pre-write validation gate: 5-layer check (FORBIDDEN_WRITE, ALLOWED_WRITE, RELATIVE_TRAVERSAL, IMPORT_ROOT, CANONICAL_ENTRY_POINT) runs before any file hits disk; violations return a correction without consuming an iteration
- Canonical entry points: enforce wrapper usage over raw libraries (e.g. `get_mongodb()` not `pymongo`) with per-entry reason text fed back to the builder
- Maturity gate: `bootstrap` vs `established` — prompts user if test directories are missing before proceeding
- Scaffold gate: Y / N / Instead prompt when inspector requests new test structure
- `scope_type` drives iteration budget: `surgical_fix(2)`, `feature_add(3)`, `refactor(3)`, `new_domain(5)`, flowing from grill-me → write-a-prd → intake → orchestrator
- EEC injected into every builder and inspector system prompt as immutable ground truth
- `phase_deploy()` is EEC-driven — gates on `deploy_command` non-null; maps written file paths to service deploy targets; no Makefile assumption
- `--config` CLI override for factory_config.json

### EEC template and vz_eec.json
- `claude/factory/eec.template.json` — blank project EEC template; `deploy_command: null` is the explicit no-deploy signal
- `vz_eec.json` — VZ/service_desk specifics extracted from repo-agnostic tooling: vault import rules, canonical entry points (`get_mongodb`, `config.py`), service paths + deploy targets, MCP tool permissions, admin domain restriction, Playwright config
- Factory agents are now repo-agnostic; all project specifics live in `{repo}_eec.json`

### Deprecations
- `claude/agents/multi_agent/acme_coding_agent.md` — replaced by factory + vz_eec.json
- `claude/agents/multi_agent/coder.md` — replaced by builder.md
- `claude/agents/multi_agent/qa.md` — replaced by inspector.md

### Skills updated for scope_type
- **grill-me**: outputs `scope_type` in interview JSON
- **write-a-prd**: includes `### Scope` section in PRD template
- **prd-to-plan**: adds scope block to plan header with factory iteration hint
- **intake**: `scope_type` added to scoring table and JSON output

---

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
