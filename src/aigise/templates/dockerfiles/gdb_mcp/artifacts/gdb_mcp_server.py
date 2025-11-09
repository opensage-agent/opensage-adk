# -*- coding: utf-8 -*-
import logging
import os
from functools import wraps
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from mcp.server import FastMCP
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.prompts import base
from mcp.server.session import ServerSession

from .helper import PwndbgTools

logger = logging.getLogger(__name__)

load_dotenv()
GDB_MCP_SSE_PORT = 1111

# Some codes are referenced from https://github.com/pwno-io/pwno-mcp


def catch_errors(tuple_on_error: bool = False):
    """Decorator to standardize exception handling for GDB-related MCP tools."""

    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                logger.exception("tool error in {}", fn.__name__)
                if tuple_on_error:
                    return {
                        "success": False,
                        "error": str(e),
                        "type": type(e).__name__,
                    }, []
                return {"success": False, "error": str(e), "type": type(e).__name__}

        return wrapper

    return decorator


mcp = FastMCP("GDB MCP Server", port=GDB_MCP_SSE_PORT, host="0.0.0.0")
session_dict: Dict[ServerSession, PwndbgTools] = {}


async def get_unit_session(session: ServerSession):
    if session not in session_dict:
        logger.info("{} not in session_dict, Please set_file first.", session)
        return None
    return session_dict[session]


@mcp.tool()
@catch_errors()
async def execute(command: str, context: Context) -> Dict[str, Any]:
    """
    Execute arbitrary GDB/pwndbg command.

    :param command: GDB command to execute
    :returns: Command output and state information
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.execute(command)


@mcp.tool()
@catch_errors()
async def set_file(binary_path: str, context: Context) -> Dict[str, Any]:
    """
    Load a binary file for debugging.

    :param binary_path: Path to the binary to load
    :returns: Loading status and binary information
    """
    if context.session not in session_dict:
        logger.info("create new session...")
        session_dict[context.session] = PwndbgTools()
    pwndbg_tools = session_dict[context.session]
    return pwndbg_tools.set_file(binary_path)


@mcp.tool()
@catch_errors()
async def set_poc_file(poc_file_path: str, context: Context) -> Dict[str, Any]:
    """
    Use `set args poc_file` to set the proof-of-concept (PoC) file for the loaded binary.
    The poc file should be in the /shared directory.

    :param poc_file_path: Path to the PoC file to set
    :returns: Status of the operation
    """
    if not poc_file_path.startswith("/shared/"):
        return {
            "success": False,
            "error": "The poc file should be in the /shared directory.",
        }
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.set_poc_file(poc_file_path)


@mcp.tool()
@catch_errors()
async def run(
    args: str = "", start: bool = False, context: Context = None
) -> Dict[str, Any]:
    """
    Run the loaded binary.

    Requires at least one enabled breakpoint to be set before running.

    :param args: Arguments to pass to the binary
    :param start: Optional - stop at program entry (equivalent to --start)
    :returns: Execution results and state
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.run(args, start)


@mcp.tool()
@catch_errors()
async def step_control(command: str, context: Context) -> Dict[str, Any]:
    """
    Execute stepping commands (continue, next, step, nexti, stepi).

    :param command: Stepping command (c, n, s, ni, si or full name)
    :returns: Execution results and new state
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.step_control(command)


@mcp.tool()
@catch_errors()
async def finish(context: Context) -> Dict[str, Any]:
    """
    Run until the current function returns.

    :returns: Execution results and new state
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.finish()


@mcp.tool()
@catch_errors()
async def get_context(
    context_type: str = "all", context: Context = None
) -> Dict[str, Any]:
    """
    Get debugging context (registers, stack, disassembly, code, backtrace).

    :param context_type: Type of context (all, regs, stack, disasm, code, backtrace)
    :returns: Requested context information
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.get_context(context_type)


@mcp.tool()
@catch_errors()
async def set_breakpoint(
    location: str, condition: Optional[str] = None, context: Context = None
) -> Dict[str, Any]:
    """
    Set a breakpoint at the specified location.

    :param location: Address or symbol for breakpoint
    :param condition: Optional breakpoint condition
    :returns: Breakpoint information
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.set_breakpoint(location, condition)


@mcp.tool()
@catch_errors()
async def list_breakpoints(context: Context = None) -> Dict[str, Any]:
    """
    List all breakpoints.

    :returns: List of breakpoints
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.list_breakpoints()


@mcp.tool()
@catch_errors()
async def delete_breakpoint(number: int, context: Context = None) -> Dict[str, Any]:
    """
    Delete a breakpoint by number.

    :param number: Breakpoint number to delete
    :returns: Deletion status
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.delete_breakpoint(number)


@mcp.tool()
@catch_errors()
async def toggle_breakpoint(
    number: int, enable: bool, context: Context = None
) -> Dict[str, Any]:
    """
    Toggle a breakpoint's state.

    :param number: Breakpoint number to toggle
    :param enable: New enabled state
    :returns: Toggled breakpoint information
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.toggle_breakpoint(number, enable)


@mcp.tool()
@catch_errors()
async def get_memory(
    address: str, size: int = 64, format: str = "hex", context: Context = None
) -> Dict[str, Any]:
    """
    Read memory at the specified address.

    :param address: Memory address to read
    :param size: Number of bytes to read
    :param format: Output format (hex, string, int)
    :returns: Memory contents in the requested format
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.get_memory(address, size, format)


@mcp.tool()
@catch_errors()
async def disassemble(address: str, context: Context = None) -> Dict[str, Any]:
    """
    Disassemble the specified address.

    :param address: Address to disassemble
    :returns: Disassembly of the specified address
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.disassemble(address)


@mcp.tool()
@catch_errors()
async def get_session_info(context: Context = None) -> Dict[str, Any]:
    """
    Get current debugging session information.

    :returns: Session state and debugging artifacts
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.get_session_info()


def main():
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
