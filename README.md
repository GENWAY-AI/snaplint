# snaplint

**Snapshot your linter errors and track only new issues in CI** — perfect for adopting linters incrementally on large codebases without breaking existing workflows.

`snaplint` captures a baseline of current linter findings and lets you diff future runs against it. Only new errors cause CI failures, while existing issues are tracked separately.

## Quick Start

Install via pip or uv:

```bash
pip install snaplint
# or
uv tool install snaplint
```

### Basic Workflow

1. **Create a baseline snapshot** of your current linter output:

```bash
flake8 src/ | snaplint take-snapshot
# Creates .snaplint/snapshot.flake8.json.gz
```

2. **Check for new issues** in CI or locally:

```bash
flake8 src/ | snaplint diff
# Exit code 0: no new issues ✓
# Exit code 1: new issues found ✗
```

The diff command shows you:

- ✗ **New errors** (red) — will fail CI
- ✓ **Removed errors** (green) — improvements!
- **File-level changes** — error count and ordering changes per file

### Auto-Detection

`snaplint` automatically detects your linter (ruff, flake8, mypy, pylint, or generic format) and creates appropriately named snapshots in `.snaplint/`:

```bash
# Each linter gets its own snapshot
ruff check . --output-format concise | snaplint take-snapshot  # → .snaplint/snapshot.ruff.json.gz
flake8 . | snaplint take-snapshot    # → .snaplint/snapshot.flake8.json.gz
mypy . | snaplint take-snapshot      # → .snaplint/snapshot.mypy.json.gz
pylint src/ | snaplint take-snapshot # → .snaplint/snapshot.pylint.json.gz
```

### Custom Snapshot Paths

Specify a custom path if needed:

```bash
flake8 . | snaplint take-snapshot snapshots/baseline.json
flake8 . | snaplint diff snapshots/baseline.json
```

### Exit Codes

- `0` — No new issues (safe to merge)
- `1` — New issues detected (CI should fail)
- `2` — Usage error (missing snapshot, no input, etc.)
- `3` — Unexpected internal error

## How It Works

`snaplint` creates SHA1 fingerprints of each error by hashing the error type + source code line. This makes error tracking resilient to:

- Line number changes (refactoring, adding imports)
- File reorganization
- Whitespace changes in unrelated code

When comparing snapshots:

- **Per-file tracking** shows which files improved or regressed
- **Order changes** are detected when errors move within a file
- **Count changes** show if the total number of issues changed
- **File-level errors** (line 0) are fully supported

Snapshots are stored as gzip-compressed JSON (`.json.gz`) with version metadata for forward compatibility.

## CI Integration

### GitHub Actions

```yaml
- name: Check for new linter issues
  run: |
    flake8 . | snaplint diff || {
      echo "❌ New linting issues detected"
      exit 1
    }
```

### Pre-commit Hook

Add to `.pre-commit-config.yaml`:

```yaml
- repo: local
  hooks:
    - id: snaplint
      name: Check linting snapshot
      entry: bash -c 'flake8 . | snaplint diff'
      language: system
      pass_filenames: false
```

## Supported Linters

`snaplint` automatically recognizes output from:

- **ruff** (use `--output-format concise`)
- **flake8** and compatible tools (flake9, etc.)
- **mypy**
- **pylint**
- **Generic format** — any tool outputting `path:line:col: message`

Unrecognized lines are skipped with a warning to stderr.

## Working with Older Python Versions

If your project needs to support older Python versions (e.g., Python 3.8) but `snaplint` requires Python 3.10+, you can run the linter with your target Python version and `snaplint` with a newer interpreter:

```bash
# Run linter with Python 3.8
python3.8 -m flake8 src/ > /tmp/lint_output.txt

# Process with snaplint using Python 3.10+
python3.10 -m snaplint take-snapshot < /tmp/lint_output.txt
python3.10 -m snaplint diff < /tmp/lint_output.txt
```

Or use separate virtual environments:

```bash
# Create venv for linting with old Python
python3.8 -m venv .venv-lint
.venv-lint/bin/pip install flake8

# Create venv for snaplint with new Python
python3.10 -m venv .venv-snaplint
.venv-snaplint/bin/pip install snaplint

# Use them together
.venv-lint/bin/flake8 src/ | .venv-snaplint/bin/snaplint diff
```

This approach lets you maintain compatibility with older Python versions in your codebase while using modern tooling for CI/CD workflows.

## Development

### Setup

```bash
# Clone the repository
git clone https://github.com/GENWAY-AI/snaplint.git
cd snaplint

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=snaplint --cov-report=html

# Run specific test file
uv run pytest tests/test_e2e.py -v
```

### Local Installation

Install as a global command for testing:

```bash
uv tool install -e .
snaplint --version
```

## License

MIT
