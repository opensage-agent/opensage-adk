#!/usr/bin/env python3
"""
Skill Initializer - Creates a new skill from template

Usage:
    init_skill.py <skill-name> --path <path> --should_run_in_sandbox <sandbox> --returns_json <true|false> [--requires_sandboxes "a,b,c"|"none"]

This script is restricted to only create tools under bash_tools/new_tools/.
The path must be within the bash_tools/new_tools/ directory structure.

Examples:
    init_skill.py my-new-skill --path /path/to/bash_tools/new_tools/general
    init_skill.py my-api-helper --path /path/to/bash_tools/new_tools/retrieval
"""

import argparse
import sys
from pathlib import Path

# Get bash_tools directory from script location
# In container: /bash_tools/new_tool_creator/init_skill.py -> /bash_tools/
# In local dev: src/opensage/bash_tools/new_tool_creator/init_skill.py -> src/opensage/bash_tools/
SCRIPT_DIR = Path(__file__).parent.resolve()
BASH_TOOLS_DIR = (
    SCRIPT_DIR.parent
)  # Go up one level from new_tool_creator to bash_tools


SKILL_TEMPLATE = """---
name: {skill_name}
description: [TODO: Complete and informative explanation of what the tool does and when to use it. Include WHEN to use this tool - specific scenarios, file types, or tasks that trigger it.]
should_run_in_sandbox: {should_run_in_sandbox}
returns_json: {returns_json}
---

# {skill_title}

[TODO: Brief description of what this tool does]

## Usage

```bash
scripts/{script_name}.sh [arguments]
```

[TODO: Add usage examples with different parameter combinations]

## Parameters

[TODO: Document all parameters. Use this format for each parameter:]

### param_name (required/optional, positional position N)

**Type**: `str` | `int` | `bool` | `list`

[TODO: Description of the parameter]

**Example**: `example_value`

### --option_name (optional, named parameter)

**Type**: `str`

[TODO: Description of the option]

**Example**: `--option_name value`

### --flag (optional, flag)

**Type**: `bool` (default: `false`)

[TODO: Description of the flag]

## Return Value

[TODO: Describe the return value format]

Returns a JSON object:

```json
{{
  "success": true,
  "result": "..."
}}
```

## Requires Sandbox

[Optional dependency environments. List extra sandboxes that must be available for
this tool to work (NOT the execution sandbox). If none, write "none".]

{requires_sandboxes}

## Timeout

[TODO: Specify timeout in seconds, e.g., "60 seconds"]
"""

EXAMPLE_SCRIPT = """#!/bin/bash

# {script_name}.sh - {skill_title}
# Usage: ./{script_name}.sh [arguments]

# TODO: Add parameter parsing and validation
# TODO: Implement tool logic
# TODO: Return JSON output

# Example JSON output:
echo '{{"success": true, "result": "Tool executed successfully"}}'
"""

EXAMPLE_REFERENCE = """# Reference Documentation for {skill_title}

This is a placeholder for detailed reference documentation.
Replace with actual reference content or delete if not needed.

Example real reference docs from other skills:
- product-management/references/communication.md - Comprehensive guide for status updates
- product-management/references/context_building.md - Deep-dive on gathering context
- bigquery/references/ - API references and query examples

## When Reference Docs Are Useful

Reference docs are ideal for:
- Comprehensive API documentation
- Detailed workflow guides
- Complex multi-step processes
- Information too lengthy for main SKILL.md
- Content that's only needed for specific use cases

## Structure Suggestions

### API Reference Example
- Overview
- Authentication
- Endpoints with examples
- Error codes
- Rate limits

### Workflow Guide Example
- Prerequisites
- Step-by-step instructions
- Common patterns
- Troubleshooting
- Best practices
"""

EXAMPLE_ASSET = """# Example Asset File

This placeholder represents where asset files would be stored.
Replace with actual asset files (templates, images, fonts, etc.) or delete if not needed.

Asset files are NOT intended to be loaded into context, but rather used within
the output Claude produces.

Example asset files from other skills:
- Brand guidelines: logo.png, slides_template.pptx
- Frontend builder: hello-world/ directory with HTML/React boilerplate
- Typography: custom-font.ttf, font-family.woff2
- Data: sample_data.csv, test_dataset.json

## Common Asset Types

- Templates: .pptx, .docx, boilerplate directories
- Images: .png, .jpg, .svg, .gif
- Fonts: .ttf, .otf, .woff, .woff2
- Boilerplate code: Project directories, starter files
- Icons: .ico, .svg
- Data files: .csv, .json, .xml, .yaml

Note: This is a text placeholder. Actual assets can be any file type.
"""


def title_case_skill_name(skill_name):
    """Convert hyphenated skill name to Title Case for display."""
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def init_skill(
    skill_name, path, *, should_run_in_sandbox, returns_json, requires_sandboxes
):
    """
    Initialize a new skill directory with template SKILL.md.

    Args:
        skill_name: Name of the skill
        path: Path where the skill directory should be created
    Returns:
        Path to created skill directory, or None if error
    """
    # Resolve paths
    requested_path = Path(path).resolve()
    allowed_base = BASH_TOOLS_DIR / "new_tools"

    # Validate that the path is within bash_tools/new_tools/
    try:
        requested_path.relative_to(allowed_base)
    except ValueError:
        print(f"❌ Error: Path must be within {allowed_base}")
        print(f"   Requested path: {requested_path}")
        print(f"   Allowed base: {allowed_base}")
        return None

    # Determine skill directory path
    skill_dir = requested_path / skill_name

    # Check if directory already exists
    if skill_dir.exists():
        print(f"❌ Error: Skill directory already exists: {skill_dir}")
        return None

    # Create skill directory
    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        print(f"✅ Created skill directory: {skill_dir}")
    except Exception as e:
        print(f"❌ Error creating directory: {e}")
        return None

    # Create SKILL.md from template
    skill_title = title_case_skill_name(skill_name)
    # Convert hyphen-case to snake_case for script name
    script_name = skill_name.replace("-", "_")
    skill_content = SKILL_TEMPLATE.format(
        skill_name=skill_name,
        skill_title=skill_title,
        script_name=script_name,
        should_run_in_sandbox=should_run_in_sandbox,
        returns_json=returns_json,
        requires_sandboxes=requires_sandboxes,
    )

    skill_md_path = skill_dir / "SKILL.md"
    try:
        skill_md_path.write_text(skill_content)
        print("✅ Created SKILL.md")
    except Exception as e:
        print(f"❌ Error creating SKILL.md: {e}")
        return None

    # Create scripts/ directory with example script
    try:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        example_script = scripts_dir / f"{script_name}.sh"
        example_script.write_text(
            EXAMPLE_SCRIPT.format(skill_title=skill_title, script_name=script_name)
        )
        example_script.chmod(0o755)
        print(f"✅ Created scripts/{script_name}.sh")
    except Exception as e:
        print(f"❌ Error creating scripts directory: {e}")
        return None

    # Print next steps
    print(f"\n✅ Tool '{skill_name}' initialized successfully at {skill_dir}")
    print("\nNext steps:")
    print("1. Edit SKILL.md to complete the TODO items and update the description")
    print("2. Implement the bash script in scripts/")
    print("3. Test the tool and ensure it returns proper JSON output")
    print("4. The tool will be automatically discovered by the framework")

    return skill_dir


def main():
    parser = argparse.ArgumentParser(
        description="Initialize a new bash_tools Skill under bash_tools/new_tools/."
    )
    parser.add_argument("skill_name", help="Hyphen-case skill name (directory name).")
    parser.add_argument(
        "--path",
        required=True,
        help="Target parent directory under bash_tools/new_tools/ (e.g. .../new_tools/general).",
    )
    parser.add_argument(
        "--should_run_in_sandbox",
        required=True,
        help="Execution sandbox for this Skill (where scripts run).",
    )
    parser.add_argument(
        "--returns_json",
        required=True,
        choices=["true", "false"],
        help="Whether the tool returns JSON (true|false).",
    )
    parser.add_argument(
        "--requires_sandboxes",
        default="none",
        help=(
            "Dependency sandboxes required to be available (comma-separated), "
            "or 'none'. This is NOT the execution sandbox."
        ),
    )

    args = parser.parse_args()
    skill_name = args.skill_name
    path = args.path
    should_run_in_sandbox = args.should_run_in_sandbox
    returns_json = args.returns_json
    requires_sandboxes = args.requires_sandboxes

    print(f"🚀 Initializing skill: {skill_name}")
    print(f"   Location: {path}")
    print()

    result = init_skill(
        skill_name,
        path,
        should_run_in_sandbox=should_run_in_sandbox,
        returns_json=returns_json,
        requires_sandboxes=requires_sandboxes,
    )

    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
