from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import IO

from snaplint.errors import ParseWarning, SnapshotReadError
from snaplint.models import (
    FileSnapshot,
    IssueKey,
    IssueLine,
    IssueSet,
    SnapshotEntry,
    SnapshotFile,
)
from snaplint.parse import get_issue_key, parse_lines


def _read_lines_sync(path: Path, line_numbers: list[int]) -> dict[int, str]:
    """Read specific lines from a file synchronously.

    Returns a dict mapping line_number -> line_content.
    Line 0 returns a special file-level marker.
    """
    result: dict[int, str] = {}

    # Handle file-level errors (line 0)
    if 0 in line_numbers:
        result[0] = f"<file-level:{path}>"

    # Read actual lines if needed
    actual_lines = [ln for ln in line_numbers if ln > 0]
    if not actual_lines:
        return result

    try:
        if not path.exists():
            return result

        with path.open("r", encoding="utf-8") as f:
            all_lines = f.readlines()
            for line_num in actual_lines:
                if 0 < line_num <= len(all_lines):
                    result[line_num] = all_lines[line_num - 1].strip()
    except (OSError, UnicodeDecodeError):
        pass

    return result


async def _read_all_source_lines_async(
    issues: list[IssueLine],
) -> dict[tuple[str, int], str]:
    """Read all source lines needed for issues in parallel.

    Returns a dict mapping (file_path, line_number) -> line_content.
    """
    # Group line numbers by file path
    path_to_lines: dict[Path, list[int]] = defaultdict(list)
    for issue in issues:
        path = Path(issue.path)
        path_to_lines[path].append(issue.line)

    # Read all files in parallel using thread pool
    async def read_one_file(
        path: Path, line_nums: list[int]
    ) -> tuple[Path, dict[int, str]]:
        lines = await asyncio.to_thread(_read_lines_sync, path, line_nums)
        return (path, lines)

    results = await asyncio.gather(
        *[read_one_file(path, line_nums) for path, line_nums in path_to_lines.items()]
    )

    # Build final lookup dict: (str_path, line_num) -> content
    lookup: dict[tuple[str, int], str] = {}
    for path, lines_dict in results:
        for line_num, content in lines_dict.items():
            lookup[(str(path), line_num)] = content

    return lookup


def _read_source_line(file_path: str, line_number: int) -> str | None:
    """Read a specific line from a source file.

    For line 0 (file-level errors), returns a special marker based on the file path.
    """
    # Line 0 indicates a file-level error (not tied to a specific line)
    if line_number == 0:
        # Use the file path as the "source line" for file-level errors
        return f"<file-level:{file_path}>"

    try:
        path = Path(file_path)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
            if 0 < line_number <= len(lines):
                return lines[line_number - 1].strip()
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _compute_sha1(error_identifier: str, source_line: str) -> str:
    """Compute SHA1 hash of error identifier + source line."""
    content = f"{error_identifier}:{source_line}"
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _issue_to_snapshot_entry(
    issue: IssueLine, source_lines: dict[tuple[str, int], str]
) -> SnapshotEntry | None:
    """Convert an IssueLine to a SnapshotEntry with SHA1 hash.

    Args:
        issue: The issue to convert
        source_lines: Pre-fetched source lines lookup dict
    """
    source_line = source_lines.get((issue.path, issue.line))
    if source_line is None:
        # Can't compute hash without source line, skip this entry
        return None

    # Use code if available, otherwise use message as identifier
    error_identifier = issue.code if issue.code else issue.message

    sha1 = _compute_sha1(error_identifier, source_line)

    # SnapshotEntry requires message=None when code is set
    return SnapshotEntry(
        path=issue.path,
        line=issue.line,
        column=issue.column,
        code=issue.code,
        message=None if issue.code else issue.message,
        sha1=sha1,
        original=issue.original,
    )


def build_issue_set(lines: Iterable[str]) -> IssueSet:
    """Build an IssueSet from an iterable of string lines."""
    issue_index: dict[IssueKey, IssueLine] = {}
    for result in parse_lines(lines):
        if isinstance(result, ParseWarning):
            print(f"snaplint: {result}", file=sys.stderr)
        elif isinstance(result, IssueLine):
            key = get_issue_key(result)
            # First one wins, ensuring stability of `original` line
            if key not in issue_index:
                issue_index[key] = result

    return IssueSet(
        items=frozenset(issue_index.keys()),
        index=issue_index,
    )


def build_snapshot_file(lines: Iterable[str]) -> SnapshotFile:
    """Build a SnapshotFile from linter output lines."""
    # Parse all issues
    issues: list[IssueLine] = []
    for result in parse_lines(lines):
        if isinstance(result, ParseWarning):
            print(f"snaplint: {result}", file=sys.stderr)
        elif isinstance(result, IssueLine):
            issues.append(result)

    # Read all source lines in parallel
    source_lines = asyncio.run(_read_all_source_lines_async(issues))

    # Group by file path
    files_dict: dict[str, list[SnapshotEntry]] = {}
    for issue in issues:
        entry = _issue_to_snapshot_entry(issue, source_lines)
        if entry is None:
            print(
                f"snaplint: warning: could not read source for "
                f"{issue.path}:{issue.line}, skipping",
                file=sys.stderr,
            )
            continue

        if entry.path not in files_dict:
            files_dict[entry.path] = []
        files_dict[entry.path].append(entry)

    # Build FileSnapshot objects
    file_snapshots: list[FileSnapshot] = []
    for path in sorted(files_dict.keys()):
        entries = tuple(files_dict[path])
        hash_sequence = tuple(entry.sha1 for entry in entries)

        file_snapshot = FileSnapshot(
            path=path,
            error_count=len(entries),
            entries=entries,
            hash_sequence=hash_sequence,
        )
        file_snapshots.append(file_snapshot)

    return SnapshotFile(files=tuple(file_snapshots))


def write_snapshot(snapshot: SnapshotFile, output: IO[bytes]) -> None:
    """Write a SnapshotFile to gzipped JSON output."""
    json_data = snapshot.model_dump(mode="json")
    json_str = json.dumps(json_data, indent=2) + "\n"
    output.write(gzip.compress(json_str.encode("utf-8")))


def read_snapshot(snapshot_file: IO[bytes]) -> SnapshotFile:
    """Read a gzipped snapshot file and build a SnapshotFile."""
    try:
        compressed_data = snapshot_file.read()
        json_str = gzip.decompress(compressed_data).decode("utf-8")
        data = json.loads(json_str)
        return SnapshotFile.model_validate(data)
    except gzip.BadGzipFile as e:
        raise SnapshotReadError(f"Invalid gzip file: {e}") from e
    except json.JSONDecodeError as e:
        raise SnapshotReadError(f"Invalid JSON in snapshot file: {e}") from e
    except Exception as e:
        raise SnapshotReadError(f"Failed to parse snapshot file: {e}") from e
