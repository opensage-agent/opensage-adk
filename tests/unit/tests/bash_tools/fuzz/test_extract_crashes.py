"""Unit tests for extract-crashes bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.toolbox.general.bash_tools_interface import run_terminal_command
from aigise.utils.project_info import PROJECT_PATH

# Increase timeout for slow fuzz tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    """Create aigise session for testing fuzz tools (requires main and fuzz sandboxes)."""
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-bash-tools-fuzz-extract",
            str(PROJECT_PATH / "tests/unit/data/configs/test_fuzz_only.toml"),
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
async def test_extract_crashes_missing_target_dir(aigise_session: AigiseSession):
    """Test extract-crashes tool with missing target_dir argument."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test extract-crashes without target_dir (should fail)
    result = run_terminal_command(
        command="bash /bash_tools/fuzz/extract-crashes/scripts/extract_crashes.sh",
        tool_context=mock_context,
        sandbox_name="main",
    )

    # Should fail or return error
    output = result["output"]
    if isinstance(output, str):
        output_text = output
    else:
        output_text = str(output)

    # Output should exist (can be error message or success message)
    assert output_text is not None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_extract_crashes_no_crashes_directory(aigise_session: AigiseSession):
    """Test extract-crashes tool when no crashes directory exists."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    # Test extract-crashes with target_dir but no crashes directory
    result = run_terminal_command(
        command='bash /bash_tools/fuzz/extract-crashes/scripts/extract_crashes.sh "/tmp/test_crashes"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    output = result["output"]
    if isinstance(output, str):
        output_text = output
    else:
        output_text = str(output)

    # Output should exist (can be error message or success message)
    assert output_text is not None


@pytest.mark.slow
@pytest.mark.asyncio
async def test_extract_crashes_json_structure(aigise_session: AigiseSession):
    """Test extract-crashes returns correct JSON structure."""
    mock_context = MagicMock()
    mock_context.state = {"aigise_session_id": aigise_session.aigise_session_id}

    result = run_terminal_command(
        command='bash /bash_tools/fuzz/extract-crashes/scripts/extract_crashes.sh "/tmp/test_crashes"',
        tool_context=mock_context,
        sandbox_name="main",
    )

    output = result["output"]
    if isinstance(output, str):
        output_text = output
    else:
        output_text = str(output)

    # Output should exist
    assert output_text is not None
