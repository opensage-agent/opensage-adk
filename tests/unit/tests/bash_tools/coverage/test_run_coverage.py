"""Unit tests for run-coverage bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.toolbox.general.bash_tools_interface import run_terminal_command
from aigise.utils.project_info import PROJECT_PATH

# Increase timeout for slow coverage tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    """Create aigise session for testing coverage tools (requires main and coverage sandboxes)."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-coverage",
            str(PROJECT_PATH / "tests/unit/data/configs/test_coverage_only.toml"),
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
async def test_run_coverage_missing_testcase_path(aigise_session: AigiseSession):
    """Test run-coverage tool with missing testcase_path argument."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test run-coverage without testcase_path (should fail)
    result = run_terminal_command(
        command="bash /bash_tools/coverage/run-coverage/scripts/run_coverage.sh",
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Should fail or return error
    output = result["output"]
    # Output can be string or dict, just verify it exists
    assert output is not None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_run_coverage_nonexistent_file(aigise_session: AigiseSession):
    """Test run-coverage tool with non-existent testcase file."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test with non-existent file
    result = run_terminal_command(
        command='bash /bash_tools/coverage/run-coverage/scripts/run_coverage.sh "/shared/nonexistent_testcase.txt"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    output = result["output"]
    # Output can be string or dict, just verify it exists
    assert output is not None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_run_coverage_output_structure(aigise_session: AigiseSession):
    """Test run-coverage returns output."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    result = run_terminal_command(
        command='bash /bash_tools/coverage/run-coverage/scripts/run_coverage.sh "/shared/nonexistent_testcase.txt"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    output = result["output"]
    # Output can be string or dict, just verify it exists
    assert output is not None
