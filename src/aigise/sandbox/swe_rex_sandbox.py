import asyncio
import base64
import concurrent.futures
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union

# SWE-ReX
from swerex.deployment.abstract import AbstractDeployment
from swerex.deployment.config import DockerDeploymentConfig
from swerex.deployment.docker import DockerDeployment
from swerex.runtime.abstract import BashAction, CreateBashSessionRequest

# removed SessionDoesNotExistError import since manager handles session creation
from .base_sandbox import BaseSandbox
from .docker_config import DockerConfig
from .template_fallback import TemplateFallbackMixin

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


class SweRexSandbox(BaseSandbox, TemplateFallbackMixin):
    """SWE-ReX sandbox implementation using SWE-ReX deployment.

    This class takes a DockerConfig, constructs a DockerDeployment accordingly,
    starts it (handling event loop presence), and prepares a default session.
    """

    def __init__(
        self,
        docker_config: DockerConfig,
    ):
        """
        Initialize SweRexSandbox.

        Args:
            docker_config: Docker configuration used to create the SWE-ReX deployment (must include image)
        """
        # Validate docker_config
        if docker_config is None or not isinstance(docker_config, DockerConfig):
            raise TypeError("docker_config must be a DockerConfig instance")
        if not docker_config.image:
            raise ValueError("DockerConfig.image must be provided for SweRexSandbox")

        super().__init__(docker_config)

        # Ensure Docker image is available (with template fallback if needed)
        self._ensure_image_with_template_fallback(docker_config)

        # Build docker_args from DockerConfig
        docker_args: List[str] = []

        # Raw passthrough first
        if docker_config.docker_args:
            docker_args.extend(docker_config.docker_args)

        # Environment
        if docker_config.environment:
            for k, v in docker_config.environment.items():
                docker_args += ["-e", f"{k}={v}"]

        # Volumes (binds)
        for spec in docker_config.volumes:
            docker_args += ["-v", spec]

        # Mounts (as --mount ...)
        for spec in docker_config.mounts:
            docker_args += ["--mount", spec]

        # Network
        if docker_config.network:
            docker_args += ["--network", docker_config.network]

        # Resources
        if docker_config.shm_size:
            docker_args += ["--shm-size", str(docker_config.shm_size)]
        if docker_config.mem_limit:
            docker_args += ["--memory", str(docker_config.mem_limit)]
        if docker_config.cpus:
            docker_args += ["--cpus", str(docker_config.cpus)]

        # Security / permissions
        if docker_config.privileged:
            docker_args += ["--privileged"]
        for opt in docker_config.security_opt:
            docker_args += ["--security-opt", str(opt)]
        for cap in docker_config.cap_add:
            docker_args += ["--cap-add", str(cap)]

        # GPUs
        if docker_config.gpus:
            docker_args += ["--gpus", str(docker_config.gpus)]

        # Extra ports in addition to SWE-ReX's own port mapping
        for container_port, host_binding in docker_config.ports.items():
            # Normalize container port to include protocol if not specified
            if "/" not in container_port:
                container_port = f"{container_port}/tcp"

            # Handle different host binding types
            if isinstance(host_binding, int):
                # Simple host port number
                docker_args += ["-p", f"{host_binding}:{container_port}"]
            elif host_binding is None:
                # Random host port
                docker_args += ["-p", container_port]
            elif isinstance(host_binding, tuple):
                # (host_ip, host_port) tuple
                docker_args += [
                    "-p",
                    f"{host_binding[0]}:{host_binding[1]}:{container_port}",
                ]
            elif isinstance(host_binding, list):
                # List of host ports
                for host_port in host_binding:
                    docker_args += ["-p", f"{host_port}:{container_port}"]

        # Construct DockerDeploymentConfig
        dep_config = DockerDeploymentConfig(
            image=str(docker_config.image),
            port=None,  # let SWE-ReX choose a free port unless specified
            docker_args=docker_args,
            startup_timeout=300.0,
            pull="missing",
            remove_images=bool(docker_config.remove_images)
            if docker_config.remove_images is not None
            else False,
            python_standalone_dir=docker_config.python_standalone_dir,
            platform=docker_config.platform,
            remove_container=bool(docker_config.remove_container)
            if docker_config.remove_container is not None
            else True,
        )

        # Create deployment and start it; then create default session
        deployment = DockerDeployment.from_config(dep_config)

        import concurrent.futures

        try:
            asyncio.get_running_loop()

            def _start_and_create():
                asyncio.run(_start_and_session())

            async def _start_and_session():
                await deployment.start()
                runtime = deployment._runtime  # type: ignore[attr-defined]
                try:
                    await runtime.create_session(CreateBashSessionRequest())
                except Exception:
                    pass

            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(_start_and_create).result()
        except RuntimeError:

            async def _start_and_session_main():
                await deployment.start()
                runtime = deployment._runtime  # type: ignore[attr-defined]
                try:
                    await runtime.create_session(CreateBashSessionRequest())
                except Exception:
                    pass

            asyncio.run(_start_and_session_main())

        # Store deployment
        self.deployment = deployment

    # Runtime and default session are created by SandboxManager at sandbox creation.

    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the runtime environment to local filesystem."""
        return _sync_run(self._copy_file_from_container_async(src_path, dst_path))

    async def _copy_file_from_container_async(self, src_path: str, dst_path: str):
        """Async implementation of copy_file_from_container."""
        runtime = self.deployment._runtime  # type: ignore[attr-defined]

        # Check if file exists
        check_action = BashAction(command=f"test -f {src_path}", check="silent")
        result = await runtime.run_in_session(check_action)

        if result.exit_code != 0:
            raise FileNotFoundError(f"File {src_path} does not exist in the container.")

        # Read file content as base64 to handle binary files correctly
        read_action = BashAction(command=f"base64 {src_path}", check="silent")
        result = await runtime.run_in_session(read_action)

        if result.exit_code == 0:
            # Decode base64 content and write to local file in binary mode
            try:
                binary_content = base64.b64decode(result.output.strip())
                with open(dst_path, "wb") as f:
                    f.write(binary_content)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to decode base64 content from {src_path}: {str(e)}"
                )
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
        base64_content = base64.b64encode(binary_content).decode("ascii")

        # Create directory if needed
        container_dir = os.path.dirname(container_path)
        mkdir_action = BashAction(command=f"mkdir -p {container_dir}", check="silent")
        await runtime.run_in_session(mkdir_action)

        # delete the file if it exists
        delete_action = BashAction(command=f"rm -f {container_path}", check="silent")
        await runtime.run_in_session(delete_action)

        # Write content to container file using base64 decoding
        # This approach handles binary files correctly
        write_action = BashAction(
            command=f"echo '{base64_content}' | base64 -d > {container_path}",
            check="silent",
        )
        result = await runtime.run_in_session(write_action)

        if result.exit_code != 0:
            raise RuntimeError(
                f"Failed to write file {container_path}: {result.output}"
            )

    def extract_file_from_container(self, filepath: str) -> str:
        """Extract the content of the specified file from the runtime environment."""
        return _sync_run(self._extract_file_from_container_async(filepath))

    async def _extract_file_from_container_async(self, filepath: str) -> str:
        """Async implementation of extract_file_from_container."""
        runtime = self.deployment._runtime  # type: ignore[attr-defined]

        # Always read as base64 to preserve raw bytes, then return latin-1 string for consistency
        action = BashAction(
            command=f"base64 {filepath}",
            check="silent",
        )
        result = await runtime.run_in_session(action)

        if result.exit_code != 0:
            raise FileNotFoundError(f"Could not read file {filepath}: {result.output}")

        output = result.output.strip()
        try:
            binary_content = base64.b64decode(output)
            return binary_content.decode("latin-1")
        except Exception as e:
            raise RuntimeError(
                f"Failed to decode base64 content from {filepath}: {str(e)}"
            )

    def run_command_in_container(self, command: str) -> Tuple[str, int]:
        """Run a command inside the runtime environment (sync wrapper)."""
        return _sync_run(self._run_command_in_container_async(command))

    async def _run_command_in_container_async(self, command: str) -> Tuple[str, int]:
        """Async implementation of run_command_in_container."""
        runtime = self.deployment._runtime  # type: ignore[attr-defined]

        action = BashAction(command=command, check="silent", timeout=60.0)
        result = await runtime.run_in_session(action)

        return result.output, result.exit_code or 0

    def get_work_dir(self) -> str:
        """Get the current working directory in the runtime environment (sync wrapper)."""
        return _sync_run(self._get_work_dir_async())

    async def _get_work_dir_async(self) -> str:
        """Async implementation of get_work_dir."""
        runtime = self.deployment._runtime  # type: ignore[attr-defined]

        action = BashAction(command="pwd", check="silent")
        result = await runtime.run_in_session(action)

        if result.exit_code == 0:
            return result.output.strip()
        else:
            return "/tmp"  # fallback
