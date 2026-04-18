---
name: write-a-prd
description: Create a PRD from a grill-me JSON handoff or a solid existing spec. Explores codebase, designs deep modules, submits as GitHub issue. Use when user wants to write a PRD, create a product requirements document, or plan a new feature.
---

You will receive either a grill-me JSON block or a raw feature description.

If given raw text with no JSON, invoke grill-me first.

## Steps

1. Parse the input. If open_questions is non-empty, stop and resolve them with the user before continuing.

2. Explore the repo to verify assertions and understand current state. Look for prior art for testing patterns.

3. Sketch major modules to build or modify. Actively look for deep modules — ones that encapsulate significant functionality behind a simple, stable, testable interface. Confirm with user which modules need tests.

4. Write the PRD using the template below and submit as a GitHub issue.

## PRD Template

### Scope
- **Type**: `surgical_fix` | `feature_add` | `refactor` | `new_domain`
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