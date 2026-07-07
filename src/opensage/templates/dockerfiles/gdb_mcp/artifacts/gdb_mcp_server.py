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
    Run an arbitrary GDB / pwndbg command verbatim and return its output.

    Maps to GDB:
        <command>                                # sent literally to gdb's MI prompt

    Use this as the escape hatch when no dedicated tool exists. For common
    operations prefer the typed tools below — they validate inputs and
    normalize edge cases (e.g. ``set_breakpoint`` auto-stars hex addresses).

    Pre-condition: ``set_file`` must have been called first to load a binary.

    :param command: Any classic GDB / pwndbg CLI command, e.g.
        ``"info registers"``, ``"x/16xw $rsp"``, ``"vmmap"``,
        ``"break *0x4014e0"``, ``"set follow-fork-mode child"``.
    :returns: ``{"command", "responses", "success", "state"}`` where
        ``responses`` is the raw list of GDB MI response records.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.execute(command)


@mcp.tool()
@catch_errors()
async def set_file(binary_path: str, context: Context) -> Dict[str, Any]:
    """
    Load (or replace) the binary that GDB will debug. **MUST be called
    once before any other tool** — without a loaded binary, breakpoints
    and run will fail.

    Maps to GDB:
        file <binary_path>

    :param binary_path: Absolute path to the binary inside the sandbox,
        e.g. ``"/shared/bomblab"``. The file must exist and be executable.
    :returns: ``{"command", "responses", "success", "state"}``.
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
    Convenience helper: set the PoC file path as the (single) argv[1] for
    the loaded binary. Equivalent to ``run`` + ``args=poc_file_path``.

    Maps to GDB:
        set args <poc_file_path>

    NOTE: this only sets argv. It does NOT redirect stdin. If your PoC
    needs to be fed on stdin instead, use ``run(args="< /shared/poc")``
    or ``execute("run < /shared/poc")``.

    :param poc_file_path: Path to the PoC file. Must live under
        ``/shared/`` (the sandbox-shared directory) — anywhere else is
        rejected up front.
    :returns: ``{"command", "responses", "success", "state"}``.
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
    Start (or restart) execution of the loaded binary.

    Maps to GDB:
        (if args)  set args <args>
        run                                # default
        start                              # if start=True (stop at main)

    Without a breakpoint the program will run to completion / crash and
    GDB will not be able to inspect intermediate state — usually you
    want at least one ``set_breakpoint`` first, or pass ``start=True``.

    :param args: argv string passed verbatim to the binary AND interpreted
        by GDB's ``set args``. Supports the full GDB syntax including
        shell redirection:
        - ``""``                       — no args
        - ``"foo bar baz"``           — argv[1..3]
        - ``"< /shared/input.bin"``   — feed file on stdin
        - ``"arg1 < input > output"`` — argv + stdin/stdout redirection
        Do NOT pass argv as a Python list; pass a single shell-style
        string.
    :param start: If True, run ``start`` instead of ``run`` so execution
        breaks at the binary's ``main`` (useful for stripped binaries
        without a known breakpoint location).
    :returns: ``{"command", "responses", "success", "state"}``. ``state``
        becomes ``"stopped"`` if a breakpoint hit, ``"exited"`` if the
        program ran to completion.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.run(args, start)


@mcp.tool()
@catch_errors()
async def step_control(command: str, context: Context) -> Dict[str, Any]:
    """
    Resume execution by one of the standard stepping commands. Only
    callable when the inferior is currently STOPPED at a breakpoint.

    Maps to GDB (one-of, depending on ``command``):
        c   | continue   →   continue       # run until next breakpoint / exit
        n   | next       →   next           # source-level step over (no descend)
        s   | step       →   step           # source-level step into
        ni  | nexti      →   nexti          # one machine instruction, step over calls
        si  | stepi      →   stepi          # one machine instruction, step into calls

    :param command: Either the short alias (``"c"``/``"n"``/``"s"``/
        ``"ni"``/``"si"``) or the full name (``"continue"``/``"next"``/
        ``"step"``/``"nexti"``/``"stepi"``). Anything else is rejected.
    :returns: ``{"command", "responses", "success", "state"}``.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.step_control(command)


@mcp.tool()
@catch_errors()
async def finish(context: Context) -> Dict[str, Any]:
    """
    Continue execution until the **currently active function returns**,
    then stop just after the return. Useful for skipping past a long
    function you've already analysed.

    Maps to GDB:
        finish

    Pre-condition: inferior must be STOPPED inside a function (won't
    work at the very top frame or before ``run``).

    :returns: ``{"command", "responses", "success", "state"}``. The
        return value of the finished function is included in the GDB
        response payload.
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
    Dump pwndbg's contextual snapshot at the current stop. This is the
    single most useful tool when stopped at a breakpoint — it gives a
    one-shot view of registers, stack, surrounding instructions, and
    callstack.

    Maps to pwndbg:
        context <context_type>

    Pre-condition: inferior must be STOPPED. Returns an error otherwise.

    :param context_type: Which slice of context to return.
        - ``"all"`` (default) — every section below
        - ``"regs"``      → ``context regs``      register dump
        - ``"stack"``     → ``context stack``     hex dump near $rsp
        - ``"disasm"``    → ``context disasm``    disasm at $pc
        - ``"code"``      → ``context code``      source lines (if symbols)
        - ``"backtrace"`` → ``context backtrace`` call stack
    :returns: ``{"command", "responses", "success", "state"}`` (or for
        ``"all"``, a dict keyed by section).
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
    Add a breakpoint. Bare hex / decimal addresses are auto-prefixed with
    ``*`` so GDB treats them as raw addresses (NOT symbol names) — you
    do not need to add the star yourself.

    Maps to GDB:
        b <location>                        # no condition
        b <location> if "<condition>"       # with condition

    Accepted ``location`` forms (all valid GDB break syntax):
        - ``"0x4014e0"``        → auto-rewritten to ``b *0x4014e0``
        - ``"*0x4014e0"``       → passed through (already starred)
        - ``"main"``            → ``b main``  (symbol name)
        - ``"file.c:42"``       → ``b file.c:42``  (source line)
        - ``"main+10"``         → ``b main+10``  (symbol + offset)
        - ``"Class::method"``   → ``b Class::method``  (C++ symbol)

    :param location: Where to break. See accepted forms above.
    :param condition: Optional GDB conditional expression evaluated at
        the breakpoint, e.g. ``"$rdi == 0x10"`` or ``"argc > 1"``. The
        breakpoint only fires when the expression is true.
    :returns: ``{"command", "responses", "success", "state"}``. The GDB
        response payload includes the assigned breakpoint number.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.set_breakpoint(location, condition)


@mcp.tool()
@catch_errors()
async def list_breakpoints(context: Context = None) -> Dict[str, Any]:
    """
    Show every currently registered breakpoint with its number, address,
    enabled state, hit count, and condition (if any).

    Maps to GDB:
        info breakpoints

    :returns: ``{"command", "responses", "success", "state"}``. Use the
        breakpoint numbers from the response with ``delete_breakpoint``
        or ``toggle_breakpoint``.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.list_breakpoints()


@mcp.tool()
@catch_errors()
async def delete_breakpoint(number: int, context: Context = None) -> Dict[str, Any]:
    """
    Permanently remove a breakpoint by its GDB-assigned number.

    Maps to GDB:
        delete <number>

    :param number: Breakpoint number as shown by ``list_breakpoints``
        (the small integer in the ``Num`` column, NOT an address).
    :returns: ``{"command", "responses", "success", "state"}``.
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
    Enable or disable an existing breakpoint without removing it. Use
    this to keep a breakpoint registered (so its number is stable) but
    temporarily silence it.

    Maps to GDB:
        enable breakpoints <number>          # if enable=True
        disable breakpoints <number>         # if enable=False

    :param number: Breakpoint number from ``list_breakpoints``.
    :param enable: ``True`` to enable, ``False`` to disable.
    :returns: ``{"command", "responses", "success", "state"}``.
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
    Read memory from the inferior at the given address.

    Maps to GDB / pwndbg (depends on ``format``):
        format="hex"      →  hexdump <address> <size>      # pwndbg: hex+ASCII
        format="string"   →  x/s <address>                 # NUL-terminated string
        anything else     →  x/<size>b <address>           # raw bytes (one per byte)

    Pre-condition: inferior must be STOPPED (otherwise GDB has no
    consistent snapshot to read from).

    :param address: Any GDB-evaluable expression resolving to an address.
        Hex (``"0x7ffffffde000"``), register (``"$rsp"``), symbol
        (``"main"``), or expression (``"$rsp+0x20"``).
    :param size: Bytes to read (default 64). Ignored for ``format="string"``.
    :param format: ``"hex"`` for hex+ASCII dump, ``"string"`` for NUL-
        terminated C string, anything else for raw bytes.
    :returns: ``{"command", "responses", "success", "state"}``.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.get_memory(address, size, format)


@mcp.tool()
@catch_errors()
async def disassemble(address: str, context: Context = None) -> Dict[str, Any]:
    """
    Disassemble instructions at / around an address.

    Maps to GDB:
        disassemble <address>

    GDB's default behavior: if ``address`` is a function symbol, dumps
    the whole function; if it's a raw address, dumps the enclosing
    function (or a small range around it).

    :param address: Any GDB-evaluable address expression. Examples:
        - ``"main"``           — disassemble the ``main`` function
        - ``"0x4014e0"``       — disassemble around that address
        - ``"$pc"``            — disassemble around the current PC
        - ``"main,main+0x80"`` — explicit start/end range
    :returns: ``{"command", "responses", "success", "state"}``.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.disassemble(address)


@mcp.tool()
@catch_errors()
async def send_to_stdin(
    data: str,
    encoding: str = "text",
    append_newline: bool = True,
    context: Context = None,
) -> Dict[str, Any]:
    """
    Write bytes to the **inferior's** stdin (NOT to GDB itself).

    Maps to:
        write(<inferior_pty_master_fd>, data)        # bypasses GDB
        # NO gdb command issued — pure I/O on the separate pty channel.

    Use this when the program is RUNNING and waiting on input (e.g. a
    menu prompt, ``getpass``, ``read(0, ...)``). Writes go directly to
    the program's stdin via a dedicated pty configured at session start
    via ``set inferior-tty <pts>``, so they will never be parsed by GDB
    as commands.

    Pre-condition: the program should currently be in a state where it's
    actually consuming stdin. Writing to a not-yet-running or already-
    exited program is silently buffered or dropped.

    :param data: Bytes to write, expressed as a string. Encoding controls
        how this string is decoded back to bytes:
        - ``encoding="text"`` (default): utf-8 encode. If
          ``append_newline=True`` (default), a trailing ``"\\n"`` is added
          unless already present — convenient for line-oriented prompts.
        - ``encoding="base64"``: base64 decode. Use this for any binary
          PoC bytes including embedded ``\\x00``, ``\\xff``, etc.
    :param encoding: ``"text"`` or ``"base64"``. See above.
    :param append_newline: Only honored for ``encoding="text"``. Set to
        ``False`` if you need to send a partial line (e.g. echo prompt
        that doesn't expect a newline).
    :returns: ``{"success", "bytes_written", "state"}``. After sending,
        usually call ``read_stdout`` to see the program's response.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.send_to_stdin(data, encoding, append_newline)


@mcp.tool()
@catch_errors()
async def read_stdout(
    timeout: float = 0.5, encoding: str = "text", context: Context = None
) -> Dict[str, Any]:
    """
    Drain accumulated bytes from the inferior's stdout pty.

    Maps to:
        read(<inferior_pty_master_fd>, ...)          # bypasses GDB
        # NO gdb command issued — pure I/O on the separate pty channel.

    A background thread inside the gdb_mcp server continuously drains the
    pty so the inferior never blocks on a full pipe. This call merely
    pops what's accumulated since the last read.

    NOTE: most ``run`` / ``step_control`` / ``finish`` / ``interrupt``
    responses already include a ``recent_stdout`` field so you usually
    don't need to call this explicitly. Use it when:
    - You want to wait specifically for more output (set ``timeout``).
    - You need binary-safe bytes (set ``encoding="base64"``).
    - You want to flush the buffer before sending stdin so the next read
      only contains the program's reply.

    :param timeout: If buffer is currently empty, wait up to this many
        seconds for the FIRST byte before returning. Once data starts
        arriving, returns within a short settling window. Set to ``0`` for
        a strictly non-blocking peek (returns immediately with whatever's
        buffered, possibly empty).
    :param encoding: How to encode the returned bytes for transport:
        - ``"text"`` (default): utf-8 decode with ``errors="replace"``.
          Lossy on non-utf8 binary output but human-readable.
        - ``"base64"``: base64 ascii. Lossless, use for binary data.
    :returns: ``{"success", "bytes", "data", "encoding", "state"}`` where
        ``bytes`` is the raw byte count drained and ``data`` is the
        encoded string per ``encoding``.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.read_stdout(timeout, encoding)


@mcp.tool()
@catch_errors()
async def interrupt(context: Context = None) -> Dict[str, Any]:
    """
    Pause a RUNNING inferior and break back into GDB. The "Ctrl-C" of
    GDB MCP.

    Maps to:
        kill -SIGINT <inferior_pid>                  # OS-level signal
        # GDB observes the signal and emits *stopped reason=signal-received

    Use this when:
    - Program is in an infinite loop / deadlock and won't hit any breakpoint
    - You issued ``continue`` but realised the wrong breakpoints are set
    - Program is blocked on a ``read()`` (interactive prompt) and you'd
      rather inspect state than answer the prompt
    - Long-running computation you want to peek into

    No-op (returns success=False with explanation) if the inferior is
    not currently RUNNING.

    Implementation note: SIGINT to the inferior pid is more robust than
    GDB's MI ``-exec-interrupt`` because it works in default all-stop
    sync mode without requiring ``set non-stop on``.

    :returns: ``{"command", "responses", "success", "state", "recent_stdout"?}``.
        After return, ``state`` should be ``"stopped"`` and you can issue
        ``get_context`` / ``execute("info registers")`` etc. The
        ``recent_stdout`` field captures any output the program produced
        before being interrupted.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.interrupt()


@mcp.tool()
@catch_errors()
async def get_session_info(context: Context = None) -> Dict[str, Any]:
    """
    Return MCP-side session metadata: which binary is loaded, current
    GDB inferior state, and the full history of commands sent in this
    session. Does NOT issue any GDB command; purely a bookkeeping read.

    Maps to GDB:
        (no GDB command — server-side state only)

    Useful for self-orientation when resuming or to audit what's been
    tried so far.

    :returns: ``{"session": {binary_path, binary_loaded, pid, state,
        command_count, command_history, ...}, "gdb_state": <str>}``.
    """
    pwndbg_tools = await get_unit_session(context.session)
    if pwndbg_tools is None:
        return {"success": False, "error": "Please set_file first."}
    return pwndbg_tools.get_session_info()


def main():
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
