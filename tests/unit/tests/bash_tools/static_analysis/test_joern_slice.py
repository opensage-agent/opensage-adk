"""Unit tests for joern-slice bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.toolbox.general.bash_tools_interface import run_terminal_command
from aigise.utils.project_info import PROJECT_PATH

# Increase timeout for slow static analysis tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    """Create aigise session for testing static analysis tools."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-static-analysis-joern-slice",
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
async def test_joern_slice_basic(aigise_session: AigiseSession):
    """Test joern-slice tool with basic parameters."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test joern-slice with function name
    result = run_terminal_command(
        command='bash /bash_tools/static_analysis/joern-slice/scripts/joern_slice.sh "file_fsmagic"',
        tool_context=mock_context,
        sandbox_name="joern",
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
    # Output should contain slice information (check for text indicating results)
    assert "File:" in output or "Lines:" in output or "No slices found" in output


@pytest.mark.slow
@pytest.mark.asyncio
async def test_joern_slice_with_file_path(aigise_session: AigiseSession):
    """Test joern-slice tool with file path parameter."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test with file path
    result = run_terminal_command(
        command='bash /bash_tools/static_analysis/joern-slice/scripts/joern_slice.sh "file_fsmagic" --file-path "file/src/fsmagic.c"',
        tool_context=mock_context,
        sandbox_name="joern",
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
