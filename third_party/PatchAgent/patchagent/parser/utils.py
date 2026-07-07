import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from patchagent.logger import logger

_pathset_cache: Dict[Path, Set[Path]] = {}
ClassicStackTracePattern = r"^\s*#(\d+)\s+(0x[\w\d]+)\s+in\s+(.+)\s+(/.*)\s*"
ClassicStackTraceAliasPattern = r"^\s*#(\d+)\s+(.+?)\s+(/[^:]+:\d+:\d+)\s*\(.*\)\s*"
JVMStackTracePattern = r"at (.*)\((.*)\)"


def guess_relpath(source_path: Optional[Path], original_path: Path) -> Optional[Path]:
    if source_path is None:
        return None

    if source_path not in _pathset_cache:
        _pathset_cache[source_path] = set(p.relative_to(source_path) for p in source_path.rglob("*") if p.is_file())

    def common_suffix_length(a: Path, b: Path) -> int:
        count = 0
        for part_a, part_b in zip(reversed(a.parts), reversed(b.parts)):
            if part_a != part_b:
                break
            count += 1
        return count

    relpath = None
    max_length = 0
    for p in _pathset_cache[source_path]:
        length = common_suffix_length(p, original_path)
        if length > max_length:
            max_length = length
            relpath = p

    logger.info(f"[🔍] Guessed relpath {original_path} -> {relpath}")
    return relpath


def remove_ansi_escape(content: str) -> str:
    return re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", content)


def remove_empty_stacktrace(stacktraces: List[List[Tuple[str, Path, int, int]]]) -> List[List[Tuple[str, Path, int, int]]]:
    return [stacktrace for stacktrace in stacktraces if len(stacktrace) > 0]


def classic_simplify_and_extract_stacktraces(
    lines: List[str],
    source_path: Optional[Path] = None,
    work_path: Optional[Path] = None,
) -> Tuple[str, List[List[Tuple[str, Path, int, int]]]]:
    body: List[str] = []
    current_count: int = -1
    stacktraces: List[List[Tuple[str, Path, int, int]]] = [[]]

    for line in lines:
        if line.strip().startswith("#"):
            count = int(line.split()[0][1:])
            if count == current_count + 1:
                current_count += 1
            else:
                stacktraces.append([])
                current_count = 0
                assert count == 0

            if (match := re.search(ClassicStackTracePattern, line)) is not None:
                function_name = match.group(3)
                entries = match.group(4).split(":")

                # NOTE: Here is an example of the entries list length may be greater than 3
                # - /usr/src/zlib-1:1.3.dfsg-3.1ubuntu2/inflate.c:429:9
                # - /usr/src/zlib-1:1.3.dfsg-3.1ubuntu2/inflate.c:1279:13
                while len(entries) > 3 or (len(entries) > 1 and any(not c.isdigit() for c in entries[1:])):
                    entries[0] = entries[0] + ":" + entries[1]
                    entries.pop(1)

                if len(entries) == 0:
                    continue

                while len(entries) < 3:
                    entries.append("0")
                filepath, line_number, column_number = entries
                assert filepath.startswith("/")

                normpath = Path(filepath).resolve()
                desc = f"{normpath}:{line_number}:{column_number}"
                if f"{filepath}:{line_number}:{column_number}" != match.group(4):
                    logger.warning(f"[🚧] Incomplete file path: {desc} vs {match.group(4)}")

                # NOTE:
                # We handle stacktraces and description messages differently based on the presence of a work_path.
                #
                # Stacktraces:
                # - When a work_path is provided and the normalized source path is within that work_path,
                #   we store only the relative path (relative to work_path) along with the function name,
                #   line number, and column number.
                # - If the source path is not directly relative to work_path, we attempt to compute a relative path
                #   using the guess_relpath() function. If successful, we store that instead.
                #
                # Descriptions:
                # - If work_path is not provided, the full description (desc) is used.
                # - When work_path is provided and the source path is within work_path,
                #   we output the relative path with appended line and column numbers.

                if work_path is not None:
                    if normpath.is_relative_to(work_path):
                        stacktraces[-1].append((function_name, normpath.relative_to(work_path), int(line_number), int(column_number)))
                elif source_path is not None:
                    if (relpath := guess_relpath(source_path, normpath)) is not None:
                        stacktraces[-1].append((function_name, relpath, int(line_number), int(column_number)))
                else:
                    stacktraces[-1].append((function_name, normpath, int(line_number), int(column_number)))

                if work_path is None:
                    body.append(f"    - {function_name} {desc}")
                elif normpath.is_relative_to(work_path):
                    body.append(f"    - {function_name} {normpath.relative_to(work_path)}:{line_number}:{column_number}")

            elif (match := re.search(ClassicStackTraceAliasPattern, line)) is not None:
                function_name = match.group(2)
                entries = match.group(3).split(":")
                if len(entries) == 0:
                    continue

                while len(entries) < 3:
                    entries.append("0")
                filepath, line_number, column_number = entries
                assert filepath.startswith("/")

                normpath = Path(filepath).resolve()
                desc = f"{normpath}:{line_number}:{column_number}"
                if f"{filepath}:{line_number}:{column_number}" != match.group(3):
                    logger.warning(f"[🚧] Incomplete file path: {desc} vs {match.group(3)}")

                if work_path is not None:
                    if normpath.is_relative_to(work_path):
                        stacktraces[-1].append((function_name, normpath.relative_to(work_path), int(line_number), int(column_number)))
                elif source_path is not None:
                    if (relpath := guess_relpath(source_path, normpath)) is not None:
                        stacktraces[-1].append((function_name, relpath, int(line_number), int(column_number)))
                else:
                    stacktraces[-1].append((function_name, normpath, int(line_number), int(column_number)))

                if work_path is None:
                    body.append(f"    - {function_name} {desc}")
                elif normpath.is_relative_to(work_path):
                    body.append(f"    - {function_name} {normpath.relative_to(work_path)}:{line_number}:{column_number}")
        else:
            body.append(re.sub(r"==[0-9]+==", "", line))

    return "\n".join(body), remove_empty_stacktrace(stacktraces)


def jvm_simplify_and_extract_stacktraces(
    lines: List[str],
    source_path: Optional[Path] = None,
    work_path: Optional[Path] = None,  # TODO: This is not used in the current implementation
    handle_cyclic: bool = False,
) -> Tuple[str, List[List[Tuple[str, Path, int, int]]]]:

    fake_body: List[str | List[Tuple[str, Path, int, int]]] = []

    for line in lines:
        match = re.search(JVMStackTracePattern, line)
        if match:
            name = match.group(1)
            classpath = name.split(".")

            location = match.group(2)
            if (match_ := re.search(r"^(.*):(\d+)$", location)) is not None:
                filename = Path(match_.group(1))
                linum = int(match_.group(2))
            else:
                continue

            filepath = Path("")
            for part in classpath:
                if part == filename.stem:
                    filepath /= filename
                    relpath = guess_relpath(source_path, filepath)
                    filepath = relpath or filepath
                    break

                filepath /= part

            if filepath is not None:
                if len(fake_body) == 0 or isinstance(fake_body[-1], str):
                    fake_body.append([])

                assert isinstance(fake_body[-1], list)
                fake_body[-1].append((name, filepath, linum, 0))
        else:
            fake_body.append(line)

    body: List[str] = []
    stacktraces: List[List[Tuple[str, Path, int, int]]] = []
    for elem in fake_body:
        if isinstance(elem, str):
            body.append(elem)
        else:
            stacktraces.append(elem)

            if not handle_cyclic:
                for i, (name, filepath, linum, _) in enumerate(elem):
                    body.append(f"- {name} ({filepath}:{linum})")
            else:
                repeat_times = 3
                for i, (name, filepath, linum, _) in enumerate(elem):
                    desc = f"- {name} ({filepath}:{linum})"
                    has_cyclic = False
                    for cycle_len in range(1, i // repeat_times):
                        always_repeat = True
                        for j in range(cycle_len):
                            for k in range(repeat_times):
                                always_repeat = always_repeat and (elem[i - j] == elem[i - j - cycle_len * k])
                        if always_repeat:
                            has_cyclic = True
                            break

                    if has_cyclic:
                        break

                    body.append(desc)

    return "\n".join(body), remove_empty_stacktrace(stacktraces)
