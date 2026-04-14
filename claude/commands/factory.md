Run `pwd` to confirm working directory. Then:

## Step 1 — Resolve the input

If an argument was provided, determine its type:

| Input | Action |
|-------|--------|
| `JIRA-1234` or `PROJECT-123` | run intake agent against jira issue |
| `owner/repo#42` | run intake agent against github issue |
| `group/repo#17` | run intake agent against gitlab issue |
| `NEW_*.prd.md` filename | treat as staging file, skip to Step 2 |
| no argument | scan staging/ for NEW_ files, skip to Step 2 |

If intake returns `INTAKE_GAP`, stop. Tell the user which fields are missing
and where the file was written in staging/. Do not proceed.

If intake returns `INTAKE_PASS`, write the contract JSON to the appropriate
staging/ subdirectory as `NEW_{repo}_{feature}.prd.md` and continue to Step 2.

## Step 2 — Find ready plans

```bash
ls {repo_root}/.claude/staging/github/NEW_*.prd.md \
   {repo_root}/.claude/staging/gitlab/NEW_*.prd.md \
   {repo_root}/.claude/staging/jira/NEW_*.prd.md 2>/dev/null
```

If no `NEW_` files exist, say:
"No plans ready. Run grill-me or provide an issue reference." and stop.

## Step 3 — Confirm receipt before promoting

Print a confirmation block listing every plan found:

    Ready to ship N plan(s):

      1. staging/github/NEW_emr_user-search.prd.md
      2. staging/jira/NEW_PROJECT_auth-tokens.prd.md

    Promote to IP_ and launch? (yes / abort)

Wait for explicit user confirmation. If the user says anything other than
yes/y, stop. Do not rename any files without confirmation.

## Step 4 — Rename NEW_ → IP_ after confirmation

```bash
for f in {repo_root}/.claude/staging/**/NEW_*.prd.md; do
  mv "$f" "${f/NEW_/IP_}"
done
```

## Step 5 — Launch orchestrator.py per plan

For each IP_ file, launch in background and capture PID:

```bash
python3 {repo_root}/.claude/factory/orchestrator.py \
  --plan {path/to/IP_file} &
echo $!
```

Capture each PID. orchestrator.py is responsible for:
- Writing /tmp/factory-{pid}.txt at each phase transition (live, dies with process)
- Writing {IP_file}.run sidecar at start, updating each phase (persistent, survives crash)
- Deleting {IP_file}.run when plan archives to done-

If orchestrator.py crashes:
- IP_ file remains in staging/
- .run sidecar records last known phase and iteration
- Resume with: python3 orchestrator.py --plan {IP_file}
- orchestrator.py detects existing .run sidecar and resumes from last phase

## Step 6 — Report

Print this block clearly — this is the user's primary reference for monitoring:

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    FACTORY LAUNCHED — N plan(s) shipping in background
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

      Plan 1: staging/github/IP_emr_user-search.prd.md
      PID:    48291
      Watch:  watch cat /tmp/factory-48291.txt

      Plan 2: staging/jira/IP_PROJECT_auth-tokens.prd.md
      PID:    48304
      Watch:  watch cat /tmp/factory-48304.txt

    If a process dies unexpectedly:
      Recover: python3 {repo_root}/.claude/factory/orchestrator.py \
               --plan {IP_file}

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Notes

- TODO: in mvp2 this report is written to a persistent log so you never
  have to hunt terminal history for the watch command again
- INTAKE_GAP files are never promoted to NEW_ — they stay in staging/
  for your review
- Never launch against an IP_ file — it is already in flight
