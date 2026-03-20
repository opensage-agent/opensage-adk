"""String utilities for str_replace_edit multi-strategy replacement.

This module provides a replacer chain for handling common LLM-generated
code edit failures:
- Tab vs space mismatches
- Escape sequence issues (\\n → \n)
- Indentation variations
- Fuzzy block matching

Implementation ported from OpenCode's edit.ts replacer chain.
"""

import re
from typing import Callable, Generator, List, Optional, Tuple

# =============================================================================
# Constants
# =============================================================================

# Similarity thresholds for block anchor fallback matching
SINGLE_CANDIDATE_SIMILARITY_THRESHOLD = 0.0
MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD = 0.3


# =============================================================================
# Utility Functions
# =============================================================================


def unescape_llm_output(text: str) -> str:
    """Fix common LLM escaping bugs (\\n → newline, \\t → tab, etc.).

    LLMs often produce double-escaped strings when they intend single escapes.
    This normalizes common patterns.

    Args:
        text (str): String potentially containing double-escaped sequences.
    Returns:
        str: String with escape sequences normalized.
    """

    def replacer(match: re.Match[str]) -> str:
        char = match.group(1)
        mapping = {
            "n": "\n",
            "t": "\t",
            "r": "\r",
            "'": "'",
            '"': '"',
            "`": "`",
            "\\": "\\",
            "\n": "\n",
            "$": "$",
        }
        return mapping.get(char, match.group(0))

    return re.sub(r"\\(n|t|r|'|\"|`|\\|\n|\$)", replacer, text)


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings.

    Args:
        s1 (str): First string.
        s2 (str): Second string.
    Returns:
        int: Minimum number of single-character edits required to change s1 into s2.
    """
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


def levenshtein_similarity(a: str, b: str) -> float:
    """Calculate similarity ratio using Levenshtein distance.

    Args:
        a (str): First string.
        b (str): Second string.
    Returns:
        float: Similarity ratio between 0.0 (completely different) and 1.0 (identical).
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    distance = levenshtein_distance(a, b)
    max_len = max(len(a), len(b))
    return 1.0 - (distance / max_len)


def _get_leading_whitespace(line: str) -> str:
    """Extract leading whitespace from a line."""
    match = re.match(r"^(\s*)", line)
    return match.group(1) if match else ""


# =============================================================================
# Replacer Type Definition
# =============================================================================

# Each replacer is a generator that yields potential matches from content
Replacer = Callable[[str, str], Generator[str, None, None]]


# =============================================================================
# Replacer Functions (Generator-based, matching OpenCode architecture)
# =============================================================================


def simple_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 1: Simple exact match.

    Just yields the find string as-is. The main replace function
    checks if it exists in content.
    """
    yield find


def line_trimmed_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 2: Match lines with trimmed comparison.

    Handles tab vs space mismatch - the most common failure case (~60%).
    Yields the actual matched substring from content (with original whitespace).
    """
    original_lines = content.split("\n")
    search_lines = find.split("\n")

    # Remove trailing empty line if present
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    for i in range(len(original_lines) - len(search_lines) + 1):
        matches = True

        for j in range(len(search_lines)):
            original_trimmed = original_lines[i + j].strip()
            search_trimmed = search_lines[j].strip()

            if original_trimmed != search_trimmed:
                matches = False
                break

        if matches:
            # Calculate the actual substring position
            match_start_index = sum(len(original_lines[k]) + 1 for k in range(i))
            match_end_index = match_start_index
            for k in range(len(search_lines)):
                match_end_index += len(original_lines[i + k])
                if k < len(search_lines) - 1:
                    match_end_index += 1  # Add newline except for last line

            yield content[match_start_index:match_end_index]


def block_anchor_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 3: Match blocks using first/last line anchors with fuzzy middle.

    Uses Levenshtein similarity for middle content matching.
    """
    original_lines = content.split("\n")
    search_lines = find.split("\n")

    if len(search_lines) < 3:
        return

    # Remove trailing empty line if present
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    first_line_search = search_lines[0].strip()
    last_line_search = search_lines[-1].strip()
    search_block_size = len(search_lines)

    # Collect all candidate positions where both anchors match
    candidates: List[Tuple[int, int]] = []
    for i in range(len(original_lines)):
        if original_lines[i].strip() != first_line_search:
            continue

        # Look for matching last line after this first line
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last_line_search:
                candidates.append((i, j))
                break  # Only match first occurrence of last line

    if not candidates:
        return

    # Handle single candidate scenario (using relaxed threshold)
    if len(candidates) == 1:
        start_line, end_line = candidates[0]
        actual_block_size = end_line - start_line + 1

        similarity = 0.0
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)

        if lines_to_check > 0:
            for j in range(1, min(search_block_size - 1, actual_block_size - 1)):
                original_line = original_lines[start_line + j].strip()
                search_line = search_lines[j].strip()
                max_len = max(len(original_line), len(search_line))
                if max_len == 0:
                    continue
                distance = levenshtein_distance(original_line, search_line)
                similarity += (1 - distance / max_len) / lines_to_check

                if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
                    break
        else:
            similarity = 1.0

        if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
            match_start_index = sum(
                len(original_lines[k]) + 1 for k in range(start_line)
            )
            match_end_index = match_start_index
            for k in range(start_line, end_line + 1):
                match_end_index += len(original_lines[k])
                if k < end_line:
                    match_end_index += 1
            yield content[match_start_index:match_end_index]
        return

    # Calculate similarity for multiple candidates
    best_match: Optional[Tuple[int, int]] = None
    max_similarity = -1.0

    for start_line, end_line in candidates:
        actual_block_size = end_line - start_line + 1

        similarity = 0.0
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)

        if lines_to_check > 0:
            for j in range(1, min(search_block_size - 1, actual_block_size - 1)):
                original_line = original_lines[start_line + j].strip()
                search_line = search_lines[j].strip()
                max_len = max(len(original_line), len(search_line))
                if max_len == 0:
                    continue
                distance = levenshtein_distance(original_line, search_line)
                similarity += 1 - distance / max_len
            similarity /= lines_to_check
        else:
            similarity = 1.0

        if similarity > max_similarity:
            max_similarity = similarity
            best_match = (start_line, end_line)

    if max_similarity >= MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD and best_match:
        start_line, end_line = best_match
        match_start_index = sum(len(original_lines[k]) + 1 for k in range(start_line))
        match_end_index = match_start_index
        for k in range(start_line, end_line + 1):
            match_end_index += len(original_lines[k])
            if k < end_line:
                match_end_index += 1
        yield content[match_start_index:match_end_index]


def whitespace_normalized_replacer(
    content: str, find: str
) -> Generator[str, None, None]:
    """Strategy 4: Collapse all whitespace for matching.

    Handles cases with extra/missing spaces, mixed whitespace types.
    """

    def normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    normalized_find = normalize_whitespace(find)

    # Handle single line matches
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if normalize_whitespace(line) == normalized_find:
            yield line
        else:
            # Check for substring matches
            normalized_line = normalize_whitespace(line)
            if normalized_find in normalized_line:
                words = find.strip().split()
                if words:
                    pattern = r"\s+".join(re.escape(word) for word in words)
                    try:
                        regex = re.compile(pattern)
                        match = regex.search(line)
                        if match:
                            yield match.group(0)
                    except re.error:
                        pass

    # Handle multi-line matches
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = lines[i : i + len(find_lines)]
            if normalize_whitespace("\n".join(block)) == normalized_find:
                yield "\n".join(block)


def indentation_flexible_replacer(
    content: str, find: str
) -> Generator[str, None, None]:
    """Strategy 5: Remove common indentation, then match.

    Handles cases where the entire block is indented differently.
    """
    import textwrap

    def remove_indentation(text: str) -> str:
        lines = text.split("\n")
        non_empty_lines = [line for line in lines if line.strip()]
        if not non_empty_lines:
            return text

        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty_lines)
        return "\n".join(
            line if not line.strip() else line[min_indent:] for line in lines
        )

    normalized_find = remove_indentation(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")

    for i in range(len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i : i + len(find_lines)])
        if remove_indentation(block) == normalized_find:
            yield block


def escape_normalized_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 6: Try matching with unescaped versions.

    Fixes cases where LLM outputs \\n instead of actual newline.
    """
    unescaped_find = unescape_llm_output(find)

    # Try direct match with unescaped find string
    if unescaped_find in content:
        yield unescaped_find

    # Also try finding escaped versions in content that match unescaped find
    lines = content.split("\n")
    find_lines = unescaped_find.split("\n")

    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        unescaped_block = unescape_llm_output(block)

        if unescaped_block == unescaped_find:
            yield block


def trimmed_boundary_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 7: Try trimmed versions of the find string.

    Handles cases where find has extra leading/trailing whitespace.
    """
    trimmed_find = find.strip()

    if trimmed_find == find:
        # Already trimmed, no point in trying
        return

    # Try to find the trimmed version
    if trimmed_find in content:
        yield trimmed_find

    # Also try finding blocks where trimmed content matches
    lines = content.split("\n")
    find_lines = find.split("\n")

    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])

        if block.strip() == trimmed_find:
            yield block


def context_aware_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 8: Match using context anchors with 50% similarity threshold.

    Similar to block anchor but uses different similarity calculation.
    """
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return

    # Remove trailing empty line if present
    if find_lines and find_lines[-1] == "":
        find_lines.pop()

    content_lines = content.split("\n")

    # Extract first and last lines as context anchors
    first_line = find_lines[0].strip()
    last_line = find_lines[-1].strip()

    # Find blocks that start and end with the context anchors
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first_line:
            continue

        # Look for matching last line
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() == last_line:
                block_lines = content_lines[i : j + 1]
                block = "\n".join(block_lines)

                # Check if the middle content has reasonable similarity
                if len(block_lines) == len(find_lines):
                    matching_lines = 0
                    total_non_empty_lines = 0

                    for k in range(1, len(block_lines) - 1):
                        block_line = block_lines[k].strip()
                        find_line = find_lines[k].strip()

                        if block_line or find_line:
                            total_non_empty_lines += 1
                            if block_line == find_line:
                                matching_lines += 1

                    if (
                        total_non_empty_lines == 0
                        or matching_lines / total_non_empty_lines >= 0.5
                    ):
                        yield block
                        break  # Only match first occurrence
                break


def multi_occurrence_replacer(content: str, find: str) -> Generator[str, None, None]:
    """Strategy 9: Find all exact matches.

    Allows the replace function to handle multiple occurrences
    based on replaceAll parameter.
    """
    start_index = 0

    while True:
        index = content.find(find, start_index)
        if index == -1:
            break

        yield find
        start_index = index + len(find)


# =============================================================================
# Default Replacer Chain (matching OpenCode order)
# =============================================================================

DEFAULT_REPLACER_CHAIN: List[Tuple[str, Replacer]] = [
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


# =============================================================================
# Main Replace Function (matching OpenCode's replace function)
# =============================================================================


def replace(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Replace old_string with new_string in content using multiple strategies.

    Tries each replacer in the chain until one succeeds. This matches
    OpenCode's replace function architecture.

    Args:
        content (str): The file content to modify.
        old_string (str): The string to search for.
        new_string (str): The replacement string.
        replace_all (bool): If True, replace all occurrences. Default False.
    Returns:
        str: The modified content string.

    Raises:
        ValueError: If old_string equals new_string.
        ValueError: If old_string not found in content.
        ValueError: If multiple matches found and replace_all is False.
    """
    if old_string == new_string:
        raise ValueError("oldString and newString must be different")

    not_found = True

    for name, replacer in DEFAULT_REPLACER_CHAIN:
        for search in replacer(content, old_string):
            index = content.find(search)
            if index == -1:
                continue

            not_found = False

            if replace_all:
                return content.replace(search, new_string)

            # Check if there's only one occurrence
            last_index = content.rfind(search)
            if index != last_index:
                continue  # Multiple occurrences, try next replacer

            # Single occurrence - do the replacement
            return content[:index] + new_string + content[index + len(search) :]

    if not_found:
        raise ValueError("oldString not found in content")

    raise ValueError(
        "Found multiple matches for oldString. Provide more surrounding lines "
        "in oldString to identify the correct match."
    )


def replace_with_info(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> Tuple[str, str]:
    """Replace with strategy info - wrapper for debugging.

    Raises:
      ValueError: Raised when this operation fails.
        Returns:
            Tuple[str, str]: Tuple of (modified_content, strategy_name)
    """
    if old_string == new_string:
        raise ValueError("oldString and newString must be different")

    not_found = True

    for name, replacer in DEFAULT_REPLACER_CHAIN:
        for search in replacer(content, old_string):
            index = content.find(search)
            if index == -1:
                continue

            not_found = False

            if replace_all:
                return content.replace(search, new_string), name

            last_index = content.rfind(search)
            if index != last_index:
                continue

            return content[:index] + new_string + content[index + len(search) :], name

    if not_found:
        raise ValueError("oldString not found in content")

    raise ValueError(
        "Found multiple matches for oldString. Provide more surrounding lines "
        "in oldString to identify the correct match."
    )


# =============================================================================
# Legacy API (for backward compatibility)
# =============================================================================


def replace_with_fallback(
    content: str,
    old_string: str,
    new_string: str,
    replacer_chain: Optional[List[Tuple[str, Replacer]]] = None,
) -> Tuple[str, str]:
    """Legacy API - use replace_with_info instead.

    Kept for backward compatibility with existing code.
    """
    return replace_with_info(content, old_string, new_string, replace_all=False)


def get_multiple_match_info(content: str, old_string: str) -> List[Tuple[int, str]]:
    """Find all occurrences with line numbers and context.

    Useful for error messages when multiple matches found.

    Args:
        content (str): File content to search.
        old_string (str): String to find.
    Returns:
        List[Tuple[int, str]]: List of (line_number, context_snippet) tuples.
    """
    lines = content.split("\n")
    matches = []

    pos = 0
    while True:
        idx = content.find(old_string, pos)
        if idx == -1:
            break

        line_num = content[:idx].count("\n") + 1
        start_line = max(0, line_num - 3)
        end_line = min(len(lines), line_num + 2)
        context = "\n".join(
            f"  {i + 1}: {lines[i]}" for i in range(start_line, end_line)
        )

        matches.append((line_num, context))
        pos = idx + 1

    return matches


# =============================================================================
# Standalone Replacer Functions (for direct use, matching old API)
# =============================================================================


def exact_replacer(content: str, old_string: str, new_string: str) -> Optional[str]:
    """Direct exact match replacement (legacy API)."""
    if old_string in content:
        count = content.count(old_string)
        if count == 1:
            return content.replace(old_string, new_string, 1)
    return None


def line_trimmed_replacer_direct(
    content: str, old_string: str, new_string: str
) -> Optional[str]:
    """Direct line-trimmed replacement with indentation preservation (legacy API)."""
    for match in line_trimmed_replacer(content, old_string):
        index = content.find(match)
        if index != -1:
            last_index = content.rfind(match)
            if index == last_index:
                return content[:index] + new_string + content[index + len(match) :]
    return None
