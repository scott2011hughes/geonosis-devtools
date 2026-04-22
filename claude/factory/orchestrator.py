#!/usr/bin/env python3
"""
lights-out factory — orchestrator.py
Drives the implement → test → feedback loop for a single PRD plan file.
Invoked by /factory command per IP_ plan file.

Usage:
  python3 orchestrator.py --plan path/to/IP_feature.prd.md [--config path/to/factory_config.json]
"""

import argparse
import atexit
from contextlib import contextmanager
import json
import os
import re
import signal
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

# ---------------------------------------------------------------------------
# Run-state — module-level; atexit handler reads this on any process exit
# ---------------------------------------------------------------------------

_run_state: dict = {
    "initialized": False,
    "cwd": None,
    "log_dir": None,
    "pid": None,
    "written_files": [],
    "passed": None,
    "qa_report": "",
    "iteration": 0,
    "max_iterations": 0,
    "scope_type": "unknown",
    "strategy": "unknown",
    "feature_tag": "",
    "last_error": None,
    "delivered": False,       # set True by phase_deliver on normal completion
    "committed": False,       # set True after git commit
}

def load_config(config_path: Path | None = None) -> dict:
    path = config_path or CONFIG_PATH
    if not path.exists():
        print(f"[factory] ERROR: config not found at {path}")
        sys.exit(1)
    with open(path) as f:
        raw = f.read()
    repo_root = str(path.parent.parent.parent)
    repo_name = Path(repo_root).name
    raw = raw.replace("{repo_root}", repo_root)
    raw = raw.replace("{repo_name}", repo_name)
    return json.loads(raw)


def load_eec(config: dict) -> dict:
    """Load project EEC from {repo_root}/{repo_name}_eec.json."""
    eec_path_str = config.get("eec_path", "")
    if not eec_path_str:
        repo_root = Path(config.get("repo_root", "."))
        repo_name = repo_root.name
        eec_path = repo_root / f"{repo_name}_eec.json"
    else:
        eec_path = Path(eec_path_str)

    if not eec_path.exists():
        print(f"\n\033[1;31mSTARTUP BLOCKED: No EEC file found.\033[0m")
        print(f"  Expected: {eec_path}")
        template = Path(__file__).parent / "eec.template.json"
        print(f"  Copy from: {template}")
        print(f"  Fill in: repo, maturity, paths, imports, canonical_entry_points, filesystem\n")
        sys.exit(1)

    with open(eec_path) as f:
        eec = json.load(f)

    # Remove example entry if not filled in
    cep = eec.get("canonical_entry_points", {})
    if "_example_remove_this" in cep:
        del cep["_example_remove_this"]

    return eec


def maturity_check(eec: dict, cwd: Path) -> bool:
    """
    Check project maturity. Returns True if ready to proceed.
    If bootstrap or test dirs missing, prompts user.
    """
    maturity = eec.get("maturity", "bootstrap")
    test_tiers = eec.get("paths", {}).get("test_tiers", {})

    missing_dirs = []
    for tier, rel_path in test_tiers.items():
        if rel_path and not (cwd / rel_path).exists():
            missing_dirs.append(f"{tier}: {rel_path}")

    if maturity == "bootstrap" or missing_dirs:
        print(f"\n\033[1;33m⚠  MATURITY GATE\033[0m")
        if maturity == "bootstrap":
            print(f"  maturity is set to 'bootstrap' — architecture may need manual setup first")
        if missing_dirs:
            print(f"  Missing test directories:")
            for d in missing_dirs:
                print(f"    {d}")
        print()
        print("Manually create the missing structure, then set maturity='established' in the EEC.")
        print("Proceed anyway? (yes / abort)")
        choice = input("> ").strip().lower()
        return choice in ("yes", "y")

    return True


# ---------------------------------------------------------------------------
# EEC validation
# ---------------------------------------------------------------------------

STDLIB_ROOTS = frozenset({
    "abc", "ast", "asyncio", "base64", "builtins", "calendar", "cgi",
    "collections", "concurrent", "contextlib", "copy", "csv", "dataclasses",
    "datetime", "decimal", "difflib", "email", "enum", "errno", "functools",
    "gc", "glob", "gzip", "hashlib", "hmac", "html", "http", "importlib",
    "inspect", "io", "itertools", "json", "keyword", "logging", "math",
    "mimetypes", "multiprocessing", "operator", "os", "pathlib", "pickle",
    "platform", "pprint", "queue", "random", "re", "shutil", "signal",
    "socket", "sqlite3", "ssl", "stat", "string", "struct", "subprocess",
    "sys", "tempfile", "textwrap", "threading", "time", "timeit", "traceback",
    "types", "typing", "unittest", "urllib", "uuid", "warnings", "weakref",
    "xml", "xmlrpc", "zipfile", "zlib",
    # common test tools (pass through)
    "pytest", "mock", "unittest",
})


def validate_file(rel_path: str, content: str, eec: dict, estimated_files: list[str] | None = None) -> list[dict]:
    """
    Run all EEC checks against a single file before it hits disk.
    Returns a list of violation dicts (empty = valid).
    Does NOT consume an iteration — caller must handle retry.
    """
    violations = []
    lines = content.splitlines()
    filesystem = eec.get("filesystem", {})
    imports_cfg = eec.get("imports", {})

    # 1. Forbidden write paths
    forbidden_write = filesystem.get("forbidden_write", [])
    rel_basename = Path(rel_path).name
    for pattern in forbidden_write:
        if rel_path == pattern or rel_basename == pattern or rel_path.endswith("/" + pattern):
            violations.append({
                "file": rel_path,
                "type": "FORBIDDEN_WRITE",
                "line": None,
                "text": rel_path,
                "correction": f"'{rel_path}' matches eec.filesystem.forbidden_write — do not write this file",
            })

    # 2. Unowned file scope check (enforced when estimated_files provided)
    if estimated_files is not None and eec.get("rules", {}).get("unowned_files"):
        if rel_path not in estimated_files:
            violations.append({
                "file": rel_path,
                "type": "OUT_OF_SCOPE",
                "line": None,
                "text": rel_path,
                "correction": (
                    f"'{rel_path}' is not in estimated_files — unowned files are read-only. "
                    f"Remove it from your output. Scoped files: {estimated_files}"
                ),
            })

    # 3. Allowed write paths (only enforced if list is non-empty)
    allowed_write = filesystem.get("allowed_write", [])
    if allowed_write:
        in_allowed = any(
            rel_path == a or rel_path.startswith(a.rstrip("/") + "/")
            for a in allowed_write
        )
        if not in_allowed:
            violations.append({
                "file": rel_path,
                "type": "ALLOWED_WRITE",
                "line": None,
                "text": rel_path,
                "correction": f"'{rel_path}' is outside eec.filesystem.allowed_write: {allowed_write}",
            })

    # 3. Relative traversal (..) — any depth, any position in file
    traversal_pat = re.compile(r"^\s*from\s+(\.{2,})")
    for n, line in enumerate(lines, 1):
        if traversal_pat.match(line):
            violations.append({
                "file": rel_path,
                "type": "RELATIVE_TRAVERSAL",
                "line": n,
                "text": line.strip(),
                "correction": "Use absolute imports — relative traversal (..) is forbidden by EEC",
            })

    # 4. Forbidden import roots (explicit list — catches wrong container paths)
    forbidden_roots = imports_cfg.get("forbidden_import_roots", [])
    if forbidden_roots:
        import_root_pat = re.compile(r"^(?:from|import)\s+(\w+)")
        for n, line in enumerate(lines, 1):
            if line.startswith((" ", "\t")):
                continue  # skip indented imports (inside functions/classes)
            m = import_root_pat.match(line.strip())
            if m:
                root = m.group(1)
                if root in forbidden_roots:
                    allowed = imports_cfg.get("allowed_roots", [])
                    violations.append({
                        "file": rel_path,
                        "type": "IMPORT_ROOT",
                        "line": n,
                        "text": line.strip(),
                        "correction": (
                            f"Root '{root}' is in eec.imports.forbidden_import_roots — "
                            f"use a root from: {allowed}"
                        ),
                    })

    # 5. Canonical entry points — module-level forbidden imports
    cep = eec.get("canonical_entry_points", {})
    for _entry_name, entry in cep.items():
        for forbidden_import in entry.get("forbidden_imports", []):
            escaped = re.escape(forbidden_import)
            pat = re.compile(rf"^(?:from|import)\s+{escaped}")
            for n, line in enumerate(lines, 1):
                if line.startswith((" ", "\t")):
                    continue  # only flag module-level
                if pat.match(line.strip()):
                    violations.append({
                        "file": rel_path,
                        "type": "CANONICAL_ENTRY_POINT",
                        "line": n,
                        "text": line.strip(),
                        "correction": (
                            f"Use: {entry.get('use', 'see EEC')} — "
                            f"reason: {entry.get('reason', 'see EEC canonical_entry_points')}"
                        ),
                    })

    return violations


def format_violations(violations: list[dict]) -> str:
    lines = [f"VALIDATION FAILED — {len(violations)} violation(s). No files were written.\n"]
    for v in violations:
        loc = f"line {v['line']}" if v.get("line") else "file level"
        lines.append(f"  [{v['type']}] {v['file']} ({loc})")
        lines.append(f"    Found:      {v['text']}")
        lines.append(f"    Correction: {v['correction']}")
        lines.append("")
    lines.append("Fix all violations and resubmit the complete JSON block.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EEC injection
# ---------------------------------------------------------------------------

def inject_eec(system_prompt: str, eec: dict) -> str:
    """Prepend EEC block to any system prompt sent to a subagent."""
    eec_json = json.dumps(eec, indent=2)
    header = (
        "<<<EEC_START>>>\n"
        f"{eec_json}\n"
        "<<<EEC_END>>>\n\n"
        "The Execution Environment Contract above is immutable ground truth.\n"
        "- Never write to filesystem.forbidden_write paths\n"
        "- Never use imports in imports.forbidden_import_roots\n"
        "- Never use relative traversal imports (from ..)\n"
        "- Always use canonical_entry_points instead of their forbidden_imports\n\n"
    )
    return header + system_prompt


# ---------------------------------------------------------------------------
# Scaffold gate
# ---------------------------------------------------------------------------

def scaffold_gate(scaffold_requests: list) -> tuple[bool, str | None]:
    """
    Present scaffold requests to the user.
    Returns (approved: bool, instead_message: str | None).
    instead_message is set when user types 'Instead: ...'
    """
    print(f"\n\033[1;33m⚠  SCAFFOLD GATE\033[0m — inspector requests new structure:\n")
    for req in scaffold_requests:
        print(f"  Create {req.get('type', 'directory')}: {req.get('path')}")
        if req.get("reason"):
            print(f"    Reason: {req['reason']}")
    print()
    print("[Y] Create all   [N] Skip — inspector adapts   [Instead] Specify alternative path")
    choice = input("> ").strip()
    if choice.upper() == "Y":
        return True, None
    elif choice.upper() == "N":
        return False, None
    else:
        return False, choice  # pass back to inspector as correction


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
# Lock guard
# ---------------------------------------------------------------------------

def assert_lock(plan: Path, log_dir: Path, pid: int):
    if not check_lock(plan):
        phase_fail(pid, log_dir, "LOCK", "lock lost — another process owns this plan, exiting")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Dirty plan detection and reset
# ---------------------------------------------------------------------------

def handle_dirty_plan(plan: Path, cwd: Path, log_dir: Path, pid: int):
    sidecar = read_sidecar(plan)
    feature_tag = sidecar.get("feature_tag", "")
    started = sidecar.get("started", "")

    log(log_dir, f"Dirty plan detected. Sidecar: phase={sidecar.get('phase')} tag={feature_tag}")

    result = subprocess.run(
        ["git", "-C", str(cwd), "log", "--oneline", f"--since={started}", "--grep", feature_tag],
        capture_output=True, text=True
    )
    if result.stdout.strip():
        log(log_dir, "Commit found — archiving and delivering without re-running")
        archive_plan(plan, log_dir, pid, _partial=True)
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


def extract_json_block(text: str) -> dict | None:
    if "```json" not in text:
        return None
    try:
        json_str = text.split("```json")[1].split("```")[0].strip()
        return json.loads(json_str)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

SCOPE_ITERATIONS = {
    "surgical_fix": 2,
    "feature_add": 3,
    "refactor": 3,
    "new_domain": 5,
}


def extract_scope_type(plan_content: str) -> str:
    """Extract scope_type from PRD JSON block in plan file."""
    data = extract_json_block(plan_content)
    if data:
        scope = data.get("scope_type", "")
        if scope in SCOPE_ITERATIONS:
            return scope
    return "feature_add"


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

def phase_plan_negotiation(client, config, eec, plan_content, scope_type,
                           pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "1/8", "PLAN NEGOTIATION")
    assert_lock(plan, log_dir, pid)

    builder_model = config["models"]["builder"]
    system = inject_eec(load_agent_prompt(config, "builder"), eec)

    scope_note = ""
    if scope_type == "surgical_fix":
        scope_note = "\n\nScope is surgical_fix — touch the minimum files necessary. If you need more than 2 files, say so explicitly in PLAN_AGREED."

    messages = [{"role": "user", "content": (
        f"PRD:\n\n{plan_content}{scope_note}\n\n"
        "Review this PRD and confirm your implementation plan. "
        "Output PLAN_AGREED: <one paragraph> when ready."
    )}]

    agreed_plan = None
    for _turn in range(10):
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


def phase_strategy_detection(agreed_plan, plan_content, eec, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "2/8", "STRATEGY DETECTION")

    # PRD-declared test_strategy is authoritative — check it first.
    # Prevents EEC custom test_command from masking playwright PRDs.
    prd_json = extract_json_block(plan_content) or {}
    declared = prd_json.get("test_strategy", "")
    if declared in ("playwright", "combined", "pytest"):
        strategy = declared
        phase_done(pid, log_dir, "2/8", f"{strategy} (from PRD test_strategy)")
        sidecar.update({"phase": "STRATEGY_DONE", "strategy": strategy})
        write_sidecar(plan, sidecar)
        return strategy

    # Fall back to keyword inference from agreed plan + plan content.
    # EEC custom test_command applies at run-time in phase_qa_evaluation —
    # it does not override strategy here, so playwright PRDs stay playwright.
    combined = (agreed_plan + plan_content).lower()
    if "playwright" in combined and ("pytest" in combined or "python test" in combined):
        strategy = "combined"
    elif "playwright" in combined:
        strategy = "playwright"
    elif "pytest" in combined or "python test" in combined:
        strategy = "pytest"
    else:
        strategy = "pytest"

    sidecar.update({"phase": "STRATEGY_DONE", "strategy": strategy})
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "2/8", strategy)
    return strategy


def phase_feature_tag(agreed_plan, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "2.5/8", "FEATURE TAG")

    words = agreed_plan.lower().split()
    stopwords = {"a", "an", "the", "and", "or", "to", "for", "of", "in", "with",
                 "that", "this", "is", "are", "was", "be", "by", "as"}
    tag_words = [re.sub(r"[^a-z0-9]", "", w) for w in words
                 if w.isalpha() and w not in stopwords][:3]
    feature_tag = "@" + "-".join(w for w in tag_words if w)

    sidecar.update({"phase": "FEATURE_TAG_DONE", "feature_tag": feature_tag})
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "2.5/8", feature_tag)
    return feature_tag


def phase_test_plan(client, config, eec, agreed_plan, strategy, feature_tag,
                    pid, log_dir, sidecar, plan, cwd):
    phase_start(pid, log_dir, "3/8", "TEST PLAN CREATION (private)")
    assert_lock(plan, log_dir, pid)

    inspector_model = config["models"]["inspector"]
    playwright_dir = Path(
        config.get("playwright_dir", "~/my_claude_automations/playwright")
    ).expanduser()

    system = inject_eec(load_agent_prompt(config, "inspector"), eec)
    messages = [{"role": "user", "content": (
        f"Implementation plan:\n\n{agreed_plan}\n\n"
        f"Test strategy: {strategy}. Feature tag: {feature_tag}. "
        "Create the test plan and output TEST_PLAN_READY: <summary> when done."
    )}]

    test_summary = None
    test_files = {}
    test_command = None
    scaffold_requests = []

    for _ in range(8):
        reply = call_claude(client, inspector_model, system, messages)
        messages.append({"role": "assistant", "content": reply})

        data = extract_json_block(reply)
        if data:
            test_files = data.get("files", {})
            test_command = data.get("command")
            scaffold_requests = data.get("scaffold_requests", [])

        sentinel = extract_sentinel(reply, "TEST_PLAN_READY:")
        if sentinel:
            test_summary = sentinel
            break
        messages.append({"role": "user", "content": "Continue building the test plan."})

    # Scaffold gate — ask user before creating any new structure
    if scaffold_requests and eec.get("scaffold_policy", {}).get("requires_user_approval", True):
        approved, instead_msg = scaffold_gate(scaffold_requests)
        if approved:
            for req in scaffold_requests:
                path = req.get("path", "")
                if path and req.get("type", "directory") == "directory":
                    (cwd / path).mkdir(parents=True, exist_ok=True)
                    log(log_dir, f"Scaffolded directory: {path}")
        elif instead_msg:
            # Send alternative back to inspector for one revision
            messages.append({"role": "user", "content":
                f"Instead of the scaffold requests, use this alternative: {instead_msg}"})
            reply = call_claude(client, inspector_model, system, messages)
            data = extract_json_block(reply)
            if data:
                test_files = data.get("files", {})
                test_command = data.get("command")

    # Validate and write test files
    all_violations = []
    for rel_path, content in test_files.items():
        violations = validate_file(rel_path, content, eec)
        all_violations.extend(violations)

    if all_violations:
        violation_msg = format_violations(all_violations)
        log(log_dir, f"Test file validation failed: {len(all_violations)} violations")
        print(f"\033[1;31m✗  TEST FILES FAILED VALIDATION — {len(all_violations)} violations\033[0m")
        print(violation_msg)
        # Send back to inspector for correction (not an iteration)
        messages.append({"role": "user", "content": violation_msg})
        reply = call_claude(client, inspector_model, system, messages)
        data = extract_json_block(reply)
        if data:
            test_files = data.get("files", {})

    # Inspector tests are staged to /tmp — never written directly to disk.
    # The orchestrator copies them in before each pytest run and removes them after,
    # so the builder cannot read them between iterations.
    tmp_test_files = {}  # tmp_path -> (cwd_dest, content)
    for rel_path, content in test_files.items():
        if strategy == "playwright":
            # Strip playwright_dir prefix if inspector gave a repo-relative path
            # (e.g. "tests/unit/playwright/foo.spec.ts" → "foo.spec.ts")
            rel = Path(rel_path)
            try:
                rel = rel.relative_to(playwright_dir)
            except ValueError:
                pass
            dest = playwright_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        else:
            tmp_path = Path(f"/tmp/inspector_{pid}_{Path(rel_path).name}")
            tmp_path.write_text(content)
            tmp_test_files[str(tmp_path)] = (cwd / rel_path, content)
            log(log_dir, f"Inspector test staged: {rel_path} → {tmp_path}")

    sidecar.update({
        "phase": "TEST_PLAN_DONE",
        "test_summary": test_summary or "test plan created",
        "test_command": test_command,
        "tmp_test_files": {k: str(v[0]) for k, v in tmp_test_files.items()},
    })
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "3/8", test_summary or "")
    return test_summary, tmp_test_files, test_command


def phase_implement(client, config, eec, agreed_plan, feedback, scope_type,
                    iteration, pid, log_dir, sidecar, plan, cwd, estimated_files=None):
    phase_start(pid, log_dir, "4/8", f"IMPLEMENT iteration {iteration}")
    assert_lock(plan, log_dir, pid)

    builder_model = config["models"]["builder"]
    system = inject_eec(load_agent_prompt(config, "builder"), eec)

    if iteration == 1:
        prompt = f"Implement this plan:\n\n{agreed_plan}"
    else:
        prompt = f"Fix the following issues:\n\n{feedback}\n\nOriginal plan:\n\n{agreed_plan}"

    reply = call_claude(client, builder_model, system,
                        [{"role": "user", "content": prompt}], max_tokens=8192)

    written_files = []
    data = extract_json_block(reply)
    if data:
        files_to_write = data.get("files", {})

        # Scope check — warn if surgical_fix but many files
        if scope_type == "surgical_fix" and len(files_to_write) > 2:
            print(f"\n\033[1;33m⚠  SCOPE WARNING — surgical_fix but {len(files_to_write)} files:\033[0m")
            for p in files_to_write.keys():
                print(f"  {p}")
            print("Continue? (Y / abort)")
            if input("> ").strip().upper() != "Y":
                sys.exit(1)

        # EEC validation — run before writing anything
        all_violations = []
        for rel_path, content in files_to_write.items():
            violations = validate_file(rel_path, content, eec, estimated_files)
            all_violations.extend(violations)

        if all_violations:
            violation_msg = format_violations(all_violations)
            log(log_dir, f"Validation failed: {len(all_violations)} violations — sending correction")
            print(f"\033[1;31m✗  VALIDATION FAILED — {len(all_violations)} violations (no iteration consumed)\033[0m")

            # Non-iteration retry
            retry_reply = call_claude(
                client, builder_model, system,
                [{"role": "user", "content": prompt},
                 {"role": "assistant", "content": reply},
                 {"role": "user", "content": violation_msg}],
                max_tokens=8192,
            )
            data = extract_json_block(retry_reply)
            if data:
                files_to_write = data.get("files", {})
                # Re-validate once
                remaining = []
                for rel_path, content in files_to_write.items():
                    remaining.extend(validate_file(rel_path, content, eec))
                if remaining:
                    log(log_dir, f"Validation still failing after retry ({len(remaining)} violations) — writing what passed")
                    files_to_write = {
                        p: c for p, c in files_to_write.items()
                        if not validate_file(p, c, eec)
                    }

        # Write validated files
        for rel_path, content in files_to_write.items():
            dest = (cwd / rel_path).resolve()
            if not str(dest).startswith(str(cwd.resolve())):
                log(log_dir, f"REJECTED unsafe path: {rel_path}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            written_files.append(rel_path)

    # Deploy services affected by written files (reads from EEC — no-op if unconfigured)
    phase_deploy(written_files, eec, cwd, log_dir)

    sidecar.update({
        "phase": f"IMPLEMENT_{iteration}_DONE",
        "iteration": iteration,
        "written_files": written_files,
    })
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "4/8", f"{len(written_files)} files written")
    return written_files


def phase_deploy(written_files: list, eec: dict, cwd: Path, log_dir: Path):
    """
    Run deploy targets for services affected by written files.
    Reads service paths and deploy_command from eec.execution.
    No-ops when deploy_command is null/absent in EEC.
    """
    deploy_cmd = eec.get("execution", {}).get("deploy_command")
    services = eec.get("execution", {}).get("services", {})
    if not deploy_cmd or not services or not written_files:
        return

    targets: set[str] = set()
    for rel_path in written_files:
        for _svc_name, svc_cfg in services.items():
            svc_path = svc_cfg.get("path", "")
            if svc_path and rel_path.startswith(svc_path):
                for t in svc_cfg.get("deploy_target", "").split():
                    targets.add(t)

    if not targets:
        return

    target_str = " ".join(sorted(targets))
    log(log_dir, f"Deploy: {deploy_cmd} -C {cwd} {target_str}")
    print(f"\033[1;33m▶  DEPLOY  —  {target_str}\033[0m")
    result = subprocess.run(
        f"{deploy_cmd} -C {cwd} {target_str}".split(),
        capture_output=False,
    )
    if result.returncode != 0:
        log(log_dir, f"Deploy returned exit {result.returncode} — continuing")


def phase_preflight(config, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "4.5/8", "PRE-FLIGHT CHECK")
    assert_lock(plan, log_dir, pid)

    healthcheck = Path(
        config.get("healthcheck_script", "~/my_claude_automations/healthcheck.sh")
    ).expanduser()
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
            return -1
        elif choice == "continue":
            return phase_preflight(config, pid, log_dir, sidecar, plan)

    return exit_code


@contextmanager
def playwright_services_running(config: dict, cwd: Path, log_dir: Path):
    """Start playwright_services declared in factory_config, yield, then kill them.

    Starts only the services needed for scoped playwright tests (e.g. Vite dev
    server). API calls are mocked via page.route() — no backend required.
    Full-stack e2e (real API + DB) is the PR gate, not the factory loop.

    Each service entry:  { "command": str, "health_url": str,
                           "poll_interval_s": int, "timeout_s": int }
    No-op when config has no playwright_services key.
    """
    services = config.get("playwright_services", {})
    procs = []
    try:
        for name, svc in services.items():
            cmd = svc.get("command", "")
            if not cmd:
                continue
            log(log_dir, f"e2e service '{name}': starting — {cmd}")
            proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            procs.append((name, proc))

            health_url = svc.get("health_url")
            timeout = svc.get("timeout_s", 60)
            interval = svc.get("poll_interval_s", 2)
            if health_url:
                deadline = time.time() + timeout
                import urllib.request
                ready = False
                while time.time() < deadline:
                    try:
                        urllib.request.urlopen(health_url, timeout=2)
                        ready = True
                        break
                    except Exception:
                        time.sleep(interval)
                if ready:
                    log(log_dir, f"e2e service '{name}': ready at {health_url}")
                else:
                    raise RuntimeError(
                        f"e2e service '{name}' did not become healthy at "
                        f"{health_url} within {timeout}s"
                    )
        yield
    finally:
        for name, proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            log(log_dir, f"e2e service '{name}': stopped (pid {proc.pid})")


@contextmanager
def inspector_tests_deployed(tmp_test_files, strategy, cwd, playwright_dir):
    """Copy inspector tests to their real paths, yield, then remove them.

    Keeps the files off disk between iterations so the builder cannot read
    them. Call without the finally block (manual promotion) when QA passes.
    """
    deployed = []
    try:
        for _tmp, (dest_path, content) in (tmp_test_files or {}).items():
            dest = (playwright_dir / dest_path.name
                    if strategy == "playwright" else dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            deployed.append(dest)
        yield deployed
    finally:
        for dest in deployed:
            try:
                dest.unlink()
            except FileNotFoundError:
                pass


def phase_qa_evaluation(client, config, eec, strategy, test_command, cwd,
                        pid, log_dir, sidecar, plan, iteration, tmp_test_files=None):  # noqa: PLR0913
    phase_start(pid, log_dir, "5/8", f"QA EVALUATION iteration {iteration}")
    assert_lock(plan, log_dir, pid)

    inspector_model = config["models"]["inspector"]
    playwright_dir = Path(
        config.get("playwright_dir", "~/my_claude_automations/playwright")
    ).expanduser()

    # Determine run command(s) — EEC overrides defaults
    eec_cmd = eec.get("execution", {}).get("test_command")
    pw_cmd = config.get("playwright_command", ["npx", "playwright", "test"])

    # Services are started by the caller (main loop) after phase_implement so that
    # bootstrap scenarios (e.g. new frontend scaffold) work correctly. This function
    # assumes services are already up when strategy is playwright/combined.
    with inspector_tests_deployed(tmp_test_files, strategy, cwd, playwright_dir):
        start = time.time()

        if strategy == "playwright":
            result = subprocess.run(pw_cmd, capture_output=True, text=True, cwd=cwd)
        elif strategy == "combined":
            # Run pytest first, then playwright — both must pass
            r1 = subprocess.run(
                eec_cmd or ["pytest", "--tb=short", "-v"],
                capture_output=True, text=True, cwd=cwd,
            )
            r2 = subprocess.run(pw_cmd, capture_output=True, text=True, cwd=cwd)
            # Merge into a single result-like object
            class _Combined:
                returncode = r1.returncode or r2.returncode
                stdout = f"=== pytest ===\n{r1.stdout}\n=== playwright ===\n{r2.stdout}"
                stderr = f"=== pytest ===\n{r1.stderr}\n=== playwright ===\n{r2.stderr}"
            result = _Combined()
        elif test_command:
            result = subprocess.run(test_command, capture_output=True, text=True, cwd=cwd)
        else:
            cmd = eec_cmd or ["pytest", "--tb=short", "-v"]
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

        duration = round(time.time() - start, 1)

    exit_code = result.returncode
    stdout = result.stdout[-3000:]
    stderr = result.stderr[-1000:]

    system = inject_eec(load_agent_prompt(config, "inspector"), eec)
    prompt = (
        f"Tests ran. Exit code: {exit_code}. Duration: {duration}s.\n\n"
        f"STDOUT (last 3000 chars):\n{stdout}\n\n"
        f"STDERR (last 1000 chars):\n{stderr}"
    )
    qa_report = call_claude(client, inspector_model, system,
                            [{"role": "user", "content": prompt}])

    blocked = "QA_VERDICT: BLOCKED" in qa_report
    passed = not blocked and ("QA_VERDICT: PASS" in qa_report or exit_code == 0)

    sidecar.update({
        "phase": f"QA_{iteration}_DONE",
        "qa_report": qa_report,
        "last_exit_code": exit_code,
    })
    write_sidecar(plan, sidecar)

    if blocked:
        phase_fail(pid, log_dir, "5/8", f"BLOCKED — precondition failure, iteration not consumed")
        print(f"\n\033[1;33m⚠  QA BLOCKED — inspector reports a precondition failure:\033[0m")
        print(f"{qa_report}\n")
        print("Fix the precondition (install deps, start services) then re-run.")
        print("This iteration was NOT counted against the limit.")
    elif passed:
        phase_done(pid, log_dir, "5/8", f"PASSED exit={exit_code} {duration}s")
        log(log_dir, f"TEST OUTPUT:\n{stdout}")
    else:
        phase_fail(pid, log_dir, "5/8", f"FAILED exit={exit_code} {duration}s")
        log(log_dir, f"TEST OUTPUT:\n{stdout}\nSTDERR:\n{stderr}")

    return passed, qa_report, blocked


_COMMIT_PREAMBLE = re.compile(
    r"^(?:"
    r"I(?:'ll| will| am going to| will be| will now)\s+"
    r"|(?:This|The|We|The\s+implementation|This\s+change|The\s+change)"
    r"(?:\s+will(?:\s+be)?)?\s+"
    r")",
    re.IGNORECASE,
)


def normalize_commit_subject(text: str) -> str:
    """
    Turn a builder PLAN_AGREED paragraph into an imperative commit subject.
    'I will add X to Y. Also refactor Z.' -> 'add X to Y'
    """
    # First sentence only
    first = re.split(r"[.\n]", text)[0].strip()
    # Strip first-person / future-tense opener, repeatedly (handles 'I will now add')
    for _ in range(3):
        stripped = _COMMIT_PREAMBLE.sub("", first).strip()
        if stripped == first:
            break
        first = stripped
    # Conventional commits want lowercase after the type prefix
    if first:
        first = first[0].lower() + first[1:]
    return first[:72]


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


def phase_git_commit(agreed_plan, feature_tag, _written_files, passed, cwd,
                     config, pid, log_dir, sidecar, plan):
    phase_start(pid, log_dir, "7/8", "GIT COMMIT")
    assert_lock(plan, log_dir, pid)

    status = "passing" if passed else "partial"
    commit_msg = f"feat: {normalize_commit_subject(agreed_plan)} [{status}] {feature_tag}"

    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", commit_msg], check=True)

    vscode_diff = Path(config.get("vscode_diff_script", "")).expanduser()
    if vscode_diff.exists():
        subprocess.run(["bash", str(vscode_diff)], cwd=cwd)

    diff_stat = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--stat", "HEAD~1", "HEAD"],
        capture_output=True, text=True,
    ).stdout

    sidecar["phase"] = "COMMITTED"
    write_sidecar(plan, sidecar)
    phase_done(pid, log_dir, "7/8", commit_msg[:60])
    log(log_dir, f"Diff stat:\n{diff_stat}")

    print(f"\n{diff_stat}")
    print(f"\n\033[1;33mReview the diff above. Push when satisfied:\033[0m")
    print(f"  git -C {cwd} push\n")


def archive_plan(plan: Path, log_dir: Path, pid: int, _partial: bool = False):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    stem = plan.name.replace("IP_", "")
    done_name = f"done-{timestamp}-{stem}"
    dest = plan.parent / done_name
    plan.rename(dest)
    release_sidecar(plan)
    release_lock(plan)
    status_path(pid).unlink(missing_ok=True)
    log(log_dir, f"Archived to {done_name}")


# ---------------------------------------------------------------------------
# Delivery report — posted as a new Claude session on any process exit
# ---------------------------------------------------------------------------

def _is_test_file(path: str) -> bool:
    p = Path(path)
    return (
        p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or "/tests/" in path
        or path.startswith("tests/")
        or "/test/" in path
    )


def build_session_report() -> str:
    s = _run_state
    if not s.get("initialized"):
        return ""

    cwd = s.get("cwd")
    written = s.get("written_files", [])
    test_files = [f for f in written if _is_test_file(f)]
    impl_files = [f for f in written if not _is_test_file(f)]

    passed = s.get("passed")
    if passed is True:
        verdict = "PASSED"
    elif passed is False:
        iters = s.get("iteration", 0)
        max_i = s.get("max_iterations", 0)
        verdict = f"FAILED — {iters}/{max_i} iterations exhausted"
    else:
        verdict = "INCOMPLETE (exited before QA)"

    # Git diff --stat: prefer committed range, fall back to unstaged
    diff_stat = ""
    if cwd:
        try:
            if s.get("committed"):
                r = subprocess.run(
                    ["git", "-C", str(cwd), "diff", "--stat", "HEAD~1", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                r = subprocess.run(
                    ["git", "-C", str(cwd), "diff", "--stat", "HEAD"],
                    capture_output=True, text=True, timeout=5,
                )
            if r.returncode == 0 and r.stdout.strip():
                diff_stat = r.stdout.strip()
        except Exception:
            pass

    log_dir = s.get("log_dir", "")
    log_file = str(Path(log_dir) / "run.log") if log_dir else ""

    sep = "-" * 62
    lines = [
        sep,
        "FACTORY DELIVERY REPORT",
        sep,
        f"Verdict:    {verdict}",
        f"Scope:      {s.get('scope_type', 'unknown')}",
        f"Iterations: {s.get('iteration', 0)}/{s.get('max_iterations', 0)}",
        f"Strategy:   {s.get('strategy', 'unknown')}",
        f"Tag:        {s.get('feature_tag', '')}",
        f"Log:        {log_file}",
        "",
    ]

    if impl_files:
        lines.append(f"Files written ({len(impl_files)}):")
        for f in impl_files:
            lines.append(f"  {f}")
    else:
        lines.append("Files written: none")

    if test_files:
        lines.append(f"\nTests introduced ({len(test_files)}):")
        for f in test_files:
            lines.append(f"  {f}")

    if diff_stat:
        lines.append(f"\nGit diff --stat:\n{diff_stat}")

    if s.get("last_error"):
        lines.append(f"\nError:\n  {s['last_error']}")

    if passed is not True and s.get("qa_report"):
        qa_tail = "\n".join(s["qa_report"].splitlines()[-20:])
        lines.append(f"\nQA Report (tail):\n{qa_tail}")

    lines.append(sep)
    return "\n".join(lines)


def post_session_report():
    """
    atexit handler — fires on normal exit, sys.exit(), unhandled exception,
    KeyboardInterrupt, and SIGTERM (via signal handler below).
    Overwrites /tmp/factory-{pid}.txt with the full delivery report so
    the caller's `watch cat` watcher picks it up immediately.
    """
    if not _run_state.get("initialized"):
        return

    report = build_session_report()
    if not report:
        return

    # Overwrite the status file — the caller's watcher sees this instantly
    pid = _run_state.get("pid")
    if pid:
        try:
            status_path(pid).write_text(report + "\n")
        except Exception:
            pass

    # Persist to log_dir for later reference
    log_dir = _run_state.get("log_dir")
    if log_dir:
        try:
            Path(log_dir).joinpath("delivery_report.txt").write_text(report + "\n")
        except Exception:
            pass

    # On abnormal exit the deliver banner never ran — print to terminal too
    if not _run_state.get("delivered"):
        print(f"\n{report}\n")


def _sigterm_handler(_signum, _frame):
    """Convert SIGTERM to a normal Python exit so atexit handlers fire."""
    _run_state["last_error"] = "Terminated (SIGTERM)"
    sys.exit(1)


atexit.register(post_session_report)
signal.signal(signal.SIGTERM, _sigterm_handler)


def phase_deliver(strategy, written_files, passed, qa_report,
                  iteration, max_iterations, scope_type, pid, log_dir, plan):
    phase_start(pid, log_dir, "8/8", "DELIVER")

    if passed:
        result_str = "PASS"
        status_str = "PASS"
    else:
        result_str = f"FAIL — exhausted {iteration}/{max_iterations} iterations"
        first_fail = next(
            (ln.strip() for ln in (qa_report or "").splitlines()
             if ln.strip().startswith("FAILED") or "Error" in ln or "ModuleNotFound" in ln),
            "see run.log for details",
        )
        status_str = f"FAIL | {first_fail[:80]}"

    banner("FACTORY — DELIVERY COMPLETE")
    print(f"""
{'=' * 62}
DELIVERY REPORT
{'=' * 62}
Result:     {result_str}
Scope:      {scope_type}
Iterations: {iteration}/{max_iterations}
Strategy:   {strategy}
Files:      {chr(10).join('  ' + f for f in written_files)}
Git:        {"committed locally — PUSH PENDING YOUR APPROVAL" if passed else "NOT committed — tests did not pass"}

QA Report:
{qa_report}
{'=' * 62}
""")

    _run_state["delivered"] = True
    archive_plan(plan, log_dir, pid, _partial=not passed)
    write_status(pid, status_str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="lights-out factory orchestrator")
    parser.add_argument("--plan", required=True, help="Path to IP_ plan file")
    parser.add_argument("--config", help="Path to factory_config.json (optional override)")
    args = parser.parse_args()

    plan = Path(args.plan).resolve()
    if not plan.exists():
        print(f"[factory] ERROR: plan file not found: {plan}")
        sys.exit(1)

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    cwd = Path(config.get("repo_root", ".")).resolve()
    pid = os.getpid()

    # Load EEC — hard stop if missing
    eec = load_eec(config)

    # Maturity gate
    if not maturity_check(eec, cwd):
        print("[factory] Aborted at maturity gate.")
        sys.exit(0)

    # Extract scope from plan
    plan_content = plan.read_text()
    scope_type = extract_scope_type(plan_content)
    plan_json = extract_json_block(plan_content) or {}
    estimated_files = plan_json.get("estimated_files", [])
    max_iterations = SCOPE_ITERATIONS.get(scope_type, config.get("max_iterations", 3))

    plan_stem = plan.name.replace("IP_", "").replace(".prd.md", "")
    log_dir = Path(config.get("log_dir", "claude/factory/logs")).resolve() / plan_stem
    log_dir.mkdir(parents=True, exist_ok=True)

    # Seed run-state so atexit handler has context from this point forward
    _run_state.update({
        "initialized": True,
        "cwd": str(cwd),
        "log_dir": str(log_dir),
        "pid": pid,
        "scope_type": scope_type,
        "max_iterations": max_iterations,
    })

    banner(f"FACTORY — STARTING  |  {cwd.name}  |  scope: {scope_type}  |  maturity: {eec.get('maturity')}")
    print(f"\033[0;37m  Watch: watch cat /tmp/factory-{pid}.txt\033[0m")
    print(f"\033[0;37m  Log:   {log_dir}/run.log\033[0m\n")
    write_status(pid, "STARTING")

    git_check = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
    )
    is_git = git_check.returncode == 0

    if sidecar_path(plan).exists():
        handle_dirty_plan(plan, cwd, log_dir, pid)

    write_lock(plan)
    log(log_dir, f"Lock acquired. PID={pid}. scope={scope_type} max_iter={max_iterations}")

    sidecar = {
        "pid": pid,
        "plan": str(plan),
        "started": datetime.now().isoformat(),
        "phase": "STARTING",
        "iteration": 0,
        "strategy": None,
        "feature_tag": None,
        "scope_type": scope_type,
    }
    write_sidecar(plan, sidecar)

    client = anthropic.Anthropic()

    try:
        agreed_plan = phase_plan_negotiation(
            client, config, eec, plan_content, scope_type, pid, log_dir, sidecar, plan)

        strategy = phase_strategy_detection(
            agreed_plan, plan_content, eec, pid, log_dir, sidecar, plan)
        _run_state["strategy"] = strategy

        feature_tag = phase_feature_tag(
            agreed_plan, pid, log_dir, sidecar, plan)
        _run_state["feature_tag"] = feature_tag

        _test_plan = phase_test_plan(
            client, config, eec, agreed_plan, strategy, feature_tag,
            pid, log_dir, sidecar, plan, cwd)
        tmp_test_files, test_command = _test_plan[1], _test_plan[2]

        # Baseline — run existing tests before builder touches anything.
        # If all fail it's a harness problem, not a builder problem.
        eec_cmd = eec.get("execution", {}).get("test_command") or ["pytest", "--tb=line", "-q"]
        baseline_result = subprocess.run(eec_cmd, capture_output=True, text=True, cwd=cwd)
        baseline_ids = set()
        for line in baseline_result.stdout.splitlines():
            if " PASSED" in line:
                baseline_ids.add(line.split(" PASSED")[0].strip())
        if baseline_result.returncode != 0 and not baseline_ids:
            phase_fail(pid, log_dir, "3.5/8", "HARNESS FAILURE — all existing tests fail before builder runs")
            log(log_dir, f"Baseline stderr:\n{baseline_result.stderr}")
            release_lock(plan)
            sys.exit(1)
        _run_state["baseline_passing"] = list(baseline_ids)
        log(log_dir, f"Baseline: {len(baseline_ids)} passing tests captured")

        passed = False
        qa_report = ""
        feedback = ""
        written_files = []
        iteration = 0

        needs_svc = strategy in ("playwright", "combined")

        for iteration in range(1, max_iterations + 1):
            _run_state["iteration"] = iteration
            sidecar["iteration"] = iteration
            write_sidecar(plan, sidecar)

            written_files = phase_implement(
                client, config, eec, agreed_plan, feedback, scope_type,
                iteration, pid, log_dir, sidecar, plan, cwd, estimated_files)
            _run_state["written_files"] = written_files

            preflight_result = phase_preflight(config, pid, log_dir, sidecar, plan)
            if preflight_result == -1:
                break

            # Start playwright services AFTER implement so bootstrap scenarios
            # (e.g. new frontend scaffold) work — files exist before service start.
            qa_svc_ctx = (
                playwright_services_running(config, cwd, log_dir)
                if needs_svc else contextmanager(lambda: iter([None]))()
            )
            with qa_svc_ctx:
                passed, qa_report, blocked = phase_qa_evaluation(
                    client, config, eec, strategy, test_command, cwd,
                    pid, log_dir, sidecar, plan, iteration, tmp_test_files)
            _run_state["passed"] = passed
            _run_state["qa_report"] = qa_report

            if blocked:
                release_lock(plan)
                sys.exit(1)

            if passed:
                break

            if iteration < max_iterations:
                phase_start(pid, log_dir, "6/8",
                            f"FEEDBACK LOOP iteration {iteration}/{max_iterations}")
                feedback = sanitize_feedback(qa_report)
                log(log_dir, f"Sanitized feedback:\n{feedback}")
                phase_done(pid, log_dir, "6/8")

        if passed and tmp_test_files:
            # QA passed — promote inspector tests to permanent paths for git commit.
            # Write directly (no context manager) so they stay on disk.
            for _tmp, (dest_path, content) in tmp_test_files.items():
                dest = (playwright_dir / dest_path.name
                        if strategy == "playwright" else dest_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content)
                written_files.append(str(dest.relative_to(cwd)))
                log(log_dir, f"Inspector test promoted: {dest.relative_to(cwd)}")

        if is_git and passed:
            phase_git_commit(
                agreed_plan, feature_tag, written_files, passed,
                cwd, config, pid, log_dir, sidecar, plan)
            _run_state["committed"] = True

        phase_deliver(
            strategy, written_files, passed, qa_report,
            iteration, max_iterations, scope_type, pid, log_dir, plan)

    except KeyboardInterrupt:
        _run_state["last_error"] = "Interrupted by user (KeyboardInterrupt)"
        log(log_dir, "Interrupted by user")
        release_lock(plan)
        sys.exit(1)
    except Exception as e:
        _run_state["last_error"] = str(e)
        log(log_dir, f"FATAL: {e}")
        phase_fail(pid, log_dir, "FATAL", str(e))
        release_lock(plan)
        raise


if __name__ == "__main__":
    main()
