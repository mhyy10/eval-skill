#!/usr/bin/env python3
"""Render runs/*.json into a single HTML report with baseline comparison."""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"


def load_runs():
    runs = []
    for path in sorted(RUNS_DIR.glob("*/run.json")):
        runs.append(json.loads(path.read_text(encoding="utf-8")))
    return runs


def avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def fmt(x, digits=2):
    return f"{x:.{digits}f}" if isinstance(x, (int, float)) else "-"


def row_class(ok):
    return "pass" if ok else "fail"


def render(runs) -> str:
    cards = []
    for i, run in enumerate(reversed(runs)):
        attempts = run["attempts"]
        pass_rate = run["asserts_pass_rate"]
        judge_scores = run.get("judge_scores") or []
        judge_avg = avg(judge_scores)

        baseline_html = ""
        prev_idx = len(runs) - i - 2
        if prev_idx >= 0:
            prev = runs[prev_idx]
            d_pr = pass_rate - prev["asserts_pass_rate"]
            prev_judge = avg(prev.get("judge_scores") or [])
            d_judge = (judge_avg - prev_judge) if (
                judge_avg is not None and prev_judge is not None) else None
            baseline_html = (
                f"<div class='delta'>vs baseline {html.escape(prev['run_id'])}: "
                f"pass-rate {'+' if d_pr >= 0 else ''}{fmt(d_pr)}"
                + (f", judge {'+' if d_judge >= 0 else ''}{fmt(d_judge)}"
                   if d_judge is not None else "")
                + "</div>"
            )

        rows = "".join(
            "<tr class='%s'><td>%d</td><td>%d/%d</td><td>%s</td><td%s>%s</td></tr>" % (
                row_class(a["asserts_passed"] == a["asserts_total"]
                          and not a.get("judge_error")),
                a["index"], a["asserts_passed"], a["asserts_total"],
                fmt((a["judge"] or {}).get("total")) if a["judge"] else "-",
                " class='judge-error'" if a.get("judge_error") else "",
                (html.escape(a["judge_error"][:120]) if a.get("judge_error")
                 else html.escape(((a["judge"] or {}).get("notes", "")[:120]))
                 if a["judge"] else ""),
            )
            for a in attempts
        )
        judge_cli = run.get("judge_cli")
        cards.append(f"""
<section class="card {'pass' if pass_rate == 1.0 else 'fail'}">
  <h2>{html.escape(run['run_id'])}</h2>
  <div class="meta">
    skill <code>{html.escape(run['skill'])}</code> &middot;
    cli <code>{html.escape(run['cli'])}</code> &middot;
    {(f"judge <code>{html.escape(judge_cli)}</code> &middot; " if judge_cli else "")}
    pass-rate <b>{fmt(pass_rate * 100, 0)}%</b> &middot;
    judge avg <b>{fmt(judge_avg)}</b>
  </div>
  {baseline_html}
  <table>
    <tr><th>#</th><th>asserts</th><th>judge</th><th>notes</th></tr>
    {rows}
  </table>
</section>""")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>eval-skill report</title>
<style>
  body {{ font: 14px/1.5 -apple-system, "Segoe UI", sans-serif;
         margin: 2rem auto; max-width: 900px; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .card {{ border: 1px solid #ddd; border-left: 4px solid #999;
          border-radius: 6px; padding: 1rem 1.25rem; margin: 1rem 0; }}
  .card.pass {{ border-left-color: #2e9e5b; }}
  .card.fail {{ border-left-color: #d64545; }}
  .meta {{ color: #555; margin-bottom: .5rem; }}
  .delta {{ font-size: .85rem; color: #775500; margin-bottom: .5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border: 1px solid #eee; padding: .3rem .5rem; text-align: left; }}
  tr.fail td {{ background: #fdecec; }}
  tr.pass td {{ background: #eefaf2; }}
  td.judge-error {{ background: #fff3cd; color: #7a5c00;
                    font-family: monospace; font-size: .85em; }}
  code {{ background: #f4f4f4; padding: 0 .3em; border-radius: 3px; }}
</style></head><body>
<h1>eval-skill report &middot; {len(runs)} run(s)</h1>
{''.join(cards)}
</body></html>"""


def main():
    runs = load_runs()
    if not runs:
        print("no runs found in", RUNS_DIR)
        sys.exit(1)
    out = RUNS_DIR / "report.html"
    out.write_text(render(runs), encoding="utf-8")
    print("report ->", out)


if __name__ == "__main__":
    main()
