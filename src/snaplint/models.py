from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StrictStr,
    model_validator,
)
from pydantic.functional_validators import AfterValidator

# Custom Types


def validate_normalized_path(v: str) -> str:
    if not isinstance(v, str):
        raise TypeError("string required")
    # On Windows, drive letters are case-insensitive, so normalize to lower
    if re.match(r"^[a-zA-Z]:\\", v):
        v = v[0].lower() + v[1:]
    # Normalize path separators and remove leading './'
    v = v.replace("\\", "/")
    while v.startswith("./"):
        v = v[2:]
    # Collapse multiple slashes
    v = re.sub(r"//+", "/", v)
    if not v:
        raise ValueError("path cannot be empty")
    return v


NormalizedPath = Annotated[str, AfterValidator(validate_normalized_path)]


def to_upper(v: str) -> str:
    return v.upper()


ConstrainedStrUpper = Annotated[str, AfterValidator(to_upper)]


# Pydantic Models


class IssueLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    original: StrictStr
    tool: Literal["flake", "ruff", "mypy", "pylint", "unknown"]
    path: NormalizedPath
    line: NonNegativeInt
    column: NonNegativeInt = 0
    code: ConstrainedStrUpper | None = None
    message: StrictStr


class IssueKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: NormalizedPath
    line: NonNegativeInt
    column: NonNegativeInt
    code: ConstrainedStrUpper | None = None
    message: StrictStr | None = None

    @model_validator(mode="after")
    def code_or_message_exists(self) -> IssueKey:
        if self.code is None and self.message is None:
            raise ValueError("Either code or message must be set")
        if self.code is not None and self.message is not None:
            raise ValueError("Message must be None if code is set")
        return self


class IssueSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: frozenset[IssueKey]
    index: Mapping[IssueKey, IssueLine]


class SnapshotEntry(BaseModel):
    """Represents a single error in the snapshot with SHA1 hash."""

    model_config = ConfigDict(frozen=True)

    path: NormalizedPath
    line: NonNegativeInt
    column: NonNegativeInt
    code: ConstrainedStrUpper | None = None
    message: StrictStr | None = None
    sha1: StrictStr  # SHA1 hash of (error_type + actual_code_line)
    original: StrictStr  # Original linter output line

    @model_validator(mode="after")
    def code_or_message_exists(self) -> SnapshotEntry:
        if self.code is None and self.message is None:
            raise ValueError("Either code or message must be set")
        if self.code is not None and self.message is not None:
            raise ValueError("Message must be None if code is set")
        return self


class FileSnapshot(BaseModel):
    """Snapshot data for a single file."""

    model_config = ConfigDict(frozen=True)

    path: NormalizedPath
    error_count: NonNegativeInt
    entries: tuple[SnapshotEntry, ...]  # Ordered list of entries
    hash_sequence: tuple[StrictStr, ...]  # Ordered sequence of SHA1 hashes


class SnapshotFile(BaseModel):
    """Root snapshot file structure."""

    model_config = ConfigDict(frozen=True)

    version: Literal["1"] = "1"
    files: tuple[FileSnapshot, ...] = Field(default_factory=tuple)


class FileDiff(BaseModel):
    """Diff information for a single file."""

    model_config = ConfigDict(frozen=True)

    path: NormalizedPath
    count_changed: bool  # Whether the number of errors changed
    order_changed: bool  # Whether the order of hashes changed
    added: tuple[SnapshotEntry, ...]
    removed: tuple[SnapshotEntry, ...]
    unchanged_count: NonNegativeInt


class DiffResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_diffs: tuple[FileDiff, ...]  # Per-file diff information
    total_added: NonNegativeInt
    total_removed: NonNegativeInt
    total_unchanged: NonNegativeInt
    files_with_changes: NonNegativeInt  # Number of files with any changes


class RenderOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    stdout_is_tty: bool
    color_enabled: bool
    sort: bool = True
    verbose: bool = False
