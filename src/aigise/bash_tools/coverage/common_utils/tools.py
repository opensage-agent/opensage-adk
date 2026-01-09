import logging
import os
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from aigise.bash_tools.coverage.python_modules.llvm_cov import parse_llvm_coverage_json
from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.neo4j_client import AsyncNeo4jClient
from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import (
    get_aigise_config_from_context,
    get_neo4j_client_from_context,
    get_sandbox_from_context,
)

logger = logging.getLogger(__name__)


def get_testcase_storage_dir(testcase_id: str) -> str:
    """
    Get the storage path for a testcase based on its ID.

    Args:
        testcase_id (str): The ID of the testcase.
    Returns:
        str: The storage path for the testcase.
    """
    return (
        f"/shared/.aigise/coverage/{testcase_id[:2]}/{testcase_id[2:4]}/{testcase_id}"
    )


def save_testcase(sandbox: BaseSandbox, testcase_path: str) -> tuple[str, str]:
    """
    Save the testcase to the sandbox environment.

    Args:
        sandbox (BaseSandbox): The sandbox environment where the testcase will be saved.
        testcase_path (str): The path to the testcase file.

    Returns:
        str: The path to the saved testcase within the sandbox.
    """
    assert testcase_path.startswith("/shared")

    md5_hash, _ = sandbox.run_command_in_container(
        f"md5sum {testcase_path} | awk '{{ print $1 }}'"
    )
    md5_hash = md5_hash.strip()
    assert len(md5_hash) == 32, f"Invalid md5 hash: {md5_hash}"

    dst_dir = get_testcase_storage_dir(md5_hash)
    dst_path = os.path.join(dst_dir, "testcase")
    sandbox.run_command_in_container(
        f"mkdir -p {dst_dir} && cp {testcase_path} {dst_path}"
    )

    return md5_hash, dst_dir


async def upload_testcase_to_database(
    sandbox: BaseSandbox, testcase_id: str, neo4j_client: AsyncNeo4jClient
):
    """
    Upload the testcase information to the Neo4j database.

    Args:
        sandbox (BaseSandbox): The sandbox environment where the testcase is stored.
        testcase_id (str): The ID of the testcase.
        neo4j_client (AsyncNeo4jClient): The Neo4j client to interact with the database.
    """
    testcase_cov_json = os.path.join(
        get_testcase_storage_dir(testcase_id), "testcase.json"
    )

    cov_data = sandbox.extract_file_from_container_bytes(testcase_cov_json)
    if not cov_data:
        logger.error(f"Failed to extract coverage data for testcase {testcase_id}")
        return

    cov = parse_llvm_coverage_json(cov_data)

    for func in cov.data[0].functions:
        # match function in existing database
        result = await neo4j_client.run_query(
            "MATCH (m:METHOD) WHERE m.NAME = $name "
            "AND (m.FILENAME CONTAINS $filepath OR $filepath CONTAINS m.FILENAME) "
            "RETURN m.id",
            {"name": func.name.split(":")[-1], "filepath": func.filenames[0]},
        )
        if not result:
            logger.error(
                f"Function {func.name} in file {func.filenames[0]} not found in database"
            )
            continue
        if len(result) > 1:
            logger.error(
                f"Multiple functions found for {func.name} in file {func.filenames[0]}"
            )
            continue
        method_id = result[0]["m.id"]
        await neo4j_client.run_query(
            "MATCH (m:METHOD {id: $method_id}) "
            "MERGE (t:TESTCASE {id: $testcase_id}) "
            "MERGE (t)-[c:COVERS]->(m) "
            "SET c.count = $count",
            {"testcase_id": testcase_id, "method_id": method_id, "count": func.count},
        )


@safe_tool_execution
@requires_sandbox("coverage", "neo4j", "joern")
async def run_coverage(testcase_path: str, *, tool_context: ToolContext) -> dict:
    """
    DEPRECATED: Use the bash script `run_coverage.sh` instead.
    This function processes the coverage data and uploads it to Neo4j.
    It assumes the bash script has already generated the coverage report and JSON.
    """
    # Logic to upload testcase to database ONLY.
    # The agent should use the bash script to execute coverage.
    # But if the agent uses the bash script, how does it trigger the upload?
    # This remains a question. For now, I'm keeping this function but modifying it?
    # Or just removing it as per user request to "remove functionality"?
    # I will strip it down to just the upload part, maybe renaming it to `upload_coverage`?
    # The user instruction was "tool.py remove functions that can be replaced".
    # `run_coverage` (execution part) IS replaced.
    # `upload_testcase_to_database` helps upload.
    # I'll leave `upload_testcase_to_database` as a helper.
    # I'll remove the tools and if the agent needs upload, we'll need a new tool.
    pass


@safe_tool_execution
@requires_sandbox("coverage", "neo4j", "joern")
async def find_testcases_covering_function(
    function_name: str, file_path: Optional[str], *, tool_context: ToolContext
) -> dict:
    """
    Tool to find testcases that cover a specified function in the codebase.

    Args:
        function_name (str): The name of the function to search for.
        file_path (Optional[str]): The absolute path to the file of the function. This can be empty,
            in which case it will match all functions with the same name.

    Returns:
        dict: A dictionary containing a list of testcase IDs that cover the specified function.
    """
    neo4j_client = await get_neo4j_client_from_context(tool_context, "analysis")

    query = "MATCH (t:TESTCASE)-[c:COVERS]->(m:METHOD) WHERE m.NAME = $name "
    if file_path:
        query += "AND (m.FILENAME CONTAINS $filepath OR $filepath CONTAINS m.FILENAME) "
    query += "RETURN t.id AS testcase_id"

    params = {"name": function_name}
    if file_path:
        params["filepath"] = file_path

    results = await neo4j_client.run_query(query, params)

    testcase_ids = [record["testcase_id"] for record in results]

    return {"testcase_ids": testcase_ids}
