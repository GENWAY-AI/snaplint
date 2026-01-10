from __future__ import annotations

import pytest

from snaplint.errors import ParseWarning
from snaplint.models import IssueLine
from snaplint.parse import parse_lines


@pytest.mark.parametrize(
    "line, expected",
    [
        # Flake8
        (
            "src/snaplint/cli.py:18:1: F401 'sys' imported but unused",
            IssueLine(
                original="src/snaplint/cli.py:18:1: F401 'sys' imported but unused",
                tool="flake",
                path="src/snaplint/cli.py",
                line=18,
                column=1,
                code="F401",
                message="'sys' imported but unused",
            ),
        ),
        # Mypy (with column and code)
        (
            "src/snaplint/cli.py:22:8: error: Function is missing a return type annotation  [no-untyped-def]",
            IssueLine(
                original="src/snaplint/cli.py:22:8: error: Function is missing a return type annotation  [no-untyped-def]",
                tool="mypy",
                path="src/snaplint/cli.py",
                line=22,
                column=8,
                code="no-untyped-def",
                message="Function is missing a return type annotation",
            ),
        ),
        # Mypy (no column, no code -> level as code)
        (
            "src/snaplint/snapshot.py:10: note: 'IO' is imported from 'typing'",
            IssueLine(
                original="src/snaplint/snapshot.py:10: note: 'IO' is imported from 'typing'",
                tool="mypy",
                path="src/snaplint/snapshot.py",
                line=10,
                column=0,
                code="note",
                message="'IO' is imported from 'typing'",
            ),
        ),
        # Pylint (Style 1)
        (
            "src/snaplint/models.py:33:0: C0114: Missing module docstring (missing-module-docstring)",
            IssueLine(
                original="src/snaplint/models.py:33:0: C0114: Missing module docstring (missing-module-docstring)",
                tool="pylint",
                path="src/snaplint/models.py",
                line=33,
                column=0,
                code="C0114",
                message="Missing module docstring",
            ),
        ),
        # Pylint (Style 2)
        (
            "src/snaplint/cli.py:1: [C0114(missing-module-docstring)] Missing module docstring",
            IssueLine(
                original="src/snaplint/cli.py:1: [C0114(missing-module-docstring)] Missing module docstring",
                tool="pylint",
                path="src/snaplint/cli.py",
                line=1,
                column=0,
                code="C0114",
                message="Missing module docstring",
            ),
        ),
        # Generic Fallback
        (
            "foo/bar.js:10:5: Some custom linter message",
            IssueLine(
                original="foo/bar.js:10:5: Some custom linter message",
                tool="unknown",
                path="foo/bar.js",
                line=10,
                column=5,
                code=None,
                message="Some custom linter message",
            ),
        ),
        # Flake8 with leading ./ and line 0
        (
            "./src/snaplint/cli.py:0:1: F401 'sys' imported but unused",
            IssueLine(
                original="./src/snaplint/cli.py:0:1: F401 'sys' imported but unused",
                tool="flake",
                path="src/snaplint/cli.py",
                line=0,
                column=1,
                code="F401",
                message="'sys' imported but unused",
            ),
        ),
        # Ruff with [*] marker (auto-fixable)
        (
            "src/main.py:3:8: F401 [*] `sys` imported but unused",
            IssueLine(
                original="src/main.py:3:8: F401 [*] `sys` imported but unused",
                tool="ruff",
                path="src/main.py",
                line=3,
                column=8,
                code="F401",
                message="[*] `sys` imported but unused",
            ),
        ),
        # Ruff with [*] marker (different code)
        (
            "src/cli.py:31:1: W293 [*] Blank line contains whitespace",
            IssueLine(
                original="src/cli.py:31:1: W293 [*] Blank line contains whitespace",
                tool="ruff",
                path="src/cli.py",
                line=31,
                column=1,
                code="W293",
                message="[*] Blank line contains whitespace",
            ),
        ),
        # Ruff without [*] marker (detected as flake8 at line level, but stream detection would catch it)
        (
            "src/utils.py:10:1: E501 Line too long (90 > 88)",
            IssueLine(
                original="src/utils.py:10:1: E501 Line too long (90 > 88)",
                tool="flake",  # Without [*], individual line is parsed as flake8
                path="src/utils.py",
                line=10,
                column=1,
                code="E501",
                message="Line too long (90 > 88)",
            ),
        ),
        # Edge case: [*] appears in message content, NOT as Ruff marker (should be flake8)
        (
            "src/docs.py:5:1: E501 Line contains [*] pattern in docstring",
            IssueLine(
                original="src/docs.py:5:1: E501 Line contains [*] pattern in docstring",
                tool="flake",  # [*] is not at start of message, so not Ruff
                path="src/docs.py",
                line=5,
                column=1,
                code="E501",
                message="Line contains [*] pattern in docstring",
            ),
        ),
    ],
)
def test_parse_line_success(line: str, expected: IssueLine):
    results = list(parse_lines([line]))
    assert len(results) == 1
    assert results[0] == expected


@pytest.mark.parametrize(
    "line, should_warn",
    [
        ("not a linter line", True),
        ("", False),
        ("  ", False),
        ("foo.py:invalid:1: E123 error", True),
    ],
)
def test_parse_line_unparsable(line: str, should_warn: bool):
    results = list(parse_lines([line]))
    if should_warn:
        assert len(results) == 1
        assert isinstance(results[0], ParseWarning)
        assert results[0].line in line
    else:
        assert len(results) == 0
