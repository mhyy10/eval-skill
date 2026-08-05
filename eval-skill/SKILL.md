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

## Choosing a judge

Default judge = the same agent that did the task, grading itself. That
is fine for a quick smoke signal but systematically biased: agents
overrate their own work and anchor on the choices they already made.

Use `--judge-cli <other>` when the score actually matters — before
shipping a skill, or when trending over time. A different CLI judges
the work cold, with no memory of doing it.

Whatever judge you use, declare its inputs in `fixture.yaml`:

```yaml
judge:
  rubric: rubric.md
  inputs:
    - path: output/edited.md
      label: edited
    - path: input/draft.md
      label: original
```

Injected inputs are the judge's contract: they say exactly which files
the grade is about. A declared input that goes missing aborts the run
on purpose — a stale contract producing confident-looking scores is
worse than no score.

Rubrics for external judges must be self-contained: the judge did not
do the task, so "did you preserve the draft's claims" works, "did you
follow the steps you planned" does not.

Judge failures never fail CI. A `judge_error` in `run.json` means the
judge could not score (missing CLI, bad output); asserts still gate.
Investigate `judge_error` before trusting a run's trend line.

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
python scripts/eval.py run ... --mount codex   # real skill loading, not prompt injection
python scripts/eval.py run ... --ci     # asserts only, exit code = gate
python scripts/report.py                # HTML report at runs/report.html
python scripts/eval.py init <dir>       # scaffold a new fixture
```

`--cli mock` runs a deterministic fake agent: use it to validate new
fixtures and the pipeline itself before spending real agent tokens.
If a real CLI cannot start (e.g. `codex.exe` under WindowsApps refuses
CreateProcess from child processes), fall back to mock and note it in
the run — a mock run validates plumbing, never skill quality.

## Injection vs. mounting

Default behavior *injects*: `SKILL.md` text is prepended to the task
prompt. `--mount codex` *mounts* instead: the skill is copied into a
temporary `CODEX_HOME/skills/<name>/`, the agent launches with that
`CODEX_HOME`, and the prompt triggers the skill via `$name`.

Choose deliberately — they measure different things:

- **Inject** when iterating on skill *content*. Fast, no CLI quirks,
  and "does the model follow these instructions" is usually what you
  are editing.
- **Mount** when validating a skill *before shipping*. It catches
  failure modes injection cannot: broken frontmatter, auxiliary files
  the skill expects, and (with real CLI) whether the platform's loader
  accepts the skill at all.

Mount rules of thumb:

- The skill's canonical name comes from frontmatter `name:`, not the
  directory. Missing `name:` aborts the run — fix the skill, don't
  work around it.
- Mount failures abort the whole run. A mount error is never recorded
  as a skill-quality failure, because the skill never got to run.
- Use `--cli mock --mount codex` to validate the mount path itself on
  machines where a real `codex` CLI cannot start. It exercises
  frontmatter parsing, `CODEX_HOME` construction, and cleanup without
  an agent.
- The temporary `CODEX_HOME` is always deleted, even under
  `--keep-workdirs` — it contains `auth.json`. Never point `--mount`
  at a `CODEX_HOME` you cannot afford to see copied temporarily.
- If `codex exec` does not expand `$name` in your environment, set
  `EVAL_DOLLAR_SUPPORT=0`; the prompt falls back to a named-skill
  instruction and the trace records `trigger_mode: fallback-named`.

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
