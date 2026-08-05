#!/usr/bin/env python3
"""Unit tests for report.py: grouping, trend series, and full render."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "report", Path(__file__).resolve().parent.parent / "scripts" / "report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def make_run(run_id, fixture="fix", cli="mock", mount=None, judge_cli=None,
             pass_rate=1.0, judge_scores=None):
    return {
        "run_id": run_id,
        "fixture": fixture,
        "cli": cli,
        "skill": "some-skill",
        "mount": mount,
        "judge_cli": judge_cli,
        "asserts_pass_rate": pass_rate,
        "judge_scores": judge_scores,
        "attempts": [{
            "index": 0,
            "asserts_passed": int(pass_rate),
            "asserts_total": 1,
            "judge": {"total": judge_scores[0]} if judge_scores else None,
            "judge_error": None,
        }],
    }


class TestGrouping(unittest.TestCase):
    def test_same_config_groups_together(self):
        runs = [make_run("r1"), make_run("r2")]
        groups = report.group_runs(runs)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(list(groups.values())[0]), 2)

    def test_mount_separates_groups(self):
        runs = [make_run("r1", mount="codex"), make_run("r2")]
        self.assertEqual(len(report.group_runs(runs)), 2)

    def test_judge_separates_groups(self):
        runs = [make_run("r1", judge_cli="mock"),
                make_run("r2", judge_cli="claude")]
        self.assertEqual(len(report.group_runs(runs)), 2)

    def test_agent_separates_groups(self):
        runs = [make_run("r1", cli="codex"), make_run("r2", cli="claude")]
        self.assertEqual(len(report.group_runs(runs)), 2)

    def test_group_label_omits_missing_parts(self):
        self.assertEqual(
            report.group_label(("fix", "mock", None, None)),
            "fix / agent=mock")
        self.assertEqual(
            report.group_label(("fix", "mock", "codex", "claude")),
            "fix / agent=mock / mount=codex / judge=claude")


class TestAvg(unittest.TestCase):
    def test_empty_is_none(self):
        self.assertIsNone(report.avg([]))
        self.assertIsNone(report.avg([None, "x"]))

    def test_filters_non_numbers(self):
        self.assertEqual(report.avg([4, None, "x", 2]), 3.0)


class TestRenderSeries(unittest.TestCase):
    def test_dots_for_each_scored_point(self):
        svg = report.render_series(
            [("r1", 0.5), ("r2", 1.0)], 0, 1, "#000", lambda v: f"{v}")
        self.assertEqual(svg.count("<circle"), 2)
        self.assertIn("<title>r2: 1.0</title>", svg)

    def test_none_breaks_line_but_keeps_dot(self):
        svg = report.render_series(
            [("r1", 0.5), ("r2", None), ("r3", 1.0)], 0, 1, "#000",
            lambda v: f"{v}")
        self.assertEqual(svg.count("<circle"), 2)
        # only grid lines, no data segment spanning the gap
        data_lines = [ln for ln in svg.split("/>")
                      if "<line" in ln and "'#eee'" not in ln]
        self.assertEqual(data_lines, [])

    def test_scored_points_connect(self):
        svg = report.render_series(
            [("r1", 0.0), ("r2", 1.0)], 0, 1, "#abc", lambda v: f"{v}")
        data_lines = [ln for ln in svg.split("/>")
                      if "<line" in ln and "'#eee'" not in ln]
        self.assertEqual(len(data_lines), 1)

    def test_extreme_values_stay_inside_plot(self):
        svg = report.render_series(
            [("r1", 1), ("r2", 5)], 1, 5, "#000", lambda v: f"{v}")
        for cy in [float(m) for m in
                   __import__("re").findall(r"cy='([\d.]+)'", svg)]:
            self.assertGreaterEqual(cy, report.SVG_PAD_T)
            self.assertLessEqual(cy, report.SVG_H - report.SVG_PAD_B)


class TestRenderTrend(unittest.TestCase):
    def test_single_run_shows_placeholder(self):
        html = report.render_trend([make_run("r1")])
        self.assertIn("trend-placeholder", html)
        self.assertNotIn("<svg", html)

    def test_two_runs_render_dual_subplots(self):
        runs = [make_run("r1", judge_scores=[4]),
                make_run("r2", judge_scores=[5])]
        html = report.render_trend(runs)
        self.assertEqual(html.count("<svg"), 2)
        self.assertIn("assert pass-rate", html)
        self.assertIn("judge avg", html)

    def test_runs_sorted_by_run_id(self):
        runs = [make_run("r2-zzz", judge_scores=[5]),
                make_run("r1-aaa", judge_scores=[4])]
        html = report.render_trend(runs)
        self.assertLess(html.index("r1-aaa: 4.0"), html.index("r2-zzz: 5.0"))

    def test_unscored_judge_runs_do_not_crash(self):
        runs = [make_run("r1", judge_scores=[4]), make_run("r2")]
        html = report.render_trend(runs)
        self.assertIn("<title>r1: 4.0</title>", html)
        judge_svg = html.split("judge avg</div>", 1)[1]
        self.assertNotIn("<title>r2:", judge_svg)


class TestRender(unittest.TestCase):
    def test_nav_and_group_sections(self):
        runs = [make_run("r1", judge_cli="mock", judge_scores=[4]),
                make_run("r2", judge_cli="mock", judge_scores=[5]),
                make_run("r3", mount="codex")]
        out = report.render(runs)
        self.assertEqual(out.count("class='group'"), 2)
        self.assertEqual(out.count("<ul class=\"nav\">"), 1)
        self.assertIn("agent=mock / judge=mock", out)
        self.assertIn("agent=mock / mount=codex", out)
        # trend only for the two-run group
        self.assertEqual(out.count("<svg"), 2)
        self.assertEqual(out.count("class='trend-placeholder'"), 1)

    def test_run_fields_escaped(self):
        runs = [make_run("r1<script>", fixture="f<x>")]
        out = report.render(runs)
        self.assertNotIn("<script>", out)
        self.assertIn("f&lt;x&gt;", out)

    def test_baseline_delta_within_group_only(self):
        runs = [make_run("r1", pass_rate=0.5),
                make_run("r2", pass_rate=1.0),
                make_run("r3", mount="codex", pass_rate=0.0)]
        out = report.render(runs)
        self.assertIn("vs baseline r1", out)
        # r3 is alone in its group: no baseline for it
        self.assertNotIn("vs baseline r2", out)


if __name__ == "__main__":
    unittest.main()
