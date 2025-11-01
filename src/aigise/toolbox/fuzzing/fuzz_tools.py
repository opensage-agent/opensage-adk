"""Fuzzing tool implementations."""

from __future__ import annotations

import logging
import tempfile
from typing import Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import (
    get_aigise_config_from_context,
    get_sandbox_from_context,
)

logger = logging.getLogger(__name__)


@safe_tool_execution
@requires_sandbox("fuzz")
async def run_fuzzing_campaign(
    duration_minutes: Optional[int],
    seeds: Optional[List[str]],
    custom_mutator: Optional[str],
    *,
    tool_context: ToolContext,
) -> Dict[str, any]:
    """
    Run a fuzzing campaign using AFL++.


    Args:
        duration_minutes: Duration of fuzzing in minutes
        seeds: Optional list of path to seed input files or directories.
            If None, will continue with previous fuzzing campaign state, or use a default seed ("1234").
        custom_mutator: Optional path to a custom mutator python script, or the function itself. If not provided, use default mutators.
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
    if duration_minutes is None:
        duration_minutes = 5  # Default to 5 minutes

    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    config = get_aigise_config_from_context(tool_context)
    fuzz_target = config.build.target_binary

    # Set up fuzzing directory
    # Check existence of /fuzz/out
    exists_fuzz_out = True
    _, exit_code = fuzz_sandbox.run_command_in_container(
        "find /fuzz/out -type f -name fuzzer_stats | grep -q ."
    )
    if exit_code != 0:
        fuzz_sandbox.run_command_in_container("rm -rf /fuzz/out && mkdir -p /fuzz/out")
        exists_fuzz_out = False

    fuzz_sandbox.run_command_in_container("rm -rf /fuzz/in && mkdir -p /fuzz/in")
    fuzz_sandbox.run_command_in_container("mkdir -p /fuzz/mutator")

    # Set up custom mutator if provided
    if custom_mutator:
        if "def fuzz(" in custom_mutator:
            with tempfile.NamedTemporaryFile(suffix=".py") as tmp_file:
                tmp_file.write(custom_mutator.encode("utf-8"))
                tmp_file.flush()
                fuzz_sandbox.copy_file_to_container(
                    tmp_file.name, "/fuzz/mutator/custom_mutator.py"
                )
        else:
            fuzz_sandbox.run_command_in_container(
                f"cp {custom_mutator} /fuzz/mutator/custom_mutator.py"
            )

    # Create seed inputs
    if seeds:
        for seed in seeds:
            fuzz_sandbox.run_command_in_container(f"cp -r {seed} /fuzz/in")
    elif not exists_fuzz_out:
        # Create a default seed input if no previous state
        fuzz_sandbox.run_command_in_container('echo "1234" > /fuzz/in/seed.txt')

    # Run fuzzing campaign
    duration_seconds = duration_minutes * 60
    env_cmd = ""
    if custom_mutator:
        env_cmd += "export PYTHONPATH=/fuzz/mutator && export AFL_PYTHON_MODULE=custom_mutator && "
    env_cmd = f"export AFL_NO_UI=1 && timeout {duration_seconds}s /out/afl-fuzz -i /fuzz/in -o /fuzz/out /out/{fuzz_target}"

    res, exit_code = fuzz_sandbox.run_command_in_container(env_cmd)

    # Analyze results
    results = _analyze_fuzzing_results(fuzz_sandbox, fuzz_target)

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
        crash_names: List of crash file names to extract. If None, extract all crashes.
        target_dir: Directory in the container to copy crashes to.
    Returns:
        Dictionary indicating success or failure.
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    crashes_dir, exit_code = fuzz_sandbox.run_command_in_container(
        "find /fuzz/out -name 'crashes' -type d"
    )
    if exit_code != 0:
        return {"success": False, "error": "No crashes directory found."}
    crashes_dir = crashes_dir.strip()
    _, exit_code = fuzz_sandbox.run_command_in_container(["ls", "-la", crashes_dir])
    if exit_code != 0:
        return {"success": False, "error": "No crashes directory found."}
    if crash_names:
        for crash_name in crash_names:
            fuzz_sandbox.run_command_in_container(
                ["cp", f"{crashes_dir}/{crash_name}", target_dir]
            )
    else:
        fuzz_sandbox.run_command_in_container(
            ["cp", "-r", f"{crashes_dir}/.", target_dir]
        )

    return {"success": True, "message": "Crashes extracted successfully."}


@requires_sandbox("fuzz")
async def check_fuzzing_stats(*, tool_context: ToolContext) -> Dict[str, any]:
    """
    Check fuzzing coverage and statistics.

    Returns:
        Dictionary containing coverage information
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")

    # Check if fuzzing output directory exists
    _, exit_code = fuzz_sandbox.run_command_in_container(
        "ls -la /fuzz/out/ 2>/dev/null"
    )

    coverage_info = {
        "has_output": exit_code == 0,
        "output_directory": "/fuzz/out/",
        "statistics": {},
    }

    if coverage_info["has_output"]:
        # Get basic statistics
        stats_res, _ = fuzz_sandbox.run_command_in_container(
            "find /fuzz/out -name 'fuzzer_stats' -exec cat {} \\; 2>/dev/null || echo 'No stats'"
        )
        coverage_info["statistics"] = _parse_fuzzer_stats(stats_res)

    return {"success": True, "coverage_info": coverage_info}


def _analyze_fuzzing_results(fuzz_sandbox, fuzz_target: str) -> Dict[str, any]:
    """Analyze fuzzing results."""
    results = {
        "crashes_found": 0,
        # "hangs_found": 0,
        "unique_crashes": 0,
        "executions": 0,
        "exec_speed": 0,
    }

    # Check for crashes
    crash_res, _ = fuzz_sandbox.run_command_in_container(
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


def _parse_fuzzer_stats(stats_output: str) -> Dict[str, any]:
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
