---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
---

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead.

When there are no remaining open questions, output a single JSON block and nothing else:

```json
{
  "feature": "",
  "scope_type": "surgical_fix | feature_add | refactor | new_domain",
  "context": {
    "repo": "",
    "services": [],
    "likely_files": []
  },
  "problem_statement": "",
  "solution": "",
  "user_stories": [],
  "implementation_decisions": [],
  "testing_decisions": [],
  "constraints": [],
  "out_of_scope": [],
  "definition_of_done": [],
  "open_questions": []
}
```

`scope_type` is derived from the interview — do not ask explicitly. Infer from implementation_decisions:
- `surgical_fix` — targeted change to existing behavior, 1–2 files
- `feature_add` — net-new capability within existing structure
- `refactor` — internal restructuring, no new external behavior
- `new_domain` — new module, service, or cross-cutting concern

open_questions must be empty before outputting. If any remain, keep interviewing.