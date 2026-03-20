"""
Neo4j LLVM Coverage Data Uploader

This script uploads LLVM coverage data to Neo4j for efficient querying of:
- Which lines are covered by specific test cases
- Which test cases cover specific lines/functions
- Coverage statistics and relationships
"""

import json
import logging

import cxxfilt
import msgspec
from neo4j import GraphDatabase
from opensage.toolbox.coverage.llvm_cov import (
    Branch,
    CountedRegion,
    CoverageSegment,
    Export,
    File,
    Function,
    LLVMCoverageRoot,
    Summary,
)

logger = logging.getLogger(__name__)


class Neo4jCoverageUploader:
    def __init__(self, uri: str, username: str, password: str):
        """Initialize Neo4j connection."""
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self):
        """Close Neo4j connection."""
        self.driver.close()

    def create_schema(self):
        """Create Neo4j schema with constraints and indexes."""
        with self.driver.session() as session:
            # Create constraints
            constraints = [
                "CREATE CONSTRAINT test_case_name IF NOT EXISTS FOR (t:TestCase) REQUIRE t.name IS UNIQUE",
                "CREATE CONSTRAINT file_path IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE",
                "CREATE CONSTRAINT function_name_file IF NOT EXISTS FOR (fn:Function) REQUIRE (fn.name, fn.file_path) IS UNIQUE",
                "CREATE CONSTRAINT line_file_line IF NOT EXISTS FOR (l:Line) REQUIRE (l.file_path, l.line_number) IS UNIQUE",
                "CREATE CONSTRAINT region_id IF NOT EXISTS FOR (r:Region) REQUIRE r.id IS UNIQUE",
                "CREATE CONSTRAINT branch_id IF NOT EXISTS FOR (b:Branch) REQUIRE b.id IS UNIQUE",
            ]

            # Create indexes
            indexes = [
                # "CREATE INDEX line_number_idx IF NOT EXISTS FOR (l:Line) ON (l.line_number)",
                # "CREATE INDEX execution_count_idx IF NOT EXISTS FOR (r:Region) ON (r.execution_count)",
                # "CREATE INDEX coverage_percent_idx IF NOT EXISTS FOR (f:File) ON (f.line_coverage_percent)",
            ]

            for constraint in constraints:
                try:
                    session.run(constraint)
                    logger.debug(f"Created constraint: {constraint}")
                except Exception as e:
                    logger.warning(f"Constraint might already exist: {e}")

            for index in indexes:
                try:
                    session.run(index)
                    logger.debug(f"Created index: {index}")
                except Exception as e:
                    logger.warning(f"Index might already exist: {e}")

    def upload_coverage_data(
        self,
        coverage_root: LLVMCoverageRoot,
        test_case_name: str,
        **extra_metadata,
    ):
        """Upload a complete coverage dataset for a test case.

        Args:
            coverage_root (LLVMCoverageRoot): The LLVM coverage data
            test_case_name (str): Unique identifier for the test case
            test_case_name (str): Display name for the test case (optional)
            **metadata: Additional metadata to store with the test case"""
        with self.driver.session() as session:
            # Create test case node
            metadata_props = {
                "name": test_case_name,
                "version": coverage_root.version,
                "type": coverage_root.type,
                "upload_timestamp": "datetime()",
                **extra_metadata,
            }

            # Build SET clause dynamically
            set_clauses = []
            params = {"name": test_case_name}

            for key, value in metadata_props.items():
                if key == "name":
                    continue
                if value == "datetime()":
                    set_clauses.append(f"t.{key} = datetime()")
                else:
                    param_key = f"meta_{key}"
                    set_clauses.append(f"t.{key} = ${param_key}")
                    params[param_key] = value

            query = f"""
                MERGE (t:TestCase {{name: $name}})
                SET {", ".join(set_clauses)}
                """

            session.run(query, **params)

            # Process each export (usually one per test case)
            for export_idx, export in enumerate(coverage_root.data):
                self._upload_export(session, export, test_case_name, export_idx)

    def _upload_export(
        self, session, export: Export, test_case_name: str, export_idx: int
    ):
        """Upload a single export's data."""
        # Upload totals summary
        self._upload_totals(session, export.totals, test_case_name)

        # Upload files
        for file in export.files:
            self._upload_file(session, file, test_case_name)

        # Upload functions
        for function in export.functions:
            self._upload_function(session, function, test_case_name)

    def _upload_totals(self, session, totals: Summary, test_case_name: str):
        """Upload total coverage summary."""
        session.run(
            """
            MATCH (t:TestCase {name: $test_case_name})
            SET t.total_lines = $total_lines,
                t.covered_lines = $covered_lines,
                t.line_coverage_percent = $line_percent,
                t.total_functions = $total_functions,
                t.covered_functions = $covered_functions,
                t.function_coverage_percent = $function_percent,
                t.total_regions = $total_regions,
                t.covered_regions = $covered_regions,
                t.region_coverage_percent = $region_percent
            """,
            test_case_name=test_case_name,
            total_lines=totals.lines.count,
            covered_lines=totals.lines.covered,
            line_percent=totals.lines.percent,
            total_functions=totals.functions.count,
            covered_functions=totals.functions.covered,
            function_percent=totals.functions.percent,
            total_regions=totals.regions.count,
            covered_regions=totals.regions.covered,
            region_percent=totals.regions.percent,
        )

        # Add branch coverage if available
        if totals.branches:
            session.run(
                """
                MATCH (t:TestCase {name: $test_case_name})
                SET t.total_branches = $total_branches,
                    t.covered_branches = $covered_branches,
                    t.branch_coverage_percent = $branch_percent
            """,
                test_case_name=test_case_name,
                total_branches=totals.branches.count,
                covered_branches=totals.branches.covered,
                branch_percent=totals.branches.percent,
            )

    def _upload_file(
        self,
        session,
        file: File,
        test_case_name: str,
        include_segments: bool = False,
        include_branches: bool = False,
    ):
        """Upload file coverage data.

        Raises:
          NotImplementedError: Raised when this operation fails."""
        # Create file node and relationship to test case
        session.run(
            """
            MATCH (t:TestCase {name: $test_case_name})
            MERGE (f:File {path: $file_path})
            MERGE (t)-[:COVERS_FILE]->(f)
            SET f.line_coverage_count = $line_count,
                f.line_coverage_covered = $line_covered,
                f.line_coverage_percent = $line_percent,
                f.function_coverage_count = $func_count,
                f.function_coverage_covered = $func_covered,
                f.function_coverage_percent = $func_percent
            """,
            test_case_name=test_case_name,
            file_path=file.filename,
            line_count=file.summary.lines.count,
            line_covered=file.summary.lines.covered,
            line_percent=file.summary.lines.percent,
            func_count=file.summary.functions.count,
            func_covered=file.summary.functions.covered,
            func_percent=file.summary.functions.percent,
        )

        # Upload segments (line coverage)
        if include_segments:
            for segment in file.segments:
                self._upload_segment(session, segment, file.filename, test_case_name)

        # Upload branches
        if include_branches:
            raise NotImplementedError("Branch from File upload not implemented yet.")

    def _upload_segment(
        self, session, segment: CoverageSegment, file_path: str, test_case_name: str
    ):
        """Upload coverage segment (line coverage information)."""
        session.run(
            """
            MATCH (t:TestCase {name: $test_case_name})
            MATCH (f:File {path: $file_path})
            MERGE (s:Segment {file_path: $file_path, line_number: $line_number, column: $column})
            MERGE (f)-[:HAS_SEGMENT]->(s)
            MERGE (t)-[c:COVERS_SEGMENT]->(s)
            SET c.execution_count = $count,
                s.execution_count = $count,
                s.has_count = $has_count,
                s.is_region_entry = $is_region_entry,
                s.is_gap_region = $is_gap_region
            """,
            test_case_name=test_case_name,
            file_path=file_path,
            line_number=segment.line,
            column=segment.col,
            count=segment.count,
            has_count=segment.has_count,
            is_region_entry=segment.is_region_entry,
            is_gap_region=segment.is_gap_region,
        )

    @staticmethod
    def _demangle_function_name(func_name: str) -> str:
        """Demangle a function name."""
        if ":" in func_name:
            file_name, func_name = func_name.split(":")
            func_name = cxxfilt.demangle(func_name)
            return f"{file_name}:{func_name}"
        return cxxfilt.demangle(func_name)

    def _upload_function(self, session, function: Function, test_case_name: str):
        """Upload function coverage data."""
        # Determine primary file for the function (first in filenames list)
        primary_file = function.filenames[0] if function.filenames else "unknown"

        # Create function node
        session.run(
            """
            MATCH (t:TestCase {name: $test_case_name})
            MERGE (fn:Function {name: $func_name, file_path: $primary_file})
            MERGE (t)-[c:COVERS_FUNCTION]->(fn)
            SET fn.execution_count = $count,
                fn.all_files = $all_files,
                fn.demangled_name = $demangled_name,
                c.execution_count = $count
            """,
            test_case_name=test_case_name,
            func_name=function.name,
            primary_file=primary_file,
            count=function.count,
            all_files=function.filenames,
            demangled_name=self._demangle_function_name(function.name),
        )

        # Link function to files
        # Including function definition, declaration, macros
        # Assume the primary file is the definition
        session.run(
            """
            MATCH (fn: Function {name: $func_name, file_path: $primary_file})
            MERGE (f: File {path: $primary_file})
            MERGE (f)-[:CONTAINS_FUNCTION]->(fn)
            """,
            func_name=function.name,
            primary_file=primary_file,
        )

        for file_path in function.filenames:
            session.run(
                """
                MATCH (fn:Function {name: $func_name, file_path: $primary_file})
                MERGE (f:File {path: $file_path})
                MERGE (f)-[:RELATES_TO]->(fn)
                MERGE (fn)-[:RELATES_TO]->(f)
                """,
                func_name=function.name,
                primary_file=primary_file,
                file_path=file_path,
            )

        # Upload regions for this function
        for region_idx, region in enumerate(function.regions):
            self._upload_region(
                session, region, region_idx, function.name, primary_file, test_case_name
            )

        # Upload branches for this function
        for branch_idx, branch in enumerate(function.branches):
            self._upload_branch(
                session, branch, branch_idx, function.name, primary_file, test_case_name
            )

    def _upload_region(
        self,
        session,
        region: CountedRegion,
        region_idx: int,
        func_name: str,
        file_path: str,
        test_case_name: str,
    ):
        """Upload region coverage data."""
        region_id = f"{test_case_name}|{file_path}|{func_name}|region-{region_idx}"

        session.run(
            """
            MATCH (t:TestCase {name: $test_case_name})
            MATCH (fn:Function {name: $func_name, file_path: $file_path})
            MERGE (r:Region {id: $region_id})
            MERGE (t)-[c:COVERS_REGION]->(r)
            MERGE (fn)-[:HAS_REGION]->(r)
            SET c.execution_count = $execution_count,
                r.line_start = $line_start,
                r.column_start = $column_start,
                r.line_end = $line_end,
                r.column_end = $column_end,
                r.execution_count = $execution_count,
                r.file_id = $file_id,
                r.expanded_file_id = $expanded_file_id,
                r.kind = $kind
            """,
            test_case_name=test_case_name,
            func_name=func_name,
            file_path=file_path,
            region_id=region_id,
            line_start=region.line_start,
            column_start=region.column_start,
            line_end=region.line_end,
            column_end=region.column_end,
            execution_count=region.execution_count,
            file_id=region.file_id,
            expanded_file_id=region.expanded_file_id,
            kind=region.kind,
        )

    def _upload_branch(
        self,
        session,
        branch: Branch,
        branch_idx: int,
        func_name: str,
        file_path: str,
        test_case_name: str,
    ):
        """Upload branch coverage data."""
        branch_id = f"{test_case_name}|{file_path}|{func_name}|branch-{branch_idx}"

        session.run(
            """
            MATCH (t:TestCase {name: $test_case_name})
            MATCH (fn:Function {name: $func_name, file_path: $file_path})
            MERGE (b:Branch {id: $branch_id})
            MERGE (t)-[c:COVERS_BRANCH]->(b)
            MERGE (fn)-[:HAS_BRANCH]->(b)
            SET c.execution_count = $execution_count,
                b.line_start = $line_start,
                b.column_start = $column_start,
                b.line_end = $line_end,
                b.column_end = $column_end,
                b.execution_count = $execution_count,
                b.false_execution_count = $false_execution_count,
                b.file_id = $file_id,
                b.expanded_file_id = $expanded_file_id,
                b.kind = $kind
            """,
            test_case_name=test_case_name,
            func_name=func_name,
            file_path=file_path,
            branch_id=branch_id,
            line_start=branch.line_start,
            column_start=branch.column_start,
            line_end=branch.line_end,
            column_end=branch.column_end,
            execution_count=branch.execution_count,
            false_execution_count=branch.false_execution_count,
            file_id=branch.file_id,
            expanded_file_id=branch.expanded_file_id,
            kind=branch.kind,
        )


# Usage example and query helpers
class CoverageQueryHelper:
    """Helper class for common coverage queries."""

    def __init__(self, uri: str, username: str, password: str):
        """Initialize Neo4j connection."""
        self.driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self):
        self.driver.close()

    def find_tests_covering_function(
        self, func_name: str, file_path: str
    ) -> list[dict]:
        """Find all test cases that cover a specific function."""
        query = """
            MATCH (t:TestCase)-[c:COVERS_FUNCTION]->(fn:Function {file_path: $file_path})
            WHERE fn.demangled_name =~ $name_pattern
            RETURN t.name as test_case, c.execution_count as execution_count,
                t.line_coverage_percent as test_line_coverage
            ORDER BY c.execution_count DESC
            """

        with self.driver.session() as session:
            result = session.run(
                query, file_path=file_path, name_pattern=f".*{func_name}.*"
            )
            return [dict(record) for record in result]

    def find_functions_covered_by_test(self, test_case_name: str) -> list[dict]:
        """Find all functions covered by a specific test case."""
        query = """
            MATCH (t:TestCase {name: $test_case_name})-[c:COVERS_FUNCTION]->(fn:Function)
            RETURN fn.name as function_name, fn.file_path as file_path,
                c.execution_count as execution_count
            ORDER BY c.execution_count DESC
            """
        with self.driver.session() as session:
            result = session.run(query, test_case_name=test_case_name)
            return [dict(record) for record in result]
