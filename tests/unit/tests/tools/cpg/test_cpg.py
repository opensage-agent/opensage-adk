import pytest
import pytest_asyncio

from aigise.session import AigiseSession, get_aigise_session
from aigise.session.neo4j_client import AsyncNeo4jClient
from aigise.utils.project_info import PROJECT_PATH


@pytest_asyncio.fixture(scope="module")
async def aigise_session():
    aigise_session = None
    try:
        aigise_session = get_aigise_session(
            "test-session", str(PROJECT_PATH / "tests/unit/data/configs/test_cpg.toml")
        )

        aigise_session.sandboxes.initialize_shared_volumes()
        await aigise_session.sandboxes.launch_all_sandboxes()
        yield aigise_session
    finally:
        if aigise_session is not None:
            aigise_session.cleanup()
        # TODO: remove the shared volume and the neo4j sandbox


@pytest.mark.slow
@pytest.mark.asyncio
async def test_cpg_initialization(aigise_session: AigiseSession):
    await aigise_session.sandboxes.wait_for_ready("codeql")
    await aigise_session.sandboxes.wait_for_ready("joern")
    await aigise_session.sandboxes.wait_for_ready("neo4j")

    neo4j_client = AsyncNeo4jClient(
        aigise_session.config.neo4j.uri,
        aigise_session.config.neo4j.user,
        aigise_session.config.neo4j.password,
        database=aigise_session.neo4j._get_database_name_for_type("analysis"),
    )

    cpg_nodes = await neo4j_client.run_query("MATCH (n) RETURN count(n) AS count")
    count = cpg_nodes[0]["count"]
    assert count > 1000  # Expecting more than 1000 nodes in the CPG


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    import sys

    pytest.main([__file__] + sys.argv[1:])
