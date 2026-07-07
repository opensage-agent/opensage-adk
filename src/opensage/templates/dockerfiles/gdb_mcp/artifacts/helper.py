"""
Pwndbg tools for MCP server

Provides MCP tool implementations for GDB/pwndbg commands.
Each tool returns immediate results suitable for LLM interaction.
"""

import base64
import logging
import os
import pty
import select
import signal
import threading
import tty
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from pygdbmi import gdbcontroller

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Maintains the complete state of a debugging session"""

    # Binary information
    binary_path: Optional[str] = None
    binary_loaded: bool = False
    entry_point: Optional[str] = None

    # Process state
    pid: Optional[int] = None
    state: str = "idle"  # idle, running, stopped, exited

    # Execution history
    command_history: List[Dict[str, Any]] = field(default_factory=list)
    record_command_history: List[str] = field(default_factory=list)

    # Session metadata
    session_id: str = field(default_factory=lambda: datetime.now().isoformat())
    created_at: datetime = field(default_factory=datetime.now)

    def record_command(self, command: str, result: Dict[str, Any]):
        """Record a command and its result in history"""
        self.command_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "command": command,
                "result": result,
            }
        )
        self.record_command_history.append(command)

    def update_state(self, new_state: str):
        """Update the process state"""
        old_state = self.state
        self.state = new_state
        logger.debug(f"State transition: {old_state} -> {new_state}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert session state to dictionary for serialization"""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "binary_path": self.binary_path,
            "binary_loaded": self.binary_loaded,
            "entry_point": self.entry_point,
            "pid": self.pid,
            "state": self.state,
            "command_count": len(self.command_history),
            "command_history": self.record_command_history,
        }


class GdbController:
    """Manages GDB instance and command execution via Machine Interface"""

    def __init__(self, pwndbg: str = "pwndbg"):
        """
        Initialize GDB controller

        Args:
            gdb_path: Path to GDB executable (default: "pwndbg")"""
        self.controller = gdbcontroller.GdbController(
            command=[
                pwndbg,
                "--interpreter=mi3",
                "--quiet",
                "--nh",
            ]
        )
        self._initialized = False
        self._inferior_pid = None
        self._state = "idle"  # idle, running, stopped, exited

    def execute_command(self, command: str, timeout_sec: float = 5.0) -> Dict[str, Any]:
        """
        Execute a classic GDB command (non-MI) and return raw responses.

        Args:
            command (str): GDB command to execute
            timeout_sec (float): Timeout for command execution
        Returns:
            Dict[str, Any]: Dictionary with raw responses, success flag, and current state.
        """
        logger.debug(f"Executing command: {command}")
        # pygdbmi

        # Send command and manually collect responses
        collected: list[dict] = []

        try:
            responses = self.controller.write(command, timeout_sec=timeout_sec)
        except Exception as e:
            self._state = "stopped"
            self.controller.gdb_process.send_signal(signal.SIGINT)
            return {
                "command": command,
                "responses": collected,
                "success": False,
                "state": self._state,
            }

        for response in responses:
            logger.debug(f"GDB response: {response}")
            if response.get("type") != "notify":
                collected.append(response)
            if response.get("type") == "notify":
                self._handle_notify(response)

        # Determine success: no explicit error result messages
        success = True
        for r in collected:
            if r.get("type") == "result" and r.get("message") == "error":
                success = False
                break

        return {
            "command": command,
            "responses": collected,
            "success": success,
            "state": self._state,
        }

    def _handle_notify(self, response: Dict[str, Any]):
        """Handle GDB notification messages to track state"""
        message = response.get("message", "")

        if message == "running":
            self._state = "running"
            logger.debug("Inferior state: RUNNING")

        elif message == "stopped":
            self._state = "stopped"
            payload = response.get("payload", {})
            if payload == None:
                payload = {}
            # Extract stop reason if available
            reason = payload.get("reason", "unknown")
            logger.debug(f"Inferior state: STOPPED (reason: {reason})")

        elif message == "thread-group-exited":
            self._state = "exited"
            logger.debug("Inferior state: EXITED")

        elif message == "thread-group-started":
            # This happens when attaching to a process
            payload = response.get("payload", {})
            if payload == None:
                payload = {}
            self._inferior_pid = payload.get("pid")
            logger.debug(f"Thread group started, PID: {self._inferior_pid}")

    def get_context(self, context_type: str) -> Dict[str, Any]:
        """Get a specific pwndbg context; return raw responses"""
        if self._state != "stopped":
            return {
                "command": f"context {context_type}",
                "responses": [],
                "success": False,
                "state": self._state,
                "error": f"Cannot get context while inferior is {self._state}",
            }
        return self.execute_command(f"context {context_type}")

    def set_file(self, filepath: str) -> Dict[str, Any]:
        """Load an executable file for debugging using command"""
        result = self.execute_command(f"file {filepath}")
        if result["success"]:
            self._state = "stopped"
        # Ensure returned state reflects any updates
        result["state"] = self._state
        return result

    def set_poc_file(self, poc_file_path: str) -> Dict[str, Any]:
        """Load a proof-of-concept (PoC) file for debugging using command"""
        result = self.execute_command(f"set args {poc_file_path}")
        if result["success"]:
            self._state = "stopped"
        # Ensure returned state reflects any updates
        result["state"] = self._state
        return result

    def run(self, args: str = "", start: bool = False) -> Dict[str, Any]:
        """Run the loaded program using command.

        Maps to GDB:
            (optional) ``set args <args>``
            ``run``  (or ``start`` if start=True)
        """
        if args:
            set_args_result = self.execute_command(f"set args {args}")
            if not set_args_result["success"]:
                return set_args_result

        run_command = "start" if start else "run"
        return self.execute_command(run_command)

    def continue_execution(self) -> Dict[str, Any]:
        """Continue execution using command"""
        return self.execute_command("continue")

    def finish(self) -> Dict[str, Any]:
        """Finish current function using command (-exec-finish)"""
        return self.execute_command("finish")

    def next(self) -> Dict[str, Any]:
        """Step over using command"""
        return self.execute_command("next")

    def step(self) -> Dict[str, Any]:
        """Step into using command"""
        return self.execute_command("step")

    def nexti(self) -> Dict[str, Any]:
        """Step one instruction using command"""
        return self.execute_command("ni")

    def stepi(self) -> Dict[str, Any]:
        """Step into one instruction using command"""
        return self.execute_command("si")

    def set_breakpoint(
        self, location: str, condition: Optional[str] = None
    ) -> Dict[str, Any]:
        """Set a breakpoint using command.

        Bare hex addresses (``0x...``) and bare decimal addresses are
        auto-prefixed with ``*`` so GDB treats them as raw addresses
        instead of symbol names. Symbol names, source-line refs
        (``file.c:42``), and inputs already starting with ``*`` are
        passed through unchanged.

        Maps to GDB:
            ``b <location>``                       (no condition)
            ``b <location> if "<condition>"``      (with condition)
        """
        normalized = self._normalize_break_location(location)
        command = f"b {normalized}"
        if condition:
            command = f'b {normalized} if "{condition}"'
        return self.execute_command(command)

    @staticmethod
    def _normalize_break_location(location: str) -> str:
        """Auto-prefix bare addresses with ``*`` so ``b`` treats them as
        addresses, not symbol names.

        Pass-through cases (already unambiguous to GDB):
        - already starts with ``*`` (raw address form)
        - symbolic forms like ``main``, ``func+10``, ``file.c:42``,
          ``Class::method``, ``'sym with spaces'``
        """
        loc = location.strip()
        if not loc or loc.startswith("*"):
            return loc
        # bare hex address: 0x... / 0X...
        if loc.lower().startswith("0x") and all(
            c in "0123456789abcdefABCDEF" for c in loc[2:]
        ):
            return f"*{loc}"
        # bare decimal address (rare but possible)
        if loc.isdigit():
            return f"*{loc}"
        return loc

    def list_breakpoints(self) -> Dict[str, Any]:
        """List all breakpoints using command"""
        return self.execute_command("info breakpoints")

    def delete_breakpoint(self, number: int) -> Dict[str, Any]:
        """Delete a breakpoint using command"""
        return self.execute_command(f"delete {number}")

    def enable_breakpoint(self, number: int) -> Dict[str, Any]:
        """Enable a breakpoint using command"""
        return self.execute_command(f"enable breakpoints {number}")

    def disable_breakpoint(self, number: int) -> Dict[str, Any]:
        """Disable a breakpoint using command"""
        return self.execute_command(f"disable breakpoints {number}")

    def get_state(self) -> str:
        """Get current inferior state"""
        return self._state

    def close(self):
        """Clean up GDB controller"""
        if hasattr(self, "controller"):
            self.controller.exit()


class InferiorIO:
    """Owns a pty pair used as the inferior's stdin/stdout/stderr.

    A background thread continuously drains the master fd into an in-memory
    byte buffer, so the inferior never blocks on a full pty buffer (~4KB).
    Callers ``write()`` bytes to send to the inferior and ``drain()`` to pull
    accumulated stdout. Slave is in raw mode so the channel is binary-safe
    (no echo, no \\n→\\r\\n).
    """

    _READ_CHUNK = 8192

    def __init__(self) -> None:
        self.master_fd, self.slave_fd = pty.openpty()
        # Raw mode: no echo, no \n -> \r\n, no signal interpretation.
        # Required for binary PoCs and for not double-printing input.
        tty.setraw(self.slave_fd)
        self.slave_path = os.ttyname(self.slave_fd)

        self._buf = bytearray()
        self._buf_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="InferiorIO.reader",
            daemon=True,
        )
        self._reader.start()
        logger.debug(f"InferiorIO ready: slave={self.slave_path}")

    def _reader_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
            except (OSError, ValueError):
                # fd closed
                return
            if not r:
                continue
            try:
                data = os.read(self.master_fd, self._READ_CHUNK)
            except OSError:
                # Inferior side closed (or master_fd closed during shutdown).
                # PTY masters return OSError EIO when the slave hangs up.
                return
            if not data:
                return
            with self._buf_lock:
                self._buf.extend(data)

    def write(self, data: bytes) -> int:
        """Write bytes to the inferior's stdin via the pty master."""
        return os.write(self.master_fd, data)

    def drain(self, timeout: float = 0.0) -> bytes:
        """Pop and return all bytes accumulated in the stdout buffer.

        If ``timeout > 0`` and the buffer is currently empty, waits up to
        ``timeout`` seconds for the first byte before returning. Once any
        bytes are available, additional bytes that arrive during a short
        settling window (50ms) are also collected so we don't return a
        half-line just because the reader hadn't caught up.
        """
        deadline = None
        if timeout > 0:
            with self._buf_lock:
                if not self._buf:
                    deadline = _now() + timeout
        while deadline is not None and _now() < deadline:
            with self._buf_lock:
                if self._buf:
                    break
            self._stop_evt.wait(0.02)
        # short settling window
        settle_until = _now() + 0.05
        while _now() < settle_until:
            self._stop_evt.wait(0.01)
            # we just want to allow a few more chunks to land
        with self._buf_lock:
            data = bytes(self._buf)
            self._buf.clear()
        return data

    def reset(self) -> None:
        """Discard any buffered stdout (call before ``run`` to start clean)."""
        with self._buf_lock:
            self._buf.clear()

    def close(self) -> None:
        self._stop_evt.set()
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        try:
            os.close(self.slave_fd)
        except OSError:
            pass
        self._reader.join(timeout=1.0)


def _now() -> float:
    import time

    return time.monotonic()


def _decode_inferior_bytes(data: bytes, encoding: str) -> str:
    """Encode raw inferior stdout bytes for transport in a JSON tool result.

    encoding="text"   → utf-8 with errors="replace" (lossy but readable)
    encoding="base64" → base64 ascii (lossless, for binary)
    """
    if encoding == "base64":
        return base64.b64encode(data).decode("ascii")
    return data.decode("utf-8", errors="replace")


def _encode_stdin_data(data: str, encoding: str) -> bytes:
    """Decode a tool's stdin string back to raw bytes.

    encoding="text"   → utf-8 encode
    encoding="base64" → base64 decode (must be valid base64 ascii)
    """
    if encoding == "base64":
        return base64.b64decode(data)
    return data.encode("utf-8")


class PwndbgTools:
    """MCP tools for pwndbg interaction"""

    # How long ``run`` / ``step_control`` / ``finish`` / ``interrupt``
    # responses wait for the inferior's stdout to settle before draining
    # ``recent_stdout`` into the response.
    _DRAIN_AFTER_RESUME_SEC = 0.3

    def __init__(self):
        """
        Initialize pwndbg tools

        Args:
            gdb_controller: GDB controller instance"""
        self.gdb = GdbController()
        self.session = SessionState()
        # PTY for the debugged inferior's stdin/stdout. Independent of
        # GDB's MI command channel so the LLM can interact with the
        # program without its bytes being eaten by GDB or vice versa.
        self.io = InferiorIO()
        self._inferior_pid: Optional[int] = None
        self._inferior_tty_attached = False

    def _attach_inferior_tty(self) -> None:
        """Tell GDB to redirect the inferior's stdio to our pty.

        Must be called after a binary is loaded but before ``run``. Idempotent
        within a session — only sent once.
        """
        if self._inferior_tty_attached:
            return
        result = self.gdb.execute_command(f"set inferior-tty {self.io.slave_path}")
        if result.get("success"):
            self._inferior_tty_attached = True
            logger.info(f"inferior-tty attached: {self.io.slave_path}")
        else:
            logger.warning(f"failed to attach inferior-tty: {result.get('responses')}")

    def _capture_inferior_pid(self, _result: Dict[str, Any]) -> None:
        """Pull the latest inferior pid from the GdbController.

        ``GdbController._handle_notify`` records ``=thread-group-started``'s
        pid as a string in ``self.gdb._inferior_pid``; we just coerce to
        int and cache for ``interrupt()`` (which needs ``os.kill`` int pid).
        """
        pid_str = getattr(self.gdb, "_inferior_pid", None)
        if pid_str is None:
            return
        try:
            self._inferior_pid = int(pid_str)
            logger.debug(f"captured inferior pid={self._inferior_pid}")
        except (TypeError, ValueError):
            logger.warning(f"non-int inferior pid from gdb: {pid_str!r}")

    def _attach_recent_stdout(
        self, result: Dict[str, Any], timeout: float = 0.0
    ) -> Dict[str, Any]:
        """Drain the inferior stdout pty buffer into ``result.recent_stdout``.

        Always lossy-decoded as utf-8 for inline display. If the LLM wants
        the raw bytes (binary protocol output) it should call ``read_stdout``
        with encoding="base64".
        """
        try:
            data = self.io.drain(timeout=timeout)
        except Exception as e:
            logger.exception("drain failed in _attach_recent_stdout")
            data = b""
        if data:
            result["recent_stdout"] = _decode_inferior_bytes(data, "text")
            result["recent_stdout_bytes"] = len(data)
        return result

    def execute(self, command: str) -> Dict[str, Any]:
        """Execute arbitrary GDB/pwndbg command and return raw responses"""
        logger.info(f"Execute tool: {command}")
        result = self.gdb.execute_command(command)
        self.session.update_state(result["state"])
        self.session.record_command(command, result)
        return result

    def set_file(self, binary_path: str) -> Dict[str, Any]:
        """Set the file to debug; return raw responses.

        Side effect: also redirects the inferior's stdio to our pty
        (``set inferior-tty <pts>``) on first successful load, so all
        subsequent ``run`` invocations have a separate stdin/stdout
        channel from GDB's MI command pipe.
        """
        logger.info(f"Set file: {binary_path}")
        result = self.gdb.set_file(binary_path)
        if result.get("success"):
            self.session.binary_path = binary_path
            self.session.binary_loaded = True
            # Attach pty AFTER the file load. Doing it before sometimes
            # produces noisy "no inferior loaded" warnings in pwndbg.
            self._attach_inferior_tty()
        self.session.update_state(result["state"])
        self.session.record_command(
            result.get("command", f"file {binary_path}"), result
        )
        return result

    def set_poc_file(self, poc_file: str) -> Dict[str, Any]:
        """Set the proof-of-concept (PoC) file for the loaded binary; return raw responses"""
        logger.info(f"Set PoC file: {poc_file}")
        result = self.gdb.set_poc_file(poc_file)
        self.session.update_state(result["state"])
        self.session.record_command(
            result.get("command", f"set args {poc_file}"), result
        )
        return result

    def run(self, args: str = "", start: bool = False) -> Dict[str, Any]:
        """Run the loaded binary; return raw responses.

        Drains any leftover bytes in the inferior stdout buffer first so
        callers never see output from a previous invocation. After the
        underlying gdb call returns, captures the new inferior pid (for
        ``interrupt``) and attaches ``recent_stdout`` (whatever the program
        printed before pausing).
        """
        logger.info(f"Run with args: '{args}'")
        if not self.session.binary_loaded:
            return {
                "command": "run",
                "responses": [],
                "success": False,
                "state": self.gdb.get_state(),
                "error": "No binary loaded. Use set_file first.",
            }
        # Make sure pty is attached even if set_file ran before the pty
        # was created (defensive — should already be done in set_file).
        self._attach_inferior_tty()
        # Discard any stale buffered output from a previous run.
        self.io.reset()
        self._inferior_pid = None
        result = self.gdb.run(args, start=start)
        self.session.update_state(result["state"])
        self.session.record_command(result.get("command", "run"), result)
        self._capture_inferior_pid(result)
        return self._attach_recent_stdout(result, timeout=self._DRAIN_AFTER_RESUME_SEC)

    def finish(self) -> Dict[str, Any]:
        """Run until current function finishes; return raw responses.

        Attaches ``recent_stdout`` so callers see anything the program
        printed while running to the function's return.
        """
        logger.info("Finish current function")
        result = self.gdb.finish()
        self.session.update_state(result["state"])
        self.session.record_command(result.get("command", "finish"), result)
        return self._attach_recent_stdout(result, timeout=self._DRAIN_AFTER_RESUME_SEC)

    def step_control(self, command: str) -> Dict[str, Any]:
        """Execute stepping commands (c, n, s, ni, si); return raw responses.

        Attaches ``recent_stdout`` so callers see anything the inferior
        printed while running to the next stop.
        """
        logger.info(f"Step control: {command}")
        command_map = {
            "c": "continue",
            "n": "next",
            "s": "step",
            "ni": "nexti",
            "si": "stepi",
        }
        actual = command_map.get(command, command)
        current_state = self.gdb.get_state()
        if current_state != "stopped":
            return {
                "command": actual,
                "responses": [],
                "success": False,
                "state": current_state,
                "error": f"Cannot execute '{command}' in state '{current_state}'",
            }
        if actual == "continue":
            result = self.gdb.continue_execution()
        elif actual == "next":
            result = self.gdb.next()
        elif actual == "step":
            result = self.gdb.step()
        elif actual == "nexti":
            result = self.gdb.nexti()
        elif actual == "stepi":
            result = self.gdb.stepi()
        else:
            return {
                "command": actual,
                "responses": [],
                "success": False,
                "state": current_state,
                "error": f"Unknown step command '{command}'",
            }
        self.session.update_state(result["state"])
        self.session.record_command(result.get("command", actual), result)
        return self._attach_recent_stdout(result, timeout=self._DRAIN_AFTER_RESUME_SEC)

    def get_context(self, context_type: str = "all") -> Dict[str, Any]:
        """Get debugging context (registers, stack, disassembly, etc.)"""
        logger.info(f"Get context: {context_type}")
        if self.gdb.get_state() != "stopped":
            return {
                "command": f"context {context_type}",
                "responses": [],
                "success": False,
                "state": self.gdb.get_state(),
                "error": f"Cannot get context while inferior is {self.gdb.get_state()}",
            }
        if context_type == "all":
            # Aggregate raw responses of each context call
            aggregated = {
                "success": True,
                "state": self.gdb.get_state(),
                "contexts": {},
            }
            for ctx_type in ["regs", "stack", "disasm", "code", "backtrace"]:
                aggregated["contexts"][ctx_type] = self.gdb.get_context(ctx_type)
            self.session.record_command("context all", aggregated)
            return aggregated
        else:
            result = self.gdb.get_context(context_type)
            self.session.record_command(f"context {context_type}", result)
            return result

    def set_breakpoint(
        self, location: str, condition: Optional[str] = None
    ) -> Dict[str, Any]:
        """Set a breakpoint; return raw responses"""
        logger.info(f"Set breakpoint at {location}")
        result = self.gdb.set_breakpoint(location, condition)
        self.session.record_command(result.get("command", "break"), result)
        return result

    def list_breakpoints(self) -> Dict[str, Any]:
        """List all breakpoints; return raw responses"""
        logger.info("List breakpoints")
        result = self.gdb.list_breakpoints()
        self.session.record_command(result.get("command", "info breakpoints"), result)
        return result

    def delete_breakpoint(self, number: int) -> Dict[str, Any]:
        """Delete a breakpoint; return raw responses"""
        logger.info(f"Delete breakpoint #{number}")
        result = self.gdb.delete_breakpoint(number)
        self.session.record_command(result.get("command", f"delete {number}"), result)
        return result

    def toggle_breakpoint(self, number: int, enable: bool) -> Dict[str, Any]:
        """Enable or disable a breakpoint; return raw responses"""
        action = "enable" if enable else "disable"
        logger.info(f"{action} breakpoint #{number}")
        result = (
            self.gdb.enable_breakpoint(number)
            if enable
            else self.gdb.disable_breakpoint(number)
        )
        self.session.record_command(
            result.get("command", f"{action} breakpoints {number}"), result
        )
        return result

    def _get_full_context(self) -> Dict[str, Any]:
        """Get complete debugging context (raw responses per context)"""
        contexts = {}
        for ctx_type in ["regs", "stack", "disasm", "code", "backtrace"]:
            contexts[ctx_type] = self.gdb.get_context(ctx_type)
        return contexts

    def get_memory(
        self, address: str, size: int = 64, format: str = "hex"
    ) -> Dict[str, Any]:
        """Read memory at specified address; return raw responses"""
        logger.info(f"Read memory at {address}, {size} bytes as {format}")
        if format == "hex":
            cmd = f"hexdump {address} {size}"
        elif format == "string":
            cmd = f"x/s {address}"
        else:
            cmd = f"x/{size}b {address}"
        result = self.gdb.execute_command(cmd)
        self.session.record_command(cmd, result)
        return result

    def disassemble(self, address: str) -> Dict[str, Any]:
        """Disassemble the specified address; return raw responses"""
        logger.info(f"Disassemble {address}")
        result = self.gdb.execute_command(f"disassemble {address}")
        self.session.record_command(f"disassemble {address}", result)
        return result

    def get_session_info(self) -> Dict[str, Any]:
        """Get current session information (no GDB sync/parsing)"""
        return {
            "session": self.session.to_dict(),
            "gdb_state": self.gdb.get_state(),
            "inferior_pid": self._inferior_pid,
            "inferior_tty": self.io.slave_path,
        }

    # ------------------------------------------------------------------
    # Interactive I/O with the debugged inferior (separate channel from
    # GDB's MI command pipe — bytes here go to/from the program, not gdb).
    # ------------------------------------------------------------------

    def send_to_stdin(
        self,
        data: str,
        encoding: str = "text",
        append_newline: bool = True,
    ) -> Dict[str, Any]:
        """Write bytes to the inferior's stdin via the pty master.

        The inferior must be in a state where it's actually reading from
        stdin (typically RUNNING, blocked in ``read``). If the inferior
        has exited, the write may succeed but be discarded.
        """
        try:
            payload = _encode_stdin_data(data, encoding)
        except Exception as e:
            return {
                "success": False,
                "error": f"failed to decode data ({encoding}): {e}",
                "state": self.gdb.get_state(),
            }
        if encoding == "text" and append_newline and not payload.endswith(b"\n"):
            payload = payload + b"\n"
        try:
            written = self.io.write(payload)
        except OSError as e:
            return {
                "success": False,
                "error": f"pty write failed: {e}",
                "state": self.gdb.get_state(),
            }
        self.session.record_command(
            f"send_to_stdin({len(payload)}B)",
            {"bytes_written": written, "encoding": encoding},
        )
        return {
            "success": True,
            "bytes_written": written,
            "state": self.gdb.get_state(),
        }

    def read_stdout(
        self, timeout: float = 0.5, encoding: str = "text"
    ) -> Dict[str, Any]:
        """Pop accumulated inferior stdout from the pty buffer.

        Waits up to ``timeout`` seconds for the FIRST byte if the buffer
        is empty, then drains everything available. Always non-blocking
        once any data is present.
        """
        try:
            data = self.io.drain(timeout=max(0.0, float(timeout)))
        except Exception as e:
            return {
                "success": False,
                "error": f"drain failed: {e}",
                "state": self.gdb.get_state(),
            }
        return {
            "success": True,
            "bytes": len(data),
            "data": _decode_inferior_bytes(data, encoding),
            "encoding": encoding,
            "state": self.gdb.get_state(),
        }

    def interrupt(self) -> Dict[str, Any]:
        """Send SIGINT to the inferior, breaking it back into GDB.

        Use when the inferior is RUNNING (e.g. infinite loop, deadlock,
        long compute) and you need to regain control. Cheaper and more
        reliable than ``-exec-interrupt`` because it doesn't require
        non-stop mode.

        Implementation: ``os.kill(inferior_pid, SIGINT)`` followed by a
        bounded read of GDB's pending notifications so ``state`` is
        synchronised before returning.
        """
        pid = self._inferior_pid
        if pid is None:
            return {
                "success": False,
                "error": "no inferior pid recorded; nothing to interrupt",
                "state": self.gdb.get_state(),
            }
        if self.gdb.get_state() not in ("running",):
            return {
                "success": False,
                "error": (
                    f"inferior is not running (state={self.gdb.get_state()}); "
                    f"interrupt is a no-op"
                ),
                "state": self.gdb.get_state(),
            }
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            return {
                "success": False,
                "error": f"inferior pid {pid} no longer exists",
                "state": self.gdb.get_state(),
            }
        # Pull pending gdb notifications so the *stopped* notification is
        # consumed and our state machine flips to "stopped".
        try:
            pending = self.gdb.controller.get_gdb_response(
                timeout_sec=1.0, raise_error_on_timeout=False
            )
            for r in pending:
                if r.get("type") == "notify":
                    self.gdb._handle_notify(r)
        except Exception:
            logger.exception("failed to read post-interrupt notifications")
            pending = []
        result = {
            "command": f"kill -INT {pid}",
            "responses": pending,
            "success": True,
            "state": self.gdb.get_state(),
        }
        self.session.update_state(result["state"])
        self.session.record_command(f"interrupt(pid={pid})", result)
        return self._attach_recent_stdout(result, timeout=self._DRAIN_AFTER_RESUME_SEC)

    def close(self) -> None:
        """Release pty fds, stop reader thread, exit gdb."""
        try:
            self.io.close()
        except Exception:
            logger.exception("InferiorIO.close failed")
        try:
            self.gdb.close()
        except Exception:
            logger.exception("GdbController.close failed")
