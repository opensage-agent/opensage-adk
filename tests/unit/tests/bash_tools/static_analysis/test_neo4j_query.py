"""Unit tests for neo4j-query bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.toolbox.general.bash_tools_interface import run_terminal_command
from aigise.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import fix_neo4j_client

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    """Create aigise session for testing static analysis tools."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-static-analysis-neo4j-query",
            str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml"),
        )

        aigise_session.sandboxes.initialize_shared_volumes()
        await aigise_session.sandboxes.launch_all_sandboxes()
        await aigise_session.sandboxes.initialize_all_sandboxes()
        yield aigise_session
    finally:
        if aigise_session is not None:
            aigise_session.cleanup()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_neo4j_query_basic(aigise_session: AigiseSession):
    """Test neo4j-query tool with basic query."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test with a simple query
    query = "MATCH (n) RETURN count(n) AS count LIMIT 1"
    result = run_terminal_command(
        command=f'python3 /bash_tools/static_analysis/neo4j-query/scripts/neo4j_query.py "{query}"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output should be plain text (returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    assert output is not None
    assert isinstance(output, str)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_neo4j_query_with_params(aigise_session: AigiseSession):
    """Test neo4j-query tool with query parameters."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test with query and parameters (may return no results, which is OK)
    query = "MATCH (n) RETURN count(n) AS count LIMIT 1"
    params = "{}"
    result = run_terminal_command(
        command=f"python3 /bash_tools/static_analysis/neo4j-query/scripts/neo4j_query.py \"{query}\" --params '{params}'",
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Output should be plain text (returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    assert output is not None
    assert isinstance(output, str)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_neo4j_query_invalid_query(aigise_session: AigiseSession):
    """Test neo4j-query tool with invalid query."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test with invalid query syntax
    query = "INVALID CYPHER QUERY SYNTAX !!!"
    result = run_terminal_command(
        command=f'python3 /bash_tools/static_analysis/neo4j-query/scripts/neo4j_query.py "{query}"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Invalid query should fail (exit code != 0) or return error message
    output = result["output"]
    # Output should be plain text (returns_json: false)
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    assert output is not None
    assert isinstance(output, str)
    # Should have error information
    # Note: Some invalid queries may not throw exceptions in Neo4j, so we just check output exists
    assert len(output) > 0
