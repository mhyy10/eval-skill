#!/usr/bin/env python3
"""Unit tests for eval.py's pure components: yaml-lite, asserts, json."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "eval", Path(__file__).resolve().parent.parent / "scripts" / "eval.py")
eval_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eval_mod)

parse_simple_yaml = eval_mod.parse_simple_yaml
run_assertions = eval_mod.run_assertions
extract_json = eval_mod.extract_json


def make_skill_dir(tmp: Path, name: str = "my-skill") -> Path:
    skill = tmp / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: test skill\n"
        "---\n\n"
        "Do the thing.\n", encoding="utf-8")
    (skill / "helper.txt").write_text("auxiliary\n", encoding="utf-8")
    return skill


class TestFrontmatterName(unittest.TestCase):
    def test_reads_name(self):
        with tempfile.TemporaryDirectory() as td:
            skill = make_skill_dir(Path(td), "alpha-skill")
            self.assertEqual(eval_mod.parse_frontmatter_name(skill),
                             "alpha-skill")

    def test_missing_frontmatter_fails(self):
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "bare"
            skill.mkdir()
            (skill / "SKILL.md").write_text("no frontmatter here\n",
                                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                eval_mod.parse_frontmatter_name(skill)

    def test_missing_name_field_fails(self):
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "noname"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\ndescription: only\n---\nbody\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                eval_mod.parse_frontmatter_name(skill)

    def test_unclosed_frontmatter_fails(self):
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "unclosed"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: x\nbody\n",
                                            encoding="utf-8")
            with self.assertRaises(SystemExit):
                eval_mod.parse_frontmatter_name(skill)


class TestCodexHome(unittest.TestCase):
    def test_copies_skill_and_allowlist_only(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            skill = make_skill_dir(td, "beta")
            real_home = td / "real-home"
            real_home.mkdir()
            (real_home / "auth.json").write_text("{}", encoding="utf-8")
            (real_home / "config.toml").write_text("x=1\n", encoding="utf-8")
            (real_home / "history.jsonl").write_text("secret\n",
                                                     encoding="utf-8")
            (real_home / "skills").mkdir()
            (real_home / "skills" / "other-skill").mkdir()

            home, copied = eval_mod.build_codex_home(skill, "beta",
                                                     real_home)
            try:
                self.assertTrue((home / "skills" / "beta" / "SKILL.md")
                                .exists())
                self.assertTrue((home / "skills" / "beta" / "helper.txt")
                                .exists())
                self.assertTrue((home / "auth.json").exists())
                self.assertTrue((home / "config.toml").exists())
                self.assertFalse((home / "history.jsonl").exists())
                self.assertFalse((home / "skills" / "other-skill").exists())
                self.assertIn("auth.json", copied)
                self.assertIn("config.toml", copied)
            finally:
                eval_mod.cleanup_codex_home(home)
            self.assertFalse(home.exists())

    def test_missing_allowlist_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            skill = make_skill_dir(td, "gamma")
            real_home = td / "empty-home"
            real_home.mkdir()
            home, copied = eval_mod.build_codex_home(skill, "gamma",
                                                     real_home)
            try:
                self.assertTrue((home / "skills" / "gamma").exists())
                self.assertNotIn("auth.json", copied)
                self.assertNotIn("config.toml", copied)
            finally:
                eval_mod.cleanup_codex_home(home)


class TestMountedPrompt(unittest.TestCase):
    def test_dollar_trigger(self):
        prompt, mode = eval_mod.build_mounted_task_prompt(
            "my-skill", "do the task", dollar_supported=True)
        self.assertEqual(mode, "dollar")
        self.assertTrue(prompt.startswith("$my-skill\n"))
        self.assertIn("do the task", prompt)

    def test_fallback_trigger(self):
        prompt, mode = eval_mod.build_mounted_task_prompt(
            "my-skill", "do the task", dollar_supported=False)
        self.assertEqual(mode, "fallback-named")
        self.assertNotIn("$my-skill", prompt)
        self.assertIn("`my-skill`", prompt)

    def test_detect_dollar_support_override(self):
        import os
        os.environ["EVAL_DOLLAR_SUPPORT"] = "0"
        try:
            self.assertFalse(eval_mod.detect_dollar_support("codex"))
        finally:
            del os.environ["EVAL_DOLLAR_SUPPORT"]
        os.environ["EVAL_DOLLAR_SUPPORT"] = "1"
        try:
            self.assertTrue(eval_mod.detect_dollar_support("claude"))
        finally:
            del os.environ["EVAL_DOLLAR_SUPPORT"]


class TestJudgePrompt(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "output").mkdir()
        (self.tmp / "input").mkdir()
        (self.tmp / "output" / "edited.md").write_text(
            "# Edited\n\nTight content.\n", encoding="utf-8")
        (self.tmp / "input" / "draft.md").write_text(
            "Rambling draft...\n", encoding="utf-8")

    def test_no_inputs_returns_rubric_only(self):
        prompt, injections = eval_mod.build_judge_prompt(
            "RUBRIC", self.tmp, inputs=None)
        self.assertIn("<rubric>\nRUBRIC\n</rubric>", prompt)
        self.assertNotIn("<file", prompt)
        self.assertEqual(injections, [])

    def test_inputs_injected_with_labels(self):
        prompt, injections = eval_mod.build_judge_prompt(
            "RUBRIC", self.tmp,
            inputs=[{"path": "output/edited.md", "label": "edited"},
                    {"path": "input/draft.md", "label": "original"}])
        self.assertIn('<file label="edited" path="output/edited.md">',
                      prompt)
        self.assertIn("Tight content.", prompt)
        self.assertIn('<file label="original" path="input/draft.md">',
                      prompt)
        self.assertIn("Rambling draft...", prompt)
        self.assertEqual(len(injections), 2)
        self.assertFalse(injections[0]["truncated"])

    def test_missing_input_aborts(self):
        with self.assertRaises(SystemExit):
            eval_mod.build_judge_prompt(
                "RUBRIC", self.tmp,
                inputs=[{"path": "output/missing.md", "label": "x"}])

    def test_truncation_marks_content(self):
        big = "x" * 100
        (self.tmp / "output" / "big.md").write_text(big, encoding="utf-8")
        prompt, injections = eval_mod.build_judge_prompt(
            "RUBRIC", self.tmp,
            inputs=[{"path": "output/big.md", "label": "big"}],
            max_input_bytes=10)
        self.assertIn("[truncated at 10 bytes; full size 100]", prompt)
        self.assertTrue(injections[0]["truncated"])
        self.assertEqual(injections[0]["bytes"], 100)

    def test_label_defaults_to_path(self):
        prompt, injections = eval_mod.build_judge_prompt(
            "RUBRIC", self.tmp,
            inputs=[{"path": "output/edited.md"}])
        self.assertIn('label="output/edited.md"', prompt)


class TestResolveJudgeCli(unittest.TestCase):
    def test_command_line_wins(self):
        self.assertEqual(
            eval_mod.resolve_judge_cli("claude", "codex", "mock"),
            "claude")

    def test_fixture_beats_agent_cli(self):
        self.assertEqual(
            eval_mod.resolve_judge_cli(None, "codex", "mock"), "codex")

    def test_defaults_to_agent_cli(self):
        self.assertEqual(
            eval_mod.resolve_judge_cli(None, None, "mock"), "mock")


class TestYamlLite(unittest.TestCase):
    def test_scalars(self):
        spec = parse_simple_yaml("name: foo\nruns: 3\nratio: 1.5\nflag: true\n")
        self.assertEqual(spec["name"], "foo")
        self.assertEqual(spec["runs"], 3)
        self.assertEqual(spec["ratio"], 1.5)
        self.assertIs(spec["flag"], True)

    def test_block_scalar_folds_lines(self):
        spec = parse_simple_yaml("description: >\n  line one\n  line two\nnext: x\n")
        self.assertEqual(spec["description"], "line one line two")
        self.assertEqual(spec["next"], "x")

    def test_list_of_mappings(self):
        text = (
            "assert:\n"
            "  - type: file_exists\n"
            "    path: out/a.md\n"
            "  - type: file_min_lines\n"
            "    path: out/a.md\n"
            "    count: 10\n"
        )
        spec = parse_simple_yaml(text)
        self.assertEqual(len(spec["assert"]), 2)
        self.assertEqual(spec["assert"][0], {"type": "file_exists",
                                             "path": "out/a.md"})
        self.assertEqual(spec["assert"][1]["count"], 10)

    def test_nested_mapping_and_scalar_list(self):
        text = (
            "judge:\n"
            "  rubric: rubric.md\n"
            "  max_score: 5\n"
            "patterns:\n"
            "  - foo\n"
            "  - bar\n"
        )
        spec = parse_simple_yaml(text)
        self.assertEqual(spec["judge"]["rubric"], "rubric.md")
        self.assertEqual(spec["judge"]["max_score"], 5)
        self.assertEqual(spec["patterns"], ["foo", "bar"])

    def test_real_fixture_parses(self):
        fixture = (Path(__file__).resolve().parent.parent
                   / "fixtures" / "edit-article-clarity" / "fixture.yaml")
        spec = parse_simple_yaml(fixture.read_text(encoding="utf-8"))
        self.assertEqual(spec["name"], "edit-article-clarity")
        self.assertEqual(spec["runs"], 3)
        self.assertEqual(len(spec["assert"]), 3)
        self.assertEqual(spec["assert"][1]["patterns"],
                         ["(?i)in conclusion", "(?i)as mentioned earlier"])
        self.assertEqual(spec["judge"]["max_score"], 5)
        self.assertEqual(spec["judge"]["inputs"],
                         [{"path": "output/edited.md", "label": "edited"},
                          {"path": "input/draft.md", "label": "original"}])


class TestAssertions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "out").mkdir()
        (self.tmp / "out" / "a.md").write_text(
            "line one\nline two\nline three\n", encoding="utf-8")

    def check(self, asserts):
        return run_assertions(asserts, self.tmp)

    def test_file_exists(self):
        r = self.check([{"type": "file_exists", "path": "out/a.md"}])
        self.assertTrue(r[0]["ok"])
        r = self.check([{"type": "file_exists", "path": "out/missing.md"}])
        self.assertFalse(r[0]["ok"])

    def test_file_not_exists(self):
        r = self.check([{"type": "file_not_exists", "path": "out/nope.md"}])
        self.assertTrue(r[0]["ok"])
        r = self.check([{"type": "file_not_exists", "path": "out/a.md"}])
        self.assertFalse(r[0]["ok"])

    def test_file_contains(self):
        r = self.check([{"type": "file_contains", "path": "out/a.md",
                         "patterns": ["line one", "line (two|three)"]}])
        self.assertTrue(r[0]["ok"])
        r = self.check([{"type": "file_contains", "path": "out/a.md",
                         "patterns": ["absent phrase"]}])
        self.assertFalse(r[0]["ok"])

    def test_file_not_contains(self):
        r = self.check([{"type": "file_not_contains", "path": "out/a.md",
                         "patterns": ["(?i)LINE ONE"]}])
        self.assertFalse(r[0]["ok"])  # case-insensitive regex hits
        r = self.check([{"type": "file_not_contains", "path": "out/a.md",
                         "patterns": ["conclusion"]}])
        self.assertTrue(r[0]["ok"])

    def test_file_contains_missing_target(self):
        r = self.check([{"type": "file_contains", "path": "gone.md",
                         "patterns": ["x"]}])
        self.assertFalse(r[0]["ok"])
        self.assertIn("missing", r[0]["detail"])

    def test_file_min_lines(self):
        r = self.check([{"type": "file_min_lines", "path": "out/a.md",
                         "count": 3}])
        self.assertTrue(r[0]["ok"])
        r = self.check([{"type": "file_min_lines", "path": "out/a.md",
                         "count": 4}])
        self.assertFalse(r[0]["ok"])

    def test_command(self):
        r = self.check([{"type": "command",
                         "run": "exit 0" if __import__("os").name == "nt"
                         else "true"}])
        self.assertTrue(r[0]["ok"])
        r = self.check([{"type": "command",
                         "run": "exit 3" if __import__("os").name == "nt"
                         else "false"}])
        self.assertFalse(r[0]["ok"])
        self.assertIn("exit", r[0]["detail"])

    def test_unknown_type_fails_loudly(self):
        r = self.check([{"type": "telepathy", "path": "out/a.md"}])
        self.assertFalse(r[0]["ok"])
        self.assertIn("unknown assert type", r[0]["detail"])


class TestExtractJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"total": 4}')["total"], 4)

    def test_object_inside_prose(self):
        out = extract_json('Here is my grade: {"total": 3, "notes": "ok"} done')
        self.assertEqual(out["total"], 3)

    def test_no_object(self):
        self.assertIsNone(extract_json("no json here"))

    def test_malformed(self):
        self.assertIsNone(extract_json("{not json}"))


class TestMockAgent(unittest.TestCase):
    def test_task_prompt_materializes_output(self):
        tmp = Path(tempfile.mkdtemp())
        trace = eval_mod.invoke_agent("mock", "<task>do it</task>", tmp)
        self.assertEqual(trace["exit"], 0)
        self.assertTrue((tmp / "output" / "edited.md").exists())

    def test_judge_prompt_returns_rubric_json(self):
        tmp = Path(tempfile.mkdtemp())
        trace = eval_mod.invoke_agent("mock", "<rubric>x</rubric>", tmp)
        parsed = extract_json(trace["stdout"])
        self.assertEqual(parsed["total"], 4)

    def test_failure_behavior(self):
        import os
        os.environ["EVAL_MOCK_BEHAVIOR"] = "fail"
        try:
            tmp = Path(tempfile.mkdtemp())
            trace = eval_mod.invoke_agent("mock", "<task>do it</task>", tmp)
            self.assertEqual(trace["exit"], 1)
        finally:
            del os.environ["EVAL_MOCK_BEHAVIOR"]


if __name__ == "__main__":
    unittest.main()
