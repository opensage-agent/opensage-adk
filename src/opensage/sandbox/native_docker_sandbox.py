import asyncio
import concurrent.futures
import io
import ipaddress
import logging
import os
import random
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional, Union

import docker
from docker.errors import APIError, NotFound

from opensage.config import ContainerConfig

logger = logging.getLogger(__name__)
from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState
from opensage.sandbox.utils import can_pull_image, image_exists_locally
from opensage.utils.bash_tools_staging import build_bash_tools_staging_dir
from opensage.utils.parser import get_function_info
from opensage.utils.project_info import PROJECT_PATH


@dataclass
class DockerBuildResult:
    """Result of a Docker image build operation."""

    success: bool
    image_name: str
    build_output: str
    error_message: Optional[str] = None


def build_image_from_dockerfile(
    config: ContainerConfig,
) -> DockerBuildResult:
    """Build Docker image from a direct Dockerfile.

    Args:
        dockerfile_path: Path to the Dockerfile
        image_name: Name and tag for the built image (e.g., 'myapp:latest')
        build_context: Directory to use as build context. If None, uses dockerfile directory
        build_args: Build-time variables for Docker build (--build-arg)
    Returns:
        DockerBuildResult: DockerBuildResult with build status and details
    """
    if not config.project_relative_dockerfile_path or not config.image:
        return None

    # Use absolute path if provided, otherwise use project-relative path
    if config.absolute_dockerfile_path:
        dockerfile_path = Path(config.absolute_dockerfile_path)
    else:
        dockerfile_path = Path(PROJECT_PATH) / Path(
            config.project_relative_dockerfile_path
        )

    build_context = dockerfile_path.parent
    build_args = config.build_args
    image_name = config.image

    if not dockerfile_path.exists():
        return DockerBuildResult(
            success=False,
            image_name=image_name,
            build_output="",
            error_message=f"Dockerfile not found: {dockerfile_path}",
        )

    # Determine build context directory
    if build_context is None:
        build_context = dockerfile_path.parent
    else:
        build_context = Path(build_context)

    build_context = build_context.resolve()

    try:
        # Prepare docker build command
        # --load ensures the image is loaded into the local daemon (required for buildx)
        cmd = ["docker", "build", "--load", "-t", image_name]

        # Add build args if provided
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])

        # Add dockerfile and context
        cmd.extend(["-f", str(dockerfile_path), str(build_context)])

        # Change to build context directory and run docker build
        original_cwd = os.getcwd()
        try:
            os.chdir(build_context)
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=build_context
            )

            if result.returncode == 0:
                return DockerBuildResult(
                    success=True, image_name=image_name, build_output=result.stdout
                )
            else:
                return DockerBuildResult(
                    success=False,
                    image_name=image_name,
                    build_output=result.stdout,
                    error_message=result.stderr,
                )

        finally:
            # Change back to original directory
            os.chdir(original_cwd)

    except Exception as e:
        return DockerBuildResult(
            success=False,
            image_name=image_name,
            build_output="",
            error_message=f"Build failed: {str(e)}",
        )


def ensure_docker_image(config: ContainerConfig) -> tuple[bool, Optional[str]]:
    """Ensure Docker image is available, using dockerfile fallback if needed.

    Args:
        config (ContainerConfig): ContainerConfig with image name and optional dockerfile config
    Returns:
        tuple[bool, Optional[str]]: Tuple of (success, error_message). If success is False, error_message explains why.
    """
    if not config.image:
        return False, "No image specified in ContainerConfig"

    # Check if image exists locally
    if image_exists_locally(config.image):
        return True, None

    # Try to pull image
    logger.info(f"Image {config.image} not found locally, attempting to pull...")
    if can_pull_image(config.image):
        logger.info(f"Successfully pulled {config.image}")
        return True, None

    # If pull failed, try building from dockerfile
    logger.warning(f"Failed to pull {config.image}")

    if config.absolute_dockerfile_path or config.project_relative_dockerfile_path:
        logger.info(
            f"Attempting to build {config.image} from dockerfile {config.project_relative_dockerfile_path}..."
        )

        build_result = build_image_from_dockerfile(config)

        if build_result is None:
            return False, "Dockerfile configuration incomplete"

        if build_result.success:
            logger.info(f"Successfully built {config.image} from dockerfile")
            return True, None
        else:
            return False, f"Dockerfile build failed: {build_result.error_message}"

    # No dockerfile fallback available
    return (
        False,
        f"Image {config.image} not available and no dockerfile fallback configured",
    )


class NativeDockerSandbox(BaseSandbox):
    """Native Docker sandbox implementation using direct Docker API."""

    backend_type = "native"
    _HELPER_IMAGE_CANDIDATES = (
        "alpine:latest",
        "busybox:latest",
    )
    _cached_helper_image: str | None = None

    def __init__(
        self,
        container_config: ContainerConfig,
        session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        """
                Initialize NativeDockerSandbox.

                Args:
                    container_config (ContainerConfig): ContainerConfig options controlling container launch (must include image or container_id)

        Raises:
          TypeError: Raised when this operation fails.
          ValueError: Raised when this operation fails.
          RuntimeError: Raised when this operation fails."""
        if container_config is None or not isinstance(
            container_config, ContainerConfig
        ):
            raise TypeError("container_config must be a ContainerConfig instance")

        # Either image or container_id must be provided
        if not container_config.image and not container_config.container_id:
            raise ValueError("ContainerConfig must have either image or container_id")

        super().__init__(container_config, session_id, self.backend_type, sandbox_type)

        # Initialize Docker client with configuration
        self.client = docker.from_env(timeout=self.container_config_obj.timeout)

        # Connect to existing container or create new one
        if container_config.container_id:
            try:
                self.container_id = self._connect_to_existing_container(
                    container_config.container_id
                )
            except (ValueError, NotFound, APIError) as e:
                logger.warning(
                    f"Failed to connect to existing container {container_config.container_id}: {e}"
                )
                logger.info("Falling back to creating new container")
                # Clear container_id and fallback to creating new container
                container_config.container_id = None
                # Ensure we have an image for fallback
                if not container_config.image:
                    raise ValueError(
                        "Fallback to create new container failed: no image specified in ContainerConfig"
                    )
                # Ensure Docker image is available (with dockerfile build if needed)
                success, error_message = ensure_docker_image(container_config)
                if not success:
                    raise RuntimeError(
                        f"Failed to obtain Docker image: {error_message}"
                    )
                # Create and start container
                self.container_id = self._get_container()
        else:
            # Ensure Docker image is available (with dockerfile build if needed)
            success, error_message = ensure_docker_image(container_config)
            if not success:
                raise RuntimeError(f"Failed to obtain Docker image: {error_message}")
            # Create and start container
            self.container_id = self._get_container()

        # Detect available shell in container
        self._detected_shell = None  # Will be set on first use

    @classmethod
    def _get_helper_image(cls) -> str:
        """Return an available helper image, pulling if necessary.

                Several sandbox operations (volume init, chmod) require a tiny helper
                container. The Docker SDK does not auto-pull images, so we do a best
                effort pull when the image is missing locally.

        Raises:
          RuntimeError: Raised when this operation fails."""
        if cls._cached_helper_image is not None:
            return cls._cached_helper_image

        for candidate in cls._HELPER_IMAGE_CANDIDATES:
            if image_exists_locally(candidate) or can_pull_image(candidate):
                cls._cached_helper_image = candidate
                return candidate

        raise RuntimeError(
            "No suitable helper image available for Docker operations. "
            f"Tried: {list(cls._HELPER_IMAGE_CANDIDATES)}. "
            "Ensure Docker can pull at least one of them."
        )

    def _connect_to_existing_container(self, container_id: str) -> str:
        """Connect to an existing container if it's running.

        Args:
            container_id (str): The ID or name of the existing container
        Returns:
            str: The container ID

        Raises:
            ValueError: If container doesn't exist or is not running
        """
        try:
            container = self.client.containers.get(container_id)

            # Check if container is running
            container.reload()  # Refresh container state
            if container.status != "running":
                raise ValueError(
                    f"Container {container_id} exists but is not running (status: {container.status})"
                )

            # Update image_name from the existing container if not already set
            if not self.container_config_obj.image:
                self.container_config_obj.image = (
                    container.image.tags[0]
                    if container.image.tags
                    else container.image.id
                )

            logger.info(
                f"Connected to existing container {container_id} (image: {self.container_config_obj.image})"
            )
            return container.id

        except NotFound:
            raise ValueError(f"Container {container_id} not found")
        except APIError as e:
            raise ValueError(
                f"Failed to connect to container {container_id}: {e.explanation}"
            )

    def _get_container(self) -> str:
        """Create and start a new container from the specified image."""
        run_kwargs: dict[str, Any] = dict(
            stdin_open=True,
            tty=True,
            detach=True,
        )

        if self.container_config_obj.container_name is None:
            self.container_config_obj.container_name = (
                f"opensage_{self.sandbox_type}_{self.opensage_session_id}"
            )

        # Set command from config
        # If command is None, use sh to keep container alive (sh is more universal)
        # Shell selection for command execution is handled by _detect_shell() method
        # If command is empty string, don't set command to use Dockerfile's default CMD
        # If command is set to a specific value, use that
        if hasattr(self.container_config_obj, "command"):
            if self.container_config_obj.command is None:
                # Use sh to keep container running (more compatible across images)
                # Actual shell for executing commands is auto-detected later
                run_kwargs["command"] = ["sh", "-c", "while true; do sleep 1000; done"]
            elif self.container_config_obj.command == "":
                # Empty string means use Dockerfile's default CMD - don't set command
                pass
            else:
                run_kwargs["command"] = self.container_config_obj.command
        else:
            # Fallback
            run_kwargs["command"] = ["sh", "-c", "while true; do sleep 1000; done"]

        # Apply config to kwargs
        if self.container_config_obj.container_name:
            run_kwargs["name"] = self.container_config_obj.container_name
        if self.container_config_obj.environment:
            run_kwargs["environment"] = self.container_config_obj.environment
        if self.container_config_obj.working_dir:
            run_kwargs["working_dir"] = self.container_config_obj.working_dir
        if self.container_config_obj.user:
            run_kwargs["user"] = self.container_config_obj.user
        if self.container_config_obj.network:
            run_kwargs["network"] = self.container_config_obj.network
        if self.container_config_obj.privileged:
            run_kwargs["privileged"] = True
        if self.container_config_obj.security_opt:
            run_kwargs["security_opt"] = self.container_config_obj.security_opt
        if self.container_config_obj.cap_add:
            run_kwargs["cap_add"] = self.container_config_obj.cap_add
        if self.container_config_obj.gpus is not None:
            run_kwargs["device_requests"] = (
                [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]
                if self.container_config_obj.gpus == "all"
                else None
            )
        if self.container_config_obj.shm_size is not None:
            run_kwargs["shm_size"] = self.container_config_obj.shm_size
        if self.container_config_obj.mem_limit is not None:
            run_kwargs["mem_limit"] = self.container_config_obj.mem_limit
        if self.container_config_obj.cpus is not None:
            # docker SDK uses nano_cpus or cpuset; keep simple mapping to cpus via host_config is complex; skip if not trivial
            run_kwargs["cpuset_cpus"] = str(self.container_config_obj.cpus)

        # Volumes: list of binds "host:cont[:mode]"
        if self.container_config_obj.volumes:
            binds: dict[str, dict[str, str]] = {}
            for spec in self.container_config_obj.volumes:
                if isinstance(spec, str) and ":" in spec:
                    parts = spec.split(":")
                    host = parts[0]
                    target = parts[1] if len(parts) > 1 else "/"
                    mode = parts[2] if len(parts) > 2 else "rw"
                    binds[host] = {"bind": target, "mode": mode}
            if binds:
                run_kwargs["volumes"] = binds

        # Ports: dict[str, int | None | {"host": str, "port": int}]
        if self.container_config_obj.ports:
            port_bindings: dict[str, Any] = {}
            for container_port, host_binding in self.container_config_obj.ports.items():
                # Normalize container port to include protocol if not specified
                if "/" not in container_port:
                    container_port = f"{container_port}/tcp"

                # Handle different host binding types
                if isinstance(host_binding, int):
                    # Simple host port number
                    port_bindings[container_port] = host_binding
                elif host_binding is None:
                    # Random host port
                    port_bindings[container_port] = None
                elif isinstance(host_binding, dict):
                    # {"host": str, "port": int}
                    if "host" not in host_binding or "port" not in host_binding:
                        raise ValueError(
                            f"Invalid ports binding for {container_port}: "
                            "dict must contain 'host' and 'port'"
                        )
                    port_bindings[container_port] = {
                        "HostIp": str(host_binding["host"]),
                        "HostPort": str(int(host_binding["port"])),
                    }
                else:
                    raise ValueError(
                        f"Invalid ports binding type for {container_port}: "
                        f"{type(host_binding)}. "
                        "Expected int, None, or {'host', 'port'} dict."
                    )
            if port_bindings:
                run_kwargs["ports"] = port_bindings

        container = self.client.containers.run(
            self.container_config_obj.image, **run_kwargs
        )
        logger.info(
            f"Container {container.id} started from image {self.container_config_obj.image}"
        )
        return container.id

    def copy_directory_from_container(self, src_path: str, dst_path: str):
        """Copy a directory from the container to local filesystem.

        Raises:
          ValueError: Raised when this operation fails."""
        container = self.client.containers.get(self.container_id)
        exec_result = container.exec_run(["ls", "-la", src_path])
        if exec_result.exit_code != 0:
            raise ValueError(f"Path {src_path} does not exist in the container.")

        if os.path.exists(dst_path):
            shutil.rmtree(dst_path)
        os.makedirs(dst_path, exist_ok=True)

        stream, stats = container.get_archive(src_path)
        with tempfile.NamedTemporaryFile(delete=False) as temp_tar:
            for chunk in stream:
                temp_tar.write(chunk)
            temp_tar_path = temp_tar.name

        with tarfile.open(temp_tar_path) as tar:
            tar.extractall(path=dst_path, numeric_owner=True)

        os.remove(temp_tar_path)

    def copy_file_from_container(self, src_path: str, dst_path: str):
        """Copy a file from the container to local filesystem.

        Raises:
          FileNotFoundError: Raised when this operation fails.
          RuntimeError: Raised when this operation fails."""
        container = self.client.containers.get(self.container_id)

        # Check if the file exists inside the container
        exec_result = container.exec_run(["test", "-f", src_path])
        if exec_result.exit_code != 0:
            raise FileNotFoundError(f"File {src_path} does not exist in the container.")

        # Retrieve the file as a tar stream
        stream, _ = container.get_archive(src_path)
        with tempfile.NamedTemporaryFile(delete=False) as temp_tar:
            for chunk in stream:
                temp_tar.write(chunk)
            temp_tar_path = temp_tar.name

        # Extract the file content and write it directly to dst_path
        with tarfile.open(temp_tar_path) as tar:
            members = tar.getmembers()
            file_member = members[0]
            fileobj = tar.extractfile(file_member)
            if fileobj is None:
                raise RuntimeError("Failed to extract file from the tar archive.")

            with open(dst_path, "wb") as out_file:
                out_file.write(fileobj.read())

        os.remove(temp_tar_path)

    def copy_file_to_container(self, local_path: str, container_path: str):
        """Copy a single file to the container."""
        container = self.client.containers.get(self.container_id)

        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            tar.add(local_path, arcname=os.path.basename(container_path))
        data.seek(0)

        container_dir = os.path.dirname(container_path)
        container.exec_run(["mkdir", "-p", container_dir])
        container.exec_run(["rm", "-f", container_path])
        container.put_archive(container_dir, data.getvalue())

    def copy_directory_to_container(self, src_path: str, dst_path: str):
        """Copy a directory from the host to the container.

        Raises:
          RuntimeError: Raised when this operation fails."""
        container = self.client.containers.get(self.container_id)

        mkdir_cmd = ["mkdir", "-p", dst_path]
        exit_code, output = container.exec_run(mkdir_cmd)
        if exit_code != 0:
            raise RuntimeError(
                f"Failed to create directory {dst_path} in container: {output.decode()}"
            )

        mem_tar = io.BytesIO()
        with tarfile.open(fileobj=mem_tar, mode="w") as tar:
            tar.add(src_path, arcname="")
        mem_tar.seek(0)

        container.put_archive(dst_path, mem_tar.getvalue())
        container.exec_run(["chown", "-R", "$(id -nu):$(id -ng)", dst_path])

    def delete_container(self, max_wait: int = 10):
        """Delete the container."""
        try:
            container = self.client.containers.get(self.container_id)
            container.remove(force=True)
        except NotFound:
            logger.info(f"container {self.container_id} already gone")
            return
        except APIError as e:
            logger.warning(f"docker API error on {self.container_id}: {e.explanation}")
            return
        for _ in range(max_wait):
            try:
                self.client.containers.get(self.container_id)
                time.sleep(1)
            except NotFound:
                logger.info(f"Container {self.container_id} removed")
                return
        logger.warning(f"container {self.container_id} still listed after {max_wait}s")

    def extract_file_from_container(self, filepath: str) -> str:
        """Extract the content of the specified file from the container."""
        container = self.client.containers.get(self.container_id)
        stream, _ = container.get_archive(filepath)
        file_data = b""
        for chunk in stream:
            file_data += chunk
        tar_stream = io.BytesIO(file_data)
        with tarfile.open(fileobj=tar_stream) as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            content = f.read().decode("latin-1")
        return content

    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        """Extract the content of the specified file from the container as bytes."""
        container = self.client.containers.get(self.container_id)
        stream, _ = container.get_archive(filepath)
        file_data = b""
        for chunk in stream:
            file_data += chunk
        tar_stream = io.BytesIO(file_data)
        with tarfile.open(fileobj=tar_stream) as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            content = f.read()
        return content

    def create_tar_bytes(self, file_content: str, arcname: str) -> bytes:
        """Pack the given file content into a tar archive."""
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            file_bytes = file_content.encode()
            tarinfo = tarfile.TarInfo(name=arcname)
            tarinfo.size = len(file_bytes)
            tar.addfile(tarinfo, io.BytesIO(file_bytes))
        tar_stream.seek(0)
        return tar_stream.read()

    def patch_search_replace(self, file: str, search: str, replace: str):
        """Replace all occurrences of 'search' with 'replace' in the specified file."""
        container = self.client.containers.get(self.container_id)

        # Extract the file content from the container
        file_content = self.extract_file_from_container(file)

        # Replace the search string with the replace string
        modified_content = file_content.replace(search, replace)

        # Create a tar archive of the modified content
        archive_data = self.create_tar_bytes(
            modified_content, arcname=file.split("/")[-1]
        )

        # Copy the modified content back to the container
        destination_dir = "/".join(file.split("/")[:-1])
        if not destination_dir:
            destination_dir = "/"
        container.put_archive(destination_dir, archive_data)

    def patch_file_func(self, files_func_to_content: dict[str, str], lang: str = "c"):
        """Replace a function in a file inside the container with new content."""
        container = self.client.containers.get(self.container_id)

        for key, new_function_content in files_func_to_content.items():
            parts = key.split__xx__
            if len(parts) != 2:
                logger.warning(
                    f"Key {key} is not in the correct format. Expected format: 'filepath__xx__functionname'"
                )
                continue
            filepath, function_name = parts

            # Extract the file content from the container.
            file_content = self.extract_file_from_container(filepath)

            # Use Tree-sitter to obtain function information from the file.
            functions = get_function_info(file_content, lang)
            if function_name not in functions:
                logger.warning(
                    f"Initial try, Function {function_name} not found in file {filepath}"
                )
                logger.info(
                    "Trying to do partial matching, the result may be inaccurate"
                )
                func_name = function_name.split("::")[-1]
                if func_name in functions:
                    function_name = func_name
                else:
                    logger.info("Trying to do partial matching with looser rules")
                    potential_funcs = [
                        func
                        for func in functions
                        if func_name in func or func in func_name
                    ]
                    # get the distance between the function name and the potential function name
                    if potential_funcs:
                        potential_funcs.sort(key=lambda f: abs(len(f) - len(func_name)))
                        function_name = potential_funcs[0]
                    else:
                        logger.warning(
                            f"Function {function_name} finally not found in file {filepath}"
                        )
                        continue

            start_line, end_line = functions[function_name][0]
            start_index = start_line - 1
            end_index = end_line

            # Replace
            file_lines = file_content.splitlines()
            new_function_lines = new_function_content.splitlines()
            modified_lines = (
                file_lines[:start_index] + new_function_lines + file_lines[end_index:]
            )
            modified_file_content = "\n".join(modified_lines)

            # copy back
            archive_data = self.create_tar_bytes(
                modified_file_content, arcname=filepath.split("/")[-1]
            )
            destination_dir = "/".join(filepath.split("/")[:-1])
            if not destination_dir:
                destination_dir = "/"
            container.put_archive(destination_dir, archive_data)
            logger.info(
                f"Updated function {function_name} in file {filepath} in container {self.container_id}"
            )

    def get_function_content(
        self, key: str, lang: str = "c", line_in_func: int = -1
    ) -> tuple[str, int, int]:
        """Retrieve the content of a specific function from a file inside the container."""
        container = self.client.containers.get(self.container_id)

        parts = key.split__xx__
        if len(parts) != 2:
            logger.warning(
                f"Key {key} is not in the correct format. Expected format: 'filepath__xx__functionname'"
            )
            return "", -1, -1
        filepath, function_name = parts

        # Extract the file content from the container
        file_content = self.extract_file_from_container(filepath)
        # Use Tree-sitter to obtain function information from the file
        functions = get_function_info(file_content, lang)
        if function_name not in functions:
            logger.warning(
                f"Initial try, Function {function_name} not found in file {filepath}"
            )
            logger.info("Trying to do partial matching, the result may be inaccurate")
            func_name = function_name.split("::")[-1]
            if func_name in functions:
                function_name = func_name
            else:
                return "", -1, -1
        # line_in_func helps to decide which function to extract when there are multiple functions with the same name
        if line_in_func != -1:
            for scope in functions[function_name]:
                start_line, end_line = scope
                if start_line <= line_in_func <= end_line:
                    break
        else:
            start_line, end_line = functions[function_name][-1]

        # Split the file content into lines and extract the function content
        file_lines = file_content.splitlines()
        function_lines = file_lines[
            start_line - 1 : end_line
        ]  # convert 1-indexed to 0-indexed
        function_content = "\n".join(function_lines)

        return function_content, start_line, end_line

    def get_file_content(self, filepath: str) -> str:
        """Retrieve the content of a file inside the container."""
        return self.extract_file_from_container(filepath)

    def _detect_shell(self) -> str:
        """Detect which shell is available in the container.

        Tries bash first (more features), falls back to sh (more universal).
        Result is cached in self._detected_shell.

        Returns:
            str: Path to the detected shell (/bin/bash or /bin/sh)
        """
        if self._detected_shell is not None:
            return self._detected_shell

        container = self.client.containers.get(self.container_id)

        # Try bash first
        try:
            result = container.exec_run(
                ["/bin/bash", "-c", "echo test"], stdout=True, stderr=True
            )
            if result.exit_code == 0:
                self._detected_shell = "/bin/bash"
                logger.debug(f"Detected bash shell in container {self.container_id}")
                return self._detected_shell
        except Exception:
            pass

        # Fall back to sh (should be available in all POSIX containers)
        try:
            result = container.exec_run(
                ["/bin/sh", "-c", "echo test"], stdout=True, stderr=True
            )
            if result.exit_code == 0:
                self._detected_shell = "/bin/sh"
                logger.debug(f"Detected sh shell in container {self.container_id}")
                return self._detected_shell
        except Exception:
            pass

        # Last resort: assume sh
        logger.warning(
            f"Could not detect shell in container {self.container_id}, defaulting to /bin/sh"
        )
        self._detected_shell = "/bin/sh"
        return self._detected_shell

    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        """Run a command inside the container."""
        container = self.client.containers.get(self.container_id)

        # Prepare command with timeout wrapper if specified
        if isinstance(command, list):
            # List command: wrap with timeout if needed
            if timeout:
                full_command = ["timeout", f"{timeout}s"] + command
            else:
                full_command = command
        else:
            # String command: wrap with shell
            shell = self._detect_shell()
            if timeout:
                # Use timeout command with nested shell to handle shell built-ins
                import shlex

                full_command = [
                    shell,
                    "-c",
                    f"timeout {timeout}s {shell} -c {shlex.quote(command)}",
                ]
            else:
                full_command = [shell, "-c", command]

        # Execute command (timeout is handled by container's timeout command)
        exec_result = container.exec_run(full_command, stdout=True, stderr=True)

        output = exec_result.output.decode("latin-1", errors="replace")
        exit_code = exec_result.exit_code

        # timeout command returns exit code 124 when it times out
        if exit_code == 124:
            output = f"Command timed out after {timeout} seconds\n{output}"

        return output, exit_code

    def get_work_dir(self) -> str:
        """Get the working directory of the container."""
        work_dir, exit_code = self.run_command_in_container("pwd")
        return work_dir.strip()

    @classmethod
    def _docker_cp_to_volume(
        cls, volume_name: str, source_dir: str, label: str = ""
    ) -> None:
        """Copy a local directory into a Docker volume via ``put_archive``.

                Uses the Docker SDK to stream a tar archive through the API, making it
                compatible with Docker-in-Docker (the local path doesn't need to exist
                on the host).

        Raises:
          RuntimeError: Raised when this operation fails."""
        import io
        import tarfile as _tarfile

        helper_name = f"vol_helper_{volume_name}"
        client = docker.from_env()
        container = None
        try:
            helper_image = cls._get_helper_image()
            container = client.containers.create(
                helper_image,
                name=helper_name,
                volumes={volume_name: {"bind": "/target", "mode": "rw"}},
            )
            tar_buf = io.BytesIO()
            with _tarfile.open(fileobj=tar_buf, mode="w") as tar:
                tar.add(source_dir, arcname=".")
            tar_buf.seek(0)
            container.put_archive("/target", tar_buf)
            logger.info(
                f"Successfully copied {label or source_dir} to volume {volume_name}"
            )
        except Exception as e:
            logger.error(
                f"Failed to copy {label or source_dir} to volume {volume_name}: {e}"
            )
            raise RuntimeError(f"Failed to copy data to volume: {e}")
        finally:
            if container:
                container.remove(force=True)

    @classmethod
    def _create_and_populate_volume(
        cls, volume_name: str, source_path: Path = None
    ) -> str:
        """Helper method to create a single volume and populate it with data.

                Args:
                    volume_name (str): Name of the Docker volume to create
                    source_path (Path): Local path containing data to copy into the volume (optional)

        Raises:
          RuntimeError: Raised when this operation fails.
          Exception: Raised when this operation fails.
                Returns:
                    str: The volume name that was created
        """
        import tarfile
        import tempfile

        try:
            # Create Docker volume
            subprocess.run(
                ["docker", "volume", "create", volume_name],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Created Docker volume: {volume_name}")

            # Check if source_path exists
            if not source_path or not source_path.exists():
                logger.warning(f"No data copied to volume {volume_name}")
                return volume_name

            # Check if source_path is a directory with only one .tar.gz file
            if source_path.is_dir():
                files = list(source_path.iterdir())
                if (
                    len(files) == 1
                    and files[0].is_file()
                    and files[0].name.endswith(".tar.gz")
                ):
                    # Extract the tar.gz file on host, then copy to volume
                    tar_file = files[0]
                    logger.info(
                        f"Detected single .tar.gz file: {tar_file.name}, extracting then copying to volume"
                    )

                    with tempfile.TemporaryDirectory() as temp_extract_dir:
                        try:
                            with tarfile.open(tar_file, "r:gz") as tar:
                                tar.extractall(temp_extract_dir)
                            logger.info(
                                f"Extracted {tar_file.name} to temporary directory"
                            )
                        except Exception as e:
                            logger.error(f"Failed to extract {tar_file.name}: {e}")
                            raise RuntimeError(f"Failed to extract tar.gz: {e}")

                        cls._docker_cp_to_volume(
                            volume_name, temp_extract_dir, label=tar_file.name
                        )

                    return volume_name

            # Check if source_path itself is a .tar.gz file
            if source_path.is_file() and source_path.name.endswith(".tar.gz"):
                logger.info(
                    f"Detected .tar.gz file: {source_path.name}, extracting then copying to volume"
                )

                with tempfile.TemporaryDirectory() as temp_extract_dir:
                    try:
                        with tarfile.open(source_path, "r:gz") as tar:
                            tar.extractall(temp_extract_dir)
                        logger.info(
                            f"Extracted {source_path.name} to temporary directory"
                        )
                    except Exception as e:
                        logger.error(f"Failed to extract {source_path.name}: {e}")
                        raise RuntimeError(f"Failed to extract tar.gz: {e}")

                    cls._docker_cp_to_volume(
                        volume_name, temp_extract_dir, label=source_path.name
                    )

                return volume_name

            # Otherwise, copy data to volume using docker cp (normal case)
            cls._docker_cp_to_volume(
                volume_name,
                str(source_path.resolve().absolute()),
                label=str(source_path),
            )

            return volume_name

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create Docker volume {volume_name}: {e.stderr}")
            raise RuntimeError(f"Docker volume creation failed: {e.stderr}")
        except Exception as e:
            logger.error(f"Unexpected error creating volume {volume_name}: {e}")
            raise

    @classmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        """Create and initialize three shared volumes.

                Creates three volumes:
                1. Read-only volume with sandbox scripts (mapped to /sandbox_scripts)
                2. Read-write volume with user data (mapped to /shared)
                3. Read-write volume with bash tools (mapped to /bash_tools)

                Args:
                    volume_name_prefix (str): Prefix for volume names (e.g., session_id)
                    init_data_path (Path): Path to initial data to copy into the rw volume (optional)
                    tools_top_roots (set[str] | None): Optional set of top-level bash_tools roots to stage.
                        If None, stage all bash tools (built-in + plugins).

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    tuple[str, str, str]: Tuple of (scripts_volume_id, data_volume_id, tools_volume_id)
        """
        from opensage.utils.project_info import SRC_PATH

        try:
            helper_image = cls._get_helper_image()

            # Create volume names
            scripts_volume_name = f"{volume_name_prefix}_sandbox_scripts"
            data_volume_name = f"{volume_name_prefix}_shared"

            # 1. Create and populate scripts volume
            scripts_path = SRC_PATH / "sandbox_scripts"
            scripts_volume_id = cls._create_and_populate_volume(
                scripts_volume_name, scripts_path
            )
            logger.info(
                f"Created sandbox scripts volume: {scripts_volume_id} from {scripts_path}"
            )

            # 2. Create and populate data volume
            data_volume_id = cls._create_and_populate_volume(
                data_volume_name, init_data_path
            )
            logger.info(
                f"Created shared data volume: {data_volume_id} from {init_data_path}"
            )

            # 3. Create and populate tools volume (built-in + plugin tools staged on host)
            tools_volume_name = f"{volume_name_prefix}_bash_tools"
            with build_bash_tools_staging_dir(roots_to_copy=tools_top_roots) as staging:
                tools_volume_id = cls._create_and_populate_volume(
                    tools_volume_name, staging
                )
                logger.info(
                    "Created bash tools volume: %s from staging dir %s (roots=%s)",
                    tools_volume_id,
                    staging,
                    "ALL" if tools_top_roots is None else sorted(tools_top_roots),
                )

            # 4. Set permissions to 777 on data volume to ensure write access
            chmod_result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{data_volume_id}:/target",
                    helper_image,
                    "sh",
                    "-c",
                    "chmod -R 777 /target",
                ],
                capture_output=True,
                text=True,
            )

            if chmod_result.returncode == 0:
                logger.info(
                    f"Set permissions 777 on shared data volume: {data_volume_id}"
                )
            else:
                logger.warning(
                    f"Failed to set permissions on volume {data_volume_id}: {chmod_result.stderr}"
                )

            # 5. Set permissions to 777 on tools volume to ensure all bash tools are
            # executable/writeable across sandboxes.
            chmod_result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{tools_volume_id}:/target",
                    helper_image,
                    "sh",
                    "-c",
                    "chmod -R 777 /target",
                ],
                capture_output=True,
                text=True,
            )

            if chmod_result.returncode == 0:
                logger.info(
                    f"Set permissions 777 on bash tools volume: {tools_volume_id}"
                )
            else:
                logger.warning(
                    f"Failed to set permissions on volume {tools_volume_id}: {chmod_result.stderr}"
                )

            return (scripts_volume_id, data_volume_id, tools_volume_id)

        except Exception as e:
            logger.error(f"Failed to create shared volumes: {e}")
            # Clean up any created volumes on failure
            try:
                subprocess.run(
                    ["docker", "volume", "rm", scripts_volume_name],
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    ["docker", "volume", "rm", data_volume_name],
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    ["docker", "volume", "rm", tools_volume_name],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass
            raise

    @classmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        """Delete shared volumes.

        Args:
            scripts_volume_id (str): ID of the scripts volume to delete
            data_volume_id (str): ID of the data volume to delete
            tools_volume_id (str): ID of the tools volume to delete"""
        for volume_id in [scripts_volume_id, data_volume_id, tools_volume_id]:
            if volume_id:
                try:
                    result = subprocess.run(
                        ["docker", "volume", "rm", volume_id],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode == 0:
                        logger.info(f"Deleted volume: {volume_id}")
                    else:
                        logger.warning(
                            f"Failed to delete volume {volume_id}: {result.stderr}"
                        )
                except Exception as e:
                    logger.warning(f"Error deleting volume {volume_id}: {e}")

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config
    ) -> tuple[str, "NativeDockerSandbox"]:
        """Create a single sandbox instance without initialization.

        This method only creates the Docker container and sandbox instance.
        Call initialize_all_sandboxes() afterwards to initialize.
        """

        logger.info(f"Launching {sandbox_type} sandbox for session {session_id}")

        # Lazy import to avoid circular dependency
        from opensage.sandbox.factory import (
            create_sandbox_class,
            get_initializer_class,
        )

        # Get appropriate mixin and create sandbox class
        initializer_class = get_initializer_class(sandbox_type)
        sandbox_class = create_sandbox_class(cls, initializer_class)

        # Create sandbox instance using the already-updated config
        # Start the docker container
        sandbox_instance = sandbox_class(
            container_config,
            session_id=session_id,
            backend_type=cls.backend_type,
            sandbox_type=sandbox_type,
        )

        # Store using_cached flag for later initialization
        sandbox_instance._using_cached = container_config.using_cached

        logger.info(
            f"Successfully created {sandbox_type} sandbox (not yet initialized)"
        )
        return sandbox_type, sandbox_instance

    @staticmethod
    async def _run_initializer_with_tracking(
        sandbox_type: str,
        sandbox_instance: "NativeDockerSandbox",
        init_coro: Awaitable[None],
    ) -> None:
        """Await initialization, set state, and emit logs.

        Raises:
          Exception: Raised when this operation fails."""
        final_state: Optional[SandboxState] = None
        sandboxes = None
        opensage_session_id = getattr(sandbox_instance, "opensage_session_id", None)
        if opensage_session_id:
            try:
                from opensage.session.opensage_session import get_opensage_session

                sandboxes = get_opensage_session(opensage_session_id).sandboxes
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to retrieve sandbox manager for session %s: %s",
                    opensage_session_id,
                    exc,
                )
        try:
            await init_coro
        except Exception as exc:  # pylint: disable=broad-except
            final_state = SandboxState.ERROR
            setattr(sandbox_instance, "state", final_state)
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception as state_exc:  # pylint: disable=broad-except
                    logger.warning(
                        "Failed to set sandbox '%s' state to %s: %s",
                        sandbox_type,
                        final_state.value,
                        state_exc,
                    )
            logger.error(
                "sandbox '%s' (session %s) state=%s - Initialization failed: %s",
                sandbox_type,
                opensage_session_id,
                final_state.value,
                exc,
                exc_info=exc,
            )
            raise
        else:
            final_state = SandboxState.READY
            setattr(sandbox_instance, "state", final_state)
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception as state_exc:  # pylint: disable=broad-except
                    logger.warning(
                        "Failed to set sandbox '%s' state to %s: %s",
                        sandbox_type,
                        final_state.value,
                        state_exc,
                    )
        finally:
            state_value = final_state.value if final_state else "unknown"
            logger.info(
                "sandbox '%s' (session %s) state=%s - Initialization finished",
                sandbox_type,
                opensage_session_id,
                state_value,
            )

    @classmethod
    async def initialize_all_sandboxes(
        cls,
        sandbox_instances: dict[str, BaseSandbox],
        *,
        continue_on_error: bool = False,
    ) -> dict:
        """Initialize all sandbox instances concurrently.

        This should be called after launch_all_sandboxes() and after
        registering any hooks.

        Args:
            sandbox_instances (dict[str, BaseSandbox]): Dict of sandbox_type -> NativeDockerSandbox instance
            continue_on_error (bool): If True, continue initializing other sandboxes when
                one fails, and return a map of sandbox_type -> Exception | None
                instead of raising. If False, propagate errors."""
        if not sandbox_instances:
            logger.warning("No sandbox instances to initialize")
            return {}

        init_entries = []
        for sandbox_type, sandbox_instance in sandbox_instances.items():
            logger.info(f"Initializing {sandbox_type} sandbox...")

            async def _init_one(instance: "NativeDockerSandbox") -> None:
                if getattr(instance, "_using_cached", False):
                    await instance.ensure_ready()
                else:
                    await instance.async_initialize(sandbox_instances)

            # Determine per-sandbox timeout: read from container_config.extra,
            # fallback to 60 minutes (3600s) by default.
            timeout_seconds = 3600
            container_cfg = getattr(sandbox_instance, "container_config_obj", None)
            if container_cfg and getattr(container_cfg, "extra", None):
                try:
                    timeout_seconds = int(
                        container_cfg.extra.get("initializer_timeout_sec", 3600)
                    )
                except Exception:
                    # Keep default on parse issues
                    timeout_seconds = 3600

            # Wrap with asyncio.wait_for to enforce timeout
            init_entries.append(
                (
                    sandbox_type,
                    cls._run_initializer_with_tracking(
                        sandbox_type,
                        sandbox_instance,
                        asyncio.wait_for(
                            _init_one(sandbox_instance), timeout=timeout_seconds
                        ),
                    ),
                )
            )

        # Initialize all sandboxes concurrently
        tasks = [entry[1] for entry in init_entries]
        if continue_on_error:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            result_map = {}
            for (sandbox_type, _task), res in zip(init_entries, results):
                if isinstance(res, Exception):
                    logger.error(
                        f"Initialization failed for sandbox '{sandbox_type}': {res}"
                    )
                    result_map[sandbox_type] = res
                else:
                    result_map[sandbox_type] = None
            return result_map
        else:
            await asyncio.gather(*tasks)
            logger.info(f"Successfully initialized {len(sandbox_instances)} sandboxes")
            return {sandbox_type: None for sandbox_type, _ in init_entries}

    @classmethod
    def _find_available_loopback_ip(cls, config) -> str:
        """Find an available IP address in 127.0.0.0/24 range (127.0.0.2-127.0.0.254).

        Checks all ports that will be used:
        - Placeholder port: 7777
        - Neo4j bolt_port and neo4j_http_port
        - All MCP service sse_port values

        Args:
            config: OpenSageConfig object to extract port information
        Returns:
            str: An available IP address (e.g., '127.0.0.2')
        """
        import socket

        # Collect all ports to check
        ports_to_check = [7777]  # Placeholder port

        # Add Neo4j ports
        if config.neo4j:
            if hasattr(config.neo4j, "bolt_port") and config.neo4j.bolt_port:
                ports_to_check.append(config.neo4j.bolt_port)
            if (
                hasattr(config.neo4j, "neo4j_http_port")
                and config.neo4j.neo4j_http_port
            ):
                ports_to_check.append(config.neo4j.neo4j_http_port)

        # Add MCP service ports
        if config.mcp and config.mcp.services:
            for service_name, service_config in config.mcp.services.items():
                if hasattr(service_config, "sse_port") and service_config.sse_port:
                    ports_to_check.append(service_config.sse_port)

        logger.info(f"Checking ports for availability: {ports_to_check}")

        # Keep trying random IPs until one is available

        octets = list(range(2, 255))  # Skip 127.0.0.1
        attempt = 0

        while True:
            # Reshuffle and try again if we've exhausted all options
            if attempt % len(octets) == 0:
                random.shuffle(octets)
                if attempt > 0:
                    logger.info(
                        f"Retrying IP allocation, attempt {attempt // len(octets) + 1}"
                    )
                    time.sleep(0.5)  # Brief pause before retry

            last_octet = octets[attempt % len(octets)]
            test_ip = f"127.0.0.{last_octet}"
            all_ports_available = True

            # Check if all ports are available on this IP
            for port in ports_to_check:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind((test_ip, port))
                except OSError:
                    # This port is in use on this IP, try next IP
                    all_ports_available = False
                    break

            placeholder_container_id = None
            if all_ports_available:
                logger.info(
                    f"Found available loopback IP {test_ip} for all ports: {ports_to_check}"
                )

                # Create a placeholder container to hold the IP:7777
                # This ensures no other process takes it before we launch real sandboxes
                try:
                    client = docker.from_env(timeout=3600)
                    helper_image = cls._get_helper_image()
                    placeholder_container = client.containers.run(
                        helper_image,
                        command=["sh", "-c", "sleep infinity"],
                        detach=True,
                        name=f"opensage_placeholder_{str(uuid.uuid4())}",
                        ports={"7777/tcp": (test_ip, 7777)},
                        remove=True,
                    )
                    placeholder_container_id = placeholder_container.id
                    logger.info(
                        f"Created placeholder container to reserve {test_ip}:7777"
                    )
                    return test_ip, placeholder_container_id
                except Exception as e:
                    logger.warning(
                        f"Failed to create placeholder container: {e}, trying next IP"
                    )
                    attempt += 1
                    continue

            attempt += 1

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        """Launch all sandbox instances as separate Docker containers.

                Args:
                    session_id (str): Session identifier
                    sandbox_configs (dict): Dictionary of sandbox_type -> ContainerConfig
                    shared_volume_id (str): Optional shared volume to mount to all sandboxes (unused, configs already updated)
                    scripts_volume_id (str): Optional scripts volume to mount to all sandboxes (unused, configs already updated)
                    tools_volume_id (str): Optional tools volume to mount to all sandboxes (unused, configs already updated)

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    dict: Dictionary mapping sandbox_type to NativeDockerSandbox instance
        """

        async def launch_concurrent():
            """Internal async function to launch sandboxes concurrently."""
            # Create all sandboxes concurrently
            tasks = [
                cls.create_single_sandbox(session_id, sandbox_type, container_config)
                for sandbox_type, container_config in sandbox_configs.items()
            ]

            # Wait for all sandboxes to be created
            results = await asyncio.gather(*tasks)

            # Convert results to dictionary
            return dict(results)

        sandbox_instances = {}

        try:
            from opensage.session.opensage_session import get_opensage_session

            opensage_session = get_opensage_session(session_id)
            config = opensage_session.config

            # 1. Find available loopback IP that works for all required ports
            loopback_ip, placeholder_container_id = cls._find_available_loopback_ip(
                config
            )
            logger.info(
                f"Found available loopback IP {loopback_ip} for session {session_id}"
            )

            # 2. Update config's default_host (top-level only)
            config.default_host = loopback_ip
            logger.info(f"Updated config.default_host to {loopback_ip}")

            # 2.5. Update all sandbox port mappings to use the new loopback IP
            for sandbox_type, container_config in sandbox_configs.items():
                if container_config.ports:
                    updated_ports = {}
                    for container_port, host_binding in container_config.ports.items():
                        if host_binding is None:
                            # None means expose but don't bind
                            updated_ports[container_port] = host_binding
                        elif isinstance(host_binding, int):
                            # Just a port number, bind to loopback_ip
                            updated_ports[container_port] = {
                                "host": loopback_ip,
                                "port": host_binding,
                            }
                        elif isinstance(host_binding, dict):
                            if "host" not in host_binding or "port" not in host_binding:
                                raise ValueError(
                                    f"Invalid ports binding for {sandbox_type}:{container_port}: "
                                    "dict must contain 'host' and 'port'"
                                )
                            updated_ports[container_port] = {
                                "host": loopback_ip,
                                "port": int(host_binding["port"]),
                            }
                        else:
                            raise ValueError(
                                f"Invalid ports binding for {sandbox_type}:{container_port}: "
                                f"{type(host_binding)}. "
                                "Expected int, None, or {'host', 'port'} dict."
                            )

                    container_config.ports = updated_ports
                    logger.info(
                        f"Updated {sandbox_type} sandbox ports to use {loopback_ip}"
                    )

            # 3. Wrap placeholder container as a sandbox for unified management
            helper_image = cls._get_helper_image()
            placeholder_config = ContainerConfig(
                container_id=placeholder_container_id,
                image=helper_image,
            )
            placeholder_sandbox = cls(
                placeholder_config,
                session_id=session_id,
                backend_type=cls.backend_type,
                sandbox_type="placeholder",
            )
            # Placeholder is always "cached" (already exists), won't be initialized
            placeholder_sandbox._using_cached = True
            sandbox_instances["_placeholder"] = placeholder_sandbox
            logger.info("Registered placeholder container as a managed sandbox")

            # 4. Launch all sandboxes concurrently
            sandbox_instances.update(await launch_concurrent())

            return sandbox_instances

        except Exception as e:
            logger.error(f"Failed to launch sandboxes for session {session_id}: {e}")

            # Cleanup any successfully created sandboxes (including placeholder)
            for sandbox in sandbox_instances.values():
                try:
                    if hasattr(sandbox, "delete_container"):
                        sandbox.delete_container()
                except Exception as cleanup_e:
                    logger.warning(
                        f"Failed to cleanup sandbox during error recovery: {cleanup_e}"
                    )

            raise

    @classmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        """Cache Docker containers and shared volume content.

        This method will:
        1. Backup shared volume content to a tar.gz file
        2. Commit each running container to a new image

        Args:
            session_id: Session identifier
            sandbox_instances (dict): Dictionary mapping sandbox types to NativeDockerSandbox instances
            shared_volume_id (str): Docker volume name to backup
            cache_dir (str): Directory to store cache files
            task_name (str): Task name for naming cached resources
        Returns:
            dict: Dictionary with cache results
        """

        def normalize_image_name(name: str) -> str:
            """Normalize name to comply with Docker image naming rules."""
            # Convert to lowercase
            normalized = name.lower()
            # Replace invalid characters with underscores
            normalized = re.sub(r"[^a-z0-9._-]", "_", normalized)
            # Remove leading/trailing dots and dashes
            normalized = normalized.strip(".-")
            # Ensure it doesn't start with underscore
            if normalized.startswith("_"):
                normalized = "img" + normalized
            # Limit length to reasonable size (200 chars for repository)
            if len(normalized) > 200:
                normalized = normalized[:200].rstrip("_-.")
            return normalized

        cache_results = {
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": None,
            "cached_images": {},
            "errors": [],
        }

        try:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

            # 1. Backup shared volume if it exists
            if shared_volume_id:
                try:
                    volume_backup_path = os.path.join(
                        cache_dir, f"{task_name}_shared_volume.tar.gz"
                    )

                    backup_cmd = [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{shared_volume_id}:/data:ro",
                        "-v",
                        f"{os.path.abspath(cache_dir)}:/output",
                        "alpine",
                        "tar",
                        "-czf",
                        f"/output/{task_name}_shared_volume.tar.gz",
                        "-C",
                        "/data",
                        ".",
                    ]

                    logger.info(
                        f"Backing up shared volume {shared_volume_id} to {volume_backup_path}"
                    )
                    subprocess.run(
                        backup_cmd, capture_output=True, text=True, check=True
                    )

                    cache_results["shared_volume_backup"] = volume_backup_path
                    logger.info(
                        f"Successfully backed up shared volume to {volume_backup_path}"
                    )

                except subprocess.CalledProcessError as e:
                    error_msg = (
                        f"Failed to backup shared volume {shared_volume_id}: {e.stderr}"
                    )
                    logger.error(error_msg)
                    cache_results["errors"].append(error_msg)
                except Exception as e:
                    error_msg = f"Unexpected error backing up shared volume: {e}"
                    logger.error(error_msg)
                    cache_results["errors"].append(error_msg)

            # 2. Commit each sandbox container to an image
            client = docker.from_env(timeout=3600)

            for sandbox_type, sandbox_instance in sandbox_instances.items():
                try:
                    # Get container by container_id
                    if (
                        not hasattr(sandbox_instance, "container_id")
                        or not sandbox_instance.container_id
                    ):
                        logger.warning(
                            f"Sandbox {sandbox_type} has no active container to cache"
                        )
                        continue

                    container = client.containers.get(sandbox_instance.container_id)

                    # Normalize names for Docker compliance
                    normalized_task_name = normalize_image_name(task_name)
                    normalized_sandbox_type = normalize_image_name(sandbox_type)

                    repository_name = (
                        f"{normalized_task_name}_sandbox_{normalized_sandbox_type}"
                    )
                    cached_image_name = f"{repository_name}:cached"

                    logger.info(
                        f"Committing container {container.id} to image {cached_image_name}"
                    )

                    # Commit container to new image
                    committed_image = container.commit(
                        repository=repository_name,
                        tag="cached",
                        message=f"Cached state for task {task_name}",
                    )

                    cache_results["cached_images"][sandbox_type] = {
                        "image_name": cached_image_name,
                        "image_id": committed_image.id,
                        "container_id": container.id,
                    }

                    logger.info(
                        f"Successfully committed {sandbox_type} container to {cached_image_name}"
                    )

                except Exception as e:
                    error_msg = f"Failed to commit {sandbox_type} container: {e}"
                    logger.error(error_msg)
                    cache_results["errors"].append(error_msg)

            logger.info(f"Sandbox caching completed for task {task_name}")
            return cache_results

        except Exception as e:
            error_msg = f"Failed to cache sandboxes: {e}"
            logger.error(error_msg)
            cache_results["errors"].append(error_msg)
            return cache_results

    @classmethod
    def checkpoint(cls) -> str:
        """Checkpoint the sandbox."""
        raise NotImplementedError("Checkpoint is not implemented for LocalSandbox")

    @classmethod
    def restore(cls) -> str:
        """Restore the sandbox."""
        raise NotImplementedError("Restore is not implemented for LocalSandbox")
