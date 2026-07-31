---
name: eval-skill
description: Evaluate and regression-test other SKILL.md skills. Use when the user wants to score a skill's quality, compare two versions of a skill, build eval fixtures for a skill, or wire skill checks into CI.
---

# Evaluating SKILL.md skills

A skill is good if an agent following it reliably produces good output.
Evaluation therefore means: run the skill on representative tasks, check
the output, and track how that changes as the skill evolves.

## The two judgment layers

Always separate these — mixing them is the most common eval mistake.

1. **Deterministic asserts** (hard gate): machine-checkable conditions on
   the output — files exist, patterns present/absent, commands exit 0,
   tests pass. These never lie and are the only layer allowed in CI.
2. **LLM judge** (signal): an agent grades open-ended quality against a
   written rubric. Judge scores drift run-to-run; treat them as a trend
   signal in reports, never as a CI gate.

## Writing fixtures

One fixture = one representative task + its assertions. Keep fixtures
small and pointed at one behavior of the skill.

- `task.md` must neutralize interactive steps: if the skill says
  "confirm with the user", the task must override ("proceed
  autonomously, do not ask"). Otherwise runs hang.
- Prefer asserts over judge wherever a mechanical check exists: an
  edited-article skill can assert on filler phrases being gone; a
  debugging skill can assert the correct file was named.
- Rubrics must force structured output (JSON with a `total`) so scores
  can be trended. Ask for axis subscores — they localize regressions.
- `setup/` holds the initial filesystem state. Runs happen in an
  isolated temp copy, so fixtures never pollute the real workspace.

## Running

```bash
python scripts/eval.py run --skill <skill-dir> --fixture <fixture-dir> [--cli codex|claude|mock] [--runs N]
python scripts/eval.py run ... --ci     # asserts only, exit code = gate
python scripts/report.py                # HTML report at runs/report.html
python scripts/eval.py init <dir>       # scaffold a new fixture
```

`--cli mock` runs a deterministic fake agent: use it to validate new
fixtures and the pipeline itself before spending real agent tokens.
If a real CLI cannot start (e.g. `codex.exe` under WindowsApps refuses
CreateProcess from child processes), fall back to mock and note it in
the run — a mock run validates plumbing, never skill quality.

## Regression discipline

- Commit `runs/*/run.json` with the skill change that produced them, so
  a diff in skill text is reviewed alongside the diff in scores.
- Investigate judge-score drops > 0.5 on a 5-point scale; smaller moves
  are noise. Investigate any assert that flips from pass to fail.
- Refresh baselines deliberately, not automatically: accept a new
  baseline only after a human reads the report delta.

## Anti-patterns

- Gating CI on judge scores (flaky mornings, red afternoons).
- Judging without a rubric (scores become vibes, untrendable).
- One mega-fixture testing everything (regressions can't be localized).
- Letting the skill under test see the rubric (teaching to the test).
