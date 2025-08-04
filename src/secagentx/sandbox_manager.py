"""
SandboxManager for managing sandbox instances per session.

This module provides a centralized way to manage sandbox instances,
creating them on-demand and cleaning them up when needed.
"""

import os
import logging
from typing import Dict, Optional
from secagentx.sandbox import BaseSandbox, NativeDockerSandbox, SweRexSandbox
from swerex.runtime.abstract import CreateBashSessionRequest, BashAction
from swerex.exceptions import SessionDoesNotExistError

logger = logging.getLogger(__name__)


class SandboxManager:
    """Manages sandbox instances per session_id."""
    
    _instances: Dict[str, BaseSandbox] = {}
    
    @classmethod
    def get_sandbox(cls, session_id: str) -> BaseSandbox:
        """
        Get or create a sandbox for the given session_id.
        
        Args:
            session_id: The session identifier
            
        Returns:
            BaseSandbox: A sandbox instance for the session
        """
        if session_id in cls._instances:
            return cls._instances[session_id]
        
        # Create new sandbox using the fallback logic
        sandbox = cls._create_sandbox()
        cls._instances[session_id] = sandbox
        
        logger.info(f"Created new sandbox for session {session_id}")
        return sandbox
    
    @classmethod
    def cleanup_sandbox(cls, session_id: str) -> None:
        """
        Clean up and remove sandbox for the given session_id.
        
        Args:
            session_id: The session identifier
        """
        if session_id not in cls._instances:
            return
        
        sandbox = cls._instances[session_id]
        
        try:
            # Cleanup SWE-ReX resources if they exist
            if hasattr(sandbox, 'deployment') and sandbox.deployment:
                try:
                    # SWE-ReX stop() is async, need to run it properly
                    import asyncio
                    asyncio.run(sandbox.deployment.stop())
                except Exception as e:
                    logger.warning(f"SWE-ReX deployment cleanup error: {e}")
            
            # Delete container for Native Docker
            if hasattr(sandbox, 'delete_container'):
                sandbox.delete_container()
                
        except Exception as e:
            logger.warning(f"Error cleaning up sandbox for session {session_id}: {e}")
        
        del cls._instances[session_id]
        logger.info(f"Cleaned up sandbox for session {session_id}")
    
    @classmethod
    def cleanup_all(cls) -> None:
        """Clean up all sandbox instances."""
        session_ids = list(cls._instances.keys())
        for session_id in session_ids:
            cls.cleanup_sandbox(session_id)
    
    @classmethod
    def _create_sandbox(cls) -> BaseSandbox:
        """
        Create a new sandbox instance using the fallback logic.
        Try SweRexSandbox first, fallback to NativeDockerSandbox if it fails.
        
        Returns:
            BaseSandbox: A configured sandbox instance
        """
        IMAGE_NAME = os.getenv("IMAGE_NAME", "ubuntu:20.04")
        
        sandbox = None
        try:
            from swerex.deployment.docker import DockerDeployment, DockerDeploymentConfig
            
            # Try SWE-ReX sandbox first
            deployment_config = DockerDeploymentConfig(
                image=IMAGE_NAME,
                remove_container=False,
                remove_images=False
            )
            deployment = DockerDeployment.from_config(deployment_config)
            # Start deployment in a thread-pool to avoid RuntimeError when an event
            # loop is already running.
            import asyncio, concurrent.futures
            try:
                asyncio.get_running_loop()
                # Already in an event loop – off-load to a separate thread.
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(asyncio.run, deployment.start()).result()
            except RuntimeError:
                # No running loop in this thread – safe to run directly.
                asyncio.run(deployment.start())
            
            sandbox = SweRexSandbox(
                image_name=IMAGE_NAME,
                compile_command=os.getenv("COMPILE_COMMAND", ""),
                run_command=os.getenv("RUN_COMMAND", ""),
                poc_dir=os.getenv("POC_DIR", "/tmp/poc"),
                deployment=deployment
            )

            logger.info("Created SweRexSandbox instance (runtime started)")
            return sandbox
                
        except Exception as e:
            logger.warning(f"SWE-ReX sandbox failed: {e}")
            logger.info("Falling back to Native Docker sandbox")
            
            # Cleanup SWE-ReX resources if they were created
            if sandbox and hasattr(sandbox, 'deployment'):
                try:
                    # SWE-ReX stop() is async, need to run it properly
                    import asyncio
                    asyncio.run(sandbox.deployment.stop())
                except Exception as e:
                    logger.warning(f"SWE-ReX cleanup error during fallback: {e}")
            
            # Fallback to Native Docker sandbox
            sandbox = NativeDockerSandbox(
                image_name=IMAGE_NAME,
                compile_command=os.getenv("COMPILE_COMMAND", ""),
                run_command=os.getenv("RUN_COMMAND", ""),
                poc_dir=os.getenv("POC_DIR", "/tmp/poc")
            )
            
            # Test Native Docker sandbox
            test_output, test_exit_code = sandbox.run_command_in_container("echo 'native test'")
            if test_exit_code == 0 and "native test" in test_output:
                logger.info("Created Native Docker sandbox")
                return sandbox
            else:
                raise Exception(f"Native Docker sandbox test failed: {test_output}") 