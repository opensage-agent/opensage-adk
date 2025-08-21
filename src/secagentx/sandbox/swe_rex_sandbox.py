import asyncio
import base64
import os
import tempfile
import concurrent.futures
from typing import Optional, Tuple, Union
# SWE-ReX
from swerex.deployment.abstract import AbstractDeployment
from swerex.runtime.abstract import CreateBashSessionRequest, BashAction
# removed SessionDoesNotExistError import since manager handles session creation
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

    # Runtime and default session are created by SandboxManager at sandbox creation.

    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the runtime environment to local filesystem."""
        return _sync_run(self._copy_file_from_container_async(src_path, dst_path))

    async def _copy_file_from_container_async(self, src_path: str, dst_path: str):
        """Async implementation of copy_file_from_container."""
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        # Check if file exists
        check_action = BashAction(
            command=f"test -f {src_path}",
            check="silent"
        )
        result = await runtime.run_in_session(check_action)
        
        if result.exit_code != 0:
            raise FileNotFoundError(f"File {src_path} does not exist in the container.")
        
        # Read file content as base64 to handle binary files correctly
        read_action = BashAction(
            command=f"base64 {src_path}",
            check="silent"
        )
        result = await runtime.run_in_session(read_action)
        
        if result.exit_code == 0:
            # Decode base64 content and write to local file in binary mode
            try:
                binary_content = base64.b64decode(result.output.strip())
                with open(dst_path, "wb") as f:
                    f.write(binary_content)
            except Exception as e:
                raise RuntimeError(f"Failed to decode base64 content from {src_path}: {str(e)}")
        else:
            raise RuntimeError(f"Failed to read file {src_path}: {result.output}")

    def copy_file_to_container(self, local_path: str, container_path: str):
        """Copy a single file to the runtime environment."""
        return _sync_run(self._copy_file_to_container_async(local_path, container_path))

    async def _copy_file_to_container_async(self, local_path: str, container_path: str):
        """Async implementation of copy_file_to_container."""
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        # Read local file content in binary mode to handle any file type
        with open(local_path, "rb") as f:
            binary_content = f.read()
        
        # Encode binary content as base64 for safe shell transmission
        base64_content = base64.b64encode(binary_content).decode('ascii')
        
        # Create directory if needed
        container_dir = os.path.dirname(container_path)
        mkdir_action = BashAction(
            command=f"mkdir -p {container_dir}",
            check="silent"
        )
        await runtime.run_in_session(mkdir_action)

        # delete the file if it exists
        delete_action = BashAction(
            command=f"rm -f {container_path}",
            check="silent"
        )
        await runtime.run_in_session(delete_action)
        
        # Write content to container file using base64 decoding
        # This approach handles binary files correctly
        write_action = BashAction(
            command=f"echo '{base64_content}' | base64 -d > {container_path}",
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
        runtime = self.deployment._runtime  # type: ignore[attr-defined]
        
        # Try to read as text first (for backwards compatibility)
        action = BashAction(
            command=f"file {filepath} | grep -q text && cat {filepath} || base64 {filepath}",
            check="silent"
        )
        result = await runtime.run_in_session(action)
        
        if result.exit_code == 0:
            # Check if the file was detected as binary by seeing if output is base64
            output = result.output.strip()
            try:
                # If it's valid base64, decode it and return as latin-1 string for compatibility
                binary_content = base64.b64decode(output)
                return binary_content.decode('latin-1')
            except:
                # If not base64, it's text content, return as-is
                return output
        else:
            raise FileNotFoundError(f"Could not read file {filepath}: {result.output}")

    def run_command_in_container(self, command: str) -> Tuple[str, int]:
        """Run a command inside the runtime environment (sync wrapper)."""
        return _sync_run(self._run_command_in_container_async(command))

    async def _run_command_in_container_async(self, command: str) -> Tuple[str, int]:
        """Async implementation of run_command_in_container."""
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