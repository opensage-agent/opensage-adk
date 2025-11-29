"""
Pwndbg tools for MCP server

Provides MCP tool implementations for GDB/pwndbg commands.
Each tool returns immediate results suitable for LLM interaction.
"""

import logging
import signal
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
            gdb_path: Path to GDB executable (default: "pwndbg")
        """
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
            command: GDB command to execute
            timeout_sec: Timeout for command execution

        Returns:
            Dictionary with raw responses, success flag, and current state.
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
        """Run the loaded program using command"""
        if args:
            set_args_result = self.execute_command(f"b {args}")
            set_args_result = self.execute_command(f"continue")
            if not set_args_result["success"]:
                return set_args_result

        run_command = "start" if start else "run"
        result = self.execute_command(run_command)
        return result

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
        """Set a breakpoint using command"""
        command = f"b {location}"
        if condition:
            command = f'b {location} if "{condition}"'
        return self.execute_command(command)

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


class PwndbgTools:
    """MCP tools for pwndbg interaction"""

    def __init__(self):
        """
        Initialize pwndbg tools

        Args:
            gdb_controller: GDB controller instance
        """
        self.gdb = GdbController()
        self.session = SessionState()

    def execute(self, command: str) -> Dict[str, Any]:
        """Execute arbitrary GDB/pwndbg command and return raw responses"""
        logger.info(f"Execute tool: {command}")
        result = self.gdb.execute_command(command)
        self.session.update_state(result["state"])
        self.session.record_command(command, result)
        return result

    def set_file(self, binary_path: str) -> Dict[str, Any]:
        """Set the file to debug; return raw responses"""
        logger.info(f"Set file: {binary_path}")
        result = self.gdb.set_file(binary_path)
        if result.get("success"):
            self.session.binary_path = binary_path
            self.session.binary_loaded = True
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
        """Run the loaded binary; return raw responses"""
        logger.info(f"Run with args: '{args}'")
        if not self.session.binary_loaded:
            return {
                "command": "run",
                "responses": [],
                "success": False,
                "state": self.gdb.get_state(),
                "error": "No binary loaded. Use set_file first.",
            }
        result = self.gdb.run(args, start=start)
        self.session.update_state(result["state"])
        self.session.record_command(result.get("command", "run"), result)
        return result

    def finish(self) -> Dict[str, Any]:
        """Run until current function finishes; return raw responses"""
        logger.info("Finish current function")
        result = self.gdb.finish()
        self.session.update_state(result["state"])
        self.session.record_command(result.get("command", "finish"), result)
        return result

    def step_control(self, command: str) -> Dict[str, Any]:
        """Execute stepping commands (c, n, s, ni, si); return raw responses"""
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
        return result

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
        return {"session": self.session.to_dict(), "gdb_state": self.gdb.get_state()}
