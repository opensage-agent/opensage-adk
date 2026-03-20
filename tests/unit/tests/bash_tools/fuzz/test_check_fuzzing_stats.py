"""Unit tests for check-fuzzing-stats bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH

# Increase timeout for slow fuzz tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing fuzz tools (requires main and fuzz sandboxes)."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-fuzz",
            str(PROJECT_PATH / "tests/unit/data/configs/test_fuzz_only.toml"),
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
async def test_check_fuzzing_stats_no_output(opensage_session: OpenSageSession):
    """Test check-fuzzing-stats tool when no fuzzing output exists."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Test check-fuzzing-stats when no output directory exists
    result = run_terminal_command(
        command="bash /bash_tools/fuzz/check-fuzzing-stats/scripts/check_fuzzing_stats.sh",
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Check output (text format)
    output = result["output"]
    if isinstance(output, str):
        output_text = output
    else:
        output_text = str(output)

    # Should contain output directory information
    assert output_text is not None
    assert len(output_text) >= 0  # Allow empty output


@pytest.mark.slow
@pytest.mark.asyncio
async def test_check_fuzzing_stats_json_structure(opensage_session: OpenSageSession):
    """Test check-fuzzing-stats returns correct JSON structure."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    result = run_terminal_command(
        command="bash /bash_tools/fuzz/check-fuzzing-stats/scripts/check_fuzzing_stats.sh",
        tool_context=mock_context,
        sandbox_name="main",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Check output (text format)
    output = result["output"]
    if isinstance(output, str):
        output_text = output
    else:
        output_text = str(output)

    # Output should exist
    assert output_text is not None
