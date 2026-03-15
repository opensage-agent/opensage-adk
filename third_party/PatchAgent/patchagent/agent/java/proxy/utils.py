import re
from typing import List


def revise_patch(patch: str) -> str:
    def revise_hunk_header(lines: List[str]) -> List[str]:
        # Important! remove the newline after the header
        i = 1
        while i < len(lines):
            if not lines[i].strip():
                lines.pop(i)
            else:
                break
        # only header
        if len(lines) <= 1:
            return lines
        orignal_line_number = sum(1 for line in lines[1:] if not line.startswith("+"))
        patched_line_number = sum(1 for line in lines[1:] if not line.startswith("-"))
        match = re.findall(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@", lines[0])
        if not match:
            return lines
        line_numbers = match[0]
        header = f"@@ -{line_numbers[0]},{orignal_line_number} +{line_numbers[2]},{patched_line_number} @@"
        return [header] + lines[1:]

    def revise_block_header(lines: List[str]) -> List[str]:

        # find the hunk header
        hunk_start_index = []
        for index, line in enumerate(lines):
            if line.startswith("@@"):
                hunk_start_index.append(index)

        # hunk
        if len(hunk_start_index) == 0:
            return lines

        block_lines = []
        # multiple blocks
        for i in range(len(hunk_start_index) - 1):
            block_lines += revise_hunk_header(lines[hunk_start_index[i] : hunk_start_index[i + 1]])
        block_lines += revise_hunk_header(lines[hunk_start_index[-1] :])
        return block_lines

    # first find the block starts with --- and +++
    all_lines = patch.splitlines()
    block_start_index = []
    for index, line in enumerate(all_lines):
        if line.startswith("---"):
            block_start_index.append(index)

    # no block found
    if len(block_start_index) == 0:
        return patch

    # reconstruct the patch
    revised_patch = []
    for i in range(len(block_start_index) - 1):
        file_header = all_lines[block_start_index[i] : block_start_index[i] + 2]
        revised_block = file_header + revise_block_header(all_lines[block_start_index[i] + 2 : block_start_index[i + 1]])
        revised_patch += revised_block

    file_header = all_lines[block_start_index[-1] : block_start_index[-1] + 2]
    revised_block = file_header + revise_block_header(all_lines[block_start_index[-1] + 2 :])
    revised_patch += revised_block

    return "\n".join(revised_patch)
