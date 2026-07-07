import atexit
import json
import subprocess
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, TypeVar

from patchagent.logger import logger
from patchagent.lsp.language import LanguageServer

T = TypeVar("T")


class ClangdServer(LanguageServer):
    """
    Language Server Protocol Specification:
        https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
    """

    def __init__(self, source_path: Path):
        super().__init__(source_path)

        self.start()

    def add_header(self, message: str) -> bytes:
        return f"Content-Length: {len(message.encode())}\r\n\r\n{message}".encode()

    def notify(self, method: str, params: Dict) -> None:
        message = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )
        packet = self.add_header(message)
        self.stdin.write(packet)
        self.stdin.flush()

    def call(self, method: str, params: Dict) -> Dict:
        self.current_id += 1
        message = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self.current_id,
                "method": method,
                "params": params,
            }
        )
        packet = self.add_header(message)

        self.stdin.write(packet)
        self.stdin.flush()

        return self.recv()

    def recv(self) -> Dict:
        while True:
            header = b"Content-Length: "

            read_header = b""
            while len(read_header) < len(header):
                read_header += self.stdout.read(len(header) - len(read_header))

            assert read_header == header, f"Expected header {header!r}, got {read_header!r}"

            content = b""
            while not content.endswith(b"\r\n\r\n"):
                content += self.stdout.read(1)

            num_bytes = int(content.strip())
            raw = b""
            while len(raw) < num_bytes:
                raw += self.stdout.read(num_bytes - len(raw))

            data = json.loads(raw)
            if data.get("id") == self.current_id:
                return data

    def initialize(self) -> None:
        self.call(
            "initialize",
            {
                "processId": None,
                "rootPath": None,
                "rootUri": f"file://{self.source_path}",
                "capabilities": {},
                "trace": "off",
                "workspaceFolders": None,
            },
        )
        self.notify("initialized", {})

    def start(self) -> None:
        compile_command_json = self.source_path / "compile_commands.json"
        assert compile_command_json.is_file(), "compile_commands.json not found"

        self.current_id: int = 0
        self.process: subprocess.Popen[bytes] = subprocess.Popen(
            [
                "/usr/bin/clangd",
                "--clang-tidy",
                "--log=error",
                f"--compile-commands-dir={self.source_path}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

        assert self.process.stdin is not None
        assert self.process.stdout is not None
        assert self.process.stderr is None

        self.stdin: IO[bytes] = self.process.stdin
        self.stdout: IO[bytes] = self.process.stdout

        self.initialize()
        atexit.register(self.stop)

    def stop(self) -> None:
        try:
            self.notify("shutdown", {})
            self.notify("exit", {})
        except BrokenPipeError:
            logger.warning("[⚠️] BrokenPipeError encountered, terminating the process directly...")

        self.process.terminate()

    def _retry_on_broken_pipe(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Retry mechanism for handling BrokenPipeError.
        Restarts the clangd server once and retries the operation.
        """
        try:
            return func(*args, **kwargs)
        except BrokenPipeError:
            logger.warning("[⚠️] BrokenPipeError encountered, restarting clangd server and retrying...")
            self.stop()
            self.start()
            return func(*args, **kwargs)

    def find_definition_internal(self, path: Path, line: int, chr: int) -> List[str]:
        content = path.read_text(errors="ignore")

        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": f"file://{path}",
                    "languageId": "c",
                    "version": 1,
                    "text": content,
                }
            },
        )

        packet = self.call(
            "textDocument/definition",
            {
                "textDocument": {
                    "uri": f"file://{path}",
                },
                "position": {
                    "line": line,
                    "character": chr,
                },
            },
        )

        results = packet["result"]
        if results is None:
            return []

        locations = []
        for result in results:
            prefix, filepath = f"file://{self.source_path}/", result["uri"]
            if isinstance(filepath, str) and not filepath.startswith(prefix):
                continue

            filepath = filepath[len(prefix) :]
            linum = result["range"]["start"]["line"] + 1
            colnum = result["range"]["start"]["character"] + 1
            locations.append(f"{filepath}:{linum}:{colnum}")

        return locations

    def hover_internal(self, path: Path, line: int, chr: int) -> str:
        content = path.read_text(errors="ignore")

        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": f"file://{path}",
                    "languageId": "c",
                    "version": 1,
                    "text": content,
                }
            },
        )
        packet = self.call(
            "textDocument/hover",
            {
                "textDocument": {"uri": f"file://{path}"},
                "position": {
                    "line": line,
                    "character": chr,
                },
            },
        )

        results = packet["result"]
        return results["contents"]["value"] if results else ""

    def find_definition(self, path: Path, line: int, column: int) -> List[str]:
        assert not path.is_absolute()
        filepath, linum, colnum = self.source_path / path, line - 1, column - 1
        logger.info(f"[🚧] find_definition for {filepath}:{linum}:{colnum}")

        return self._retry_on_broken_pipe(self.find_definition_internal, filepath, linum, colnum)

    def hover(self, path: Path, line: int, column: int) -> str:
        assert not path.is_absolute()
        filepath, linum, colnum = self.source_path / path, line - 1, column - 1
        logger.info(f"[🚧] hover for {filepath}:{linum}:{colnum}")

        return self._retry_on_broken_pipe(self.hover_internal, filepath, linum, colnum)
