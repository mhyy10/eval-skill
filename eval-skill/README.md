# eval-skill

Evaluate and regression-test SKILL.md skills. Stdlib-only Python, no deps.

## What it does

Runs a skill against a fixture (a representative task + assertions),
checks the output in two layers, and renders an HTML report:

1. **Deterministic asserts** — files exist, patterns present/absent,
   line counts, shell commands exit 0. This is the CI gate.
2. **LLM judge** — an agent grades quality against a rubric (JSON score).
   This is a trend signal, never a CI gate.

## Commands

```bash
python scripts/eval.py run --skill <skill-dir> --fixture <fixture-dir> [--cli codex|claude|mock] [--runs N] [--keep-workdirs]
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
