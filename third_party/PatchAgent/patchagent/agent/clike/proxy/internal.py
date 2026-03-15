import math
import os
from pathlib import Path
from typing import Dict, List, Tuple

import clang.cindex
from clang.cindex import Config

from patchagent.agent.base import AgentStopException, PatchFoundException
from patchagent.agent.clike.proxy.utils import (
    extract_cpp_function_name,
    revise_clike_patch,
)
from patchagent.logger import logger
from patchagent.parser.utils import guess_relpath
from patchagent.task import PatchTask, ValidationResult


def _configure_libclang() -> None:
    # Keep llvm-16 as first choice, then fall back to common distro locations.
    candidates = [
        "/usr/lib/llvm-16/lib/libclang.so.1",
        "/lib/x86_64-linux-gnu/libclang-17.so.17",
        "/lib/x86_64-linux-gnu/libclang-14.so.13",
        "/lib/x86_64-linux-gnu/libclang-11.so.1",
    ]
    for lib_path in candidates:
        if os.path.exists(lib_path):
            Config.set_library_file(lib_path)
            return


_configure_libclang()

MAX_VIEWCODE_LINES = 40
MAX_VALIDATION_TRIES = 5


def viewcode(task: PatchTask, _path: str, _start_line: int, _end_line: int, auto_hint: bool = False) -> Tuple[Dict, str]:
    total_lines = _end_line - _start_line + 1
    adjusted_lines = max(MAX_VIEWCODE_LINES, total_lines) - total_lines

    path = guess_relpath(task.builder.source_path, Path(_path)) or Path(os.path.normpath(_path).lstrip("/"))
    start_line = max(1, _start_line - math.floor(adjusted_lines / 2))
    end_line = _end_line + math.ceil(adjusted_lines / 2)

    lines = task.builder.language_server.viewcode(path, start_line, end_line)

    if lines is None:
        result = f"Sorry, the file {path} does not exist."
    else:
        end_line = min(end_line, start_line + len(lines) - 1)
        desc = f"Here is the code snippet from line {start_line} to line {end_line} in {path}:\n"

        code = "".join(f"{start_line + i:{math.floor(math.log10(end_line)) + 1}}| {line}" for i, line in enumerate(lines))
        result = desc + code

        if auto_hint:
            for stack in task.report.stacktraces:
                key_line = []
                for _, filepath, line, column in stack:
                    assert not filepath.is_absolute()
                    if path == filepath and start_line <= line <= end_line and line not in key_line:
                        key_line.append(line)

                for line in key_line:
                    line_content: str = lines[line - start_line]
                    hints = []
                    for column in range(len(line_content)):
                        if line_content[column].isalpha():  # only consider the alphabetic characters
                            hint = task.builder.language_server.hover(path, line, column)
                            if hint is not None and len(hint) > 0 and hint not in hints:
                                hints.append(hint)
                    if len(hints) > 0:
                        result += (
                            "\nWe think the following hints might be helpful:\n"
                            f"The line {line} in {path} which appears in the stack trace is:\n{line_content}\n"
                            "Here are the definitions of the symbols in the line:\n"
                        )
                        for i, hint in enumerate(hints):
                            result += f"{i + 1}. {hint}\n"
                    else:
                        logger.warning(f"[🚧] Failed to get hint for {path}:{line}")

    return {"path": path.as_posix(), "start_line": start_line, "end_line": end_line}, result


def locate(task: PatchTask, symbol: str, auto_hint: bool = False) -> Tuple[Dict, str]:
    def helper(task: PatchTask, symbol: str) -> List[str]:
        fast_path_locations = task.builder.language_server.locate_symbol(symbol)

        if len(fast_path_locations) != 1:
            for tool_calls in reversed(task.current_context.tool_calls):
                if tool_calls["name"] != "viewcode":
                    continue
                relpath, start_line, end_line = tool_calls["args"]["path"], tool_calls["args"]["start_line"], tool_calls["args"]["end_line"]
                realpath: Path = task.builder.source_path / relpath
                if realpath.is_file():
                    try:
                        index = clang.cindex.Index.create()
                        tu = index.parse(realpath)

                        location_set = set()
                        for token in tu.get_tokens(extent=tu.cursor.extent):
                            if token.kind.name == "IDENTIFIER" and token.spelling == symbol and start_line <= token.location.line <= end_line:
                                for loc in task.builder.language_server.find_definition(Path(relpath), token.location.line, token.location.column):
                                    location_set.add(loc)

                        if len(location_set) > 0:
                            return list(location_set)
                    except clang.cindex.TranslationUnitLoadError as e:
                        logger.warning(f"[🚧] Failed to locate the symbol {symbol} in {realpath}: {e}")
                        break

            for stack in task.report.stacktraces:
                for idx, frame in enumerate(stack):
                    name, filepath, line, column = frame
                    assert not filepath.is_absolute()
                    if symbol == extract_cpp_function_name(name):
                        if idx + 1 < len(stack):
                            location = task.builder.language_server.find_definition(filepath, line, column)
                            if len(location) > 0:
                                return location
                        else:
                            realpath = task.builder.source_path / filepath
                            if realpath.is_file():
                                with realpath.open() as f:
                                    lines = f.readlines()
                                for i in range(min(line, len(lines)) - 1, -1, -1):
                                    if symbol in lines[i]:
                                        location = task.builder.language_server.find_definition(
                                            filepath,
                                            i + 1,
                                            lines[i].index(symbol) + 1,
                                        )
                                        if len(location) > 0:
                                            return location

        return fast_path_locations

    symbol = extract_cpp_function_name(symbol) or symbol
    locations = helper(task, symbol)

    if len(locations) > 1:
        logger.warning(f"[🚧] {symbol} has multiple definitions.")
    elif len(locations) == 0:
        logger.warning(f"[🚧] Failed to find the definition of {symbol}.")

    if len(locations) == 0:
        result = f"Sorry, we cannot locate the symbol {symbol}, please consider use some alias names."
    else:
        result = f"Here is the location of the symbol {symbol}:\n" + "\n".join(locations)

    return {"symbol": symbol}, result


def validate(task: PatchTask, patch: str, auto_hint: bool = False) -> Tuple[Dict, str]:
    num_tries = 0
    for tool_call in reversed(task.current_context.tool_calls):
        if tool_call["name"] != "validate":
            break
        num_tries += 1
    if num_tries >= MAX_VALIDATION_TRIES:
        raise AgentStopException("The number of validation tries has reached the maximum limit.")

    patch = revise_clike_patch(patch, task.builder)
    ret, report = task.validate(patch)

    if ret == ValidationResult.BugFree:
        task.current_context.patch = patch
        raise PatchFoundException(patch)

    if ret != ValidationResult.BugDetected:
        report = ret.value

    header = "Sorry, the patch is incorrect. Here is the applied patch, which may have been revised and differ from the original:"
    desc = f"Here is the validation report:\n{report}"
    result = f"{header}\n{patch}\n{desc}"
    return {"patch": patch}, result
