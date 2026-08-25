import tempfile
import unittest
from pathlib import Path

from evoagent.diff_parser import ParsedDiff, parse_unified_diff
from evoagent.models import ChangedLine
from evoagent.tools.catalog import (
    build_runtime_tools,
    build_tool_metadata,
    os_path_join,
)


class _MemoryStub:
    def __init__(self, hits):
        self._hits = hits
        self.calls = []

    def recall(self, tenant_id, repository, query, limit):
        self.calls.append((tenant_id, repository, query, limit))
        return self._hits


class _DefinitionsMixin:
    def build(self, **kwargs):
        self.defs = {d.tool.name: d.tool for d in build_runtime_tools(**kwargs)}
        return self.defs

    def handler(self, name):
        return self.defs[name].handler


class ToolCatalogTest(unittest.TestCase, _DefinitionsMixin):
    def test_build_tool_metadata_declares_all_tools(self):
        metadata = build_tool_metadata()
        names = sorted(metadata)
        self.assertEqual(
            names,
            ["changed_line", "find_callers", "find_tests", "list_changed_files",
             "read_file", "recall_memory", "run_static_analysis", "run_tests",
             "search_code", "search_diff"],
        )
        self.assertEqual(metadata["run_tests"].side_effect, True)
        self.assertEqual(metadata["run_tests"].requires_sandbox, True)
        self.assertEqual(metadata["search_diff"].risk_level, "low")
        self.assertEqual(metadata["run_tests"].risk_level, "high")

    def test_search_diff_matches(self):
        self.build(diff="line has Secret\nplain line\nanother secret here")
        hits = self.handler("search_diff")(query="secret")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["diff_line"], 1)
        self.assertEqual(hits[1]["diff_line"], 3)

    def test_search_diff_requires_query(self):
        self.build(diff="x")
        with self.assertRaises(ValueError):
            self.handler("search_diff")(query="   ")

    def test_changed_line_found_and_missing(self):
        parsed = ParsedDiff(
            files=["a.py"],
            added_lines=[ChangedLine("a.py", 3, "new_code")],
        )
        self.build(parsed=parsed)
        found = self.handler("changed_line")(path="a.py", line=3)
        self.assertTrue(found["found"])
        self.assertEqual(found["content"], "new_code")
        missing = self.handler("changed_line")(path="a.py", line=99)
        self.assertFalse(missing["found"])

    def test_list_changed_files(self):
        self.build(parsed=ParsedDiff(files=["a.py", "b.py"], added_lines=[]))
        self.assertEqual(self.handler("list_changed_files")(), ["a.py", "b.py"])

    def test_recall_memory_no_context(self):
        self.build(parsed=ParsedDiff(files=[], added_lines=[]))
        self.assertEqual(self.handler("recall_memory")(query="q"), [])

    def test_recall_memory_with_repository(self):
        memory = _MemoryStub([{"text": "past review"}])
        self.build(
            parsed=ParsedDiff(files=[], added_lines=[]),
            memory_manager=memory, repository="fr/action"
        )
        hits = self.handler("recall_memory")(query="q", limit=3)
        self.assertEqual(hits, [{"text": "past review"}])
        self.assertEqual(memory.calls[0][:3], ("default", "fr/action", "q"))
        self.assertEqual(memory.calls[0][3], 3)

    def test_read_file_requires_workspace(self):
        self.build()
        result = self.handler("read_file")(path="a.py")
        self.assertFalse(result["available"])

    def test_read_file_reads_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "a.py"
            p.write_text("one\ntwo\nthree\n", encoding="utf-8")
            self.build(workspace=str(root))
            result = self.handler("read_file")(path=str(p), start=1, end=3)
            self.assertEqual(len(result["lines"]), 3)
            self.assertEqual(result["lines"][1]["content"], "two")

    def test_read_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(workspace=tmp)
            result = self.handler("read_file")(path=str(Path(tmp) / "nope.py"))
            self.assertFalse(result["available"])

    def test_search_code_requires_workspace(self):
        self.build()
        self.assertFalse(self.handler("search_code")(query="q")["available"])

    def test_search_code_empty_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.build(workspace=tmp)
            self.assertEqual(self.handler("search_code")(query=" "), {"matches": []})

    def test_search_code_with_path_and_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("HIDDEN_TOKEN here\nno", encoding="utf-8")
            (root / "other.txt").write_text("HIDDEN_TOKEN here\n", encoding="utf-8")
            self.build(workspace=str(root))
            result = self.handler("search_code")(query="hidden_token", path=".py")
            self.assertEqual(len(result["matches"]), 1)
            self.assertTrue(result["matches"][0]["file"].endswith("app.py"))
            self.assertEqual(result["matches"][0]["line"], 1)

    def test_find_callers_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mod.py").write_text("use_sym(foo)\n", encoding="utf-8")
            (root / "other.py").write_text("use_sym(bar)\n", encoding="utf-8")
            self.build(workspace=str(root))
            callers = self.handler("find_callers")(symbol="use_sym", scope="mod")
            self.assertEqual(len(callers["callers"]), 1)
            self.assertTrue(callers["callers"][0]["file"].endswith("mod.py"))

    def test_find_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_x.py").write_text("import target_func\n", encoding="utf-8")
            (root / "not_a_test.txt").write_text("target_func\n", encoding="utf-8")
            self.build(workspace=str(root))
            tests = self.handler("find_tests")(target="target_func")
            self.assertEqual(len(tests["tests"]), 1)
            self.assertTrue(tests["tests"][0]["file"].endswith("test_x.py"))

    def test_run_static_analysis(self):
        self.build()
        self.assertFalse(self.handler("run_static_analysis")(path="x")["available"])
        with tempfile.TemporaryDirectory() as tmp:
            self.build(workspace=tmp)
            result = self.handler("run_static_analysis")(path="x")
            self.assertTrue(result["available"])

    def test_run_tests(self):
        self.build()
        self.assertFalse(self.handler("run_tests")()["available"])
        with tempfile.TemporaryDirectory() as tmp:
            self.build(workspace=tmp)
            result = self.handler("run_tests")(command="pytest")
            self.assertTrue(result["available"])
            self.assertEqual(result["command"], "pytest")

    def test_walk_prunes_ignored_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "app.py").write_text("x\n", encoding="utf-8")
            (root / ".git" / "ignored.py").write_text("HIDDEN_TOKEN\n", encoding="utf-8")
            self.build(workspace=str(root))
            matches = self.handler("search_code")(query="hidden_token")
            self.assertEqual(matches, {"matches": []})

    def test_os_path_join(self):
        import os
        self.assertEqual(os_path_join("a", "b"), os.path.join("a", "b"))

    def test_parsed_added_lines_forwarded(self):
        diff = "--- a/b.py\n+++ b/b.py\n@@ -1 +1,1 @@\n+added_tail\n"
        parsed = parse_unified_diff(diff)
        self.build(parsed=parsed)
        self.assertEqual(parsed.files, ["b.py"])
        self.assertEqual(parsed.added_lines[0].content, "added_tail")


if __name__ == "__main__":
    unittest.main()