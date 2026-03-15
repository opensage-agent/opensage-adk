import re
import string
from pathlib import Path
from typing import List, Union

from patchagent.builder import Builder
from patchagent.logger import logger
from patchagent.parser.utils import guess_relpath


def revise_clike_patch(patch: str, builder: Builder) -> str:
    def _revise_hunk(lines: List[str], file_content: List[str]) -> str:
        orignal_line_number = sum(1 for line in lines[1:] if not line.startswith("+"))
        patched_line_number = sum(1 for line in lines[1:] if not line.startswith("-"))

        # @@ -3357,10 +3357,16 @@
        # extract the line number and the number of lines
        assert re.match(r"@@ -\d+,\d+ \+\d+,\d+ @@", lines[0]) is not None
        numbers = re.findall(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@", lines[0])[0]

        hunk = ""
        modified_line_number = None
        corrected_line_number = None

        for test_line_no in range(max(1, int(numbers[0]) - 5), min(len(file_content) - len(lines), int(numbers[0]) + 5)):
            temp_hunk = ""
            temp_modified_line_number = 0
            line_number = test_line_no

            for line_no in range(1, len(lines)):
                if lines[line_no].startswith("-"):
                    if lines[line_no][1:].strip() != file_content[line_number - 1].strip():
                        temp_modified_line_number += 1
                    temp_hunk += "-" + file_content[line_number - 1]
                    line_number += 1
                elif lines[line_no].startswith("+"):
                    temp_hunk += lines[line_no] + "\n"
                else:
                    if lines[line_no].strip() != file_content[line_number - 1].strip():
                        temp_modified_line_number += 1
                    temp_hunk += " " + file_content[line_number - 1]
                    line_number += 1

            if modified_line_number is None or temp_modified_line_number < modified_line_number:
                modified_line_number = temp_modified_line_number
                corrected_line_number = test_line_no
                hunk = temp_hunk

        header = f"@@ -{corrected_line_number},{orignal_line_number} +{corrected_line_number},{patched_line_number} @@\n"

        return header + hunk

    def _revise_block(lines: List[str], source_path: Path) -> List[str]:
        assert re.match(r"--- a/.*", lines[0]) is not None
        assert re.match(r"\+\+\+ b/.*", lines[1]) is not None

        file_path_a = re.findall(r"--- a/(.*)", lines[0])[0]
        guess_file_path_a = guess_relpath(source_path, Path(file_path_a))

        assert guess_file_path_a is not None

        revised_file_path_a = guess_file_path_a.as_posix()

        revised_lines = [
            f"--- a/{revised_file_path_a}\n",
            f"+++ b/{revised_file_path_a}\n",
        ]

        with (source_path / revised_file_path_a).open("r", errors="ignore") as f:
            file_content = f.readlines()

        last_line = -1
        for line_no in range(2, len(lines)):
            if lines[line_no].startswith("@@"):
                if last_line != -1:
                    hunk_lines = _revise_hunk(lines[last_line:line_no], file_content)
                    revised_lines.append(hunk_lines)
                last_line = line_no
        if last_line != -1:
            hunk_lines = _revise_hunk(lines[last_line:], file_content)
            revised_lines.append(hunk_lines)

        return revised_lines

    def _revise_patch(patch: str, source_path: Path) -> str:
        lines = patch.splitlines()
        revised_lines = []

        last_line = -1
        for line_no in range(len(lines)):
            if lines[line_no].startswith("--- a/"):
                if last_line != -1:
                    block_lines = _revise_block(lines[last_line:line_no], source_path)
                    revised_lines += block_lines
                last_line = line_no
        if last_line != -1:
            block_lines = _revise_block(lines[last_line:], source_path)
            revised_lines += block_lines

        return "".join(revised_lines)

    try:
        formatted_patch = builder.format_patch(patch)
        if formatted_patch is not None:
            return formatted_patch

        revised_patch = _revise_patch(patch, builder.source_path)
        return builder.format_patch(revised_patch) or revised_patch
    except AssertionError:
        return patch


def extract_cpp_function_name(function_name: str) -> Union[str, None]:
    def remove_bracket_pairs(s: str, left: str, right: str) -> str:
        last_parenthesis = s.rfind(right)
        balance = 1
        for i in range(last_parenthesis - 1, -1, -1):
            if s[i] == right:
                balance += 1
            elif s[i] == left:
                balance -= 1
            if balance == 0:
                return s[:i]

        return s

    result = function_name
    if ")" in result:
        result = remove_bracket_pairs(result, "(", ")")
    if ">" in result:
        result = remove_bracket_pairs(result, "<", ">")
    if "::" in result:
        result = result.split("::")[-1]
    if " " in result:
        result = result.split(" ")[-1]

    ident_chars = string.ascii_letters + string.digits + "_~"

    if re.match(r"operator\s*[\(\+\-\*\&\|\^!~<>=]", result):
        return None

    if any(c not in ident_chars for c in result) or len(result) == 0:
        logger.warning(f"[🚧] Failed to extract function name from '{function_name}' (result: '{result})'")

    return result
