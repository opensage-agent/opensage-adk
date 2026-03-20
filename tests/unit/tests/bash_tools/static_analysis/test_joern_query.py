"""Unit tests for joern-query bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing static analysis tools."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-static-analysis-joern-query",
            str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml"),
        )

        opensage_session.sandboxes.initialize_shared_volumes()
        await opensage_session.sandboxes.launch_all_sandboxes()
        await opensage_session.sandboxes.initialize_all_sandboxes()
        yield opensage_session
    finally:
        if opensage_session is not None:
            opensage_session.cleanup()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_joern_query_basic(opensage_session: OpenSageSession):
    """Test joern-query tool with basic query."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Test with a simple Joern query
    query = 'cpg.method.name(".*").l'
    result = run_terminal_command(
        command=f'python3 /bash_tools/static_analysis/joern-query/scripts/joern_query.py "{query}"',
        tool_context=mock_context,
        sandbox_name="joern",
    )

    # Debug: print actual result
    print(f"\nDEBUG: result = {result}")
    print(f"DEBUG: exit_code = {result.get('exit_code')}")
    print(f"DEBUG: success = {result.get('success')}")
    print(f"DEBUG: output type = {type(result.get('output'))}")
    print(f"DEBUG: output = {repr(result.get('output')[:200])}")

    # Check exit code first
    assert result["exit_code"] == 0, f"Expected exit_code=0, got {result['exit_code']}"
    assert result["success"] is True, (
        f"Expected success=True, got {result['success']}, exit_code={result['exit_code']}"
    )

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
async def test_joern_query_complex(opensage_session: OpenSageSession):
    """Test joern-query tool with complex query."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Test with a more complex query
    query = 'cpg.method.name("file_fsmagic").l'
    result = run_terminal_command(
        command=f'python3 /bash_tools/static_analysis/joern-query/scripts/joern_query.py "{query}"',
        tool_context=mock_context,
        sandbox_name="joern",
    )

    # Debug: print actual result
    print(f"\nDEBUG: result = {result}")
    print(f"DEBUG: exit_code = {result.get('exit_code')}")
    print(f"DEBUG: success = {result.get('success')}")
    print(f"DEBUG: output type = {type(result.get('output'))}")
    print(f"DEBUG: output = {repr(result.get('output')[:200])}")

    # Check exit code first
    assert result["exit_code"] == 0, f"Expected exit_code=0, got {result['exit_code']}"
    assert result["success"] is True, (
        f"Expected success=True, got {result['success']}, exit_code={result['exit_code']}"
    )

    # Output should be plain text (returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    assert output is not None
    assert isinstance(output, str)
