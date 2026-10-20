"""Every function and test needs an ``# Intent:`` and a ``# Why:`` comment directly above it."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("custom_components", "tests", "tools", ".github/scripts")


# Intent: list the Python files the rule applies to.
# Why: generated or vendored files do not exist yet, so everything under these folders is ours.
def _python_files() -> list[Path]:
    files: list[Path] = []
    for folder in SCANNED:
        files += [p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts and p.stat().st_size > 0]
    return sorted(files)


# Intent: return the comment lines directly above a function, decorators included.
# Why: the intent comment sits above the first decorator, not between decorator and def.
def _comment_block_above(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    first = min([node.lineno, *[decorator.lineno for decorator in node.decorator_list]])
    block: list[str] = []
    index = first - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        block.insert(0, lines[index].strip())
        index -= 1
    return block


# Intent: scan all functions, methods, nested functions and tests for the two required comments.
# Why: the rule is easy to forget and cheap to check; a missing comment is a review finding, not a style nit.
def test_every_function_has_intent_and_why_comments() -> None:
    missing: list[str] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                block = _comment_block_above(lines, node)
                has_intent = any(line.startswith("# Intent:") for line in block)
                has_why = any(line.startswith("# Why:") for line in block)
                if not (has_intent and has_why):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")

    assert not missing, "functions without # Intent: and # Why: comments:\n" + "\n".join(missing)
