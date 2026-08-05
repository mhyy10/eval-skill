#!/usr/bin/env python3
"""eval-skill runner: run fixtures against a SKILL.md target, judge, report.

Commands:
  run      Execute a fixture N times, apply deterministic asserts + LLM judge
  report   Render runs/*.json into an HTML report
  init     Scaffold a new fixture directory

Stdlib only (plus a tiny vendored YAML-subset parser for fixture files).
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"

# Files copied from the real CODEX_HOME into the temporary one used by
# --mount codex. Anything not on this list is NOT inherited: other skills,
# memories, history, and sqlite state stay out of the eval sandbox so the
# mounted skill is the only one the agent can route to.
CODEX_HOME_ALLOWLIST = ("auth.json", "config.toml")

# Default per-file size cap for judge input injection. Fixtures can
# override via judge.max_input_bytes; larger caps trade prompt size and
# judge attention for completeness on big outputs.
DEFAULT_JUDGE_MAX_INPUT_BYTES = 32 * 1024


# ---------------------------------------------------------------- yaml-lite

def parse_scalar(text: str):
    text = text.strip()
    if not text:
        return ""
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    return text


def parse_simple_yaml(text: str):
    """Parse the small YAML subset used by fixture.yaml files.

    Supports: nested mappings, lists of mappings, inline scalars,
    block scalars with '>', single-line lists of scalars under a key.
    """
    lines = [ln for ln in text.splitlines()]
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]
    last_key_at_indent: dict[int, tuple[dict, str]] = {}
    fold_target: list | None = None  # [container, key, indent]

    for raw in lines:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        if fold_target is not None:
            container, key, findent = fold_target
            if indent > findent:
                container[key] = (container[key] + " " + stripped).strip()
                continue
            fold_target = None

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        if stripped.startswith("- "):
            item_text = stripped[2:]
            if not isinstance(parent, list):
                pcontainer, pkey = last_key_at_indent[stack[-1][0]]
                new_list: list = []
                pcontainer[pkey] = new_list
                parent = new_list
                stack.append((stack[-1][0], parent))
            if ":" in item_text:
                k, _, v = item_text.partition(":")
                item: dict = {k.strip(): parse_scalar(v)}
                parent.append(item)
                stack.append((indent, item))
                last_key_at_indent[indent] = (item, k.strip())
            else:
                parent.append(parse_scalar(item_text))
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if value == ">":
            parent[key] = ""
            fold_target = [parent, key, indent]
        elif value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
            last_key_at_indent[indent] = (parent, key)
        else:
            parent[key] = parse_scalar(value)
            last_key_at_indent[indent] = (parent, key)

    return root


# ---------------------------------------------------------------- assertions

def run_assertions(asserts: list, workdir: Path) -> list[dict]:
    results = []
    for a in asserts or []:
        atype = a.get("type")
        target = workdir / a.get("path", "")
        ok, detail = True, ""
        if atype == "file_exists":
            ok = target.exists()
            detail = f"{a['path']} {'found' if ok else 'missing'}"
        elif atype == "file_not_exists":
            ok = not target.exists()
            detail = f"{a['path']} {'absent' if ok else 'should not exist'}"
        elif atype in ("file_contains", "file_not_contains"):
            if not target.exists():
                ok, detail = False, f"{a['path']} missing"
            else:
                content = target.read_text(encoding="utf-8", errors="replace")
                for pat in a.get("patterns", []):
                    found = re.search(pat, content) is not None
                    if atype == "file_contains" and not found:
                        ok, detail = False, f"pattern /{pat}/ not found"
                        break
                    if atype == "file_not_contains" and found:
                        ok, detail = False, f"forbidden pattern /{pat}/ found"
                        break
                else:
                    detail = f"{len(a.get('patterns', []))} pattern(s) ok"
        elif atype == "file_min_lines":
            if not target.exists():
                ok, detail = False, f"{a['path']} missing"
            else:
                n = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
                ok = n >= int(a.get("count", 0))
                detail = f"{n} lines (need >= {a.get('count')})"
        elif atype == "command":
            proc = subprocess.run(
                a["run"], shell=True, cwd=workdir,
                capture_output=True, text=True, timeout=120,
            )
            ok = proc.returncode == 0
            detail = f"exit {proc.returncode}"
        else:
            ok, detail = False, f"unknown assert type: {atype}"
        results.append({"type": atype, "ok": ok, "detail": detail})
    return results


# ---------------------------------------------------------------- mount

def parse_frontmatter_name(skill_dir: Path) -> str:
    """Read the canonical skill name from SKILL.md frontmatter.

    The name in frontmatter is what the platform routes on and what
    `$name` triggers, so mounting must use it rather than the directory
    name (the two can drift apart). Missing or malformed frontmatter is
    a bug in the skill itself, so fail fast instead of guessing.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise SystemExit(f"mount: SKILL.md not found in {skill_dir}")
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SystemExit(
            f"mount: {skill_md} has no frontmatter; cannot determine "
            "the skill's canonical name for $name triggering. Add a "
            "'name:' field inside a --- frontmatter block.")
    end = text.find("\n---", 3)
    if end == -1:
        raise SystemExit(f"mount: {skill_md} frontmatter is not closed")
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "name":
            name = value.strip().strip('"').strip("'")
            if name:
                return name
    raise SystemExit(
        f"mount: {skill_md} frontmatter has no 'name:' field; cannot "
        "determine the skill's canonical name for $name triggering.")


def build_codex_home(skill_dir: Path, skill_name: str,
                     real_home: Path) -> tuple[Path, list[str]]:
    """Create a temporary CODEX_HOME with only the mounted skill plus
    allowlisted auth/config files. Returns (home_path, copied_files).

    Caller owns cleanup via cleanup_codex_home(); we never leave this
    directory behind, because auth.json contains credentials.
    """
    home = Path(tempfile.mkdtemp(prefix="eval-codex-home-"))
    copied: list[str] = []
    dest_skill = home / "skills" / skill_name
    shutil.copytree(skill_dir, dest_skill)
    copied.append(f"skills/{skill_name}/")
    for name in CODEX_HOME_ALLOWLIST:
        src = real_home / name
        if src.exists() and src.is_file():
            shutil.copy2(src, home / name)
            copied.append(name)
    return home, copied


def cleanup_codex_home(home: Path) -> None:
    shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------- agent

def build_task_prompt(skill_dir: Path, task_text: str) -> str:
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    return (
        "You must strictly follow the skill below while completing the task.\n\n"
        "<skill>\n" + skill_md + "\n</skill>\n\n"
        "<task>\n" + task_text + "\n</task>\n\n"
        "Work autonomously; do not ask for confirmation."
    )


def build_mounted_task_prompt(skill_name: str, task_text: str,
                              dollar_supported: bool) -> tuple[str, str]:
    """Build the task prompt for --mount runs.

    Returns (prompt, trigger_mode). With `$name` support the skill is
    triggered through the platform's real skill-loading path; without
    it we fall back to a weaker explicit reference and record the
    downgrade so reports can flag the difference.
    """
    if dollar_supported:
        return (
            f"${skill_name}\n\n<task>\n{task_text}\n</task>\n\n"
            "Work autonomously; do not ask for confirmation."
        ), "dollar"
    return (
        f"You have access to a skill named `{skill_name}` loaded via "
        "your skills directory. Use that skill to complete the task "
        "below.\n\n<task>\n" + task_text + "\n</task>\n\n"
        "Work autonomously; do not ask for confirmation."
    ), "fallback-named"


def detect_dollar_support(cli: str) -> bool:
    """Whether `cli` expands `$skill-name` triggers in non-interactive
    exec mode. Conservative: assume support where it is documented,
    otherwise fall back. Overridable for environments known to differ.
    """
    override = os.environ.get("EVAL_DOLLAR_SUPPORT")
    if override is not None:
        return override.lower() in ("1", "true", "yes")
    return cli == "codex"


def build_judge_prompt(rubric_text: str, workdir: Path,
                       inputs: list | None = None,
                       max_input_bytes: int = DEFAULT_JUDGE_MAX_INPUT_BYTES
                       ) -> tuple[str, list[dict]]:
    """Build the judge prompt, optionally injecting declared input files.

    With `inputs` (a list of {path, label} mappings resolved against
    `workdir`), the judge receives the files' contents explicitly and
    no longer depends on remembering a prior task. Files larger than
    `max_input_bytes` are truncated with a marker. Returns (prompt,
    injections) where `injections` records what was injected (or why
    not) for the trace.

    Declared inputs that do not exist abort the run: they are part of
    the evaluation contract, and silently skipping them would let a
    stale fixture keep producing misleading scores.
    """
    prompt = (
        "You are an external grader evaluating an agent's work against "
        "a rubric. The files below are the agent's declared outputs; "
        "grade only against them and the rubric, and be honest and "
        "critical.\n\n<rubric>\n" + rubric_text + "\n</rubric>\n"
    )
    injections: list[dict] = []
    if not inputs:
        return prompt, injections

    sections = []
    for entry in inputs:
        rel = entry.get("path")
        label = entry.get("label", rel)
        target = workdir / rel
        if not target.exists():
            raise SystemExit(
                f"judge input declared but not found: {rel} "
                f"(label '{label}'). The fixture's judge.inputs must "
                "match files the task actually produces.")
        raw = target.read_bytes()
        truncated = len(raw) > max_input_bytes
        content = raw[:max_input_bytes].decode("utf-8", errors="replace")
        if truncated:
            content += (f"\n\n[truncated at {max_input_bytes} bytes; "
                        f"full size {len(raw)}]")
        sections.append(
            f'<file label="{label}" path="{rel}">\n{content}\n</file>')
        injections.append({
            "path": rel, "label": label,
            "bytes": len(raw), "truncated": truncated,
        })
    return prompt + "\n" + "\n\n".join(sections) + "\n", injections


def resolve_judge_cli(args_judge_cli: str | None,
                      fixture_judge_cli: str | None,
                      agent_cli: str) -> str:
    """Pick the judge CLI: command line > fixture > agent CLI."""
    return args_judge_cli or fixture_judge_cli or agent_cli


def resolve_codex_bin() -> str:
    """Find a codex binary that actually launches.

    EVAL_CODEX_BIN env var wins. Otherwise probe every `codex` on PATH
    via where.exe, preferring npm-installed versions (which can spawn
    from child processes) over WindowsApps/desktop-app versions (which
    cannot).
    """
    if override := os.environ.get("EVAL_CODEX_BIN"):
        return override
    try:
        out = subprocess.run(
            ["where.exe", "codex"], capture_output=True, text=True,
            timeout=5,
        )
        candidates = [ln.strip() for ln in out.stdout.splitlines()
                      if ln.strip()]
    except FileNotFoundError:
        candidates = []
    if not candidates:
        # where.exe missed it; fall back to shutil.which
        if p := shutil.which("codex"):
            candidates = [p]
    # Prefer npm-installed binaries: they live outside WindowsApps and
    # can be spawned from child processes.
    npm_hint = os.path.join(os.environ.get("APPDATA", ""), "npm")
    candidates.sort(key=lambda p: 0 if npm_hint in p else 1)
    for path in candidates:
        try:
            subprocess.run(
                [path, "--version"], capture_output=True, timeout=10,
            )
            return path
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise SystemExit(
        "no working codex binary found on PATH; "
        "set EVAL_CODEX_BIN to a codex that can start")


def run_mock_agent(prompt: str, workdir: Path) -> dict:
    """Deterministic stand-in for a real agent CLI.

    Use for validating the eval pipeline itself (fixtures, asserts,
    reporting) when no real CLI is available. Judge prompts return a
    fixed rubric-shaped JSON; task prompts materialize the fixture's
    expected output so asserts can be exercised. Pass EVAL_MOCK_BEHAVIOR
    =ok|fail to test the failure path.
    """
    behavior = os.environ.get("EVAL_MOCK_BEHAVIOR", "ok")
    if "<rubric>" in prompt:
        total = 4 if behavior == "ok" else 2
        payload = json.dumps({"structure": 4, "clarity": 4, "fidelity": 4,
                              "total": total, "notes": "mock judge"})
        return {
            "cmd": "mock", "exit": 0,
            "stdout": payload,
            "stderr": "",
        }
    if behavior == "ok":
        (workdir / "output").mkdir(parents=True, exist_ok=True)
        (workdir / "output" / "edited.md").write_text(
            "# Why our deploy pipeline is slow\n\n"
            "The pipeline is slow for two reasons.\n\n"
            "## Sequential tests\n\n"
            "Tests run one after another, so total time is the sum of all "
            "test times.\n\n"
            "## No build caching\n\n"
            "The build rebuilds everything even when most packages are "
            "unchanged. Caching stores results so they need not be "
            "recomputed.\n\n"
            "## Fixes\n\n"
            "Parallelize the tests and cache the build.\n",
            encoding="utf-8")
        return {"cmd": "mock", "exit": 0, "stdout": "mock task done", "stderr": ""}
    return {"cmd": "mock", "exit": 1, "stdout": "", "stderr": "mock failure"}


def invoke_agent(cli: str, prompt: str, workdir: Path,
                 codex_home: Path | None = None) -> dict:
    if cli == "mock":
        return run_mock_agent(prompt, workdir)

    env = None
    if codex_home is not None:
        env = dict(os.environ)
        env["CODEX_HOME"] = str(codex_home)

    if cli == "codex":
        codex_bin = resolve_codex_bin()
        cmd = [
            codex_bin, "exec", "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            "-c", "approval=never",
            "-C", str(workdir),
            "-",  # read prompt from stdin
        ]
        stdin_data = prompt
    elif cli == "claude":
        cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
        stdin_data = None
    else:
        raise SystemExit(f"unknown CLI: {cli}")

    try:
        proc = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True,
            encoding="utf-8",
            input=stdin_data,
            env=env,
            timeout=int(os.environ.get("EVAL_AGENT_TIMEOUT", "900")),
        )
        return {
            "cmd": " ".join(cmd[:6]) + " ...",
            "exit": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-2000:],
        }
    except FileNotFoundError:
        return {"cmd": cmd[0], "exit": -1, "stdout": "",
                "stderr": f"CLI not found on PATH: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"cmd": cmd[0], "exit": -2, "stdout": "", "stderr": "timeout"}


def extract_json(text: str):
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- commands

def cmd_run(args):
    fixture_dir = Path(args.fixture).resolve()
    spec = parse_simple_yaml((fixture_dir / "fixture.yaml").read_text(encoding="utf-8"))
    skill_dir = Path(args.skill).resolve()
    runs = int(args.runs or spec.get("runs", 3))
    task_text = (fixture_dir / spec["task"]).read_text(encoding="utf-8")
    rubric_text = ""
    judge_spec = spec.get("judge") or {}
    judge_inputs = judge_spec.get("inputs")
    judge_max_bytes = int(
        judge_spec.get("max_input_bytes", DEFAULT_JUDGE_MAX_INPUT_BYTES))
    judge_cli = resolve_judge_cli(
        getattr(args, "judge_cli", None), judge_spec.get("cli"), args.cli)
    if judge_spec and not args.ci:
        rubric_text = (fixture_dir / judge_spec["rubric"]).read_text(
            encoding="utf-8")

    mount = getattr(args, "mount", None)
    if mount is not None:
        if mount != "codex":
            raise SystemExit(
                f"--mount {mount} is not implemented. Only '--mount codex' "
                "is supported in this version; claude mounting would "
                "require installing into the user's real ~/.claude/skills "
                "and is a separate, riskier feature.")
        if args.cli == "claude":
            raise SystemExit(
                "--mount codex cannot be combined with --cli claude. "
                "Use '--cli codex --mount codex' for a real run, or "
                "'--cli mock --mount codex' to validate mount/cleanup "
                "logic without a live agent.")
        # Validate the skill BEFORE any run starts; a mount problem is a
        # framework/environment error, never a skill-quality signal, so
        # we abort the whole run rather than recording failed attempts.
        skill_name = parse_frontmatter_name(skill_dir)
        dollar_supported = detect_dollar_support("codex")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{spec['name']}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    attempts = []
    for i in range(runs):
        workdir = Path(tempfile.mkdtemp(prefix=f"eval-{spec['name']}-{i}-"))
        setup = fixture_dir / "setup"
        if setup.exists():
            shutil.copytree(setup, workdir, dirs_exist_ok=True)

        codex_home = None
        mount_copied: list[str] = []
        trigger_mode = None
        if mount == "codex":
            real_home = Path(
                os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            codex_home, mount_copied = build_codex_home(
                skill_dir, skill_name, real_home)
            task_prompt, trigger_mode = build_mounted_task_prompt(
                skill_name, task_text, dollar_supported)
        else:
            task_prompt = build_task_prompt(skill_dir, task_text)

        try:
            task_trace = invoke_agent(args.cli, task_prompt, workdir,
                                      codex_home=codex_home)
        finally:
            if codex_home is not None:
                cleanup_codex_home(codex_home)

        asserts = run_assertions(spec.get("assert"), workdir)

        judge_result = None
        judge_trace = None
        judge_error = None
        judge_injections: list[dict] = []
        if rubric_text and task_trace["exit"] == 0:
            # The judge may be a different CLI than the agent under
            # test; only give it a mounted CODEX_HOME when it is codex.
            judge_home = None
            if mount == "codex" and judge_cli == "codex":
                judge_home, _ = build_codex_home(
                    skill_dir, skill_name,
                    Path(os.environ.get("CODEX_HOME",
                                        str(Path.home() / ".codex"))))
            try:
                judge_prompt, judge_injections = build_judge_prompt(
                    rubric_text, workdir,
                    inputs=judge_inputs, max_input_bytes=judge_max_bytes)
                judge_trace = invoke_agent(
                    judge_cli, judge_prompt, workdir,
                    codex_home=judge_home)
            finally:
                if judge_home is not None:
                    cleanup_codex_home(judge_home)
            if judge_trace["exit"] != 0:
                judge_error = (
                    f"judge CLI '{judge_cli}' exited "
                    f"{judge_trace['exit']}: "
                    f"{judge_trace['stderr'][:200]}")
            else:
                judge_result = extract_json(judge_trace["stdout"])
                if judge_result is None:
                    judge_error = (
                        f"judge CLI '{judge_cli}' returned no JSON "
                        "object in stdout")

        trace_gz = run_dir / f"attempt-{i}.trace.json.gz"
        with gzip.open(trace_gz, "wt", encoding="utf-8") as fh:
            json.dump({
                "task": task_trace,
                "judge": judge_trace,
                "judge_cli": judge_cli,
                "judge_inputs": judge_injections if judge_inputs else "none",
                "mount": {
                    "enabled": mount == "codex",
                    "copied": mount_copied,
                    "trigger_mode": trigger_mode,
                    "skill_name": skill_name if mount == "codex" else None,
                },
            }, fh, ensure_ascii=False)

        attempts.append({
            "index": i,
            "agent_exit": task_trace["exit"],
            "asserts": asserts,
            "asserts_passed": sum(1 for a in asserts if a["ok"]),
            "asserts_total": len(asserts),
            "judge": judge_result,
            "judge_error": judge_error,
            "workdir": str(workdir),
            "trace": trace_gz.name,
        })
        status = "PASS" if all(a["ok"] for a in asserts) else "FAIL"
        print(f"[attempt {i}] asserts {status} "
              f"({attempts[-1]['asserts_passed']}/{attempts[-1]['asserts_total']})"
              + (f" mount={trigger_mode}" if trigger_mode else "")
              + (f" judge={judge_result.get('total')}" if judge_result else "")
              + (f" JUDGE-ERROR" if judge_error else ""))

        if not args.keep_workdirs and task_trace["exit"] == 0:
            shutil.rmtree(workdir, ignore_errors=True)

    summary = {
        "run_id": run_id,
        "fixture": spec["name"],
        "skill": str(skill_dir),
        "cli": args.cli,
        "mount": mount,
        "judge_cli": judge_cli if rubric_text else None,
        "ci_mode": args.ci,
        "timestamp": stamp,
        "attempts": attempts,
        "asserts_pass_rate": (
            sum(a["asserts_passed"] for a in attempts)
            / max(1, sum(a["asserts_total"] for a in attempts))
        ),
        "judge_scores": [
            a["judge"]["total"] for a in attempts
            if a["judge"] and isinstance(a["judge"].get("total"), (int, float))
        ],
    }
    (run_dir / "run.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"run saved -> {run_dir / 'run.json'}")

    if args.ci:
        ok = summary["asserts_pass_rate"] == 1.0
        print("CI gate:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)


def cmd_init(args):
    dest = Path(args.name).resolve()
    if dest.exists():
        raise SystemExit(f"already exists: {dest}")
    (dest / "setup" / "input").mkdir(parents=True)
    (dest / "fixture.yaml").write_text(
        "name: " + dest.name + "\n"
        "description: TODO what this fixture checks\n"
        "task: task.md\n"
        "assert:\n"
        "  - type: file_exists\n"
        "    path: output/result.md\n"
        "judge:\n"
        "  rubric: rubric.md\n"
        "  max_score: 5\n"
        "runs: 3\n", encoding="utf-8")
    (dest / "task.md").write_text("TODO: the task the agent must perform.\n",
                                  encoding="utf-8")
    (dest / "rubric.md").write_text(
        "Score 1-5 on quality. Return JSON only: "
        '{"total": N, "notes": "..."}.\n', encoding="utf-8")
    (dest / "setup" / "input" / ".gitkeep").write_text("", encoding="utf-8")
    print(f"fixture scaffolded -> {dest}")


def main():
    ap = argparse.ArgumentParser(prog="eval")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--skill", required=True, help="path to skill directory")
    p_run.add_argument("--fixture", required=True, help="path to fixture directory")
    p_run.add_argument("--cli", default="codex",
                       choices=["codex", "claude", "mock"])
    p_run.add_argument("--mount", default=None, choices=["codex"],
                       help="mount the skill into a temporary CODEX_HOME "
                            "and trigger it via $name instead of injecting "
                            "SKILL.md into the prompt. Only 'codex' is "
                            "implemented; combine with '--cli mock' to "
                            "validate mount/cleanup logic without a live "
                            "agent.")
    p_run.add_argument("--judge-cli", default=None,
                       choices=["codex", "claude", "mock"],
                       help="CLI used for judge calls. Overrides the "
                            "fixture's judge.cli; defaults to --cli "
                            "(the agent under test).")
    p_run.add_argument("--runs", type=int, default=None)
    p_run.add_argument("--ci", action="store_true",
                       help="deterministic asserts only; exit non-zero on failure")
    p_run.add_argument("--keep-workdirs", action="store_true")
    p_run.set_defaults(fn=cmd_run)

    p_init = sub.add_parser("init")
    p_init.add_argument("name", help="new fixture directory path")
    p_init.set_defaults(fn=cmd_init)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
