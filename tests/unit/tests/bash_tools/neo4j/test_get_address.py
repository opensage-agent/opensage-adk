"""Unit tests for get-neo4j-address bash tool."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.toolbox.general.bash_tools_interface import run_terminal_command
from opensage.utils.project_info import PROJECT_PATH

# Increase timeout for slow tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    """Create opensage session for testing neo4j tools (requires main and neo4j sandboxes)."""
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-bash-tools-neo4j",
            str(PROJECT_PATH / "tests/unit/data/configs/test_neo4j_only.toml"),
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
async def test_get_neo4j_address_basic(opensage_session: OpenSageSession):
    """Test get-neo4j-address tool returns valid IP address."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Test get-neo4j-address
    result = run_terminal_command(
        command="bash /bash_tools/neo4j/get-address/scripts/get_neo4j_address.sh",
        tool_context=mock_context,
        sandbox_name="neo4j",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    # Parse JSON output
    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    assert "result" in output
    assert isinstance(output["result"], str)
    # Should not be empty
    assert len(output["result"]) > 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_neo4j_address_ip_format(opensage_session: OpenSageSession):
    """Test get-neo4j-address returns valid IPv4 format."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    result = run_terminal_command(
        command="bash /bash_tools/neo4j/get-address/scripts/get_neo4j_address.sh",
        tool_context=mock_context,
        sandbox_name="neo4j",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    ip_address = output["result"]
    # Validate IPv4 format (e.g., 172.17.0.3)
    ipv4_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
    assert re.match(ipv4_pattern, ip_address), (
        f"IP address '{ip_address}' is not in valid IPv4 format"
    )

    # Verify each octet is between 0-255
    octets = ip_address.split(".")
    assert len(octets) == 4
    for octet in octets:
        assert 0 <= int(octet) <= 255, f"Octet '{octet}' is not in valid range [0-255]"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_neo4j_address_json_structure(opensage_session: OpenSageSession):
    """Test get-neo4j-address returns correct JSON structure."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    result = run_terminal_command(
        command="bash /bash_tools/neo4j/get-address/scripts/get_neo4j_address.sh",
        tool_context=mock_context,
        sandbox_name="neo4j",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0

    output = result["output"]
    if isinstance(output, str):
        output = json.loads(output)

    # Verify JSON structure
    assert isinstance(output, dict)
    assert "result" in output
    assert isinstance(output["result"], str)
    # Should not have error key when successful
    assert "error" not in output


@pytest.mark.slow
@pytest.mark.asyncio
async def test_get_neo4j_address_consistency(opensage_session: OpenSageSession):
    """Test get-neo4j-address returns consistent IP address across multiple calls."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Call the tool multiple times
    results = []
    for _ in range(3):
        result = run_terminal_command(
            command="bash /bash_tools/neo4j/get-address/scripts/get_neo4j_address.sh",
            tool_context=mock_context,
            sandbox_name="neo4j",
        )
        assert result["success"] is True
        assert result["exit_code"] == 0

        output = result["output"]
        if isinstance(output, str):
            output = json.loads(output)
        results.append(output["result"])

    # All calls should return the same IP address
    assert len(set(results)) == 1, f"IP address should be consistent, got: {results}"
