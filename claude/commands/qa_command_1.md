Run a QA-only coverage pass against existing code. Writes tests, runs them, and produces a bug report plan file. No code is changed. Use /ship afterward to start the fix cycle.

## Usage

`/qa <description of what to test>`

Example: `/qa we forgot to test the integration between dcrm-decom widget and the list-dcrm-decom API`

If invoked with no description, ask: "What existing behavior or API do you want test coverage for?"

---

## Steps to Execute

### Step 1 — Strategy Detection

Determine test strategy from the description and codebase:
- Contains "playwright" or describes a widget/portal/UI → `playwright`
- Contains "pytest" or describes a Python service → `pytest`
- Both present → `combined`
- Unclear → `playwright` (default for this platform)

### Step 2 — Feature Tag Derivation

Derive a short kebab-case tag from the description. Rules (same as acme Phase 2.5):
- Use the primary noun/verb (e.g. `api-integration`, `field-validation`)
- Max 3 words hyphenated
- Prefix with widget/domain slug if applicable (e.g. `dcrm-decom-api-integration`)

Store as `feature_tag` with `@` prefix. Print: `[qa-skill] Feature tag: <feature_tag>`

### Step 3 — QA Test Plan

Run at step start:
```bash
printf "\n\033[1;33m▶  QA COVERAGE — writing tests for: <description>\033[0m\n"
date '+QA-COVERAGE | WRITING TESTS | %H:%M:%S' > /tmp/acme-status.txt
```

Spawn the **qa** subagent with:

```
You are a senior QA engineer with an adversarial mindset.

The following describes EXISTING functionality that needs test coverage.
No code has been written — your job is to write tests that expose any gaps,
bugs, or missing behaviors in what already exists.

Description: <user description>
Test strategy: <strategy>
Feature tag: <feature_tag>
Working directory: <resolve at run time — orchestrator substitutes output of `pwd`>

Write thorough tests. Append `<feature_tag>` and the widget/domain slug tag
to every Playwright test title (e.g. `@dcrm-decom @api-integration`).

**Performance — group tests to avoid redundant navigations:**
Group related tests into `test.describe` blocks and put the shared `page.goto()` in a `test.beforeEach`. Tests that start from the same widget state (same URL, same precondition) should share a single navigation rather than each doing their own. Example:

```javascript
test.describe('Advanced panel toggle', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 30000 });
  });
  test('panel hidden by default @tag', async ({ page, widgetFrame }) => { ... });
  test('toggle shows panel @tag', async ({ page, widgetFrame }) => { ... });
  test('toggle again hides panel @tag', async ({ page, widgetFrame }) => { ... });
});
```

Note: `storageState` (auth.json) is already loaded at the Playwright config level — do not add it manually.

Output all test files in a single JSON block:
{
  "files": {
    "path/to/test.spec.js": "file content"
  }
}

Then output: TEST_PLAN_READY: <brief summary>
```

Exchange up to 8 messages with QA. Stop on `TEST_PLAN_READY:`. Write files from JSON block to disk.

### Step 4 — Pre-test Gate
```bash
printf "\n\033[1;33m▶  PRE-TEST PERMISSION CHECK\033[0m\n"
date '+QA-COVERAGE | PRE-FLIGHT | %H:%M:%S' > /tmp/acme-status.txt
set +e; bash ~/my_claude_automations/healthcheck.sh --pre-test; HC=$?; set -e
```

If HC=2: print the red blocked banner, list fixes, and STOP — do not run tests until user resolves issues.

### Step 5 — Run Tests
```bash
printf "\n\033[1;33m▶  RUNNING TESTS\033[0m\n"
date '+QA-COVERAGE | RUNNING TESTS | %H:%M:%S' > /tmp/acme-status.txt
```

Run based on strategy:
- **playwright**: `cd ~/my_claude_automations/playwright && npm test -- --grep "<feature_tag>"`
- **pytest**: `pytest --tb=short -v` in cwd
- **combined**: both sequentially

Capture stdout, stderr, exit code, duration.

### Step 6 — QA Evaluation

Send to QA subagent:

```
Tests ran. Exit code: <code>. Duration: <duration>s.

STDOUT (last 3000 chars): <stdout>
STDERR (last 1000 chars): <stderr>
```

Evaluate results in behavioral terms only. For each failure describe:
- What behavior was observed
- What behavior was expected
- Severity (critical/high/medium/low)
- Which component is affected (API slug, widget, function)

Do NOT include test function names, file paths, or assertion details.

Store QA's full evaluation as `qa_report`.

### Step 7 — Write Bug Report Plan
```bash
printf "\n\033[1;33m▶  WRITING BUG REPORT PLAN\033[0m\n"
date '+QA-COVERAGE | WRITING PLAN | %H:%M:%S' > /tmp/acme-status.txt
```

Write a plan file to `$(pwd)/.claude/planning/qa-<feature_tag_no_at>-<YYYYMMDD-HHMM>.md`:

```markdown
# Bug Report: <user description>

## Context
QA coverage run on <date>. Tests written for: <description>.
No code has been changed. Fixes should be implemented in the next acme cycle (/ship).

## Test Results
- Strategy: <strategy>
- Feature tag: <feature_tag>
- Tests run: <N> | Passed: <N> | Failed: <N>
- Duration: <Ns>

## Failures

<qa_report — behavioral descriptions only, formatted as subsections>

## Passing Tests (confidence areas)
<brief list of what was verified working>

## Recommended Fix Scope
<1-paragraph summary for the coder: what needs to change and where>
```

If all tests passed, write the plan anyway noting full coverage with no failures found.

### Step 8 — Stop
```bash
date '+QA-COVERAGE | COMPLETE | %H:%M:%S' > /tmp/acme-status.txt
```

Print:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QA RUN COMPLETE
Tests: <N> passed · <N> failed · (<duration>s)
Bug report plan: $(pwd)/.claude/planning/qa-<tag>-<timestamp>.md

Review the plan, edit if needed, then type /ship to start
the fix cycle with acme_coding_agent.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Do NOT proceed further. Do NOT invoke acme. Stop here.
