"""Fuzzing tool implementations."""

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

from opensage.toolbox.sandbox_requirements import requires_sandbox
from opensage.utils.agent_utils import (
    get_opensage_config_from_context,
    get_sandbox_from_context,
)

logger = logging.getLogger(__name__)


@requires_sandbox("main")
async def simplified_python_fuzzer(
    script: str, *, tool_context: ToolContext
) -> Dict[str, Any]:
    r"""
    create a simplified python fuzzer script that mutates one seed, feeds it to the target program, and monitors whether the target program crashes.
    The script should follow the following high level design:
    1. Load the seed file or write your own seed, if you write your own seed file, your seed file should be stored in /tmp, only use one seed file and overwrite it when mutating, you should be aware of the python version, which may not support some features.
    2. Mutate the seed file, you should consider the format and grammar of the seed file, and mutate it in a way that is likely to trigger the vulnerability, you should concentrate on the part of the seed that you are not sure why it didn't trigger the vulnerability. You should do grammar based mutation, and pay attention to end of file and end of line. Always have a generation or mutation logic, do not just fuzz a fixed set of inputs.
    3. Feed the mutated seed file to the target program, you should find how to run the target program, or how to submit the input to a remote server to get feedback from the target program.
    5. If the target program crashes, save the crash and the corresponding input to a file, that should be stored in /tmp
    7. Repeat the process for an infinite iterations, until the target program crashes.
    You should not output print out anything, you should save the crash and the corresponding input to a file
    You script will be run with the command python3 for one minute.
    ** IMPORTANT **
    Before you start to call this tool, you should first reason about the structure of the target harness, and how the input is encoded and passed to the target program, you should perform grammar based mutation instead of just fuzzing a fixed set of inputs.
    """
    # 1. Extract the Python code block
    fuzzer_code = script

    # 3. Get sandbox
    sandbox = get_sandbox_from_context(tool_context, "main")

    # 4. Create temporary directory and write the fuzzer script
    with tempfile.TemporaryDirectory() as temp_dir:
        fuzzer_script_path = os.path.join(temp_dir, "fuzzer.py")
        with open(fuzzer_script_path, "w", encoding="utf-8") as f:
            f.write(fuzzer_code)

        # 5. Copy fuzzer script to container
        container_fuzzer_path = "/tmp/fuzzer.py"
        try:
            await sandbox.acopy_file_to_container(
                fuzzer_script_path, container_fuzzer_path
            )
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to copy fuzzer script to container: {str(e)}",
            }

        # 6. Run the fuzzer script for 180 seconds
        logger.info("Running fuzzer script for 180 seconds...")
        output, exit_code = await sandbox.arun_command_in_container(
            f"timeout 70s python3 {container_fuzzer_path}",
            timeout=70,  # Give a bit more time for timeout command to complete
        )

        # 7. Check for crash files or outputs
        crash_files, _ = await sandbox.arun_command_in_container(
            "find /tmp -name 'crash_*' -o -name 'crash.txt' 2>/dev/null"
        )

        result = {
            "success": True,
            "output": output,
            "exit_code": exit_code,
            "crash_files_found": crash_files.strip() if crash_files else "",
        }

        # 8. If crash files exist, try to read them
        if crash_files.strip():
            crash_file_list = [
                f.strip() for f in crash_files.strip().split("\n") if f.strip()
            ]
            result["crash_details"] = []
            for crash_file in crash_file_list[:5]:  # Limit to first 5 crash files
                crash_content, _ = await sandbox.arun_command_in_container(
                    f"head -n 100 {crash_file} 2>/dev/null"
                )
                result["crash_details"].append(
                    {"file": crash_file, "content": crash_content}
                )

        return result


@requires_sandbox("fuzz")
async def run_fuzzing_campaign(
    seeds: Optional[List[str]] = None,
    custom_mutator: Optional[str] = None,
    *,
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """
    Run a fuzzing campaign using AFL++.


    Args:
        seeds (Optional[List[str]]): Optional list of path to seed input files or directories.
            If None, will continue with previous fuzzing campaign state, or use a default seed ("1234"). You should normally create a seed file and indicate its path.
        custom_mutator (Optional[str]): Optional path to a custom mutator python script, or the function itself. If not provided, use default mutators.
            It should define a function::

                def fuzz(buf: bytearray, add_buf: bytearray, max_size: int) -> bytearray:
                    \"\"\"
                    Called per fuzzing iteration.
                    Args:
                        buf (bytearray): The buffer that should be mutated.
                        add_buf (bytearray): A second buffer that can be used as mutation source.
                        max_size (int): Maximum size of the mutated output. The mutation must not
                            produce data larger than max_size.
                    Returns:
                        bytearray: A new bytearray containing the mutated data
                    \"\"\"
                    ...

    Returns:
        Dictionary containing fuzzing results and statistics
    """
    duration_minutes = 3

    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    config = get_opensage_config_from_context(tool_context)
    fuzz_target = config.build.target_binary

    # Set up fuzzing directory
    # Check existence of /fuzz/out
    exists_fuzz_out = True
    _, exit_code = await fuzz_sandbox.arun_command_in_container(
        "find /fuzz/out -type f -name fuzzer_stats | grep -q ."
    )
    if exit_code != 0:
        await fuzz_sandbox.arun_command_in_container(
            "rm -rf /fuzz/out && mkdir -p /fuzz/out"
        )
        exists_fuzz_out = False

    await fuzz_sandbox.arun_command_in_container("rm -rf /fuzz/in && mkdir -p /fuzz/in")
    await fuzz_sandbox.arun_command_in_container("mkdir -p /fuzz/mutator")

    # Set up custom mutator if provided
    if custom_mutator:
        if "def fuzz(" in custom_mutator:
            with tempfile.NamedTemporaryFile(suffix=".py") as tmp_file:
                tmp_file.write(custom_mutator.encode("utf-8"))
                tmp_file.flush()
                await fuzz_sandbox.acopy_file_to_container(
                    tmp_file.name, "/fuzz/mutator/custom_mutator.py"
                )
        else:
            await fuzz_sandbox.arun_command_in_container(
                f"cp {custom_mutator} /fuzz/mutator/custom_mutator.py"
            )

    # Create seed inputs
    if seeds:
        for seed in seeds:
            await fuzz_sandbox.arun_command_in_container(f"cp -r {seed} /fuzz/in")
    elif not exists_fuzz_out:
        # Create a default seed input if no previous state
        await fuzz_sandbox.arun_command_in_container('echo "1234" > /fuzz/in/seed.txt')

    # Run fuzzing campaign
    duration_seconds = duration_minutes * 60  # TODO: make this configurable
    env_cmd = ""
    if custom_mutator:
        env_cmd += "export PYTHONPATH=/fuzz/mutator && export AFL_PYTHON_MODULE=custom_mutator && "
    env_cmd = f"export AFL_NO_UI=1 && timeout {duration_seconds}s /out/afl-fuzz -i /fuzz/in -o /fuzz/out /out/{fuzz_target}"
    # TODO: this will override the env_cmd? make the customer mutator not useful....

    res, exit_code = await fuzz_sandbox.arun_command_in_container(env_cmd)

    # Analyze results
    results = await _analyze_fuzzing_results(fuzz_sandbox, fuzz_target)

    return {
        "success": True,
        "fuzz_target": fuzz_target,
        "duration_minutes": duration_minutes,
        "results": results,
        "output": res,
    }


@requires_sandbox("fuzz")
async def extract_crashes(
    crash_names: Optional[List[str]], target_dir: str, *, tool_context: ToolContext
):
    """
    Extract crash inputs from fuzzing output.

    Args:
        crash_names (Optional[List[str]]): List of crash file names to extract. If None, extract all crashes.
        target_dir (str): Directory in the container to copy crashes to.
    Returns:
        Dictionary indicating success or failure.
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    crashes_dir, exit_code = await fuzz_sandbox.arun_command_in_container(
        "find /fuzz/out -name 'crashes' -type d"
    )
    if exit_code != 0:
        return {"success": False, "error": "No crashes directory found."}
    crashes_dir = crashes_dir.strip()
    _, exit_code = await fuzz_sandbox.arun_command_in_container(
        ["ls", "-la", crashes_dir]
    )
    if exit_code != 0:
        return {"success": False, "error": "No crashes directory found."}
    if crash_names:
        for crash_name in crash_names:
            await fuzz_sandbox.arun_command_in_container(
                ["cp", f"{crashes_dir}/{crash_name}", target_dir]
            )
    else:
        await fuzz_sandbox.arun_command_in_container(
            ["cp", "-r", f"{crashes_dir}/.", target_dir]
        )

    return {"success": True, "message": "Crashes extracted successfully."}


@requires_sandbox("fuzz")
async def check_fuzzing_stats(*, tool_context: ToolContext) -> Dict[str, Any]:
    """
    Check fuzzing coverage and statistics.

    Returns:
        Dict[str, Any]: Dictionary containing coverage information
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")

    # Check if fuzzing output directory exists
    _, exit_code = await fuzz_sandbox.arun_command_in_container(
        "ls -la /fuzz/out/ 2>/dev/null"
    )

    coverage_info = {
        "has_output": exit_code == 0,
        "output_directory": "/fuzz/out/",
        "statistics": {},
    }

    if coverage_info["has_output"]:
        # Get basic statistics
        stats_res, _ = await fuzz_sandbox.arun_command_in_container(
            "find /fuzz/out -name 'fuzzer_stats' -exec cat {} \\; 2>/dev/null || echo 'No stats'"
        )
        coverage_info["statistics"] = _parse_fuzzer_stats(stats_res)

    return {"success": True, "coverage_info": coverage_info}


async def _analyze_fuzzing_results(fuzz_sandbox, fuzz_target: str) -> Dict[str, Any]:
    """Analyze fuzzing results."""
    results = {
        "crashes_found": 0,
        # "hangs_found": 0,
        "unique_crashes": 0,
        "executions": 0,
        "exec_speed": 0,
    }

    # Check for crashes
    crash_res, _ = await fuzz_sandbox.arun_command_in_container(
        "find /fuzz/out -name 'crashes' -type d -exec ls {} \\; 2>/dev/null || echo 'No crashes'"
    )
    if "No crashes" not in crash_res:
        results["crashes_found"] = len(
            [line for line in crash_res.splitlines() if line.strip()]
        )

    # Check for hangs
    # hang_res, _ = fuzz_sandbox.run_command_in_container(
    #     "find /fuzz/out -name 'hangs' -type d -exec ls {} \\; 2>/dev/null || echo 'No hangs'"
    # )
    # if "No hangs" not in hang_res:
    #     results["hangs_found"] = len(
    #         [line for line in hang_res.splitlines() if line.strip()]
    #     )

    return results


def _parse_fuzzer_stats(stats_output: str) -> Dict[str, Any]:
    """Parse AFL++ fuzzer statistics."""
    stats = {}

    for line in stats_output.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            # Convert numeric values
            if value.isdigit():
                stats[key] = int(value)
            elif value.replace(".", "").isdigit():
                stats[key] = float(value)
            else:
                stats[key] = value

    return stats
