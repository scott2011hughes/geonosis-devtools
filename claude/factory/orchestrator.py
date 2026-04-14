#!/usr/bin/env python3
"""
lights-out factory — orchestrator.py
Drives the implement → test → feedback loop for a single PRD plan file.
Invoked by /factory command per IP_ plan file.

Usage:
  python3 orchestrator.py --plan path/to/IP_feature.prd.md
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "factory_config.json"

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[factory] ERROR: config not found at {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        raw = f.read()
    # Expand {repo_root} to the factory directory's parent parent (.claude/factory -> .claude -> repo)
    repo_root = str(CONFIG_PATH.parent.parent.parent)
    raw = raw.replace("{repo_root}", repo_root)
    return json.loads(raw)

# ---------------------------------------------------------------------------
# Agent MD loader — strips YAML frontmatter, returns body as system prompt
# ---------------------------------------------------------------------------

def load_agent_prompt(config: dict, agent_name: str) -> str:
    agent_dir = Path(config.get("agent_dir", ".claude/agents"))
    md_path = agent_dir / f"{agent_name}.md"
    if not md_path.exists():
        print(f"[factory] WARNING: agent file not found: {md_path} — using fallback prompt")
        return f"You are a {agent_name} agent."
    content = md_path.read_text()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content.strip()

# ---------------------------------------------------------------------------
# Sidecar / lock helpers
# ---------------------------------------------------------------------------

def sidecar_path(plan: Path) -> Path:
    return plan.with_suffix(plan.suffix + ".run")

def lock_path(plan: Path) -> Path:
    return plan.with_suffix(plan.suffix + ".lock")

def write_sidecar(plan: Path, state: dict):
    with open(sidecar_path(plan), "w") as f:
        json.dump(state, f, indent=2)

def read_sidecar(plan: Path) -> dict | None:
    p = sidecar_path(plan)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)

def write_lock(plan: Path):
    with open(lock_path(plan), "w") as f:
        f.write(str(os.getpid()))

def check_lock(plan: Path) -> bool:
    """Return True if we still own the lock."""
    lp = lock_path(plan)
    if not lp.exists():
        return False
    with open(lp) as f:
        return f.read().strip() == str(os.getpid())

def release_lock(plan: Path):
    lp = lock_path(plan)
    if lp.exists():
        lp.unlink()

def release_sidecar(plan: Path):
    sp = sidecar_path(plan)
    if sp.exists():
        sp.unlink()

# ---------------------------------------------------------------------------
# Status / logging
# ---------------------------------------------------------------------------

def status_path(pid: int) -> Path:
    return Path(f"/tmp/factory-{pid}.txt")

def write_status(pid: int, message: str):
    with open(status_path(pid), "w") as f:
        f.write(f"{message} | {datetime.now().strftime('%H:%M:%S')}\n")

def log(log_dir: Path, message: str):
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "run.log", "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")
    print(f"[factory] {message}")

def banner(message: str):
    line = "━" * 62
    print(f"\n\033[1;36m{line}\033[0m")
    print(f"\033[1;36m  {message}\033[0m")
    print(f"\033[1;36m{line}\033[0m")

def phase_start(pid: int, log_dir: Path, phase: str, detail: str = ""):
    msg = f"PHASE {phase}{' | ' + detail if detail else ''}"
    print(f"\n\033[1;33m▶  {msg}\033[0m")
    write_status(pid, msg)
    log(log_dir, f"START {msg}")

def phase_done(pid: int, log_dir: Path, phase: str, detail: str = ""):
    msg = f"PHASE {phase} DONE{' | ' + detail if detail else ''}"
    print(f"\033[1;32m✔  {msg}\033[0m")
    write_status(pid, msg)
    log(log_dir, f"DONE  {msg}")

def phase_fail(pid: int, log_dir: Path, phase: str, detail: str = ""):
    msg = f"PHASE {phase} FAILED{' | ' + detail if detail else ''}"
    print(f"\033[1;31m✗  {msg}\033[0m")
    write_status(pid, msg)
    log(log_dir, f"FAIL  {msg}")

# ---------------------------------------------------------------------------
# Lock guard — called before every destructive action
# ---------------------------------------------------------------------------

def assert_lock(plan: Path, log_dir: Path, pid: int):
    if not check_lock(plan):
        phase_fail(pid, log_dir, "LOCK", "lock lost — another process owns this plan, exiting")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Dirty plan detection and reset
# ---------------------------------------------------------------------------

def handle_dirty_plan(plan: Path, cwd: Path, log_dir: Path, pid: int):
    """
    Called when a .run sidecar exists at startup.
    If a commit with the feature tag exists since run start → archive and deliver.
    Otherwise → git reset hard, remove sidecar, start fresh.
    """
    sidecar = read_sidecar(plan)
    feature_tag = sidecar.get("feature_tag", "")
    started = sidecar.get("started", "")

    log(log_dir, f"Dirty plan detected. Sidecar: phase={sidecar.get('phase')} tag={feature_tag}")

    # Check for an existing commit with the feature tag since run start
    result = subprocess.run(
        ["git", "-C", str(cwd), "log", "--oneline", f"--since={started}", "--grep", feature_tag],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        log(log_dir, "Commit found — archiving and delivering without re-running")
        archive_plan(plan, log_dir, pid, partial=True)
        print(f"\033[1;32m✔  Previous run committed successfully. Archived as done.\033[0m")
        sys.exit(0)
    else:
        log(log_dir, "No commit found — resetting repo and starting fresh")
        subprocess.run(["git", "-C", str(cwd), "reset", "--hard", "HEAD"], check=True)
        subprocess.run(["git", "-C", str(cwd), "clean", "-fd"], check=True)
        release_sidecar(plan)
        log(log_dir, "Repo reset. Sidecar removed. Starting fresh.")

# ---------------------------------------------------------------------------
# Claude SDK call
# ---------------------------------------------------------------------------

def call_claude(client: anthropic.Anthropic, model: str, system: str, messages: list,
                max_tokens: int = 4096) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return response.content[0].text

# ---------------------------------------------------------------------------
# Sentinel extraction
# ---------------------------------------------------------------------------

def extract_sentinel(text: str, sentinel: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(sentinel):
            return line[len(sentinel):].strip()
    return None

# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

def phase_plan_negotiation(client, config, plan_content, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "1/8", "PLAN NEGOTIATION")
    assert_lock(plan, log_dir, pid)

    builder_model = config["models"]["builder"]
    system = load_agent_prompt(config, "builder")
    messages = [{"role": "user", "content": (
        f"PRD:\n\n{plan_content}\n\n"
        "Review this PRD and confirm your implementation plan. "
        "Output PLAN_AGREED: <one paragraph> when ready."
    )}]

    agreed_plan = None
    for turn in range(10):
        reply = call_claude(client, builder_model, system, messages)
        messages.append({"role": "assistant", "content": reply})
        sentinel = extract_sentinel(reply, "PLAN_AGREED:")
        if sentinel:
            agreed_plan = sentinel
            break
        messages.append({"role": "user", "content": "Continue refining the plan."})

    if not agreed_plan:
        agreed_plan = messages[-1]["content"][:500]

    sidecar["phase"] = "PLAN_NEGOTIATION_DONE"
    sidecar["agreed_plan"] = agreed_plan
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "1/8", agreed_plan[:80])
    return agreed_plan


def phase_strategy_detection(agreed_plan, plan_content, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "2/8", "STRATEGY DETECTION")

    combined = (agreed_plan + plan_content).lower()
    if "playwright" in combined and ("pytest" in combined or "python test" in combined):
        strategy = "combined"
    elif "playwright" in combined:
        strategy = "playwright"
    elif "pytest" in combined or "python test" in combined:
        strategy = "pytest"
    elif "api_mcp" in combined:
        strategy = "api_mcp"
    else:
        strategy = "pytest"

    sidecar["phase"] = "STRATEGY_DONE"
    sidecar["strategy"] = strategy
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "2/8", strategy)
    return strategy


def phase_feature_tag(agreed_plan, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "2.5/8", "FEATURE TAG")

    words = agreed_plan.lower().split()
    stopwords = {"a", "an", "the", "and", "or", "to", "for", "of", "in", "with", "that", "this"}
    tag_words = [w for w in words if w.isalpha() and w not in stopwords][:3]
    feature_tag = "@" + "-".join(tag_words)

    sidecar["phase"] = "FEATURE_TAG_DONE"
    sidecar["feature_tag"] = feature_tag
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "2.5/8", feature_tag)
    return feature_tag


def phase_test_plan(client, config, agreed_plan, strategy, feature_tag, pid, log_dir, sidecar, plan, cwd):
    phase_start(pid, log_dir, "3/8", "TEST PLAN CREATION (private)")
    assert_lock(plan, log_dir, pid)

    inspector_model = config["models"]["inspector"]
    playwright_dir = Path(config.get("playwright_dir", "~/my_claude_automations/playwright")).expanduser()

    system = load_agent_prompt(config, "inspector")
    messages = [{"role": "user", "content": (
        f"Implementation plan:\n\n{agreed_plan}\n\n"
        f"Test strategy: {strategy}. Feature tag: {feature_tag}. "
        "Create the test plan and output TEST_PLAN_READY: <summary> when done."
    )}]

    test_summary = None
    test_files = {}
    test_command = None

    for _ in range(8):
        reply = call_claude(client, inspector_model, system, messages)
        messages.append({"role": "assistant", "content": reply})

        # Extract JSON block
        if "```json" in reply:
            try:
                json_str = reply.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_str)
                test_files = data.get("files", {})
                test_command = data.get("command")
            except Exception:
                pass

        sentinel = extract_sentinel(reply, "TEST_PLAN_READY:")
        if sentinel:
            test_summary = sentinel
            break
        messages.append({"role": "user", "content": "Continue building the test plan."})

    # Write test files to disk
    for rel_path, content in test_files.items():
        if strategy == "playwright":
            dest = playwright_dir / rel_path
        else:
            dest = cwd / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    sidecar["phase"] = "TEST_PLAN_DONE"
    sidecar["test_summary"] = test_summary or "test plan created"
    sidecar["test_command"] = test_command
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "3/8", test_summary or "")
    # Return test info but NEVER pass to builder
    return test_summary, test_files, test_command


def phase_implement(client, config, agreed_plan, feedback, iteration, pid, log_dir, sidecar, plan, cwd):
    phase_start(pid, log_dir, f"4/8", f"IMPLEMENT iteration {iteration}")
    assert_lock(plan, log_dir, pid)

    builder_model = config["models"]["builder"]
    system = load_agent_prompt(config, "builder")

    if iteration == 1:
        prompt = f"Implement this plan:\n\n{agreed_plan}"
    else:
        prompt = f"Fix the following issues:\n\n{feedback}\n\nOriginal plan:\n\n{agreed_plan}"

    reply = call_claude(client, builder_model, system,
                        [{"role": "user", "content": prompt}], max_tokens=8192)

    written_files = []
    if "```json" in reply:
        try:
            json_str = reply.split("```json")[1].split("```")[0].strip()
            data = json.loads(json_str)
            for rel_path, content in data.get("files", {}).items():
                # Path safety — reject traversal attempts
                dest = (cwd / rel_path).resolve()
                if not str(dest).startswith(str(cwd.resolve())):
                    log(log_dir, f"REJECTED unsafe path: {rel_path}")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content)
                written_files.append(rel_path)
        except Exception as e:
            log(log_dir, f"Failed to parse builder JSON: {e}")

    sidecar["phase"] = f"IMPLEMENT_{iteration}_DONE"
    sidecar["iteration"] = iteration
    sidecar["written_files"] = written_files
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "4/8", f"{len(written_files)} files written")
    return written_files


def phase_preflight(config, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "4.5/8", "PRE-FLIGHT CHECK")
    assert_lock(plan, log_dir, pid)

    healthcheck = Path(config.get("healthcheck_script", "~/my_claude_automations/healthcheck.sh")).expanduser()
    if not healthcheck.exists():
        log(log_dir, "No healthcheck script found — skipping preflight")
        phase_done(pid, log_dir, "4.5/8", "skipped")
        return 0

    result = subprocess.run(["bash", str(healthcheck), "--pre-test"], capture_output=True)
    exit_code = result.returncode

    if exit_code == 0:
        phase_done(pid, log_dir, "4.5/8", "passed")
    elif exit_code == 1:
        print(f"\033[1;33m△  Pre-flight warnings — proceeding with caution\033[0m")
        log(log_dir, "Pre-flight warnings")
    else:
        phase_fail(pid, log_dir, "4.5/8", "BLOCKED")
        print(f"""
\033[1;31m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m
\033[1;31m  ✗  PRE-FLIGHT FAILED — TESTS BLOCKED\033[0m
\033[1;31m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m

Fix the issues above, then:
  continue  — re-run preflight and proceed
  skip      — skip tests, go to commit
  abort     — stop entirely
""")
        choice = input("> ").strip().lower()
        if choice == "abort":
            sys.exit(1)
        elif choice == "skip":
            return -1  # signal to skip phase 5
        elif choice == "continue":
            return phase_preflight(config, pid, log_dir, sidecar, plan)

    return exit_code


def phase_qa_evaluation(client, config, strategy, test_command, cwd, pid, log_dir,
                        sidecar, plan, iteration):
    phase_start(pid, log_dir, f"5/8", f"QA EVALUATION iteration {iteration}")
    assert_lock(plan, log_dir, pid)

    inspector_model = config["models"]["inspector"]
    playwright_dir = Path(config.get("playwright_dir", "~/my_claude_automations/playwright")).expanduser()

    # Run tests
    start = time.time()
    if strategy == "playwright":
        cmd = ["npm", "test"]
        test_cwd = playwright_dir
    elif strategy == "pytest":
        cmd = ["pytest", "--tb=short", "-v"]
        test_cwd = cwd
    elif strategy == "combined":
        cmd = ["pytest", "--tb=short", "-v"]
        test_cwd = cwd
    elif test_command:
        cmd = test_command
        test_cwd = cwd
    else:
        cmd = ["pytest", "--tb=short", "-v"]
        test_cwd = cwd

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=test_cwd)
    duration = round(time.time() - start, 1)
    exit_code = result.returncode
    stdout = result.stdout[-3000:]
    stderr = result.stderr[-1000:]

    # Send to inspector for behavioral evaluation
    system = load_agent_prompt(config, "inspector")
    prompt = (
        f"Tests ran. Exit code: {exit_code}. Duration: {duration}s.\n\n"
        f"STDOUT (last 3000 chars):\n{stdout}\n\n"
        f"STDERR (last 1000 chars):\n{stderr}"
    )
    qa_report = call_claude(client, inspector_model, system,
                            [{"role": "user", "content": prompt}])

    passed = "QA_VERDICT: PASS" in qa_report or exit_code == 0

    sidecar["phase"] = f"QA_{iteration}_DONE"
    sidecar["qa_report"] = qa_report
    sidecar["last_exit_code"] = exit_code
    write_sidecar(plan, sidecar)

    if passed:
        phase_done(pid, log_dir, "5/8", f"PASSED exit={exit_code} {duration}s")
    else:
        phase_fail(pid, log_dir, "5/8", f"FAILED exit={exit_code} {duration}s")

    return passed, qa_report


def sanitize_feedback(qa_report: str) -> str:
    """Strip test internals — only behavioral descriptions reach the builder."""
    lines = []
    skip_prefixes = ("assert", "assertionerror", "e assert", "test_", "def test")
    for line in qa_report.splitlines():
        ll = line.lower().strip()
        if any(ll.startswith(p) for p in skip_prefixes):
            continue
        if ".py::" in line or ".spec.js" in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def phase_git_commit(agreed_plan, feature_tag, written_files, passed, cwd,
                     config, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "7/8", "GIT COMMIT")
    assert_lock(plan, log_dir, pid)

    status = "passing" if passed else "partial"
    summary = agreed_plan[:72]
    commit_msg = f"feat: {summary} [{status}] {feature_tag}"

    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", commit_msg], check=True)

    # Open VS Code diffs
    vscode_diff = Path(config.get("vscode_diff_script", "")).expanduser()
    if vscode_diff.exists():
        subprocess.run(["bash", str(vscode_diff)], cwd=cwd)

    diff_stat = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--stat", "HEAD~1", "HEAD"],
        capture_output=True, text=True
    ).stdout

    sidecar["phase"] = "COMMITTED"
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "7/8", commit_msg[:60])
    log(log_dir, f"Diff stat:\n{diff_stat}")

    print(f"\n{diff_stat}")
    print(f"\n\033[1;33mReview the diff above. Push when satisfied:\033[0m")
    print(f"  git -C {cwd} push\n")


def archive_plan(plan: Path, log_dir: Path, pid: int, partial: bool = False):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    stem = plan.name.replace("IP_", "")
    done_name = f"done-{timestamp}-{stem}"
    dest = plan.parent / done_name
    plan.rename(dest)
    release_sidecar(plan)
    release_lock(plan)
    log(log_dir, f"Archived to {done_name}")


def phase_deliver(agreed_plan, strategy, written_files, passed, qa_report,
                  iteration, max_iterations, pid, log_dir, plan):
    phase_start(pid, log_dir, "8/8", "DELIVER")

    result_str = "PASSED ✓" if passed else f"PARTIAL — {iteration}/{max_iterations} iterations"
    banner("LIGHTS-OUT FACTORY — DELIVERY COMPLETE")
    print(f"""
{'=' * 62}
DELIVERY REPORT
{'=' * 62}
Result:     {result_str}
Iterations: {iteration}/{max_iterations}
Strategy:   {plan.parent.name}
Files:      {chr(10).join('  ' + f for f in written_files)}
Git:        committed locally — PUSH PENDING YOUR APPROVAL

QA Report:
{qa_report}
{'=' * 62}
""")

    archive_plan(plan, log_dir, pid, partial=not passed)
    write_status(pid, f"COMPLETE | {result_str}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="lights-out factory orchestrator")
    parser.add_argument("--plan", required=True, help="Path to IP_ plan file")
    args = parser.parse_args()

    plan = Path(args.plan).resolve()
    if not plan.exists():
        print(f"[factory] ERROR: plan file not found: {plan}")
        sys.exit(1)

    config = load_config()
    max_iterations = config.get("max_iterations", 3)
    cwd = Path(config.get("repo_root", ".")).resolve()
    pid = os.getpid()

    # Log directory per plan
    plan_stem = plan.name.replace("IP_", "").replace(".prd.md", "")
    log_dir = Path(config.get("log_dir", "claude/factory/logs")).resolve() / plan_stem
    log_dir.mkdir(parents=True, exist_ok=True)

    banner("LIGHTS-OUT FACTORY — STARTING")
    print(f"\033[0;37m  Watch: watch cat /tmp/factory-{pid}.txt\033[0m")
    print(f"\033[0;37m  Log:   {log_dir}/run.log\033[0m\n")
    write_status(pid, "STARTING")

    # Check git
    git_check = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True
    )
    is_git = git_check.returncode == 0

    # Handle dirty plan
    if sidecar_path(plan).exists():
        handle_dirty_plan(plan, cwd, log_dir, pid)

    # Acquire lock
    write_lock(plan)
    log(log_dir, f"Lock acquired. PID={pid}")

    # Init sidecar
    plan_content = plan.read_text()
    sidecar = {
        "pid": pid,
        "plan": str(plan),
        "started": datetime.now().isoformat(),
        "phase": "STARTING",
        "iteration": 0,
        "strategy": None,
        "feature_tag": None,
    }
    write_sidecar(plan, sidecar)

    client = anthropic.Anthropic()

    try:
        # Phase 1 — Plan negotiation
        agreed_plan = phase_plan_negotiation(
            client, config, plan_content, pid, log_dir, sidecar, plan)

        # Phase 2 — Strategy
        strategy = phase_strategy_detection(
            agreed_plan, plan_content, pid, log_dir, sidecar, plan)

        # Phase 2.5 — Feature tag
        feature_tag = phase_feature_tag(
            agreed_plan, pid, log_dir, sidecar, plan)

        # Phase 3 — Test plan (private)
        test_summary, test_files, test_command = phase_test_plan(
            client, config, agreed_plan, strategy, feature_tag,
            pid, log_dir, sidecar, plan, cwd)

        # Implement → test loop
        passed = False
        qa_report = ""
        feedback = ""
        written_files = []

        for iteration in range(1, max_iterations + 1):
            sidecar["iteration"] = iteration
            write_sidecar(plan, sidecar)

            # Phase 4 — Implement
            written_files = phase_implement(
                client, config, agreed_plan, feedback, iteration,
                pid, log_dir, sidecar, plan, cwd)

            # Phase 4.5 — Preflight
            preflight_result = phase_preflight(config, pid, log_dir, sidecar, plan)
            if preflight_result == -1:  # user chose skip
                break

            # Phase 5 — QA
            passed, qa_report = phase_qa_evaluation(
                client, config, strategy, test_command, cwd,
                pid, log_dir, sidecar, plan, iteration)

            if passed:
                break

            # Phase 6 — Feedback (if iterations remain)
            if iteration < max_iterations:
                phase_start(pid, log_dir, "6/8", f"FEEDBACK LOOP iteration {iteration}/{max_iterations}")
                feedback = sanitize_feedback(qa_report)
                log(log_dir, f"Sanitized feedback:\n{feedback}")
                phase_done(pid, log_dir, "6/8")

        # Phase 7 — Commit
        if is_git:
            phase_git_commit(
                agreed_plan, feature_tag, written_files, passed,
                cwd, config, pid, log_dir, sidecar, plan)

        # Phase 8 — Deliver
        phase_deliver(
            agreed_plan, strategy, written_files, passed, qa_report,
            iteration, max_iterations, pid, log_dir, plan)

    except KeyboardInterrupt:
        log(log_dir, "Interrupted by user")
        release_lock(plan)
        sys.exit(1)
    except Exception as e:
        log(log_dir, f"FATAL: {e}")
        phase_fail(pid, log_dir, "FATAL", str(e))
        release_lock(plan)
        raise


if __name__ == "__main__":
    main()
