"""Fuzzing tool implementations."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import requires_sandbox
from aigise.utils.agent_utils import get_sandbox_from_context

logger = logging.getLogger(__name__)


@requires_sandbox("fuzz")
async def run_fuzzing_campaign(
    duration_minutes: int = 5,
    seed_inputs: Optional[List[str]] = None,
    *,
    tool_context: ToolContext
) -> Dict[str, any]:
    """
    Run a fuzzing campaign using AFL++.
    
    Args:
        duration_minutes: Duration of fuzzing in minutes
        seed_inputs: Optional list of seed input files
        
    Returns:
        Dictionary containing fuzzing results and statistics
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    
    # Extract fuzz target from arvo script
    res, exit_code = fuzz_sandbox.run_command_in_container("cat /bin/arvo")
    if exit_code != 0:
        return {"success": False, "error": f"Failed to read arvo script: {res}"}
    infos = _extract_infos_from_arvo_script(res)
    fuzz_target = infos["FUZZ_TARGET"]
    
    # Set up fuzzing directory
    fuzz_sandbox.run_command_in_container("mkdir -p /fuzz/in /fuzz/out")
    
    # Create seed inputs
    if seed_inputs:
        for i, seed in enumerate(seed_inputs):
            fuzz_sandbox.run_command_in_container(f"echo '{seed}' > /fuzz/in/seed_{i}")
    else:
        fuzz_sandbox.run_command_in_container("echo '1234' > /fuzz/in/seed")
    
    # Run fuzzing campaign
    duration_seconds = duration_minutes * 60
    env_cmd = f"export AFL_NO_UI=1 && timeout {duration_seconds}s /out/afl-fuzz -i /fuzz/in -o /fuzz/out /out/{fuzz_target}"
    
    res, exit_code = fuzz_sandbox.run_command_in_container(env_cmd)
    
    # Analyze results
    results = _analyze_fuzzing_results(fuzz_sandbox, fuzz_target)
    
    return {
        "success": True,
        "fuzz_target": fuzz_target,
        "duration_minutes": duration_minutes,
        "results": results,
        "output": res
    }


@requires_sandbox("fuzz")
async def analyze_crash(
    crash_file_path: str,
    *,
    tool_context: ToolContext
) -> Dict[str, any]:
    """
    Analyze a crash file to determine the cause and impact.
    
    Args:
        crash_file_path: Path to the crash file
        
    Returns:
        Dictionary containing crash analysis results
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    
    # Extract fuzz target
    res, exit_code = fuzz_sandbox.run_command_in_container("cat /bin/arvo")
    if exit_code != 0:
        return {"success": False, "error": f"Failed to read arvo script: {res}"}
    infos = _extract_infos_from_arvo_script(res)
    fuzz_target = infos["FUZZ_TARGET"]
    
    # Run the target with the crash file
    cmd = f"/out/{fuzz_target} {crash_file_path}"
    res, exit_code = fuzz_sandbox.run_command_in_container(cmd)
    
    # Analyze the crash
    crash_info = _parse_crash_output(res)
    
    return {
        "success": True,
        "crash_file": crash_file_path,
        "fuzz_target": fuzz_target,
        "crash_info": crash_info,
        "raw_output": res
    }


@requires_sandbox("fuzz")
async def generate_poc(
    crash_file_path: str,
    *,
    tool_context: ToolContext
) -> Dict[str, any]:
    """
    Generate a proof-of-concept exploit from a crash file.
    
    Args:
        crash_file_path: Path to the crash file
        
    Returns:
        Dictionary containing PoC generation results
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    
    # For now, this is a placeholder implementation
    # In a real implementation, this would use tools like exploit-db, 
    # vulnerability scanners, or custom exploit generation
    
    crash_analysis = await analyze_crash(crash_file_path, tool_context=tool_context)
    
    # Generate basic PoC information
    poc_info = {
        "crash_type": crash_analysis.get("crash_info", {}).get("type", "unknown"),
        "severity": "medium",  # Placeholder
        "exploitability": "possible",  # Placeholder
        "recommendations": [
            "Review input validation",
            "Implement proper bounds checking",
            "Use memory-safe programming practices"
        ]
    }
    
    return {
        "success": True,
        "crash_file": crash_file_path,
        "poc_info": poc_info,
        "crash_analysis": crash_analysis
    }


@requires_sandbox("fuzz")
async def check_fuzzing_coverage(
    *,
    tool_context: ToolContext
) -> Dict[str, any]:
    """
    Check fuzzing coverage and statistics.
    
    Returns:
        Dictionary containing coverage information
    """
    fuzz_sandbox = get_sandbox_from_context(tool_context, "fuzz")
    
    # Check if fuzzing output directory exists
    res, exit_code = fuzz_sandbox.run_command_in_container("ls -la /fuzz/out/ 2>/dev/null || echo 'No fuzzing output'")
    
    coverage_info = {
        "has_output": "/fuzz/out/" in res,
        "output_directory": "/fuzz/out/",
        "statistics": {}
    }
    
    if coverage_info["has_output"]:
        # Get basic statistics
        stats_res, _ = fuzz_sandbox.run_command_in_container("find /fuzz/out -name 'fuzzer_stats' -exec cat {} \\; 2>/dev/null || echo 'No stats'")
        coverage_info["statistics"] = _parse_fuzzer_stats(stats_res)
    
    return {
        "success": True,
        "coverage_info": coverage_info
    }


def _extract_infos_from_arvo_script(arvo_script: str) -> Dict[str, str]:
    """Extract information from arvo script."""
    infos = {}
    # find 'export XXX=YYYY' in arvo_script
    env_names = ["SANITIZER", "FUZZING_LANGUAGE", "ARCHITECTURE"]
    for line in arvo_script.splitlines():
        for env_name in env_names:
            if line.startswith(f"export {env_name}="):
                infos[env_name] = line.split("=", 1)[1].strip().strip('"')

    # find first appearance of "   /out/{fuzz_target} /tmp/poc"
    for line in arvo_script.splitlines():
        m = re.match(r"^\s+/out/(\S+)\s+/tmp/poc", line)
        if m:
            infos["FUZZ_TARGET"] = m.group(1)
            break
    return infos


def _analyze_fuzzing_results(fuzz_sandbox, fuzz_target: str) -> Dict[str, any]:
    """Analyze fuzzing results."""
    results = {
        "crashes_found": 0,
        "hangs_found": 0,
        "unique_crashes": 0,
        "executions": 0,
        "exec_speed": 0
    }
    
    # Check for crashes
    crash_res, _ = fuzz_sandbox.run_command_in_container("find /fuzz/out -name 'crashes' -type d -exec ls {} \\; 2>/dev/null || echo 'No crashes'")
    if "No crashes" not in crash_res:
        results["crashes_found"] = len([line for line in crash_res.splitlines() if line.strip()])
    
    # Check for hangs
    hang_res, _ = fuzz_sandbox.run_command_in_container("find /fuzz/out -name 'hangs' -type d -exec ls {} \\; 2>/dev/null || echo 'No hangs'")
    if "No hangs" not in hang_res:
        results["hangs_found"] = len([line for line in hang_res.splitlines() if line.strip()])
    
    return results


def _parse_crash_output(output: str) -> Dict[str, any]:
    """Parse crash output to extract useful information."""
    crash_info = {
        "type": "unknown",
        "signal": None,
        "address": None,
        "stack_trace": []
    }
    
    # Look for common crash patterns
    if "SIGSEGV" in output or "segmentation fault" in output.lower():
        crash_info["type"] = "segmentation_fault"
    elif "SIGABRT" in output or "abort" in output.lower():
        crash_info["type"] = "abort"
    elif "SIGFPE" in output:
        crash_info["type"] = "floating_point_exception"
    
    # Extract signal information
    signal_match = re.search(r'SIG(\w+)', output)
    if signal_match:
        crash_info["signal"] = signal_match.group(1)
    
    return crash_info


def _parse_fuzzer_stats(stats_output: str) -> Dict[str, any]:
    """Parse AFL++ fuzzer statistics."""
    stats = {}
    
    for line in stats_output.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            
            # Convert numeric values
            if value.isdigit():
                stats[key] = int(value)
            elif value.replace('.', '').isdigit():
                stats[key] = float(value)
            else:
                stats[key] = value
    
    return stats
