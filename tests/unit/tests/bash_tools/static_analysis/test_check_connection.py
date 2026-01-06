"""Unit tests for check-connection bash tool."""

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
            "test-bash-tools-static-analysis-check-connection",
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
async def test_check_connection_basic(aigise_session: AigiseSession):
    """Test check-connection tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}
    fix_neo4j_client(aigise_session, "analysis")

    # Test check-connection (should use env vars from ~/.bashrc)
    result = run_terminal_command(
        command="python3 /bash_tools/static_analysis/check-connection/scripts/check_connection.py",
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Command should execute successfully (exit_code == 0)
    assert result["success"] is True, (
        f"Command execution failed. Exit code: {result.get('exit_code')}, "
        f"Output: {result.get('output')}"
    )
    assert result["exit_code"] == 0

    # Check output (should be text indicating connection success)
    output = result["output"]

    # Convert to string if it's a dict (shouldn't happen with returns_json: false, but handle it)
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    # Check if output indicates successful connection
    output_lower = output.lower()
    assert "connection successful" in output_lower, (
        f"Neo4j connection failed or output does not indicate success. Output: {output}"
    )
