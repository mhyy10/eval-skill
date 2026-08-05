# eval-skill

Evaluate and regression-test SKILL.md skills. Stdlib-only Python, no deps.

## Installation

`eval-skill` is a skill (not an agent), so installation means copying
the folder into whatever directory your agent treats as its skills
root. Pick the one that matches your harness; install separately for
each one you use.

### Claude Code

```bash
./scripts/install.sh --target claude
# or:  ./scripts/install.sh --dest ~/.claude/skills/eval-skill
```

Or clone and install manually:

```bash
git clone https://github.com/your-name/eval-skill ~/.claude/skills/eval-skill
```

### Codex CLI / Codex App

```bash
./scripts/install.sh --target codex
# or:  ./scripts/install.sh --dest ~/.codex/skills/eval-skill
```

### Windows (PowerShell)

```powershell
.\scripts\install.ps1 -Target claude   # or -Target codex
.\scripts\install.ps1 -Dest "C:\path\to\your\skills\eval-skill"
```

The installers symlink the repo into the target directory so updates
are picked up without reinstalling; on Windows they fall back to a
copy if Developer Mode / elevated symlinks aren't available.

## What it does

Runs a skill against a fixture (a representative task + assertions),
checks the output in two layers, and renders an HTML report:

1. **Deterministic asserts** — files exist, patterns present/absent,
   line counts, shell commands exit 0. This is the CI gate.
2. **LLM judge** — an agent grades quality against a rubric (JSON score).
   This is a trend signal, never a CI gate.

## Injection vs. mounting

By default the runner *injects* the skill: `SKILL.md`'s full text is
prepended to the task prompt. That measures "what the model does after
reading the skill" but not "what happens when the platform loads the
skill through its real mechanism." Those can diverge — a skill that
reads fine as a prompt may never be routed to, or may reference
auxiliary files the injection path silently misses.

`--mount codex` switches to *mounting*: the skill directory is copied
into a temporary `CODEX_HOME/skills/<name>/`, the agent is launched
with `CODEX_HOME` pointing there, and the task prompt triggers the
skill via `$name`. This measures the skill as the platform actually
loads it.

Key properties of the mount implementation:

- **Isolation.** The temporary `CODEX_HOME` contains only the mounted
  skill plus an allowlist (`auth.json`, `config.toml`) needed for the
  CLI to start. Your other skills, memories, history, and sqlite state
  are not inherited, so the mounted skill is the only one the agent can
  route to.
- **Credential hygiene.** The temporary home is deleted after every
  attempt, even on failure and even with `--keep-workdirs` (which keeps
  the *workdir*, never the home, because `auth.json` holds tokens).
- **Canonical naming.** The skill name for `$name` is read from
  `SKILL.md`'s frontmatter `name:` field, not the directory name. If
  frontmatter or `name:` is missing, the run aborts before any attempt
  — a mount problem is a framework/environment error, never a skill-
  quality signal.
- **`$name` fallback.** Whether `codex exec` expands `$name` in non-
  interactive mode is auto-detected (override with
  `EVAL_DOLLAR_SUPPORT=0|1`). If unsupported, the prompt falls back to
  an explicit "use the skill named `X`" instruction, and the trace
  records `trigger_mode: fallback-named` so reports can flag the
  downgrade.

Valid combinations: `--cli codex --mount codex` (real mounted run),
`--cli mock --mount codex` (validates mount/cleanup logic without a
live agent). `--mount claude` is rejected: Claude Code has no per-run
skills-root override, so mounting would mean writing into your real
`~/.claude/skills` — a separate, riskier feature that is not
implemented.

Current verification status: mount/cleanup/frontmatter/fallback logic
is covered by unit tests and by `--cli mock` end-to-end runs. A real
`codex exec` end-to-end mounted run has not yet been performed on this
machine (the WindowsApps-bundled `codex.exe` cannot be spawned from a
child process); that verification is a known TODO, to be done on a
machine with a conventionally-installed codex CLI.

## External judge

By default the judge is the same agent that just completed the task,
grading its own work. That couples the score to the agent's self-
assessment biases. `--judge-cli` decouples it: the judge becomes a
separate process (potentially a different CLI) that receives the
declared outputs explicitly.

```bash
python scripts/eval.py run ... --judge-cli claude   # judge on a different CLI
```

Judge CLI resolution order: `--judge-cli` > the fixture's `judge.cli`
> `--cli` (the agent under test). The actual judge used is recorded in
`run.json` as `judge_cli`.

### Declaring judge inputs

With an external judge, "grade your own just-completed work" no longer
makes sense — the judge did not do the task. Declare the files the
judge should grade in `fixture.yaml`:

```yaml
judge:
  rubric: rubric.md
  max_score: 5
  inputs:
    - path: output/edited.md
      label: edited
    - path: input/draft.md
      label: original
```

The runner injects each as `<file label="..." path="...">...</file>`
in the judge prompt. A declared input that does not exist aborts the
run — judge inputs are part of the evaluation contract, and silently
skipping one would keep producing misleading scores. Files larger than
32 KB are truncated with a marker; raise the cap with
`judge.max_input_bytes` if a fixture genuinely needs bigger inputs.

Fixtures without `judge.inputs` still work: the judge prompt contains
only the rubric, and the trace records `judge_inputs: none` so reports
can distinguish injected from non-injected scoring.

### Judge failures

A judge that fails (CLI not found, non-zero exit, no JSON in stdout)
does NOT fail the run — judge scores are a trend signal, never a CI
gate. The failure is recorded per-attempt as `judge_error` in
`run.json`, printed as `JUDGE-ERROR` in the run log, and shown in the
HTML report, so a misconfigured `--judge-cli` is visible rather than
silently producing score-free runs.

### Known gap: judge model

There is currently no `--judge-model`. The judge uses whatever model
its CLI is configured with. Model-level judge control is deliberately
deferred until there is enough judge-score data to choose a judge
model on evidence rather than vibes.

## Commands

```bash
python scripts/eval.py run --skill <skill-dir> --fixture <fixture-dir> [--cli codex|claude|mock] [--runs N] [--keep-workdirs]
python scripts/eval.py run ... --mount codex   # load skill via CODEX_HOME, not prompt injection
python scripts/eval.py run ... --judge-cli X   # judge on CLI X, not the agent under test
python scripts/eval.py run ... --ci     # asserts only; exit 1 if any fail
python scripts/report.py                # writes runs/report.html
python scripts/eval.py init <dir>       # scaffold a new fixture
python -m unittest discover -s tests    # unit tests for the runner itself
```

`--cli mock` is a deterministic stand-in agent for validating the
pipeline itself (fixtures, asserts, reporting) when no real agent CLI
is available. `EVAL_MOCK_BEHAVIOR=fail` exercises the failure path.

Known platform limit: on this machine the Codex desktop app's
`codex.exe` lives under `C:\Program Files\WindowsApps\...` and cannot
be spawned from any child process (CreateProcess returns
access-denied, ACL is install-scoped). Use `--cli mock` locally, or
run on a machine with a conventionally-installed codex/claude CLI.

## Fixture layout

```
my-fixture/
  fixture.yaml    # name, task, asserts, judge rubric, run count
  task.md         # the prompt the agent must complete
  rubric.md       # scoring rubric for the judge
  setup/          # optional: files copied into the run's temp workdir
    input/...
```

Assert types: `file_exists`, `file_not_exists`, `file_contains`,
`file_not_contains`, `file_min_lines`, `command`.

## Example

`fixtures/edit-article-clarity` evaluates the `edit-article` skill on a
deliberately rambling draft, asserting filler phrases are gone and the
edited file has real structure.

## Roadmap

- `--mount claude|codex`: load the skill through the platform's real
  skill-loading mechanism instead of prompt injection (tests routing).
- External judge configuration (separate model from the agent under test).
- Multi-run trend charts in the HTML report.
