import asyncio
import os
import tempfile
import concurrent.futures
from typing import Optional, Tuple, Union
# SWE-ReX
from swerex.deployment.abstract import AbstractDeployment
from swerex.runtime.abstract import CreateBashSessionRequest, BashAction
from swerex.exceptions import SessionDoesNotExistError
from .base_sandbox import BaseSandbox


# Thread-pool helper for running async code from sync context

def _sync_run(coro):
    """Safely execute an async coroutine from synchronous context.

    1. If the current thread has *no* running event loop, just call ``asyncio.run``.
    2. If a loop is already running (e.g. when called from async ADK code),
       offload the coroutine to a separate thread that can create its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Handle event loop presence transparently
        return asyncio.run(coro)

    # Already inside an event loop – execute in thread pool
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


class SweRexSandbox(BaseSandbox):
    """SWE-ReX sandbox implementation using SWE-ReX deployment."""
    
    def __init__(self, 
                 image_name: str, 
                 compile_command: str, 
                 run_command: str, 
                 poc_dir: str,
                 deployment: AbstractDeployment):
        """
        Initialize SweRexSandbox.
        
        Args:
            image_name: Docker image name to use
            compile_command: Command to compile the target
            run_command: Command to run the target
            poc_dir: Directory for PoC files
            deployment: SWE-ReX deployment instance
        """
        super().__init__(image_name, compile_command, run_command, poc_dir)
        self.deployment = deployment

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_runtime_and_session(self) -> None:
        """Lazily start runtime and create default bash session if absent."""
        # 1. Ensure runtime started
        if getattr(self.deployment, "_runtime", None) is None:
            await self.deployment.start()

        runtime = self.deployment._runtime  # type: ignore[attr-defined]

        # 2. Ensure default session exists
        try:
            await runtime.run_in_session(  # type: ignore[arg-type]
                BashAction(command="true", check="silent")
            )
        except SessionDoesNotExistError:
            await runtime.create_session(CreateBashSessionRequest())

    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the runtime environment to local filesystem."""
        return _sync_run(self._copy_file_from_container_async(src_path, dst_path))

    async def _copy_file_from_container_async(self, src_path: str, dst_path: str):
        """Async implementation of copy_file_from_container."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        # Check if file exists
        check_action = BashAction(
            command=f"test -f {src_path}",
            check="silent"
        )
        result = await runtime.run_in_session(check_action)
        
        if result.exit_code != 0:
            raise FileNotFoundError(f"File {src_path} does not exist in the container.")
        
        # Read file content
        read_action = BashAction(
            command=f"cat {src_path}",
            check="silent"
        )
        result = await runtime.run_in_session(read_action)
        
        if result.exit_code == 0:
            # Write content to local file
            with open(dst_path, "w", encoding="utf-8") as f:
                f.write(result.output)
        else:
            raise RuntimeError(f"Failed to read file {src_path}: {result.output}")

    def copy_file_to_container(self, local_path: str, container_path: str):
        """Copy a single file to the runtime environment."""
        return _sync_run(self._copy_file_to_container_async(local_path, container_path))

    async def _copy_file_to_container_async(self, local_path: str, container_path: str):
        """Async implementation of copy_file_to_container."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        # Read local file content
        with open(local_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Create directory if needed
        container_dir = os.path.dirname(container_path)
        mkdir_action = BashAction(
            command=f"mkdir -p {container_dir}",
            check="silent"
        )
        await runtime.run_in_session(mkdir_action)
        
        # Write content to container file using cat with heredoc
        # Escape single quotes in content to prevent shell injection
        escaped_content = content.replace("'", "'\"'\"'")
        write_action = BashAction(
            command=f"cat > {container_path} << 'EOF'\n{escaped_content}\nEOF",
            check="silent"
        )
        result = await runtime.run_in_session(write_action)
        
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to write file {container_path}: {result.output}")

    def run_poc(self, poc_command: str) -> Tuple[str, int]:
        """Run a PoC command in the runtime environment."""
        return _sync_run(self._run_poc_async(poc_command))

    async def _run_poc_async(self, poc_command: str) -> Tuple[str, int]:
        """Async implementation of run_poc."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        action = BashAction(
            command=poc_command,
            check="silent",
            timeout=30.0
        )
        result = await runtime.run_in_session(action)
        
        return result.output, result.exit_code or 0

    def compile_target(self) -> str:
        """Compile target in the runtime environment using the compile_command."""
        return _sync_run(self._compile_target_async())

    async def _compile_target_async(self) -> str:
        """Async implementation of compile_target."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        # Get working directory first
        workdir = await self._get_work_dir_async()
        
        # Run compile command in working directory
        compile_cmd = f"cd {workdir} && {self.compile_command}"
        action = BashAction(
            command=compile_cmd,
            check="silent",
            timeout=120.0
        )
        result = await runtime.run_in_session(action)
        
        return result.output

    def extract_file_from_container(self, filepath: str) -> str:
        """Extract the content of the specified file from the runtime environment."""
        return _sync_run(self._extract_file_from_container_async(filepath))

    async def _extract_file_from_container_async(self, filepath: str) -> str:
        """Async implementation of extract_file_from_container."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        action = BashAction(
            command=f"cat {filepath}",
            check="silent"
        )
        result = await runtime.run_in_session(action)
        
        if result.exit_code == 0:
            return result.output
        else:
            raise FileNotFoundError(f"Could not read file {filepath}: {result.output}")

    def run_command_in_container(self, command: str) -> Tuple[str, int]:
        """Run a command inside the runtime environment (sync wrapper)."""
        return _sync_run(self._run_command_in_container_async(command))

    async def _run_command_in_container_async(self, command: str) -> Tuple[str, int]:
        """Async implementation of run_command_in_container."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        action = BashAction(
            command=command,
            check="silent",
            timeout=60.0
        )
        result = await runtime.run_in_session(action)
        
        return result.output, result.exit_code or 0

    def get_work_dir(self) -> str:
        """Get the current working directory in the runtime environment (sync wrapper)."""
        return _sync_run(self._get_work_dir_async())

    async def _get_work_dir_async(self) -> str:
        """Async implementation of get_work_dir."""
        await self._ensure_runtime_and_session()
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        action = BashAction(
            command="pwd",
            check="silent"
        )
        result = await runtime.run_in_session(action)
        
        if result.exit_code == 0:
            return result.output.strip()
        else:
            return "/tmp"  # fallback