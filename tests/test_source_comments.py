"""Comment rules: code has a plain comment (or docstring) above every function; test functions have Intent and Why."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_FOLDERS = ("custom_components", "tools", ".github/scripts", "tests", "tests_ha")
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


# Intent: list the Python files of some folders, skipping caches and empty files.
# Why: both rules scan by folder, so the same walk serves code and tests.
def _python_files(folders: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files += [p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts and p.stat().st_size > 0]
    return sorted(files)


# Intent: return the comment lines directly above a function, decorators included.
# Why: the comment sits above the first decorator, not between decorator and def.
def _comment_block_above(lines: list[str], node: FunctionNode) -> list[str]:
    first = min([node.lineno, *[decorator.lineno for decorator in node.decorator_list]])
    block: list[str] = []
    index = first - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        block.insert(0, lines[index].strip())
        index -= 1
    return block


# Intent: tell a bare interface stub (a body that is only "...") from real code.
# Why: a Protocol method has no behaviour to explain; the docstring of its class describes the interface.
def _is_stub(node: FunctionNode) -> bool:
    body = node.body
    return len(body) == 1 and isinstance(body[0], ast.Expr) and getattr(body[0].value, "value", None) is Ellipsis


# Intent: collect the functions that fail a rule as "path:line name" strings.
# Why: one list lets a failing test name all offenders at once instead of the first only.
def _offenders(
    applies: Callable[[FunctionNode], bool], is_valid: Callable[[FunctionNode, list[str]], bool]
) -> list[str]:
    missing: list[str] = []
    for path in _python_files(CODE_FOLDERS):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and applies(node):
                if not is_valid(node, _comment_block_above(lines, node)):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
    return missing


# Intent: every function that is not a test has a plain comment or a docstring saying what it is for.
# Why: the rule is easy to forget and cheap to check; a missing comment is a review finding, not a style nit.
def test_every_non_test_function_has_a_comment() -> None:
    missing = _offenders(
        lambda node: not node.name.startswith("test_"),
        lambda node, block: bool(block) or ast.get_docstring(node) is not None or _is_stub(node),
    )

    assert not missing, "functions without a comment or docstring:\n" + "\n".join(missing)


# Intent: every test function has an # Intent: and a # Why: comment directly above it.
# Why: a test should say what it protects and why that matters, so a later change can judge whether it still applies.
def test_every_test_function_has_intent_and_why() -> None:
    # Intent: accept a comment block only when it carries both labels.
    # Why: a test that says what it protects but not why leaves the next reader guessing.
    def valid(node: FunctionNode, block: list[str]) -> bool:
        return any(line.startswith("# Intent:") for line in block) and any(line.startswith("# Why:") for line in block)

    missing = _offenders(lambda node: node.name.startswith("test_"), valid)

    assert not missing, "test functions without # Intent: and # Why: comments:\n" + "\n".join(missing)
