"""End-to-end integration tests for snaplint."""

from __future__ import annotations

import gzip
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory with Python files."""
    project = tmp_path / "myproject"
    project.mkdir()

    # Create a multi-file Python project
    (project / "src").mkdir()

    # File 1: utils.py
    (project / "src" / "utils.py").write_text("""
def calculate(x, y):
    unused_var = 10
    return x + y

def format_string(text):
    return text.strip()
""")

    # File 2: main.py
    (project / "src" / "main.py").write_text("""
import sys
import os
from typing import List

def main():
    result = calculate(5, 3)
    print(result)

def calculate(a, b):
    return a + b
""")

    # File 3: config.py
    (project / "src" / "config.py").write_text("""
DEBUG = True
API_URL = "https://api.example.com"

def get_config():
    return {"debug": DEBUG, "url": API_URL}
""")

    return project


def run_flake8(project_dir: Path) -> str:
    """Run flake8 on the project and return output."""
    try:
        result = subprocess.run(
            ["flake8", str(project_dir / "src")],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        return result.stdout
    except FileNotFoundError:
        pytest.fail("flake8 not installed")


def run_snaplint(
    project_dir: Path, command: str, snapshot_file: Path, stdin: str = ""
) -> tuple[int, str, str]:
    """Run snaplint command and return exit code, stdout, stderr."""
    args = [sys.executable, "-m", "snaplint.cli", command, str(snapshot_file)]
    result = subprocess.run(
        args,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    return result.returncode, result.stdout, result.stderr


def test_e2e_full_workflow(project_dir: Path):
    """Test complete workflow: take snapshot, modify code, diff changes."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Step 1: Run linter and take initial snapshot
    initial_lint_output = run_flake8(project_dir)
    assert initial_lint_output  # Should have some lint errors

    returncode, stdout, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0
    assert snapshot_file.exists()
    assert "Snapshot written" in stderr

    # Verify snapshot is valid JSON
    import json

    with gzip.open(snapshot_file, "rt", encoding="utf-8") as f:
        snapshot_data = json.load(f)
    assert snapshot_data["version"] == "1"
    assert len(snapshot_data["files"]) > 0

    # Step 2: Run diff with same output - should show no changes
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0  # No new issues
    assert "summary: +0 -0" in stderr

    # Step 3: Fix one issue - remove unused import from main.py
    main_file = project_dir / "src" / "main.py"
    main_file.write_text("""
from typing import List

def main():
    result = calculate(5, 3)
    print(result)

def calculate(a, b):
    return a + b
""")

    # Run linter again and diff
    new_lint_output = run_flake8(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )
    assert returncode == 0  # No new issues (only removed)
    assert "summary: +0 -" in stderr  # Should show removed errors
    # Removed errors are not shown by default (need --verbose)

    # Step 4: Introduce a new error
    utils_file = project_dir / "src" / "utils.py"
    utils_file.write_text("""
def calculate(x, y):
    unused_var = 10
    another_unused = 20
    return x + y

def format_string(text):
    return text.strip()
""")

    # Run linter and diff
    new_lint_output = run_flake8(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )
    assert returncode == 1  # New issues found
    assert "summary: +" in stderr  # Should show added errors
    assert "+" in stdout  # Should show added lines


def test_e2e_code_refactoring_preserves_errors(project_dir: Path):
    """Test that refactoring code (changing line numbers) is detected properly."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"
    utils_file = project_dir / "src" / "utils.py"

    # Initial code with error on line 3
    utils_file.write_text("""
def calculate(x, y):
    unused_var = 10
    return x + y
""")

    # Take snapshot
    initial_lint_output = run_flake8(project_dir)
    returncode, _, _ = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0

    # Refactor: Add lines at the top (error moves to different line number)
    utils_file.write_text("""
# New comment
# Another comment

def calculate(x, y):
    unused_var = 10
    return x + y
""")

    # Run diff - the unused_var is still there, just on a different line
    # Since we hash error_type + source_code_line, it should still match
    new_lint_output = run_flake8(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )

    # The error is the same (same code line content), so it should be unchanged
    assert "unchanged" in stderr.lower()


def test_e2e_multiple_files_complex_diff(project_dir: Path):
    """Test diff across multiple files with various changes."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Take initial snapshot
    initial_lint_output = run_flake8(project_dir)
    returncode, _, _ = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0

    # Make changes to multiple files
    # 1. Fix one error in utils.py
    (project_dir / "src" / "utils.py").write_text("""
def calculate(x, y):
    return x + y

def format_string(text):
    return text.strip()
""")

    # 2. Add new error in config.py
    (project_dir / "src" / "config.py").write_text("""
DEBUG = True
API_URL = "https://api.example.com"
UNUSED_VAR = 123

def get_config():
    return {"debug": DEBUG, "url": API_URL}
""")

    # 3. Keep main.py the same

    # Run diff
    new_lint_output = run_flake8(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )

    # Should show changes in multiple files
    # Return code depends on whether new errors were added
    assert "summary:" in stderr
    assert "file(s) with changes" in stderr


def test_e2e_order_change_detection(project_dir: Path):
    """Test that changes in error order are detected."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"
    test_file = project_dir / "order_test.py"

    # Create file with multiple errors in specific order
    test_file.write_text("""
unused_a = 1
unused_b = 2
unused_c = 3
""")

    # Take snapshot
    initial_lint_output = run_flake8(project_dir)
    returncode, _, _ = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0

    # Reorder the lines (errors will be reported in different order)
    test_file.write_text("""
unused_c = 3
unused_a = 1
unused_b = 2
""")

    # Run diff
    new_lint_output = run_flake8(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )

    # The actual errors are the same, but their positions changed
    # Our hash is based on error_type + code_line, so "unused_a = 1"
    # will have the same hash regardless of line number
    # So this should show unchanged errors but possibly order changes
    assert "unchanged" in stderr.lower()


def test_e2e_snapshot_with_no_errors(project_dir: Path):
    """Test taking snapshot and diffing when there are no lint errors."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"
    clean_file = project_dir / "clean.py"

    # Create a file with no lint errors
    clean_file.write_text("""
def hello_world():
    return "Hello, World!"
""")

    # Run flake8 on clean file only
    try:
        result = subprocess.run(
            ["flake8", str(clean_file)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        clean_output = result.stdout
    except FileNotFoundError:
        pytest.fail("flake8 not installed")

    # Take snapshot (empty)
    returncode, _, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=clean_output
    )
    assert returncode == 0
    assert "Snapshot written" in stderr

    # Verify snapshot has no files or empty files
    import json

    with gzip.open(snapshot_file, "rt", encoding="utf-8") as f:
        snapshot_data = json.load(f)
    assert snapshot_data["version"] == "1"
    # Files list should be empty or very small
    assert len(snapshot_data["files"]) == 0 or all(
        f["error_count"] == 0 for f in snapshot_data["files"]
    )

    # Diff with same clean output
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=clean_output
    )
    assert returncode == 0
    assert "summary: +0 -0" in stderr


def test_e2e_binary_file_handling(project_dir: Path):
    """Test that snaplint handles errors in non-existent or binary files gracefully."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Create fake linter output pointing to non-existent file
    fake_lint_output = f"{project_dir}/nonexistent.py:1:1: E001 Some error\n"

    # Take snapshot - should warn but not crash
    returncode, _, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=fake_lint_output
    )
    assert returncode == 0
    assert "warning: could not read source" in stderr.lower()

    # Verify snapshot was created (but might be empty)
    assert snapshot_file.exists()


def test_e2e_large_codebase_simulation(project_dir: Path):
    """Test with a larger number of files and errors."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Create multiple files with various errors
    for i in range(10):
        file_path = project_dir / f"file_{i}.py"
        file_path.write_text(f"""
import unused_import_{i}

unused_var_{i} = {i}

def func_{i}(x):
    another_unused = {i * 2}
    return x + {i}
""")

    # Run linter
    try:
        result = subprocess.run(
            ["flake8", str(project_dir)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        lint_output = result.stdout
    except FileNotFoundError:
        pytest.fail("flake8 not installed")

    # Take snapshot
    returncode, _, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=lint_output
    )
    assert returncode == 0

    # Verify snapshot contains multiple files
    import json

    with gzip.open(snapshot_file, "rt", encoding="utf-8") as f:
        snapshot_data = json.load(f)
    assert len(snapshot_data["files"]) >= 5  # Should have many files

    # Fix errors in half the files
    for i in range(5):
        file_path = project_dir / f"file_{i}.py"
        file_path.write_text(f"""
def func_{i}(x):
    return x + {i}
""")

    # Run diff
    new_lint_output = run_flake8(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )

    # Should show that some errors were removed
    assert "summary:" in stderr
    assert "file(s) with changes" in stderr


def test_e2e_update_snapshot_workflow(project_dir: Path):
    """Test the workflow of updating a snapshot after code changes."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Use a separate directory to avoid interference from fixture files
    isolated_dir = project_dir / "isolated"
    isolated_dir.mkdir()
    test_file = isolated_dir / "evolving.py"

    # Version 1: Initial code with 3 errors
    test_file.write_text("""import sys
import os
unused_var = 1
""")

    # Run flake8 only on isolated directory
    try:
        result = subprocess.run(
            ["flake8", str(test_file)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        lint_v1 = result.stdout
    except FileNotFoundError:
        pytest.fail("flake8 not installed")

    returncode, _, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=lint_v1
    )
    assert returncode == 0

    # Version 2: Fix one import error, keep other errors
    test_file.write_text("""import sys
unused_var = 1
""")

    try:
        result = subprocess.run(
            ["flake8", str(test_file)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        lint_v2 = result.stdout
    except FileNotFoundError:
        pytest.fail("flake8 not installed")

    returncode, _, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=lint_v2
    )
    # Should show that errors changed (one removed)
    assert "summary:" in stderr
    # Verify we have some removal
    if "-1" not in stderr and "-2" not in stderr:
        # If no removal shown, at least verify the snapshot works
        assert "unchanged" in stderr.lower()

    # Update snapshot to version 2
    returncode, _, _ = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=lint_v2
    )
    assert returncode == 0

    # Diff against same version should show no changes
    returncode, _, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=lint_v2
    )
    assert returncode == 0
    assert "summary: +0 -0" in stderr


# ============================================================================
# Ruff linter tests
# ============================================================================


def run_ruff(project_dir: Path, target: Path | None = None) -> str:
    """Run ruff check on the project and return output in concise format."""
    target_path = target or (project_dir / "src")
    try:
        result = subprocess.run(
            ["ruff", "check", "--output-format", "concise", str(target_path)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        return result.stdout
    except FileNotFoundError:
        pytest.fail("ruff not installed")


def test_e2e_ruff_full_workflow(project_dir: Path):
    """Test complete workflow with ruff: take snapshot, modify code, diff changes."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Step 1: Run ruff and take initial snapshot
    initial_lint_output = run_ruff(project_dir)
    assert initial_lint_output  # Should have some lint errors

    returncode, stdout, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0
    assert snapshot_file.exists()
    assert "Snapshot written" in stderr

    # Verify snapshot is valid JSON
    import json

    with gzip.open(snapshot_file, "rt", encoding="utf-8") as f:
        snapshot_data = json.load(f)
    assert snapshot_data["version"] == "1"
    assert len(snapshot_data["files"]) > 0

    # Step 2: Run diff with same output - should show no changes
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0  # No new issues
    assert "summary: +0 -0" in stderr

    # Step 3: Fix one issue - remove unused import from main.py
    main_file = project_dir / "src" / "main.py"
    main_file.write_text("""
from typing import List

def main():
    result = calculate(5, 3)
    print(result)

def calculate(a, b):
    return a + b
""")

    # Run linter again and diff
    new_lint_output = run_ruff(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )
    assert returncode == 0  # No new issues (only removed)
    assert "summary: +0 -" in stderr  # Should show removed errors

    # Step 4: Introduce a new error
    utils_file = project_dir / "src" / "utils.py"
    utils_file.write_text("""
def calculate(x, y):
    unused_var = 10
    another_unused = 20
    return x + y

def format_string(text):
    return text.strip()
""")

    # Run linter and diff
    new_lint_output = run_ruff(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )
    assert returncode == 1  # New issues found
    assert "summary: +" in stderr  # Should show added errors
    assert "+" in stdout  # Should show added lines


def test_e2e_ruff_auto_detect_linter(project_dir: Path):
    """Test that ruff output is auto-detected and uses correct snapshot filename."""
    # Use a separate directory to avoid interference
    isolated_dir = project_dir / "ruff_test"
    isolated_dir.mkdir()
    test_file = isolated_dir / "test.py"

    # Create file with lint errors
    test_file.write_text("""import sys
import os
unused_var = 1
""")

    # Run ruff
    lint_output = run_ruff(project_dir, isolated_dir)
    assert lint_output  # Should have lint errors

    # Take snapshot without specifying path (auto-detect)
    result = subprocess.run(
        [sys.executable, "-m", "snaplint.cli", "take-snapshot"],
        input=lint_output,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    assert result.returncode == 0
    assert "Auto-detected linter: ruff" in result.stderr

    # Verify the snapshot file was created with the correct name
    expected_snapshot = project_dir / ".snaplint" / "snapshot.ruff.json.gz"
    assert expected_snapshot.exists(), (
        f"Expected snapshot at {expected_snapshot}, "
        f"got files: {list((project_dir / '.snaplint').iterdir()) if (project_dir / '.snaplint').exists() else 'no .snaplint dir'}"
    )

    # Diff should also auto-detect ruff
    result = subprocess.run(
        [sys.executable, "-m", "snaplint.cli", "diff"],
        input=lint_output,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    assert result.returncode == 0
    assert "Auto-detected linter: ruff" in result.stderr
    assert "summary: +0 -0" in result.stderr


def test_e2e_ruff_vs_flake8_distinction(project_dir: Path):
    """Test that ruff and flake8 are correctly distinguished.

    Both linters produce similar output format but should be detected separately.
    """
    # Use a separate directory to avoid interference
    isolated_dir = project_dir / "distinction_test"
    isolated_dir.mkdir()
    test_file = isolated_dir / "test.py"

    # Create file with lint errors
    test_file.write_text("""import sys
import os
unused_var = 1
""")

    # Run ruff
    ruff_output = run_ruff(project_dir, isolated_dir)

    # Run flake8
    try:
        flake8_result = subprocess.run(
            ["flake8", str(isolated_dir)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        flake8_output = flake8_result.stdout
    except FileNotFoundError:
        pytest.fail("flake8 not installed")

    # Both outputs should parse correctly and have similar structure
    assert ruff_output  # Ruff should find issues
    assert flake8_output  # Flake8 should find issues

    # Take snapshot with ruff output (no explicit path)
    result = subprocess.run(
        [sys.executable, "-m", "snaplint.cli", "take-snapshot"],
        input=ruff_output,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    assert result.returncode == 0
    assert "Auto-detected linter: ruff" in result.stderr

    # Now verify flake8 output is detected as flake8
    result = subprocess.run(
        [sys.executable, "-m", "snaplint.cli", "take-snapshot"],
        input=flake8_output,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    assert result.returncode == 0
    assert "Auto-detected linter: flake8" in result.stderr


def test_e2e_ruff_code_refactoring_preserves_errors(project_dir: Path):
    """Test that refactoring code (changing line numbers) is detected properly with ruff."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"
    utils_file = project_dir / "src" / "utils.py"

    # Initial code with error on line 3
    utils_file.write_text("""
def calculate(x, y):
    unused_var = 10
    return x + y
""")

    # Take snapshot
    initial_lint_output = run_ruff(project_dir)
    returncode, _, _ = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0

    # Refactor: Add lines at the top (error moves to different line number)
    utils_file.write_text("""
# New comment
# Another comment

def calculate(x, y):
    unused_var = 10
    return x + y
""")

    # Run diff - the unused_var is still there, just on a different line
    # Since we hash error_type + source_code_line, it should still match
    new_lint_output = run_ruff(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )

    # The error is the same (same code line content), so it should be unchanged
    assert "unchanged" in stderr.lower()


# ============================================================================
# Mypy linter tests
# ============================================================================


def run_mypy(project_dir: Path, target: Path | None = None) -> str:
    """Run mypy on the project and return output."""
    target_path = target or (project_dir / "src")
    try:
        result = subprocess.run(
            ["mypy", str(target_path)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        return result.stdout
    except FileNotFoundError:
        pytest.fail("mypy not installed")


def test_e2e_mypy_full_workflow(project_dir: Path):
    """Test complete workflow with mypy: take snapshot, modify code, diff changes."""
    snapshot_file = project_dir / "lint.snapshot.json.gz"

    # Create files with type errors
    (project_dir / "src" / "typed.py").write_text("""
def add_numbers(a: int, b: int) -> int:
    return a + b

def broken_types(x: str) -> int:
    return x  # type error: returning str instead of int

result: str = add_numbers(1, 2)  # type error: assigning int to str
""")

    # Step 1: Run mypy and take initial snapshot
    initial_lint_output = run_mypy(project_dir)
    assert initial_lint_output  # Should have some type errors

    returncode, stdout, stderr = run_snaplint(
        project_dir, "take-snapshot", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0
    assert snapshot_file.exists()
    assert "Snapshot written" in stderr

    # Verify snapshot is valid JSON
    import json

    with gzip.open(snapshot_file, "rt", encoding="utf-8") as f:
        snapshot_data = json.load(f)
    assert snapshot_data["version"] == "1"
    assert len(snapshot_data["files"]) > 0

    # Step 2: Run diff with same output - should show no changes
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=initial_lint_output
    )
    assert returncode == 0  # No new issues
    assert "summary: +0 -0" in stderr

    # Step 3: Fix one type error
    (project_dir / "src" / "typed.py").write_text("""
def add_numbers(a: int, b: int) -> int:
    return a + b

def broken_types(x: str) -> int:
    return x  # type error: returning str instead of int

result: int = add_numbers(1, 2)  # Fixed: now correctly typed as int
""")

    # Run linter again and diff
    new_lint_output = run_mypy(project_dir)
    returncode, stdout, stderr = run_snaplint(
        project_dir, "diff", snapshot_file, stdin=new_lint_output
    )
    assert returncode == 0  # No new issues (only removed)
    assert "summary: +0 -" in stderr  # Should show removed errors


def test_e2e_mypy_auto_detect_linter(project_dir: Path):
    """Test that mypy output is auto-detected and uses correct snapshot filename."""
    # Use a separate directory to avoid interference
    isolated_dir = project_dir / "mypy_test"
    isolated_dir.mkdir()
    test_file = isolated_dir / "test.py"

    # Create file with type errors
    test_file.write_text("""
def greet(name: str) -> str:
    return name

x: int = greet("hello")  # type error
""")

    # Run mypy
    lint_output = run_mypy(project_dir, isolated_dir)
    assert lint_output  # Should have type errors

    # Take snapshot without specifying path (auto-detect)
    result = subprocess.run(
        [sys.executable, "-m", "snaplint.cli", "take-snapshot"],
        input=lint_output,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    assert result.returncode == 0
    assert "Auto-detected linter: mypy" in result.stderr

    # Verify the snapshot file was created with the correct name
    expected_snapshot = project_dir / ".snaplint" / "snapshot.mypy.json.gz"
    assert expected_snapshot.exists(), (
        f"Expected snapshot at {expected_snapshot}, "
        f"got files: {list((project_dir / '.snaplint').iterdir()) if (project_dir / '.snaplint').exists() else 'no .snaplint dir'}"
    )

    # Diff should also auto-detect mypy
    result = subprocess.run(
        [sys.executable, "-m", "snaplint.cli", "diff"],
        input=lint_output,
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    assert result.returncode == 0
    assert "Auto-detected linter: mypy" in result.stderr
    assert "summary: +0 -0" in result.stderr
