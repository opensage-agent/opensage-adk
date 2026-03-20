"""Unit tests for get-call-paths-to-function bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import fix_neo4j_client

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing static analysis tools."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-static-analysis-get-call-paths",
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
async def test_get_call_paths_basic(opensage_session: OpenSageSession):
    """Test get-call-paths-to-function tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test get-call-paths for a known function (using default source function)
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/get-call-paths-to-function/scripts/get_call_paths_to_function.py "file_fsmagic"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Check output (should be plain text since returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    # Output should be plain text
    assert output is not None
    assert isinstance(output, str)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_call_paths_with_source_function(opensage_session: OpenSageSession):
    """Test get-call-paths-to-function tool with source function parameter."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    # Test with source function specified
    result = run_terminal_command(
        command='python3 /bash_tools/static_analysis/get-call-paths-to-function/scripts/get_call_paths_to_function.py "file_fsmagic" --src-function "file_or_fd"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Check output (should be plain text since returns_json: false)
    output = result["output"]
    if isinstance(output, dict):
        output = str(output)
    elif not isinstance(output, str):
        output = str(output) if output is not None else ""

    # Output should be plain text
    assert output is not None
    assert isinstance(output, str)
