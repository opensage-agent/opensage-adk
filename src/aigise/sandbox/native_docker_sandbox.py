import io
import logging
import os
import shutil
import tarfile
import tempfile
import time
from typing import Any

import docker
from docker.errors import APIError, NotFound

from aigise.sandbox.base_sandbox import BaseSandbox
from aigise.sandbox.docker_config import DockerConfig
from aigise.sandbox.template_fallback import TemplateFallbackMixin
from aigise.utils.parser import get_function_info

logger = logging.getLogger(__name__)


class NativeDockerSandbox(BaseSandbox, TemplateFallbackMixin):
    """Native Docker sandbox implementation using direct Docker API."""

    def __init__(
        self,
        docker_config: DockerConfig,
    ):
        """
        Initialize NativeDockerSandbox.

        Args:
            docker_config: DockerConfig options controlling container launch (must include image or container_id)
        """
        if docker_config is None or not isinstance(docker_config, DockerConfig):
            raise TypeError("docker_config must be a DockerConfig instance")

        # Either image or container_id must be provided
        if not docker_config.image and not docker_config.container_id:
            raise ValueError("DockerConfig must have either image or container_id")

        super().__init__(docker_config)

        # Initialize Docker client with configuration
        self.client = docker.from_env(timeout=self.docker_config_obj.timeout)

        # Connect to existing container or create new one
        if docker_config.container_id:
            try:
                self.container_id = self._connect_to_existing_container(
                    docker_config.container_id
                )
            except (ValueError, NotFound, APIError) as e:
                logger.warning(
                    f"Failed to connect to existing container {docker_config.container_id}: {e}"
                )
                logger.info("Falling back to creating new container")
                # Clear container_id and fallback to creating new container
                docker_config.container_id = None
                # Ensure we have an image for fallback
                if not docker_config.image:
                    raise ValueError(
                        "Fallback to create new container failed: no image specified in DockerConfig"
                    )
                # Ensure Docker image is available (with template fallback if needed)
                self._ensure_image_with_template_fallback(docker_config)
                # Create and start container
                self.container_id = self._get_container()
        else:
            # Ensure Docker image is available (with template fallback if needed)
            self._ensure_image_with_template_fallback(docker_config)
            # Create and start container
            self.container_id = self._get_container()

    def _connect_to_existing_container(self, container_id: str) -> str:
        """Connect to an existing container if it's running.

        Args:
            container_id: The ID or name of the existing container

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
            if not self.docker_config_obj.image:
                self.image_name = (
                    container.image.tags[0]
                    if container.image.tags
                    else container.image.id
                )

            logger.info(
                f"Connected to existing container {container_id} (image: {self.image_name})"
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

        # Set command from config
        # If command is None, default to "bash" for backward compatibility
        # If command is empty string, don't set command to use Dockerfile's default CMD
        # If command is set to a specific value, use that
        if hasattr(self.docker_config_obj, "command"):
            if self.docker_config_obj.command is None:
                run_kwargs["command"] = "bash"
            elif self.docker_config_obj.command == "":
                # Empty string means use Dockerfile's default CMD - don't set command
                pass
            else:
                run_kwargs["command"] = self.docker_config_obj.command
        else:
            # Fallback for old DockerConfig without command field
            run_kwargs["command"] = "bash"

        # Apply config to kwargs
        if self.docker_config_obj.environment:
            run_kwargs["environment"] = self.docker_config_obj.environment
        if self.docker_config_obj.working_dir:
            run_kwargs["working_dir"] = self.docker_config_obj.working_dir
        if self.docker_config_obj.user:
            run_kwargs["user"] = self.docker_config_obj.user
        if self.docker_config_obj.network:
            run_kwargs["network"] = self.docker_config_obj.network
        if self.docker_config_obj.privileged:
            run_kwargs["privileged"] = True
        if self.docker_config_obj.security_opt:
            run_kwargs["security_opt"] = self.docker_config_obj.security_opt
        if self.docker_config_obj.cap_add:
            run_kwargs["cap_add"] = self.docker_config_obj.cap_add
        if self.docker_config_obj.gpus is not None:
            run_kwargs["device_requests"] = (
                [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]
                if self.docker_config_obj.gpus == "all"
                else None
            )
        if self.docker_config_obj.shm_size is not None:
            run_kwargs["shm_size"] = self.docker_config_obj.shm_size
        if self.docker_config_obj.mem_limit is not None:
            run_kwargs["mem_limit"] = self.docker_config_obj.mem_limit
        if self.docker_config_obj.cpus is not None:
            # docker SDK uses nano_cpus or cpuset; keep simple mapping to cpus via host_config is complex; skip if not trivial
            run_kwargs["cpuset_cpus"] = str(self.docker_config_obj.cpus)

        # Volumes: list of binds "host:cont[:mode]"
        if self.docker_config_obj.volumes:
            binds: dict[str, dict[str, str]] = {}
            for spec in self.docker_config_obj.volumes:
                if isinstance(spec, str) and ":" in spec:
                    parts = spec.split(":")
                    host = parts[0]
                    target = parts[1] if len(parts) > 1 else "/"
                    mode = parts[2] if len(parts) > 2 else "rw"
                    binds[host] = {"bind": target, "mode": mode}
            if binds:
                run_kwargs["volumes"] = binds

        # Ports: dict[str, Union[int, None, tuple[str, int], List[int]]]
        if self.docker_config_obj.ports:
            port_bindings: dict[str, Any] = {}
            for container_port, host_binding in self.docker_config_obj.ports.items():
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
                elif isinstance(host_binding, tuple):
                    # (host_ip, host_port) tuple
                    port_bindings[container_port] = {
                        "HostIp": host_binding[0],
                        "HostPort": str(host_binding[1]),
                    }
                elif isinstance(host_binding, list):
                    # List of host ports
                    port_bindings[container_port] = [str(port) for port in host_binding]
            if port_bindings:
                run_kwargs["ports"] = port_bindings

        container = self.client.containers.run(self.image_name, **run_kwargs)
        logger.info(f"Container {container.id} started from image {self.image_name}")
        return container.id

    def copy_directory_from_container(self, src_path: str, dst_path: str):
        """Copy a directory from the container to local filesystem."""
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
        """Copy a file from the container to local filesystem."""
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
        """Copy a directory from the host to the container."""
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

            # TODO: FIXME: if there are multiple functions with the same name, we need to find the one that matches the line number
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

    def run_command_in_container(self, command: str | list[str]) -> tuple[str, int]:
        """Run a command inside the container."""
        container = self.client.containers.get(self.container_id)
        if isinstance(command, list):
            full_command = command
        else:
            full_command = ["/bin/bash", "-lc", command]
        exec_result = container.exec_run(full_command, stdout=True, stderr=True)
        # TODO: other encoding?
        output = exec_result.output.decode("latin-1", errors="replace")
        exit_code = exec_result.exit_code

        return output, exit_code

    def get_work_dir(self) -> str:
        """Get the working directory of the container."""
        work_dir, exit_code = self.run_command_in_container("pwd")
        return work_dir.strip()
