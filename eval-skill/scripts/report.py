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


def collect_sub_axes(runs) -> list[str]:
    """Find all judge sub-axis keys across runs, preserving first-seen order.
    
    Excludes 'total' and 'notes' which are rendered separately.
    Returns axis names in the order they first appear."""
    seen = set()
    axes = []
    skip = {"total", "notes"}
    for run in runs:
        for attempt in run.get("attempts", []):
            judge = attempt.get("judge")
            if not judge or not isinstance(judge, dict):
                continue
            for key in judge:
                if key not in skip and key not in seen:
                    seen.add(key)
                    axes.append(key)
    return axes


def attempt_sub_score(judge, axis: str):
    """Get a sub-axis score from a judge dict, or None."""
    if not judge or not isinstance(judge, dict):
        return None
    v = judge.get(axis)
    return v if isinstance(v, (int, float)) else None


def run_sub_axis_avg(run, axis: str):
    """Average a sub-axis across all scored attempts in a run."""
    scores = [attempt_sub_score(a.get("judge"), axis)
              for a in run.get("attempts", [])]
    return avg(scores)


# ---------------------------------------------------------------- grouping

def group_key(run) -> tuple:
    """Runs trend together only when agent, mount, judge, and fixture
    all match; otherwise a score change may reflect a config change
    rather than a skill change."""
    return (
        run["fixture"],
        run["cli"],
        run.get("mount"),
        run.get("judge_cli"),
    )


def group_label(key) -> str:
    fixture, cli, mount, judge_cli = key
    parts = [fixture, f"agent={cli}"]
    if mount:
        parts.append(f"mount={mount}")
    if judge_cli:
        parts.append(f"judge={judge_cli}")
    return " / ".join(parts)


def group_runs(runs) -> dict:
    groups: dict = {}
    for run in runs:
        groups.setdefault(group_key(run), []).append(run)
    return groups


# ---------------------------------------------------------------- svg trend

SVG_W = 720
SVG_H = 110
SVG_PAD_L = 34
SVG_PAD_R = 10
SVG_PAD_T = 8
SVG_PAD_B = 18


def _svg_line(x1, y1, x2, y2, color):
    return (f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}'"
            f" stroke='{color}' stroke-width='1.5'/>")


def _svg_dot(cx, cy, color, title):
    return (f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='3.5' fill='{color}'>"
            f"<title>{html.escape(title)}</title></circle>")


def _svg_text(x, y, text, anchor="end", size=9, color="#666"):
    return (f"<text x='{x:.1f}' y='{y:.1f}' font-size='{size}' "
            f"fill='{color}' text-anchor='{anchor}'>"
            f"{html.escape(text)}</text>")


def render_series(points, y_min, y_max, color, value_fmt) -> str:
    """One trend line. `points` is a list of (run_id, value|None);
    None values break the line (e.g. ci-mode runs have no judge)."""
    n = len(points)
    plot_w = SVG_W - SVG_PAD_L - SVG_PAD_R
    plot_h = SVG_H - SVG_PAD_T - SVG_PAD_B
    span = max(1e-9, y_max - y_min)

    def x_of(i):
        return SVG_PAD_L + (plot_w * i / max(1, n - 1))

    def y_of(v):
        return SVG_PAD_T + plot_h * (1 - (v - y_min) / span)

    parts = []
    for frac in (0, 0.5, 1):
        yv = y_min + span * frac
        y = y_of(yv)
        parts.append(_svg_line(SVG_PAD_L, y, SVG_W - SVG_PAD_R, y, "#eee"))
        parts.append(_svg_text(SVG_PAD_L - 4, y + 3, value_fmt(yv)))
    prev = None
    for i, (run_id, value) in enumerate(points):
        if value is None:
            prev = None
            continue
        x, y = x_of(i), y_of(value)
        if prev is not None:
            parts.append(_svg_line(prev[0], prev[1], x, y, color))
        parts.append(_svg_dot(x, y, color, f"{run_id}: {value_fmt(value)}"))
        prev = (x, y)
    if n >= 1:
        parts.append(_svg_text(SVG_PAD_L, SVG_H - 4,
                               points[0][0][:13], anchor="start"))
        if n > 1:
            parts.append(_svg_text(SVG_W - SVG_PAD_R, SVG_H - 4,
                                   points[-1][0][:13], anchor="end"))
    return (f"<svg width='{SVG_W}' height='{SVG_H}' "
            f"viewBox='0 0 {SVG_W} {SVG_H}' "
            f"xmlns='http://www.w3.org/2000/svg' role='img'>"
            + "".join(parts) + "</svg>")


def render_trend(group_runs_list) -> str:
    """Trend charts for one group: assert pass-rate, judge total, and
    sub-axes (structure/clarity/fidelity). Groups with fewer than two
    runs get a placeholder."""
    runs = sorted(group_runs_list, key=lambda r: r["run_id"])
    if len(runs) < 2:
        return ("<div class='trend-placeholder'>"
                "需要 &ge;2 个同配置 run 才能显示趋势；"
                "当前该组只有 " + str(len(runs)) + " 个 run。"
                "</div>")
    
    pr_points = [(r["run_id"], r["asserts_pass_rate"]) for r in runs]
    judge_points = [
        (r["run_id"], avg(r.get("judge_scores") or [])) for r in runs]
    
    # Collect sub-axes from all runs in this group
    sub_axes = collect_sub_axes(runs)
    
    parts = []
    parts.append(f"<div class='trend-title'>assert pass-rate</div>")
    parts.append(render_series(pr_points, 0, 1, "#2e9e5b",
                               lambda v: f"{v * 100:.0f}%"))
    parts.append(f"<div class='trend-title'>judge total</div>")
    parts.append(render_series(judge_points, 1, 5, "#3a6ea5",
                               lambda v: f"{v:.1f}"))
    
    # Add one trend line per sub-axis
    for axis in sub_axes:
        axis_points = [(r["run_id"], run_sub_axis_avg(r, axis)) for r in runs]
        # Determine color based on axis name
        if axis == "structure":
            color = "#9333ea"  # purple
        elif axis == "clarity":
            color = "#0ea5e9"  # sky blue
        elif axis == "fidelity":
            color = "#f59e0b"  # amber
        else:
            color = "#6b7280"  # gray
        parts.append(f"<div class='trend-title'>{axis}</div>")
        parts.append(render_series(axis_points, 1, 5, color,
                                   lambda v: f"{v:.1f}"))
    
    return f"<div class='trend'>{''.join(parts)}</div>"


# ---------------------------------------------------------------- render

def render(runs) -> str:
    groups = group_runs(runs)
    order: list = []
    for run in runs:
        k = group_key(run)
        if k not in order:
            order.append(k)

    nav = "".join(
        f"<li><a href='#g{i}'>{html.escape(group_label(k))}</a>"
        f" ({len(groups[k])} run(s))</li>"
        for i, k in enumerate(order))

    sections = []
    for gi, key in enumerate(order):
        g_runs = groups[key]
        trend_html = render_trend(g_runs)
        # Collect sub-axes for this group to show in attempt tables
        sub_axes = collect_sub_axes(g_runs)
        
        cards = []
        for i, run in enumerate(reversed(g_runs)):
            attempts = run["attempts"]
            pass_rate = run["asserts_pass_rate"]
            judge_scores = run.get("judge_scores") or []
            judge_avg = avg(judge_scores)

            baseline_html = ""
            prev_idx = len(g_runs) - i - 2
            if prev_idx >= 0:
                prev = g_runs[prev_idx]
                d_pr = pass_rate - prev["asserts_pass_rate"]
                prev_judge = avg(prev.get("judge_scores") or [])
                d_judge = (judge_avg - prev_judge) if (
                    judge_avg is not None and prev_judge is not None
                ) else None
                
                # Also show sub-axis deltas if available
                sub_deltas = []
                for axis in sub_axes:
                    curr = run_sub_axis_avg(run, axis)
                    prev_val = run_sub_axis_avg(prev, axis)
                    if curr is not None and prev_val is not None:
                        delta = curr - prev_val
                        sign = "+" if delta >= 0 else ""
                        sub_deltas.append(
                            f"{axis} {sign}{fmt(delta)}")
                
                baseline_html = (
                    f"<div class='delta'>vs baseline "
                    f"{html.escape(prev['run_id'])}: "
                    f"pass-rate {'+' if d_pr >= 0 else ''}{fmt(d_pr)}"
                    + (f", judge {'+' if d_judge >= 0 else ''}"
                       f"{fmt(d_judge)}" if d_judge is not None else "")
                    + ((", " + ", ".join(sub_deltas)) if sub_deltas else "")
                    + "</div>"
                )

            # Build table header with sub-axes
            header_cols = ["#", "asserts", "judge"]
            for axis in sub_axes:
                header_cols.append(axis)
            header_cols.append("notes")
            header_html = "<tr>" + "".join(
                f"<th>{col}</th>" for col in header_cols) + "</tr>"
            
            # Build rows
            rows = ""
            for a in attempts:
                judge = a.get("judge")
                judge_total = fmt((judge or {}).get("total")) if judge else "-"
                
                # Sub-axis cells
                sub_cells = ""
                for axis in sub_axes:
                    score = attempt_sub_score(judge, axis)
                    sub_cells += f"<td>{fmt(score) if score is not None else '-'}</td>"
                
                rows += (
                    "<tr class='%s'><td>%d</td><td>%d/%d</td><td>%s</td>"
                    "%s"
                    "<td%s>%s</td></tr>" % (
                        row_class(a["asserts_passed"] == a["asserts_total"]
                                  and not a.get("judge_error")),
                        a["index"], a["asserts_passed"], a["asserts_total"],
                        judge_total,
                        sub_cells,
                        " class='judge-error'"
                        if a.get("judge_error") else "",
                        (html.escape(a["judge_error"][:120])
                         if a.get("judge_error")
                         else html.escape(
                             ((a["judge"] or {}).get("notes", "")[:120]))
                         if a["judge"] else ""),
                    )
                )
            
            judge_cli = run.get("judge_cli")
            cards.append(f"""
<section class="card {'pass' if pass_rate == 1.0 else 'fail'}">
  <h2>{html.escape(run['run_id'])}</h2>
  <div class="meta">
    skill <code>{html.escape(run['skill'])}</code> &middot;
    cli <code>{html.escape(run['cli'])}</code> &middot;
    {(f"judge <code>{html.escape(judge_cli)}</code> &middot; "
      if judge_cli else "")}
    pass-rate <b>{fmt(pass_rate * 100, 0)}%</b> &middot;
    judge avg <b>{fmt(judge_avg)}</b>
  </div>
  {baseline_html}
  <table>
    {header_html}
    {rows}
  </table>
</section>""")
        sections.append(
            f"<section class='group' id='g{gi}'>"
            f"<h2 class='group-title'>{html.escape(group_label(key))}</h2>"
            f"{trend_html}{''.join(cards)}</section>")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>eval-skill report</title>
<style>
  body {{ font: 14px/1.5 -apple-system, "Segoe UI", sans-serif;
         margin: 2rem auto; max-width: 900px; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .group {{ margin-bottom: 2.5rem; }}
  .group-title {{ font-size: 1.05rem; border-bottom: 1px solid #ddd;
                  padding-bottom: .3rem; }}
  .trend {{ margin: .5rem 0 1rem 0; }}
  .trend-title {{ font-size: .8rem; color: #666; margin-top: .4rem; }}
  .trend-placeholder {{ border: 1px dashed #ccc; color: #888;
                         padding: .6rem .8rem; font-size: .85rem;
                         border-radius: 6px; margin: .5rem 0 1rem 0; }}
  .card {{ border: 1px solid #ddd; border-left: 4px solid #999;
          border-radius: 6px; padding: 1rem 1.25rem; margin: 1rem 0; }}
  .card.pass {{ border-left-color: #2e9e5b; }}
  .card.fail {{ border-left-color: #d64545; }}
  .meta {{ color: #555; margin-bottom: .5rem; }}
  .delta {{ font-size: .85rem; color: #775500; margin-bottom: .5rem; }}
  .nav {{ font-size: .9rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border: 1px solid #eee; padding: .3rem .5rem; text-align: left; }}
  tr.fail td {{ background: #fdecec; }}
  tr.pass td {{ background: #eefaf2; }}
  td.judge-error {{ background: #fff3cd; color: #7a5c00;
                    font-family: monospace; font-size: .85em; }}
  code {{ background: #f4f4f4; padding: 0 .3em; border-radius: 3px; }}
</style></head><body>
<h1>eval-skill report &middot; {len(runs)} run(s)</h1>
<ul class="nav">{nav}</ul>
{''.join(sections)}
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
