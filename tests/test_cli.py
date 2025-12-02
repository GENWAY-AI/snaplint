from __future__ import annotations

import gzip
import io
import json
import sys
from pathlib import Path

import pytest

from snaplint.cli import main


def mock_stdin(monkeypatch, text: str):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def mock_argv(monkeypatch, *args: str):
    monkeypatch.setattr(sys, "argv", ["snaplint", *args])


@pytest.fixture
def snapshot_file(tmp_path: Path) -> Path:
    return tmp_path / "lint.snapshot.json.gz"


@pytest.fixture
def test_file(tmp_path: Path) -> Path:
    """Create a test Python file for snapshot tests."""
    file = tmp_path / "file.py"
    file.write_text("line1\nline2\n")
    return file


def test_cli_diff_no_new_issues(
    monkeypatch,
    snapshot_file,
    test_file,
    capsys,
):
    # Write the snapshot by first creating it properly
    input_content = f"{test_file}:1:1: E001 msg1\n"
    mock_stdin(monkeypatch, text=input_content)
    mock_argv(monkeypatch, "take-snapshot", str(snapshot_file))

    # Take the snapshot first
    assert main() == 0

    # Now diff against the same input
    mock_stdin(monkeypatch, text=input_content)
    mock_argv(monkeypatch, "diff", str(snapshot_file))

    return_code = main()

    assert return_code == 0
    captured = capsys.readouterr()
    # No changes should be shown
    assert "summary: +0 -0 (unchanged 1)" in captured.err


def test_cli_diff_new_issues(
    monkeypatch,
    snapshot_file,
    test_file,
    capsys,
):
    # Take a snapshot with one error
    input_content = f"{test_file}:1:1: E001 msg1\n"
    mock_stdin(monkeypatch, text=input_content)
    mock_argv(monkeypatch, "take-snapshot", str(snapshot_file))
    assert main() == 0

    # Now diff with a different error
    mock_stdin(monkeypatch, text=f"{test_file}:2:1: E002 msg2\n")
    mock_argv(monkeypatch, "diff", str(snapshot_file))

    return_code = main()

    assert return_code == 1
    captured = capsys.readouterr()
    assert f"{test_file}:2:1: E002 msg2 (+)" in captured.out
    assert "summary: +1 -1" in captured.err


def test_cli_diff_removed_issues(
    monkeypatch,
    snapshot_file,
    test_file,
    capsys,
):
    # Take a snapshot with one error
    input_content = f"{test_file}:1:1: E001 msg1\n"
    mock_stdin(monkeypatch, text=input_content)
    mock_argv(monkeypatch, "take-snapshot", str(snapshot_file))
    assert main() == 0

    # Empty stdin
    mock_stdin(monkeypatch, text="")
    mock_argv(monkeypatch, "diff", str(snapshot_file))

    return_code = main()

    assert return_code == 0
    captured = capsys.readouterr()
    # Removed errors are not shown by default
    assert f"{test_file}:1:1: E001 msg1 (-)" not in captured.out
    assert "summary: +0 -1" in captured.err


def test_cli_diff_removed_issues_verbose(
    monkeypatch,
    snapshot_file,
    test_file,
    capsys,
):
    # Take a snapshot with one error
    input_content = f"{test_file}:1:1: E001 msg1\n"
    mock_stdin(monkeypatch, text=input_content)
    mock_argv(monkeypatch, "take-snapshot", str(snapshot_file))
    assert main() == 0

    # Empty stdin with verbose flag
    mock_stdin(monkeypatch, text="")
    mock_argv(monkeypatch, "diff", str(snapshot_file), "--verbose")

    return_code = main()

    assert return_code == 0
    captured = capsys.readouterr()
    # Removed errors are shown in verbose mode
    assert f"{test_file}:1:1: E001 msg1 (-)" in captured.out
    assert "summary: +0 -1" in captured.err


def test_cli_usage_error_no_args(monkeypatch, capsys):
    mock_argv(monkeypatch)
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2
    assert "the following arguments are required: command" in capsys.readouterr().err


def test_cli_usage_error_bad_command(monkeypatch, capsys):
    mock_argv(monkeypatch, "badcmd")
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2
    assert "invalid choice: 'badcmd'" in capsys.readouterr().err


def test_cli_snapshot_not_found(monkeypatch, capsys):
    mock_argv(monkeypatch, "diff", "nonexistent.txt")
    return_code = main()
    assert return_code == 2
    assert "Snapshot file not found" in capsys.readouterr().err


def test_cli_no_stdin(monkeypatch, snapshot_file, capsys):
    snapshot_file.touch()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    mock_argv(monkeypatch, "diff", str(snapshot_file))
    return_code = main()
    assert return_code == 2
    assert "stdin is empty" in capsys.readouterr().err


def test_cli_take_snapshot(
    monkeypatch,
    snapshot_file,
    test_file,
    capsys,
):
    input_content = f"{test_file}:1:1: E001 msg1\n"
    mock_stdin(monkeypatch, text=input_content)
    mock_argv(monkeypatch, "take-snapshot", str(snapshot_file))

    return_code = main()

    assert return_code == 0

    # Verify snapshot is valid gzipped JSON
    with gzip.open(snapshot_file, "rt", encoding="utf-8") as f:
        snapshot_data = json.load(f)
    assert snapshot_data["version"] == "1"
    assert len(snapshot_data["files"]) == 1

    assert f"Snapshot written to {snapshot_file}" in capsys.readouterr().err
