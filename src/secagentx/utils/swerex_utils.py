import asyncio
import os
from typing import Tuple, Optional
from swerex.deployment.docker import DockerDeployment
from swerex.runtime.abstract import Command, BashAction, CreateBashSessionRequest
from swerex.runtime.local import LocalRuntime


class SWEReXRunner:
    """
    SWE-ReX based command runner to replace run_command_in_container functionality.
    Provides both synchronous and asynchronous interfaces for running commands.
    """
    
    def __init__(self, image_name: str = None, container_id: str = None):
        """
        Initialize SWE-ReX runner.
        
        Args:
            image_name: Docker image name to use for deployment
            container_id: Existing container ID to connect to (if None, creates new deployment)
        """
        self.image_name = image_name
        self.container_id = container_id
        self.deployment = None
        self.runtime = None
        self._session_created = False
        
    async def _ensure_runtime(self):
        """Ensure runtime is started and ready."""
        if self.runtime is None:
            if self.container_id:
                # TODO: Implement connection to existing container
                # For now, we'll create a new deployment
                pass
            
            if self.image_name:
                self.deployment = DockerDeployment(image=self.image_name)
            else:
                # Use local deployment as fallback
                from swerex.deployment.local import LocalDeployment
                self.deployment = LocalDeployment()
            
            await self.deployment.start()
            self.runtime = self.deployment.runtime
            
            # Check if runtime is alive
            is_alive = await self.runtime.is_alive()
            if not is_alive:
                raise RuntimeError("SWE-ReX runtime failed to start")
    
    async def run_command(self, command: str, timeout: Optional[float] = None) -> Tuple[str, int]:
        """
        Run a command using SWE-ReX (async version).
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (None for no timeout)
            
        Returns:
            Tuple of (output, exit_code)
        """
        await self._ensure_runtime()
        
        # Create a Command object
        cmd = Command(
            command=["/bin/bash", "-c", command],
            timeout=timeout,
            check=False  # Don't raise exception on non-zero exit code
        )
        
        # Execute the command
        response = await self.runtime.execute(cmd)
        
        return response.stdout, response.exit_code or 0
    
    async def run_command_in_session(self, command: str, timeout: Optional[float] = None) -> Tuple[str, int]:
        """
        Run a command in a persistent bash session (async version).
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (None for no timeout)
            
        Returns:
            Tuple of (output, exit_code)
        """
        await self._ensure_runtime()
        
        # Create session if not already created
        if not self._session_created:
            await self.runtime.create_session(CreateBashSessionRequest())
            self._session_created = True
        
        # Create bash action
        action = BashAction(
            command=command,
            timeout=timeout,
            check="silent"  # Extract exit code but don't raise exception
        )
        
        # Execute in session
        response = await self.runtime.run_in_session(action)
        
        return response.output, response.exit_code or 0
    
    def run_command_sync(self, command: str, timeout: Optional[float] = None) -> Tuple[str, int]:
        """
        Run a command using SWE-ReX (synchronous version).
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (None for no timeout)
            
        Returns:
            Tuple of (output, exit_code)
        """
        return asyncio.run(self.run_command(command, timeout))
    
    def run_command_in_session_sync(self, command: str, timeout: Optional[float] = None) -> Tuple[str, int]:
        """
        Run a command in a persistent bash session (synchronous version).
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (None for no timeout)
            
        Returns:
            Tuple of (output, exit_code)
        """
        return asyncio.run(self.run_command_in_session(command, timeout))
    
    async def close(self):
        """Close the runtime and cleanup resources."""
        if self.deployment:
            await self.deployment.stop()
            self.deployment = None
            self.runtime = None
            self._session_created = False
    
    def close_sync(self):
        """Close the runtime and cleanup resources (synchronous version)."""
        asyncio.run(self.close())


# Global SWE-ReX runner instance
_swerex_runner = None


def get_swerex_runner() -> SWEReXRunner:
    """
    Get or create a global SWE-ReX runner instance.
    
    Returns:
        SWEReXRunner instance
    """
    global _swerex_runner
    
    if _swerex_runner is None:
        # Get image name from environment or use default
        image_name = os.getenv("IMAGE_NAME", "python:3.12")
        container_id = os.getenv("CONTAINER_ID")
        
        _swerex_runner = SWEReXRunner(
            image_name=image_name,
            container_id=container_id
        )
    
    return _swerex_runner


def run_command_in_container_swerex(container_id: str, command: str) -> Tuple[str, int]:
    """
    SWE-ReX based replacement for run_command_in_container.
    
    Args:
        container_id: Container ID (for compatibility, not used in SWE-ReX)
        command: Command to execute
        
    Returns:
        Tuple of (output, exit_code)
    """
    runner = get_swerex_runner()
    return runner.run_command_sync(command)


def run_command_in_session_swerex(container_id: str, command: str) -> Tuple[str, int]:
    """
    Run command in a persistent bash session using SWE-ReX.
    
    Args:
        container_id: Container ID (for compatibility, not used in SWE-ReX)
        command: Command to execute
        
    Returns:
        Tuple of (output, exit_code)
    """
    runner = get_swerex_runner()
    return runner.run_command_in_session_sync(command)


# Convenience functions for backward compatibility
def grep_tool_swerex(expression: str) -> dict:
    """
    SWE-ReX based replacement for grep_tool.
    
    Args:
        expression: Regex pattern to search for
        
    Returns:
        dict: A dictionary with key "result" pointing to a list of grep matches
    """
    import os
    
    container_id = os.getenv("CONTAINER_ID", "default")
    grep_command = " ".join([
        'grep',
        "-rniE",
        expression,  
        "--",
        "/src"  
    ])
    
    output = ""
    try:
        output, exit_code = run_command_in_container_swerex(container_id, grep_command)
    except Exception as e:
        return {
            "result": [],
            "error": f"Failed to run grep command: {e}"
        }

    # Split into lines and check count
    lines = output.strip().splitlines()
    if len(lines) > 100 or len(output) > 5000:
        return {
            "result": [],
            "error": "Pattern too broad; please provide a more specific pattern."
        }

    dict_result = {"result": []}
    
    for line in lines:
        if line.strip():
            dict_result["result"].append({
                "full_line": line.strip()
            })

    return dict_result 