import logging
import os
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.session.neo4j_client import AsyncNeo4jClient
from aigise.toolbox.coverage.llvm_cov import parse_llvm_coverage_json
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
    Tool to run code coverage analysis on a specified file within the sandbox environment.
    The testcase_path should be under the /shared directory, if it's not, you should first copy it to the /shared directory.

    Args:
        file_path (str): The absoluate path to the file for which to run coverage analysis.

    Returns:
        dict: A dictionary containing the id of the input file and the summarized coverage results.
    """
    target_binary = get_aigise_config_from_context(tool_context).build.target_binary
    cov_sandbox = get_sandbox_from_context(tool_context, "coverage")
    testcase_id, saved_testcase_dir = save_testcase(cov_sandbox, testcase_path)
    saved_testcase_path = os.path.join(saved_testcase_dir, "testcase")
    msg, err = cov_sandbox.run_command_in_container(
        [
            "bash",
            "/sandbox_scripts/coverage/export_cov.sh",
            f"/out/{target_binary}",
            saved_testcase_path,
            os.path.dirname(saved_testcase_path),
        ]
    )
    if err != 0:
        logger.error(f"Coverage analysis failed, stderr: {msg}")
        return {"error": "Coverage analysis failed"}

    report_msg, err = cov_sandbox.run_command_in_container(
        f"sed -n '1p;$p' {saved_testcase_dir}/report.txt"
    )
    if err != 0:
        logger.error(f"Reading coverage report failed, stderr: {report_msg}")
        return {"error": "Reading coverage report failed"}

    neo4j_client = await get_neo4j_client_from_context(tool_context, "analysis")
    await upload_testcase_to_database(cov_sandbox, testcase_id, neo4j_client)

    return {"testcase_id": testcase_id, "summary": report_msg}


@safe_tool_execution
@requires_sandbox("coverage", "neo4j", "joern")
async def show_coverage(
    testcase_id: str,
    function_name: str,
    file_path: Optional[str],
    *,
    tool_context: ToolContext,
) -> dict:
    """
    Tool to show code coverage results for a specified file and testcase within the sandbox environment.

    Args:
        testcase_id (str): The id of the testcase for which to show coverage results.
        function_name (str): The name of the function for which to show coverage results.
        file_path (Optional[str]): The absolute path to the file of the function. This can be empty,
            in which case it will match all functions with the same name.

    Returns:
        dict: A dictionary containing the coverage results for the specified file and testcase.
    """
    cov_sandbox = get_sandbox_from_context(tool_context, "coverage")
    profdata_path = os.path.join(
        get_testcase_storage_dir(testcase_id), "testcase.profdata"
    )
    target_binary = get_aigise_config_from_context(tool_context).build.target_binary

    if file_path:
        filename = os.path.basename(file_path)
        name_regex = f".*{filename}:{function_name}"
    else:
        name_regex = function_name

    msg, err = cov_sandbox.run_command_in_container(
        [
            "bash",
            "/sandbox_scripts/coverage/show_cov.sh",
            f"/out/{target_binary}",
            profdata_path,
            name_regex,
        ]
    )
    if err != 0:
        logger.error(f"Show coverage failed, stderr: {msg}")
        return {"error": "Show coverage failed"}

    return {"coverage": msg}


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
