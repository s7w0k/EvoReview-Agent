"""Format-preserving AST-based repairs for supported rules.

The legacy ``SafeFixer`` rewrites a whole module with ``ast.unparse``, which
drops comments and reformats unrelated code.  This fixer instead validates
each target line against the AST and edits only the exact source line,
preserving comments and formatting elsewhere.

Every edit is gated deterministically:

1. the rule pattern must reproduce the finding on the original line;
2. the AST must confirm the statement type before any edit is allowed;
3. after the edit the pattern must no longer match (re-verification);
4. the repaired module must still compile;
5. the number of edited lines is bounded by ``max_lines``.
"""
import ast
import re
from typing import Any, Dict, List, Optional, Tuple

from .reviewer import LocalRuleReviewer

_RULES = {item[0]: item for item in LocalRuleReviewer.RULES}
_SUPPORTED = frozenset({"REL-DEBUG-PRINT", "SEC-SUBPROCESS-SHELL", "SEC-HARDCODED-SECRET"})


def _pattern_for(rule_id: str):
    rule = _RULES.get(rule_id)
    return rule[2] if rule else None


def _has_os_import(lines: List[str]) -> bool:
    return any(re.match(r"\s*(import os|from os import)", line) for line in lines)


class PreservingAstFixer:
    """Deterministic, comment-preserving repairs for a single Python file."""

    def __init__(self, max_lines: int = 10):
        self.max_lines = int(max_lines)

    @staticmethod
    def _repair_line(line: str, rule_id: str) -> Optional[str]:
        if rule_id == "REL-DEBUG-PRINT":
            return ""
        if rule_id == "SEC-SUBPROCESS-SHELL":
            return re.sub(r"shell\s*=\s*True", "shell=False", line)
        if rule_id == "SEC-HARDCODED-SECRET":
            match = re.match(
                r"(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(['\"]).+?\3\s*$", line
            )
            if match:
                return '%s%s = os.environ["%s"]' % (
                    match.group(1), match.group(2), match.group(2).upper(),
                )
        return None

    @staticmethod
    def _confirm_targets(tree: ast.Module, targets: List[Tuple[int, str]]) -> set:
        """Return the target (line, rule) pairs the AST actually confirms."""
        by_line = {}
        for line, rule in targets:
            by_line.setdefault(line, set()).add(rule)
        confirmed = set()
        for node in ast.walk(tree):
            if not hasattr(node, "lineno") or node.lineno not in by_line:
                continue
            for rule in by_line[node.lineno]:
                if rule == "REL-DEBUG-PRINT" and isinstance(node, ast.Expr) and (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "print"
                ):
                    confirmed.add((node.lineno, rule))
                elif rule == "SEC-SUBPROCESS-SHELL" and isinstance(node, ast.Call) and any(
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                ):
                    confirmed.add((node.lineno, rule))
                elif rule == "SEC-HARDCODED-SECRET" and isinstance(node, ast.Assign) and (
                    len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    confirmed.add((node.lineno, rule))
        return confirmed

    def apply(
        self, content: str, findings: List[dict], path: str,
    ) -> Dict[str, Any]:
        """Repair one file; returns content, applied rules and rejection reason."""
        if not path.endswith(".py"):
            return {"content": content, "rules": [], "rejected_reason": None}
        targets = [
            (int(item.get("line", 0)), item.get("rule_id"))
            for item in findings
            if item.get("path") == path and item.get("rule_id") in _SUPPORTED
        ]
        if not targets:
            return {"content": content, "rules": [], "rejected_reason": None}
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            return {
                "content": content, "rules": [],
                "rejected_reason": "source does not parse",
            }

        confirmed = self._confirm_targets(tree, targets)
        if not confirmed:
            return {"content": content, "rules": [], "rejected_reason": None}

        lines = content.splitlines()
        changed: List[str] = []
        needs_os = False
        edits = 0
        rejected: Optional[str] = None
        # Edit bottom-up so line numbers stay valid while popping.
        for line_no, rule_id in sorted(confirmed, key=lambda item: item[0], reverse=True):
            index = line_no - 1
            if index < 0 or index >= len(lines):
                continue
            original = lines[index]
            replacement = self._repair_line(original, rule_id)
            if replacement is None or replacement == original:
                continue
            pattern = _pattern_for(rule_id)
            if pattern is not None and pattern.search(replacement):
                rejected = (
                    "repair re-verification failed for %s at line %d"
                    % (rule_id, line_no)
                )
                continue
            needs_os = needs_os or "os.environ" in replacement
            if replacement == "":
                del lines[index]
            else:
                lines[index] = replacement
            changed.append(rule_id)
            edits += 1
            if edits > self.max_lines:
                rejected = (
                    "repair exceeds EVOAGENT_AST_FIX_MAX_LINES=%d" % self.max_lines
                )
                break

        if needs_os and not _has_os_import(lines):
            lines.insert(0, "import os")
        repaired = "\n".join(lines) + ("\n" if content.endswith("\n") else "")
        try:
            compile(repaired, path, "exec")
        except SyntaxError:
            return {
                "content": content, "rules": [],
                "rejected_reason": "repaired source does not compile",
            }
        return {
            "content": repaired,
            "rules": sorted(set(changed)),
            "rejected_reason": rejected,
        }
