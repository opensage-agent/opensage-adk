#!/usr/bin/env python3
"""Find evaluation results where a specific task ID succeeded.

This script recursively searches for evaluation_results.json files and
checks if the specified task ID has a true result.
"""

import json
from pathlib import Path
from typing import List

import fire


def find_successful_task(path: str, task_id: str) -> List[str]:
    """Find all evaluation_results.json where task_id has result=true.

    Args:
        path: Directory to search recursively
        task_id: Task ID to look for (e.g., "arvo:13956" or "arvo_13956")

    Returns:
        List of paths to evaluation_results.json files where task succeeded

    Example:
        python find_successful_task.py /path/to/evals "arvo:13956"
    """
    search_path = Path(path)

    if not search_path.exists():
        print(f"Error: Path does not exist: {path}")
        return []

    if not search_path.is_dir():
        print(f"Error: Path is not a directory: {path}")
        return []

    # Normalize task_id (handle both "arvo:123" and "arvo_123" formats)
    normalized_id = task_id.replace("_", ":")

    successful_files = []
    total_files = 0

    # Recursively find all evaluation_results.json files
    for result_file in search_path.rglob("evaluation_results.json"):
        total_files += 1

        try:
            with open(result_file, "r") as f:
                data = json.load(f)

            # Check if results field exists
            if "results" not in data:
                continue

            results = data["results"]

            # Check if task_id exists and is true
            # Try both original and normalized formats
            task_result = results.get(task_id) or results.get(normalized_id)

            if task_result is True:
                successful_files.append(str(result_file))
                print(f"✓ Found: {result_file}")

        except json.JSONDecodeError:
            print(f"⚠ Warning: Invalid JSON in {result_file}")
        except Exception as e:
            print(f"⚠ Warning: Error reading {result_file}: {e}")

    print("\n" + "=" * 80)
    print(f"Summary:")
    print(f"  Total evaluation_results.json files: {total_files}")
    print(f"  Files where '{task_id}' succeeded: {len(successful_files)}")
    print("=" * 80)

    if successful_files:
        print("\nSuccessful evaluations:")
        for file_path in successful_files:
            print(f"  {file_path}")
    else:
        print(f"\nNo evaluations found where task '{task_id}' succeeded.")

    return successful_files


if __name__ == "__main__":
    fire.Fire(find_successful_task)
