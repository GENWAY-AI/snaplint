from __future__ import annotations

import argparse
import re
import sys
from importlib.metadata import version as get_version
from pathlib import Path

from snaplint.diff import diff_snapshot_files
from snaplint.errors import SnaplintError, SnapshotReadError, UsageError
from snaplint.models import RenderOptions
from snaplint.render import render_diff
from snaplint.snapshot import build_snapshot_file, read_snapshot, write_snapshot

# Ruff summary line patterns (appear at end of output)
# Examples:
#   "Found 8 errors."
#   "[*] 6 fixable with the `--fix` option."
RUFF_SUMMARY_RE = re.compile(
    r"^(Found \d+ errors?\.|\[\*\] \d+ fixable with the .--fix. option\.)"
)


def _looks_like_lint_code(text: str) -> bool:
    """Check if text looks like a linter error code (e.g., F401, E501, W293).

    A lint code typically starts with a letter and contains at least one digit.
    """
    if not text or len(text) < 2:
        return False
    return text[0].isalpha() and any(c.isdigit() for c in text)


def _has_flake8_style_error(line: str) -> bool:
    """Check if a line matches flake8-style format: path:line:col: CODE message."""
    parts = line.split(":")
    if len(parts) < 4:
        return False

    try:
        int(parts[1])  # line number
        int(parts[2])  # column number
    except (ValueError, IndexError):
        return False

    code_part = parts[3].strip()
    if not code_part:
        return False

    first_word = code_part.split()[0] if code_part.split() else ""
    return _looks_like_lint_code(first_word)


def _has_ruff_autofix_marker(line: str) -> bool:
    """Check if a line has Ruff's [*] auto-fix marker in the correct position.

    Ruff format: path:line:col: CODE [*] message
    The [*] marker appears immediately after the error code.
    """
    parts = line.split(":")
    if len(parts) < 4:
        return False

    try:
        int(parts[1])  # line number
        int(parts[2])  # column number
    except (ValueError, IndexError):
        return False

    # Get the message part after "path:line:col: "
    code_and_msg = parts[3].strip()
    if not code_and_msg:
        return False

    # Split into code and message, check if message starts with [*]
    words = code_and_msg.split(maxsplit=1)
    if len(words) < 2:
        return False

    code, msg = words[0], words[1]
    if not _looks_like_lint_code(code):
        return False

    return msg.lstrip().startswith("[*]")


def _detect_linter_from_lines(lines: list[str]) -> str:
    """Detect the linter type from the output lines.

    Returns a linter name like 'ruff', 'flake8', 'mypy', 'pylint', or 'generic'.

    Ruff is detected by its distinctive summary lines at the end:
    - "Found X errors."
    - "[*] X fixable with the --fix option."
    """
    has_flake8_style_errors = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check for Ruff summary lines (most reliable detection)
        if RUFF_SUMMARY_RE.match(line):
            return "ruff"

        # Check for [*] marker in error lines (Ruff auto-fix indicator)
        # The marker appears right after the error code: "CODE [*] message"
        if _has_ruff_autofix_marker(line):
            return "ruff"

        # Check for mypy patterns
        if ": error:" in line or ": warning:" in line or ": note:" in line:
            if "[" in line and "]" in line:
                return "mypy"

        # Check for pylint patterns
        if "************* Module" in line:
            return "pylint"

        # Check for flake8/ruff style errors (path:line:col: CODE message)
        if _has_flake8_style_error(line):
            has_flake8_style_errors = True

    # If we found flake8-style errors but no Ruff indicators, it's flake8
    if has_flake8_style_errors:
        return "flake8"

    return "generic"


def _get_default_snapshot_path(linter_type: str) -> Path:
    """Get the default snapshot path for a given linter type."""
    snaplint_dir = Path(".snaplint")
    return snaplint_dir / f"snapshot.{linter_type}.json.gz"


def main() -> int:
    try:
        return _main()
    except SnaplintError as e:
        print(f"error: {e}", file=sys.stderr)
        if isinstance(e, UsageError):
            return 2
        if isinstance(e, SnapshotReadError):
            return 2
        return 3
    except Exception as e:
        print(f"unexpected internal error: {e}", file=sys.stderr)
        return 3


def _main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot linter findings.")
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {get_version('snaplint')}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # diff command
    parser_diff = subparsers.add_parser(
        "diff", help="Compare linter output to a snapshot."
    )
    parser_diff.add_argument(
        "snapshot_path",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Path to the snapshot file. If omitted, auto-detects linter "
            "and uses .snaplint/snapshot.<linter>.json.gz"
        ),
    )
    parser_diff.add_argument(
        "--verbose",
        action="store_true",
        help="Show removed errors in addition to added errors",
    )

    # take-snapshot command
    parser_take_snapshot = subparsers.add_parser(
        "take-snapshot", help="Write linter output to a snapshot file."
    )
    parser_take_snapshot.add_argument(
        "snapshot_path",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Path to the snapshot file. If omitted, auto-detects linter "
            "and uses .snaplint/snapshot.<linter>.json.gz"
        ),
    )

    args = parser.parse_args()

    if args.command == "take-snapshot":
        return _run_take_snapshot(args)
    if args.command == "diff":
        return _run_diff(args)

    # This should be unreachable due to the initial check
    return 3


def _run_take_snapshot(args: argparse.Namespace) -> int:
    if sys.stdin.isatty():
        raise UsageError("stdin is empty. Pipe linter output to snaplint.")

    # Read all input lines
    input_lines = list(sys.stdin)

    # Determine snapshot path
    if args.snapshot_path is None:
        # Auto-detect linter type and create default path
        linter_type = _detect_linter_from_lines(input_lines)
        snapshot_path = _get_default_snapshot_path(linter_type)

        # Create .snaplint directory if it doesn't exist
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Auto-detected linter: {linter_type}", file=sys.stderr)
        print(f"Using snapshot file: {snapshot_path}", file=sys.stderr)
    else:
        snapshot_path = args.snapshot_path

    # Build snapshot from stdin
    snapshot_file = build_snapshot_file(input_lines)

    try:
        with snapshot_path.open("wb") as f:
            write_snapshot(snapshot_file, f)
    except OSError as e:
        raise SnapshotReadError(
            f"Could not write to snapshot file {snapshot_path}: {e}"
        ) from e

    print(f"Snapshot written to {snapshot_path}", file=sys.stderr)
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    # If explicit path provided, check if it exists before reading stdin
    if args.snapshot_path is not None:
        snapshot_path = args.snapshot_path
        if not snapshot_path.exists():
            raise SnapshotReadError(f"Snapshot file not found: {snapshot_path}")

    if sys.stdin.isatty():
        raise UsageError("stdin is empty. Pipe linter output to snaplint.")

    # Read all input lines
    input_lines = list(sys.stdin)

    # Determine snapshot path for auto-detect case
    if args.snapshot_path is None:
        # Auto-detect linter type and create default path
        linter_type = _detect_linter_from_lines(input_lines)
        snapshot_path = _get_default_snapshot_path(linter_type)

        if not snapshot_path.exists():
            raise SnapshotReadError(
                f"No snapshot file found at {snapshot_path}.\n"
                f"Auto-detected linter: {linter_type}\n"
                f"Run 'snaplint take-snapshot' first to create a snapshot, "
                f"or specify a snapshot path explicitly."
            )

        print(f"Auto-detected linter: {linter_type}", file=sys.stderr)
        print(f"Using snapshot file: {snapshot_path}", file=sys.stderr)

    try:
        with snapshot_path.open("rb") as f:
            snapshot_file = read_snapshot(f)
    except OSError as e:
        raise SnapshotReadError(
            f"Could not read snapshot file {snapshot_path}: {e}"
        ) from e

    current_file = build_snapshot_file(input_lines)

    diff_result = diff_snapshot_files(
        current=current_file,
        snapshot=snapshot_file,
    )

    render_options = RenderOptions(
        stdout_is_tty=sys.stdout.isatty(),
        color_enabled=True,  # Always true, render function checks tty
        verbose=args.verbose,
    )

    render_diff(
        diff=diff_result,
        options=render_options,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    if diff_result.total_added > 0:
        return 1  # New issues found

    return 0  # No new issues


if __name__ == "__main__":
    sys.exit(main())
