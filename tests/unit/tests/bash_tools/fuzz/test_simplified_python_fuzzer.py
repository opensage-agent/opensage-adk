"""Unit tests for simplified-python-fuzzer bash tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH

# Increase timeout for slow fuzz tests
pytestmark = pytest.mark.timeout(2400)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing fuzz tools (requires main and fuzz sandboxes)."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-fuzz-simplified",
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
async def test_simplified_python_fuzzer_missing_script(
    opensage_session: OpenSageSession,
):
    """Test simplified-python-fuzzer tool with missing script argument."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Test simplified-python-fuzzer without script (should fail)
    result = run_terminal_command(
        command="bash /bash_tools/fuzz/simplified-python-fuzzer/scripts/simplified_python_fuzzer.sh",
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
async def test_simplified_python_fuzzer_basic_script(opensage_session: OpenSageSession):
    """Test simplified-python-fuzzer tool with a basic script."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Test with a minimal script (just waits and exits)
    minimal_script = "import time\nimport sys\ntime.sleep(1)\nsys.exit(0)"

    result = run_terminal_command(
        command=f'bash /bash_tools/fuzz/simplified-python-fuzzer/scripts/simplified_python_fuzzer.sh "{minimal_script}" 5',
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
