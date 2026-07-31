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
