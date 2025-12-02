from __future__ import annotations

import argparse
import sys
from importlib.metadata import version as get_version
from pathlib import Path

from snaplint.diff import diff_snapshot_files
from snaplint.errors import SnaplintError, SnapshotReadError, UsageError
from snaplint.models import RenderOptions
from snaplint.render import render_diff
from snaplint.snapshot import build_snapshot_file, read_snapshot, write_snapshot


def _detect_linter_from_lines(lines: list[str]) -> str:
    """Detect the linter type from the first few lines of output.

    Returns a linter name like 'flake8', 'mypy', 'pylint', or 'generic'.
    """
    for line in lines[:10]:  # Check first 10 lines
        line = line.strip()
        if not line:
            continue

        # Check for mypy patterns
        if ": error:" in line or ": warning:" in line or ": note:" in line:
            if "[" in line and "]" in line:
                return "mypy"

        # Check for pylint patterns
        if "************* Module" in line:
            return "pylint"

        # Check for flake8 patterns (most common: path:line:col: CODE message)
        parts = line.split(":")
        if len(parts) >= 4:
            try:
                int(parts[1])  # line number
                int(parts[2])  # column number
                # Check if the next part starts with a code (letter + number)
                code_part = parts[3].strip()
                if code_part and len(code_part) > 0:
                    first_word = code_part.split()[0]
                    if first_word and len(first_word) >= 3:
                        # Check if it looks like a code
                        has_letter = first_word[0].isalpha()
                        has_digit = any(c.isdigit() for c in first_word)
                        if has_letter and has_digit:
                            return "flake8"
            except (ValueError, IndexError):
                pass

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
