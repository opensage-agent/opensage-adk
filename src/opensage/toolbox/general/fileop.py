import base64
import logging
import shlex
import textwrap
from typing import Any, Dict, Optional

from google.adk.tools.tool_context import ToolContext

from opensage.toolbox.general.edit_failure_analyzer import analyze_edit_failure
from opensage.toolbox.general.string_utils import (
    get_multiple_match_info,
    replace_with_fallback,
    unescape_llm_output,
)
from opensage.utils.agent_utils import get_sandbox_from_context

logger = logging.getLogger(__name__)


def _run_python_script(sandbox, script: str, description: str) -> str:
    """Helper to run a generated python script safely in the container."""
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("utf-8")
    # We unwrap the script inside the container and execute it
    cmd = f"python3 -c \"import base64; exec(base64.b64decode('{encoded_script}').decode('utf-8'))\""

    logger.info(f"Running file operation ({description}): {cmd}")
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code != 0:
        return f"Error ({exit_code}): {output}"
    return output


def _detect_file_indentation(content: str) -> str:
    """Detect the indentation style used in file content."""
    lines = content.split("\n")
    tab_count = 0
    space_counts = {2: 0, 4: 0, 8: 0}

    for line in lines:
        if line.startswith("\t"):
            tab_count += 1
        elif line.startswith("  "):
            stripped = line.lstrip(" ")
            leading = len(line) - len(stripped)
            if leading >= 8 and leading % 8 == 0:
                space_counts[8] += 1
            elif leading >= 4 and leading % 4 == 0:
                space_counts[4] += 1
            elif leading >= 2 and leading % 2 == 0:
                space_counts[2] += 1

    if tab_count > sum(space_counts.values()):
        return "TABS"
    elif space_counts[4] >= space_counts[2] and space_counts[4] >= space_counts[8]:
        return "4-SPACES"
    elif space_counts[2] >= space_counts[4] and space_counts[2] >= space_counts[8]:
        return "2-SPACES"
    elif space_counts[8] > 0:
        return "8-SPACES"
    else:
        return "MIXED"


def view_file(
    path: str, start_line: int = 1, end_line: int = -1, *, tool_context: ToolContext
) -> str:
    """
    View the contents of a file, specifying a line range.
    Lines are numbered.

    Args:
        path (str): Path to the file.
        start_line (int): Starting line number (1-indexed, default 1).
        end_line (int): Ending line number (inclusive, default -1 for end of file).
    Returns:
        str: The content of the file within the range, prefixed with line numbers.
        Includes indentation hint at the top for editing guidance.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # Check if file exists first
    check_cmd = f"test -f {shlex.quote(path)}"
    _, exit_code = sandbox.run_command_in_container(check_cmd)
    if exit_code != 0:
        return f"Error: File {path} not found or not a regular file."

    # Use nl to number lines, then sed to filter range
    # nl -b a: number all lines
    # sed -n '{start},{end}p'
    range_spec = f"{start_line},$" if end_line == -1 else f"{start_line},{end_line}"

    cmd = f"nl -b a {shlex.quote(path)} | sed -n '{range_spec}p'"
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code != 0:
        return f"Error viewing file: {output}"

    # Detect indentation style and prepend hint
    # Read full file content for indentation detection
    cat_cmd = f"cat {shlex.quote(path)}"
    full_content, cat_exit = sandbox.run_command_in_container(cat_cmd)

    indent_style = "UNKNOWN"
    if cat_exit == 0 and full_content:
        indent_style = _detect_file_indentation(full_content)

    # Add indentation hint header
    header = f"[File: {path}] [Indentation: {indent_style}]\n"
    if indent_style == "TABS":
        header += "[⚠️ This file uses TABS for indentation - ensure your edits use TABS, not spaces]\n"
    header += "-" * 60 + "\n"

    return header + output


def edit_file(
    path: str,
    content: str,
    start_line: int,
    end_line: int,
    *,
    tool_context: ToolContext,
) -> str:
    """
    Replace lines [start_line, end_line] (inclusive) in a file with new content.
    To insert without replacing, usage depends on logic, but typically you replace a range.
    To delete, provide empty content.

    Args:
        path (str): Path to the file.
        content (str): New content to insert/replace.
        start_line (int): Start line number (1-indexed).
        end_line (int): End line number (1-indexed, inclusive).
    Returns:
        str: Success message or error.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # We use a python script to handle file IO cleanly to avoid shell escaping issues
    # and to handle newlines correctly.

    script = textwrap.dedent(f"""
        import sys
        import base64
        import os

        path = "{path}"

        # Decode content
        content_b64 = "{base64.b64encode(content.encode("utf-8")).decode("utf-8")}"
        new_content = base64.b64decode(content_b64).decode('utf-8')

        start = {start_line}
        end = {end_line}

        if not os.path.exists(path):
            print(f"Error: File {{path}} not found")
            sys.exit(1)

        with open(path, 'r') as f:
            lines = f.readlines()

        # Validate bounds
        # start is 1-indexed
        if start < 1:
             print(f"Error: Start line {{start}} must be >= 1")
             sys.exit(1)

        # Convert to 0-indexed slice
        idx_start = start - 1
        idx_end = end # slice is exclusive, but end_line is inclusive, so end (idx) match

        # If start is beyond end of file, we append?
        # Standard behavior: if start > len, maybe just append?
        # Let's enforce bounds strictly for safety/clarity unless it gets annoying.
        if idx_start > len(lines):
             print(f"Error: Start line {{start}} is beyond EOF ({{len(lines)}} lines)")
             sys.exit(1)

        # Prepare new lines
        # Determine if we need to add a newline to the new content chunks
        # Usually user provides a block. We split it into lines.
        replacement_lines = new_content.splitlines(keepends=True)

        # If the input string didn't have a trailing newline but we are inserting as lines,
        # we might want to ensure consistency.
        # But `splitlines(keepends=True)` keeps \n if present.
        # If user sends "a\nb", we get ["a\n", "b"].
        # If we insert "b" into middle of file, it merges with next line if no \n.
        # Let's trust the user's content exactly.

        lines[idx_start:idx_end] = replacement_lines

        with open(path, 'w') as f:
            f.writelines(lines)

        print(f"Successfully edited {{path}} (Replaced lines {{start}}-{{end}})")
    """)

    return _run_python_script(sandbox, script, "edit_file")


def search_file(path: str, regex: str, *, tool_context: ToolContext) -> str:
    """
    Search for a regular expression in a file.

    Args:
        path (str): Path to the file.
        regex (str): valid python/grep regex pattern.
    Returns:
        str: Matching lines with line numbers.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # Use grep -nE for extended regex and line numbers
    # Ensure regex is quoted
    cmd = f"grep -nE {shlex.quote(regex)} {shlex.quote(path)}"
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code == 1:
        return "No matches found."
    elif exit_code != 0:
        return f"Error searching file: {output}"

    return output


def replace_in_file(
    path: str, old_text: str, new_text: str, *, tool_context: ToolContext
) -> str:
    """
    Replace all occurrences of a string with another string in a file.
    Performs exact string replacement (not regex).

    Args:
        path (str): Path to the file.
        old_text (str): The exact string to find.
        new_text (str): The string to replace it with.
    Returns:
        str: Success message or error.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    script = textwrap.dedent(f"""
        import sys
        import base64
        import os

        path = "{path}"

        old_b64 = "{base64.b64encode(old_text.encode("utf-8")).decode("utf-8")}"
        new_b64 = "{base64.b64encode(new_text.encode("utf-8")).decode("utf-8")}"

        old_str = base64.b64decode(old_b64).decode('utf-8')
        new_str = base64.b64decode(new_b64).decode('utf-8')

        if not os.path.exists(path):
            print(f"Error: File {{path}} not found")
            sys.exit(1)

        with open(path, 'r') as f:
            content = f.read()

        if old_str not in content:
            print(f"Warning: String not found in {{path}}. No changes made.")
            # We don't exit 1, just warn?
            sys.exit(0)

        new_content = content.replace(old_str, new_str)

        with open(path, 'w') as f:
            f.write(new_content)

        print(f"Successfully replaced text in {{path}}")
    """)

    return _run_python_script(sandbox, script, "replace_in_file")


async def str_replace_edit(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    analyze_failure: bool = True,
    *,
    tool_context: ToolContext,
) -> str:
    """
    Replace a string in a file using 9 matching strategies.

    Strategies tried in order:
    1. simple - Exact match
    2. line_trimmed - Ignore leading/trailing whitespace per line
    3. block_anchor - Match first/last lines with fuzzy middle (Levenshtein)
    4. whitespace_normalized - Collapse all whitespace
    5. indentation_flexible - Remove common indentation
    6. escape_normalized - Unescape \\n, \\t, etc.
    7. trimmed_boundary - Try trimmed version of search string
    8. context_aware - 50% middle-line similarity matching
    9. multi_occurrence - Find all exact matches (for replace_all)

    Args:
        path (str): Path to the file.
        old_string (str): The string to find.
        new_string (str): The string to replace it with.
        replace_all (bool): If True, replace all occurrences. Default False.
        analyze_failure (bool): If True, use LLM to analyze why edit failed. Default True.
    Returns:
        str: Success message or error with context. If analyze_failure is True and
        the edit fails, includes LLM analysis of why the edit failed.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # Read file content before edit (needed for failure analysis)
    file_content = None
    if analyze_failure:
        cat_cmd = f"cat {shlex.quote(path)}"
        file_content, cat_exit = sandbox.run_command_in_container(cat_cmd)
        if cat_exit != 0:
            file_content = None  # File doesn't exist or can't be read

    # The script includes the full OpenCode-style replacer chain
    script = textwrap.dedent(f"""
        import sys
        import base64
        import os
        import re
        import textwrap

        path = "{path}"
        replace_all = {replace_all}

        old_b64 = "{base64.b64encode(old_string.encode("utf-8")).decode("utf-8")}"
        new_b64 = "{base64.b64encode(new_string.encode("utf-8")).decode("utf-8")}"

        old_str = base64.b64decode(old_b64).decode('utf-8')
        new_str = base64.b64decode(new_b64).decode('utf-8')

        if old_str == new_str:
            print("Error: oldString and newString must be different")
            sys.exit(1)

        if not os.path.exists(path):
            print(f"Error: File {{path}} not found")
            sys.exit(1)

        with open(path, 'r') as f:
            content = f.read()

        # =================================================================
        # Constants
        # =================================================================
        SINGLE_CANDIDATE_SIMILARITY_THRESHOLD = 0.0
        MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD = 0.3

        # =================================================================
        # Utility Functions
        # =================================================================

        def unescape_llm_output(text):
            def replacer(match):
                char = match.group(1)
                mapping = {{'n': '\\n', 't': '\\t', 'r': '\\r', "'": "'", '"': '"', '`': '`', '\\\\': '\\\\', '\\n': '\\n', '$': '$'}}
                return mapping.get(char, match.group(0))
            return re.sub(r'\\\\(n|t|r|\\'|"|`|\\\\|\\n|\\$)', replacer, text)

        def levenshtein_distance(s1, s2):
            if s1 == "" or s2 == "":
                return max(len(s1), len(s2))
            if len(s1) < len(s2):
                return levenshtein_distance(s2, s1)
            previous_row = list(range(len(s2) + 1))
            for i, c1 in enumerate(s1):
                current_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = previous_row[j + 1] + 1
                    deletions = current_row[j] + 1
                    substitutions = previous_row[j] + (c1 != c2)
                    current_row.append(min(insertions, deletions, substitutions))
                previous_row = current_row
            return previous_row[-1]

        def detect_indentation(text):
            lines = text.split('\\n')
            tab_count = 0
            space_counts = {{2: 0, 4: 0, 8: 0}}
            for line in lines:
                if line.startswith('\\t'):
                    tab_count += 1
                elif line.startswith('  '):
                    stripped = line.lstrip(' ')
                    leading = len(line) - len(stripped)
                    if leading >= 8 and leading % 8 == 0:
                        space_counts[8] += 1
                    elif leading >= 4 and leading % 4 == 0:
                        space_counts[4] += 1
                    elif leading >= 2 and leading % 2 == 0:
                        space_counts[2] += 1
            if tab_count > sum(space_counts.values()):
                return "TABS"
            elif space_counts[4] >= space_counts[2] and space_counts[4] >= space_counts[8]:
                return "4 SPACES"
            elif space_counts[2] >= space_counts[4] and space_counts[2] >= space_counts[8]:
                return "2 SPACES"
            elif space_counts[8] > 0:
                return "8 SPACES"
            return "MIXED/UNKNOWN"

        def visualize_whitespace(text):
            return text.replace('\\t', '→TAB→').replace(' ', '·')

        def adapt_indentation(matched_str, new_str):
            matched_indent = detect_indentation(matched_str)
            new_indent = detect_indentation(new_str)

            if matched_indent == new_indent or matched_indent == "MIXED/UNKNOWN":
                return new_str

            if matched_indent == "TABS" and new_indent in ("2 SPACES", "4 SPACES", "8 SPACES"):
                space_size = int(new_indent.split()[0])
                result_lines = []
                for line in new_str.split('\\n'):
                    stripped = line.lstrip(' ')
                    leading_spaces = len(line) - len(stripped)
                    if leading_spaces > 0:
                        tabs = leading_spaces // space_size
                        remainder = leading_spaces % space_size
                        result_lines.append('\\t' * tabs + ' ' * remainder + stripped)
                    else:
                        result_lines.append(line)
                return '\\n'.join(result_lines)

            if matched_indent in ("2 SPACES", "4 SPACES", "8 SPACES") and new_indent == "TABS":
                space_size = int(matched_indent.split()[0])
                result_lines = []
                for line in new_str.split('\\n'):
                    result_lines.append(line.replace('\\t', ' ' * space_size))
                return '\\n'.join(result_lines)

            return new_str

        # =================================================================
        # Generator-based Replacers (OpenCode architecture)
        # Each yields actual matched substrings from content
        # =================================================================

        def simple_replacer(content, find):
            yield find

        def line_trimmed_replacer(content, find):
            original_lines = content.split('\\n')
            search_lines = find.split('\\n')
            if search_lines and search_lines[-1] == '':
                search_lines.pop()

            for i in range(len(original_lines) - len(search_lines) + 1):
                matches = True
                for j in range(len(search_lines)):
                    if original_lines[i + j].strip() != search_lines[j].strip():
                        matches = False
                        break
                if matches:
                    match_start = sum(len(original_lines[k]) + 1 for k in range(i))
                    match_end = match_start
                    for k in range(len(search_lines)):
                        match_end += len(original_lines[i + k])
                        if k < len(search_lines) - 1:
                            match_end += 1
                    yield content[match_start:match_end]

        def block_anchor_replacer(content, find):
            original_lines = content.split('\\n')
            search_lines = find.split('\\n')
            if len(search_lines) < 3:
                return
            if search_lines and search_lines[-1] == '':
                search_lines.pop()

            first_line = search_lines[0].strip()
            last_line = search_lines[-1].strip()

            candidates = []
            for i in range(len(original_lines)):
                if original_lines[i].strip() != first_line:
                    continue
                for j in range(i + 2, len(original_lines)):
                    if original_lines[j].strip() == last_line:
                        candidates.append((i, j))
                        break

            if not candidates:
                return

            if len(candidates) == 1:
                start, end = candidates[0]
                actual_size = end - start + 1
                search_size = len(search_lines)
                similarity = 0.0
                lines_to_check = min(search_size - 2, actual_size - 2)

                if lines_to_check > 0:
                    for j in range(1, min(search_size - 1, actual_size - 1)):
                        orig = original_lines[start + j].strip()
                        srch = search_lines[j].strip()
                        max_len = max(len(orig), len(srch))
                        if max_len == 0:
                            continue
                        dist = levenshtein_distance(orig, srch)
                        similarity += (1 - dist / max_len) / lines_to_check
                        if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
                            break
                else:
                    similarity = 1.0

                if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
                    match_start = sum(len(original_lines[k]) + 1 for k in range(start))
                    match_end = match_start
                    for k in range(start, end + 1):
                        match_end += len(original_lines[k])
                        if k < end:
                            match_end += 1
                    yield content[match_start:match_end]
                return

            best_match = None
            max_sim = -1.0
            for start, end in candidates:
                actual_size = end - start + 1
                search_size = len(search_lines)
                similarity = 0.0
                lines_to_check = min(search_size - 2, actual_size - 2)

                if lines_to_check > 0:
                    for j in range(1, min(search_size - 1, actual_size - 1)):
                        orig = original_lines[start + j].strip()
                        srch = search_lines[j].strip()
                        max_len = max(len(orig), len(srch))
                        if max_len == 0:
                            continue
                        dist = levenshtein_distance(orig, srch)
                        similarity += 1 - dist / max_len
                    similarity /= lines_to_check
                else:
                    similarity = 1.0

                if similarity > max_sim:
                    max_sim = similarity
                    best_match = (start, end)

            if max_sim >= MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD and best_match:
                start, end = best_match
                match_start = sum(len(original_lines[k]) + 1 for k in range(start))
                match_end = match_start
                for k in range(start, end + 1):
                    match_end += len(original_lines[k])
                    if k < end:
                        match_end += 1
                yield content[match_start:match_end]

        def whitespace_normalized_replacer(content, find):
            def normalize(text):
                return re.sub(r'\\s+', ' ', text).strip()

            normalized_find = normalize(find)
            lines = content.split('\\n')

            for i, line in enumerate(lines):
                if normalize(line) == normalized_find:
                    yield line
                elif normalized_find in normalize(line):
                    words = find.strip().split()
                    if words:
                        pattern = r'\\s+'.join(re.escape(w) for w in words)
                        try:
                            match = re.search(pattern, line)
                            if match:
                                yield match.group(0)
                        except:
                            pass

            find_lines = find.split('\\n')
            if len(find_lines) > 1:
                for i in range(len(lines) - len(find_lines) + 1):
                    block = lines[i:i + len(find_lines)]
                    if normalize('\\n'.join(block)) == normalized_find:
                        yield '\\n'.join(block)

        def indentation_flexible_replacer(content, find):
            def remove_indent(text):
                lines = text.split('\\n')
                non_empty = [l for l in lines if l.strip()]
                if not non_empty:
                    return text
                min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
                return '\\n'.join(l if not l.strip() else l[min_indent:] for l in lines)

            normalized_find = remove_indent(find)
            content_lines = content.split('\\n')
            find_lines = find.split('\\n')

            for i in range(len(content_lines) - len(find_lines) + 1):
                block = '\\n'.join(content_lines[i:i + len(find_lines)])
                if remove_indent(block) == normalized_find:
                    yield block

        def escape_normalized_replacer(content, find):
            unescaped = unescape_llm_output(find)
            if unescaped in content:
                yield unescaped

            lines = content.split('\\n')
            find_lines = unescaped.split('\\n')
            for i in range(len(lines) - len(find_lines) + 1):
                block = '\\n'.join(lines[i:i + len(find_lines)])
                if unescape_llm_output(block) == unescaped:
                    yield block

        def trimmed_boundary_replacer(content, find):
            trimmed = find.strip()
            if trimmed == find:
                return
            if trimmed in content:
                yield trimmed

            lines = content.split('\\n')
            find_lines = find.split('\\n')
            for i in range(len(lines) - len(find_lines) + 1):
                block = '\\n'.join(lines[i:i + len(find_lines)])
                if block.strip() == trimmed:
                    yield block

        def context_aware_replacer(content, find):
            find_lines = find.split('\\n')
            if len(find_lines) < 3:
                return
            if find_lines and find_lines[-1] == '':
                find_lines.pop()

            content_lines = content.split('\\n')
            first_line = find_lines[0].strip()
            last_line = find_lines[-1].strip()

            for i in range(len(content_lines)):
                if content_lines[i].strip() != first_line:
                    continue
                for j in range(i + 2, len(content_lines)):
                    if content_lines[j].strip() == last_line:
                        block_lines = content_lines[i:j + 1]
                        if len(block_lines) == len(find_lines):
                            matching = 0
                            total = 0
                            for k in range(1, len(block_lines) - 1):
                                bl = block_lines[k].strip()
                                fl = find_lines[k].strip()
                                if bl or fl:
                                    total += 1
                                    if bl == fl:
                                        matching += 1
                            if total == 0 or matching / total >= 0.5:
                                yield '\\n'.join(block_lines)
                                break
                        break

        def multi_occurrence_replacer(content, find):
            start = 0
            while True:
                idx = content.find(find, start)
                if idx == -1:
                    break
                yield find
                start = idx + len(find)

        # =================================================================
        # Main Replace Logic (matching OpenCode's replace function)
        # =================================================================

        REPLACER_CHAIN = [
            ("simple", simple_replacer),
            ("line_trimmed", line_trimmed_replacer),
            ("block_anchor", block_anchor_replacer),
            ("whitespace_normalized", whitespace_normalized_replacer),
            ("indentation_flexible", indentation_flexible_replacer),
            ("escape_normalized", escape_normalized_replacer),
            ("trimmed_boundary", trimmed_boundary_replacer),
            ("context_aware", context_aware_replacer),
            ("multi_occurrence", multi_occurrence_replacer),
        ]

        not_found = True

        for name, replacer in REPLACER_CHAIN:
            try:
                for search in replacer(content, old_str):
                    index = content.find(search)
                    if index == -1:
                        continue

                    not_found = False

                    adapted_new = adapt_indentation(search, new_str)

                    if replace_all:
                        new_content = content.replace(search, adapted_new)
                        with open(path, 'w') as f:
                            f.write(new_content)
                        count = content.count(search)
                        print(f"Successfully replaced {{count}} occurrence(s) in {{path}}")
                        sys.exit(0)

                    last_index = content.rfind(search)
                    if index != last_index:
                        continue

                    new_content = content[:index] + adapted_new + content[index + len(search):]
                    with open(path, 'w') as f:
                        f.write(new_content)
                    print(f"Successfully replaced text in {{path}}")
                    sys.exit(0)
            except Exception as e:
                continue

        # All strategies failed
        if not_found:
            file_indent = detect_indentation(content)
            old_indent = detect_indentation(old_str)

            print(f"Error: String not found in {{path}}.")
            print("                       indentation_flexible, escape_normalized, trimmed_boundary,")
            print("                       context_aware, multi_occurrence")
            if file_indent != old_indent and file_indent != "MIXED/UNKNOWN" and old_indent != "MIXED/UNKNOWN":
                print(f"\\n⚠️  INDENTATION MISMATCH DETECTED!")
                print(f"    The file uses {{file_indent}} but your old_string uses {{old_indent}}.")

            old_lines = old_str.split('\\n')[:5]
            print(f"\\n--- Your old_string (first 5 lines, whitespace visible) ---")
            for i, line in enumerate(old_lines):
                print(f"  {{i+1}}: {{visualize_whitespace(line)}}")

            content_lines = content.split('\\n')
            search_first = old_str.split('\\n')[0].strip() if old_str.strip() else ""
            if search_first and len(search_first) > 5:
                print(f"\\n--- Lines matching first line (trimmed): '{{search_first[:50]}}...' ---")
                found = 0
                for i, line in enumerate(content_lines):
                    if line.strip() == search_first:
                        found += 1
                        print(f"  Line {{i+1}}: {{line[:80]}}...")
                        if found >= 3:
                            print("  ...")
                            break
                if found == 0:
                    print("  (No matching lines found)")
            sys.exit(1)
        else:
            print(f"Error: Found multiple matches for oldString in {{path}}.")
            print("Provide more surrounding lines in oldString to identify the correct match,")
            print("or use replace_all=True to replace all occurrences.")

            lines = content.split('\\n')
            pos = 0
            occurrence = 0
            while True:
                idx = content.find(old_str, pos)
                if idx == -1:
                    break
                occurrence += 1
                line_num = content[:idx].count('\\n') + 1
                start_line = max(0, line_num - 3)
                end_line = min(len(lines), line_num + 2)
                print(f"\\n[Occurrence {{occurrence}} around line {{line_num}}]")
                for i in range(start_line, end_line):
                    prefix = ">>>" if i == line_num - 1 else "   "
                    print(f"{{prefix}} {{i + 1}}: {{lines[i]}}")
                pos = idx + 1
                if occurrence >= 5:
                    remaining = content.count(old_str) - occurrence
                    if remaining > 0:
                        print(f"\\n... and {{remaining}} more occurrences")
                    break
            sys.exit(1)
    """)

    result = _run_python_script(sandbox, script, "str_replace_edit")

    # If edit failed and analyze_failure is enabled, get LLM analysis
    if result.startswith("Error") and analyze_failure and file_content:
        try:
            analysis = await analyze_edit_failure(
                file_path=path,
                old_string=old_string,
                new_string=new_string,
                error_message=result,
                file_content=file_content,
                tool_context=tool_context,
            )
            if analysis:
                result = f"{result}\n\n--- LLM Failure Analysis ---\n{analysis}"
        except Exception as e:
            logger.warning(f"Failed to get LLM failure analysis: {e}")

    return result


def list_dir(path: str = ".", *, tool_context: ToolContext) -> str:
    """
    List contents of a directory.

    Args:
        path (str): Directory path (default current dir).
    Returns:
        str: Directory listing.
    """
    sandbox = get_sandbox_from_context(tool_context, "main")

    # ls -F appends / to dirs, * to executables
    cmd = f"ls -F {shlex.quote(path)}"
    output, exit_code = sandbox.run_command_in_container(cmd)

    if exit_code != 0:
        return f"Error listing directory: {output}"

    return output
