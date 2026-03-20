from re import search
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from opensage.session import OpenSageSession, get_opensage_session
from opensage.session.neo4j_client import AsyncNeo4jClient
from opensage.toolbox.static_analysis.cpg import (
    get_callee,
    get_caller,
    search_function,
)
from opensage.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import fix_neo4j_client

# Increase timeout for slow CPG tests
pytestmark = pytest.mark.timeout(1200)


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            "test-session", str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml")
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
async def test_cpg_initialization(opensage_session: OpenSageSession):
    neo4j_client = fix_neo4j_client(opensage_session, "analysis")

    cpg_nodes = await neo4j_client.run_query("MATCH (n) RETURN count(n) AS count")
    count = cpg_nodes[0]["count"]
    assert count > 1000  # Expecting more than 1000 nodes in the CPG


@pytest.mark.slow
@pytest.mark.asyncio
async def test_cpg_search_function(opensage_session: OpenSageSession):
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    res = await search_function("file_fsmagic", tool_context=mock_context)

    # 'file_path': 'file/src/fsmagic.c', 'start_line': 105, 'end_line': 435
    assert len(res["result"]) == 1
    assert res["result"][0]["function_name"] == "file_fsmagic"
    assert res["result"][0]["file_path"] == "file/src/fsmagic.c"
    assert res["result"][0]["start_line"] == 105
    assert res["result"][0]["end_line"] == 435

    res = await search_function("non_existing_function", tool_context=mock_context)
    assert len(res["result"]) == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_cpg_get_caller(opensage_session: OpenSageSession):
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    res = await get_caller("file_fsmagic", None, tool_context=mock_context)
    assert len(res["result"]) == 1
    assert res["result"][0]["function_name"] == "file_or_fd"

    res = await get_caller(
        "file_fsmagic", "file/src/fsmagic.c", tool_context=mock_context
    )
    assert len(res["result"]) == 1
    assert res["result"][0]["function_name"] == "file_or_fd"

    res = await get_caller("non_existing_function", None, tool_context=mock_context)
    assert len(res["result"]) == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_cpg_get_callee(opensage_session: OpenSageSession):
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}
    fix_neo4j_client(opensage_session, "analysis")

    res = await get_callee("file_or_fd", None, tool_context=mock_context)
    assert len(res["result"]) == 25
    callee_names = [entry["function_name"] for entry in res["result"]]
    assert "file_fsmagic" in callee_names

    res = await get_callee("file_or_fd", "src/magic.c", tool_context=mock_context)
    assert len(res["result"]) == 25
    callee_names = [entry["function_name"] for entry in res["result"]]
    assert "file_fsmagic" in callee_names

    res = await get_callee("non_existing_function", None, tool_context=mock_context)
    assert len(res["result"]) == 0


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    import sys

    pytest.main([__file__] + sys.argv[1:])
