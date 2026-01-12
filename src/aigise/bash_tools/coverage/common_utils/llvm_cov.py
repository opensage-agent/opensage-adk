from __future__ import annotations

from typing import NamedTuple

import msgspec

"""
coverage json structure: https://github.com/llvm/llvm-project/blob/main/llvm/tools/llvm-cov/CoverageExporterJson.cpp
coverage mapping: https://llvm.org/docs/CoverageMappingFormat.html
"""


class LineCoverage(msgspec.Struct):
    """Object summarizing line coverage."""

    count: int
    covered: int
    percent: float


class FunctionCoverage(msgspec.Struct):
    """Object summarizing function coverage."""

    count: int
    covered: int
    percent: float


class InstantiationCoverage(msgspec.Struct):
    """Object summarizing instantiation coverage."""

    count: int
    covered: int
    percent: float


class RegionCoverage(msgspec.Struct):
    """Object summarizing region coverage."""

    count: int
    covered: int
    notcovered: int
    percent: float


class BranchCoverage(msgspec.Struct):
    """Object summarizing branch coverage."""

    count: int
    covered: int
    notcovered: int
    percent: float


class MCDCCoverage(msgspec.Struct):
    """Object summarizing MC/DC coverage."""

    count: int
    covered: int
    notcovered: int
    percent: float


class Summary(msgspec.Struct):
    """Summary of coverage statistics."""

    lines: LineCoverage
    functions: FunctionCoverage
    instantiations: InstantiationCoverage
    regions: RegionCoverage
    branches: BranchCoverage | None = None
    mcdc: MCDCCoverage | None = None


class CoverageSegment(msgspec.Struct, array_like=True):
    """
    Describes a segment of the file with a counter.
    Array format: [Line, Col, Count, HasCount, IsRegionEntry, IsGapRegion]
    """

    line: int
    col: int
    count: int
    # NOTE: Some llvm-cov versions emit these boolean-like fields as 0/1 integers
    # in the JSON array payload. Use int here for compatibility; bool values also
    # decode successfully because bool is a subclass of int in Python.
    has_count: int
    is_region_entry: int
    is_gap_region: int = 0


class CountedRegion(msgspec.Struct, array_like=True):
    """
    Single region with execution count.
    Array format: [LineStart, ColumnStart, LineEnd, ColumnEnd, ExecutionCount, FileID, ExpandedFileID, Kind]
    """

    line_start: int
    column_start: int
    line_end: int
    column_end: int
    execution_count: int
    file_id: int
    expanded_file_id: int
    kind: int


class Branch(msgspec.Struct, array_like=True):
    """Describes a branch with execution counts."""

    line_start: int
    column_start: int
    line_end: int
    column_end: int
    execution_count: int
    false_execution_count: int
    file_id: int
    expanded_file_id: int
    kind: int


class MCDCDecisionRecord(msgspec.Struct):
    """MC/DC decision record."""

    line_start: int
    column_start: int
    line_end: int
    column_end: int
    file_id: int
    expanded_file_id: int
    conditions_num: int
    decision_region: CountedRegion


class MCDCBranchRecord(msgspec.Struct):
    """MC/DC branch record."""

    line_start: int
    column_start: int
    line_end: int
    column_end: int
    execution_count: int
    false_execution_count: int
    file_id: int
    expanded_file_id: int
    condition_id: int
    condition_ids: list[int]


class MCDCRecord(msgspec.Struct):
    """MC/DC record containing decision and branch information."""

    decision: MCDCDecisionRecord
    branches: list[MCDCBranchRecord]


class Expansion(msgspec.Struct):
    """Object that describes a single expansion."""

    filenames: list[str]
    source_region: CountedRegion
    target_regions: list[CountedRegion]
    branches: list[Branch] = msgspec.field(default_factory=list)


class Function(msgspec.Struct):
    """Coverage info for a single function."""

    name: str
    count: int
    regions: list[CountedRegion]
    filenames: list[str]
    branches: list[Branch] = msgspec.field(default_factory=list)
    mcdc_records: list[MCDCRecord] = msgspec.field(default_factory=list)


class File(msgspec.Struct):
    """Coverage for a single file."""

    filename: str
    summary: Summary
    segments: list[CoverageSegment] = msgspec.field(default_factory=list)
    expansions: list[Expansion] = msgspec.field(default_factory=list)
    branches: list[Branch] = msgspec.field(default_factory=list)
    mcdc_records: list[MCDCRecord] = msgspec.field(default_factory=list)


class Export(msgspec.Struct):
    """JSON representation of one CoverageMapping."""

    files: list[File]
    functions: list[Function]
    totals: Summary


class LLVMCoverageRoot(msgspec.Struct):
    """Root element containing metadata and coverage data."""

    data: list[Export]
    type: str
    version: str


# Convenience decoder for parsing LLVM coverage JSON
decoder = msgspec.json.Decoder(LLVMCoverageRoot)


def parse_llvm_coverage_json(json_bytes: bytes) -> LLVMCoverageRoot:
    """
    Parse LLVM coverage JSON data into structured msgspec models.

    Args:
        json_bytes: Bytes containing the JSON data from llvm-cov export

    Returns:
        LLVMCoverageRoot object with parsed coverage data

    Example:
        ```python
        with open('coverage.json', 'rb') as f:
            data = f.read()
        coverage = parse_llvm_coverage_json(data)
        ```
    """
    return decoder.decode(json_bytes)


def parse_llvm_coverage_json_str(json_str: str) -> LLVMCoverageRoot:
    """
    Parse LLVM coverage JSON string data into structured msgspec models.

    Args:
        json_str: String containing the JSON data from llvm-cov export

    Returns:
        LLVMCoverageRoot object with parsed coverage data

    Example:
        ```python
        with open('coverage.json', 'r') as f:
            data = f.read()
        coverage = parse_llvm_coverage_json_str(data)
        ```
    """
    return decoder.decode(json_str)


# Encoder for serializing back to JSON
encoder = msgspec.json.Encoder()


def encode_llvm_coverage_json(coverage: LLVMCoverageRoot) -> bytes:
    """
    Encode LLVMCoverageRoot back to JSON bytes.

    Args:
        coverage: LLVMCoverageRoot object to encode

    Returns:
        JSON bytes representation
    """
    return encoder.encode(coverage)
