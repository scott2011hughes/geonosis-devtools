---
name: write-a-prd
description: Create a PRD from a grill-me JSON handoff or a solid existing spec. Explores codebase, designs deep modules, submits as GitHub issue. Use when user wants to write a PRD, create a product requirements document, or plan a new feature.
---

You will receive either a grill-me JSON block or a raw feature description.

If given raw text with no JSON, invoke grill-me first.

## Steps

1. Parse the input. If open_questions is non-empty, stop and resolve them with the user before continuing.

2. Explore the repo to verify assertions and understand current state. Look for prior art for testing patterns.

   For every file in the touch radius that already exists: read it and identify behaviors that must survive the change — exported symbols, field names, side effects, invariants, integration points. These become the **Preserved Behaviors** section. If a file is being modified rather than created from scratch, this section is mandatory.

3. Sketch major modules to build or modify. Actively look for deep modules — ones that encapsulate significant functionality behind a simple, stable, testable interface. Confirm with user which modules need tests.

4. Write the PRD using the template below and submit as a GitHub issue. The file must open with the factory contract JSON block (see Contract Block below).

## PRD Template

### Scope
- **Type**: `surgical_fix` | `feature_add` | `refactor` | `new_domain`
- **Test strategy**: `pytest` | `playwright` | `combined`
- **Touch radius**: which layers, services, or files are affected
- **Rationale**: one sentence justifying the classification

### Problem Statement
The problem from the user's perspective.

### Solution
The solution from the user's perspective.

### User Stories
Numbered, extensive, behavioral. Format:
1. As a <actor>, I want <feature>, so that <benefit>

### Implementation Decisions
- Modules built/modified
- Interface changes
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

No file paths or code snippets — these go stale.

### Testing Decisions
- What makes a good test for this feature (external behavior only)
- Which modules get tests
- Prior art in codebase

### Preserved Behaviors
For each existing file being modified, list the behaviors the builder must not break. Be specific: name exported symbols, field names, function signatures, and integration points that must survive unchanged. If a file is new, write "N/A".

Example:
- **`core/config.py`**: `_load_infisical_secrets()` and its `os.environ` injection must remain; `app_version`, `debug`, `app_name` fields on `Settings` unchanged; `lru_cache` on `get_settings()` unchanged. Only `database_url` property changes.

### Constraints
HIPAA, security, performance, API contracts, rate limits — anything the coder must not violate.

### Out of Scope
Explicit. What this PR does NOT do.

### Definition of Done
- [ ] All user stories have passing tests
- [ ] No regressions in affected areas
- [ ] Open Questions empty
- [ ] Deployed to target env

### Open Questions
Must be empty before handoff to orchestrator.

### Further Notes
Anything else relevant.

## Contract Block

Every `.prd.md` file **must** open with this JSON block. The orchestrator reads it to configure the factory run — missing or wrong fields cause silent misbehavior.

```json
{
  "scope_type": "surgical_fix | feature_add | refactor | new_domain",
  "test_strategy": "pytest | playwright | combined",
  "feature": "slug-for-this-feature",
  "estimated_files": [
    "path/to/file1.py",
    "path/to/file2.vue"
  ]
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `scope_type` | yes | Controls max factory iterations (surgical_fix=2, feature_add/refactor=3, new_domain=5) |
| `test_strategy` | yes | `pytest` = backend only; `playwright` = UI only, mocked API; `combined` = both |
| `feature` | yes | Kebab-case slug used in git commit tag and log directory name |
| `estimated_files` | yes | Complete write scope — builder may only touch listed files |