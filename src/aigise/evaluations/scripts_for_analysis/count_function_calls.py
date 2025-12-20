#!/usr/bin/env python3
"""Count function call frequency in session_trace.json files.

This script traverses all subdirectories in a specified directory,
extracts all function_call entries from session_trace.json files,
and counts the frequency of each function call.
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

import fire


def extract_function_calls(session_trace_path: Path) -> List[str]:
    """Extract all function_call names from a session_trace.json file.

    Args:
      session_trace_path: Path to the session_trace.json file

    Returns:
      List containing all function_call names
    """
    function_calls = []

    try:
        with open(session_trace_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Iterate through all events
        events = data.get("events", [])
        for event in events:
            content = event.get("content", {})
            parts = content.get("parts", [])

            # Check each part for function_call
            for part in parts:
                if "function_call" in part:
                    function_call = part["function_call"]
                    function_name = function_call.get("name", "")
                    if function_name:
                        function_calls.append(function_name)

    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Warning: Unable to process file {session_trace_path}: {e}")

    return function_calls


def extract_function_calls_from_log(log_path: Path) -> List[str]:
    """Extract tool function_call names from an execution_info.log file.

    This scans each log line, attempts to parse embedded JSON payloads
    (if present), and collects any content.parts[*].function_call.name.
    All authors (root agent and subagents) are included.

    Args:
      log_path: Path to the execution_info.log file

    Returns:
      List containing all function_call names found in the log
    """
    function_calls = []

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                # Fast path: find the first JSON object on the line
                brace_idx = line.find("{")
                if brace_idx == -1:
                    continue
                candidate = line[brace_idx:].strip()
                # Best-effort JSON decode; skip non-JSON lines
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError:
                    continue

                content = obj.get("content")
                if not isinstance(content, dict):
                    continue
                parts = content.get("parts", [])
                if not isinstance(parts, list):
                    continue

                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if "function_call" in part:
                        function_call = part.get("function_call", {})
                        if isinstance(function_call, dict):
                            name = function_call.get("name", "")
                            if name:
                                function_calls.append(name)
    except FileNotFoundError as e:
        print(f"Warning: Unable to open log file {log_path}: {e}")

    return function_calls


def main(directory: str):
    """Main function: count frequency of all function_calls in the directory.

    Args:
        directory: Path to the directory containing subdirectories with
          execution_info.log and/or session_trace.json files
    """
    # Target directory
    base_dir = Path(directory)

    # Check if directory exists
    if not base_dir.exists():
        print(f"Error: Directory '{directory}' does not exist")
        sys.exit(1)

    if not base_dir.is_dir():
        print(f"Error: '{directory}' is not a directory")
        sys.exit(1)

    # Store all function_calls
    all_function_calls = []

    # Iterate through all subdirectories
    subdirs = sorted([d for d in base_dir.iterdir() if d.is_dir()])

    if not subdirs:
        print(f"Warning: No subdirectories found in '{directory}'")

    print(f"Scanning {len(subdirs)} subdirectories in: {base_dir}\n")

    for subdir in subdirs:
        log_file = subdir / "execution_info.log"
        session_trace_file = subdir / "session_trace.json"

        if log_file.exists():
            function_calls = extract_function_calls_from_log(log_file)
            all_function_calls.extend(function_calls)
            # print(f"✓ {subdir.name}: Found {len(function_calls)} function_calls in execution_info.log")
        elif session_trace_file.exists():
            function_calls = extract_function_calls(session_trace_file)
            all_function_calls.extend(function_calls)
            # print(f"✓ {subdir.name}: Found {len(function_calls)} function_calls")
        else:
            print(f"✗ {subdir.name}: execution_info.log / session_trace.json not found")

    # Count frequency
    function_call_counter = Counter(all_function_calls)

    # Output results
    print(f"\n{'=' * 70}")
    print(f"Statistics - Total {len(all_function_calls)} function_calls found")
    print(f"{'=' * 70}\n")

    print(f"{'Rank':<6} {'Function Name':<40} {'Count':<10} {'Ratio'}")
    print("-" * 70)

    for rank, (func_name, count) in enumerate(
        function_call_counter.most_common(), start=1
    ):
        percentage = (
            (count / len(all_function_calls) * 100) if all_function_calls else 0
        )
        print(f"{rank:<6} {func_name:<40} {count:<10} {percentage:>5.2f}%")

    # Save results to file
    output_file = base_dir / "function_call_statistics.json"

    statistics = {
        "total_function_calls": len(all_function_calls),
        "unique_functions": len(function_call_counter),
        "function_counts": dict(function_call_counter.most_common()),
        "directories_scanned": len(subdirs),
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(statistics, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    fire.Fire(main)
