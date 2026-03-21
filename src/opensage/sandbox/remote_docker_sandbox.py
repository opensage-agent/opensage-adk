"""Remote Docker Sandbox implementation.

This module provides a sandbox backend that connects to remote Docker daemons
via SSH or TCP, enabling distributed execution across multiple machines.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

import docker
from docker.errors import ImageNotFound

from opensage.sandbox.native_docker_sandbox import (
    DockerBuildResult,
    NativeDockerSandbox,
)

logger = logging.getLogger(__name__)


class RemoteDockerSandbox(NativeDockerSandbox):
    """Remote Docker sandbox implementation using Docker API over SSH/TCP.

    This backend extends NativeDockerSandbox to support remote Docker daemons,
    enabling execution on remote machines while maintaining the same interface.

    Key differences from NativeDockerSandbox:
    - Docker client connects to remote daemon (requires docker_host config)
    - Volume population uses put_archive instead of bind mounts
    - Network configuration uses remote host IP instead of loopback
    - Image operations use Docker SDK instead of subprocess
    - All operations performed via Docker API (no local dependencies)

    Configuration:
        [sandbox]
        backend = "remotedocker"
        docker_host = "ssh://user@remote-host"  # or tcp://host:2376
        docker_remote_host = "192.168.1.100"    # optional, auto-parsed if not set

    Environment Variables (fallback):
        DOCKER_HOST: Remote Docker daemon URL
        DOCKER_REMOTE_HOST: Remote host IP for service connections
        DOCKER_TLS_CERTDIR: TLS certificate directory for TCP

    Usage:
        export DOCKER_HOST="ssh://user@gpu-server"
    """

    backend_type = "remotedocker"

    # Class variable to hold injected config
    _injected_config = None
    _CACHE_DIR_ENV = "OPENSAGE_REMOTE_DOCKER_CACHE_DIR"

    def __init__(
        self,
        container_config,
        session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        """Initialize remote Docker sandbox.

                Overrides parent to use remote Docker client instead of docker.from_env().

        Raises:
          TypeError: Raised when this operation fails.
          ValueError: Raised when this operation fails.
          RuntimeError: Raised when this operation fails."""
        from opensage.sandbox.base_sandbox import BaseSandbox

        if container_config is None or not isinstance(
            container_config, type(container_config)
        ):
            raise TypeError("container_config must be a ContainerConfig instance")

        if not container_config.image and not container_config.container_id:
            raise ValueError("ContainerConfig must have either image or container_id")

        # Initialize base class
        BaseSandbox.__init__(
            self,
            container_config,
            session_id,
            backend_type or self.backend_type,
            sandbox_type,
        )

        # Use remote Docker client (not docker.from_env)
        self.client = self._get_docker_client()

        # Connect to existing or create new container
        if container_config.container_id:
            try:
                self.container_id = self._connect_to_existing_container(
                    container_config.container_id
                )
            except (ValueError, Exception) as e:
                logger.warning(
                    f"Failed to connect to container {container_config.container_id}: {e}"
                )
                logger.info("Falling back to creating new container")
                container_config.container_id = None
                if not container_config.image:
                    raise ValueError("Fallback failed: no image specified")

                success, error = self.ensure_docker_image(container_config)
                if not success:
                    raise RuntimeError(f"Failed to obtain image: {error}")

                self.container_id = self._get_container()
        else:
            success, error = self.ensure_docker_image(container_config)
            if not success:
                raise RuntimeError(f"Failed to obtain image: {error}")

            self.container_id = self._get_container()

        self._detected_shell = None

    @classmethod
    def set_config(cls, config) -> None:
        """Inject config into the backend class.

        Called by factory before backend methods are invoked.
        """
        cls._injected_config = config

    @classmethod
    def _get_docker_host(cls) -> str:
        """Get docker_host from injected config or environment.

        Raises:
          ValueError: Raised when this operation fails."""
        # Priority 1: Injected config
        if cls._injected_config and hasattr(cls._injected_config, "sandbox"):
            docker_host = getattr(cls._injected_config.sandbox, "docker_host", None)
            if docker_host:
                return docker_host

        # Priority 2: Environment variable
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            return docker_host

        raise ValueError(
            "docker_host not configured. "
            'Set in config: [sandbox] docker_host = "ssh://user@host"'
        )

    @classmethod
    def _get_docker_client(cls, timeout: Optional[int] = None) -> docker.DockerClient:
        """Get Docker client for remote daemon."""
        timeout = timeout or 3600
        docker_host = cls._get_docker_host()

        logger.info(f"Connecting to remote Docker: {docker_host}")

        tls_config = None
        cert_path = os.environ.get("DOCKER_TLS_CERTDIR")
        if cert_path and docker_host.startswith("tcp://"):
            from docker import tls as docker_tls

            tls_config = docker_tls.TLSConfig(
                client_cert=(f"{cert_path}/cert.pem", f"{cert_path}/key.pem"),
                ca_cert=f"{cert_path}/ca.pem",
                verify=True,
            )
            logger.info(f"Using TLS from {cert_path}")

        return docker.DockerClient(
            base_url=docker_host,
            tls=tls_config,
            timeout=timeout,
        )

    @classmethod
    def _get_remote_host(cls) -> str:
        """Get docker_remote_host from injected config or environment.

                Required for service connections (Neo4j, MCP).

        Raises:
          ValueError: Raised when this operation fails."""
        # Priority 1: Injected config
        if cls._injected_config and hasattr(cls._injected_config, "sandbox"):
            remote_host = getattr(
                cls._injected_config.sandbox, "docker_remote_host", None
            )
            if remote_host:
                return remote_host

        # Priority 2: Environment variable
        remote_host = os.environ.get("DOCKER_REMOTE_HOST")
        if remote_host:
            return remote_host

        raise ValueError(
            "docker_remote_host not configured. "
            'Set in config: [sandbox] docker_remote_host = "host-or-ip"'
        )

    @classmethod
    def _make_tar_from_path(cls, source_path: Path) -> bytes:
        """Pack local directory/file into uncompressed tar archive.

        Raises:
          ValueError: Raised when this operation fails."""
        tar_stream = io.BytesIO()

        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            if source_path.is_file() and source_path.name.endswith(".tar.gz"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    with tarfile.open(source_path, "r:gz") as gz_tar:
                        gz_tar.extractall(temp_dir)

                    for item in Path(temp_dir).rglob("*"):
                        if item.is_file():
                            arcname = item.relative_to(temp_dir)
                            tar.add(str(item), arcname=str(arcname))

            elif source_path.is_dir():
                files = list(source_path.iterdir())
                if len(files) == 1 and files[0].name.endswith(".tar.gz"):
                    return cls._make_tar_from_path(files[0])

                total_size = 0
                for item in source_path.rglob("*"):
                    if item.is_file():
                        file_size = item.stat().st_size
                        total_size += file_size

                        if total_size > 1024 * 1024 * 1024:
                            logger.warning(f"Directory {source_path} exceeds 1GB")

                        arcname = item.relative_to(source_path)
                        tar.add(str(item), arcname=str(arcname))

            else:
                raise ValueError(f"Unsupported source_path: {source_path}")

        tar_stream.seek(0)
        tar_bytes = tar_stream.read()
        logger.info(f"Tar archive: {len(tar_bytes) / 1024 / 1024:.2f} MB")
        return tar_bytes

    @classmethod
    def _create_and_populate_volume(
        cls,
        volume_name: str,
        source_path: Path = None,
    ) -> str:
        """Create volume and populate using put_archive (remote-compatible).

        Raises:
          Exception: Raised when this operation fails.
          RuntimeError: Raised when this operation fails."""
        client = cls._get_docker_client()

        try:
            volume = client.volumes.create(name=volume_name)
            logger.info(f"Created remote volume: {volume_name}")

            if not source_path or not source_path.exists():
                return volume.name

            logger.info(f"Packing {source_path}...")
            start = time.time()
            tar_data = cls._make_tar_from_path(source_path)
            size_mb = len(tar_data) / 1024 / 1024
            logger.info(f"Packed {size_mb:.2f} MB in {time.time() - start:.2f}s")

            try:
                client.images.get("alpine:latest")
            except ImageNotFound:
                client.images.pull("alpine:latest")

            temp_container = None
            try:
                temp_container = client.containers.create(
                    "alpine:latest",
                    command=["tail", "-f", "/dev/null"],
                    volumes={volume.name: {"bind": "/target", "mode": "rw"}},
                    detach=True,
                    name=f"populate-{volume_name}-{uuid.uuid4().hex[:8]}",
                )
                temp_container.start()

                start = time.time()
                temp_container.put_archive("/target", tar_data)
                logger.info(f"Uploaded in {time.time() - start:.2f}s")

                exit_code, _ = temp_container.exec_run(
                    ["chmod", "-R", "777", "/target"]
                )
                if exit_code == 0:
                    logger.info(f"Set permissions on {volume_name}")

                return volume.name

            except Exception as e:
                try:
                    volume.remove()
                except Exception:
                    pass
                raise RuntimeError(f"Failed to populate volume: {e}")

            finally:
                if temp_container:
                    try:
                        temp_container.stop(timeout=5)
                        temp_container.remove()
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Failed to create volume {volume_name}: {e}")
            raise

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        """Launch all sandbox instances on remote Docker daemon.

        Raises:
          Exception: Raised when this operation fails."""
        from opensage.session.opensage_session import get_opensage_session

        opensage_session = get_opensage_session(session_id)
        config = opensage_session.config

        remote_host = cls._get_remote_host()
        config.default_host = remote_host
        logger.info(f"Remote Docker: default_host={remote_host}")

        for sandbox_type, container_config in sandbox_configs.items():
            if container_config.ports:
                updated_ports = {}
                for container_port in container_config.ports.keys():
                    updated_ports[container_port] = None
                container_config.ports = updated_ports

        async def launch_concurrent():
            from opensage.sandbox.factory import (
                create_sandbox_class,
                get_initializer_class,
            )

            tasks = []
            for sandbox_type, container_config in sandbox_configs.items():
                initializer_class = get_initializer_class(sandbox_type)
                sandbox_class = create_sandbox_class(cls, initializer_class)

                async def create_one(stype, cfg):
                    sandbox_instance = sandbox_class(
                        cfg,
                        session_id=session_id,
                        backend_type=cls.backend_type,
                        sandbox_type=stype,
                    )
                    sandbox_instance._using_cached = cfg.using_cached
                    return stype, sandbox_instance

                tasks.append(create_one(sandbox_type, container_config))

            import asyncio

            results = await asyncio.gather(*tasks)
            return dict(results)

        sandbox_instances = {}

        try:
            sandbox_instances = await launch_concurrent()
            cls._update_service_ports(config, sandbox_instances)
            logger.info(f"Launched {len(sandbox_instances)} remote sandboxes")
            return sandbox_instances

        except Exception as e:
            logger.error(f"Failed to launch: {e}")
            for sandbox in sandbox_instances.values():
                try:
                    if hasattr(sandbox, "delete_container"):
                        sandbox.delete_container()
                except Exception:
                    pass
            raise

    @classmethod
    def _update_service_ports(cls, config, sandbox_instances: dict) -> None:
        """Query Docker-assigned ports and update config."""
        client = cls._get_docker_client()

        # Update Neo4j
        if config.neo4j and "neo4j" in sandbox_instances:
            neo4j_sandbox = sandbox_instances["neo4j"]
            if hasattr(neo4j_sandbox, "container_id"):
                try:
                    container = client.containers.get(neo4j_sandbox.container_id)
                    container.reload()

                    if "7687/tcp" in container.ports and container.ports["7687/tcp"]:
                        actual_port = container.ports["7687/tcp"][0]["HostPort"]
                        config.neo4j.bolt_port = int(actual_port)
                        logger.info(f"Neo4j bolt: {actual_port}")

                    if "7474/tcp" in container.ports and container.ports["7474/tcp"]:
                        actual_port = container.ports["7474/tcp"][0]["HostPort"]
                        config.neo4j.neo4j_http_port = int(actual_port)
                        logger.info(f"Neo4j HTTP: {actual_port}")

                except Exception as e:
                    logger.warning(f"Failed to query Neo4j ports: {e}")

        # Update MCP services
        # Note: service_name in config matches sandbox_type directly
        # e.g., config.mcp.services["gdb_mcp"] matches sandbox_instances["gdb_mcp"]
        if config.mcp and config.mcp.services:
            for service_name, mcp_config in config.mcp.services.items():
                if service_name in sandbox_instances:
                    mcp_sandbox = sandbox_instances[service_name]
                    if hasattr(mcp_sandbox, "container_id"):
                        try:
                            container = client.containers.get(mcp_sandbox.container_id)
                            container.reload()

                            # Find any exposed port and use it
                            if container.ports:
                                for port_spec, bindings in container.ports.items():
                                    if bindings and len(bindings) > 0:
                                        actual_port = bindings[0]["HostPort"]
                                        mcp_config._sse_port = int(actual_port)
                                        logger.info(
                                            f"MCP {service_name}: {actual_port}"
                                        )
                                        break

                        except Exception as e:
                            logger.warning(f"Failed to query {service_name} port: {e}")

    @classmethod
    def image_exists_locally(cls, image_name: str) -> bool:
        """Check if image exists on remote Docker daemon."""
        try:
            client = cls._get_docker_client()
            client.images.get(image_name)
            return True
        except ImageNotFound:
            return False
        except Exception as e:
            logger.warning(f"Error checking image {image_name}: {e}")
            return False

    @classmethod
    def can_pull_image(cls, image_name: str) -> bool:
        """Pull image on remote Docker daemon."""
        try:
            client = cls._get_docker_client()
            logger.info(f"Pulling {image_name} on remote...")
            client.images.pull(image_name)
            return True
        except Exception as e:
            logger.warning(f"Failed to pull {image_name}: {e}")
            return False

    @classmethod
    def ensure_docker_image(cls, config) -> tuple[bool, Optional[str]]:
        """Ensure image is available on remote daemon."""
        if not config.image:
            return False, "No image specified"

        if cls.image_exists_locally(config.image):
            return True, None

        logger.info(f"Image {config.image} not found, pulling...")
        if cls.can_pull_image(config.image):
            return True, None

        if config.absolute_dockerfile_path or config.project_relative_dockerfile_path:
            build_result = cls.build_image_from_dockerfile(config)

            if build_result is None:
                return False, "Dockerfile config incomplete"

            if build_result.success:
                return True, None
            else:
                return False, f"Build failed: {build_result.error_message}"

        return False, f"Image {config.image} not available"

    @classmethod
    def build_image_from_dockerfile(cls, config) -> Optional[DockerBuildResult]:
        """Build image using Docker SDK (remote-compatible)."""
        from opensage.utils.project_info import PROJECT_PATH

        has_dockerfile = (
            config.project_relative_dockerfile_path or config.absolute_dockerfile_path
        )
        if not has_dockerfile or not config.image:
            return None

        if config.absolute_dockerfile_path:
            dockerfile_path = Path(config.absolute_dockerfile_path)
        else:
            dockerfile_path = Path(PROJECT_PATH) / Path(
                config.project_relative_dockerfile_path
            )

        if not dockerfile_path.exists():
            return DockerBuildResult(
                success=False,
                image_name=config.image,
                build_output="",
                error_message=f"Dockerfile not found: {dockerfile_path}",
            )

        build_context = dockerfile_path.parent
        client = cls._get_docker_client()

        try:
            logger.info(f"Building {config.image} on remote...")
            logger.info(f"  Context: {build_context}")

            image, build_logs = client.images.build(
                path=str(build_context),
                dockerfile=str(dockerfile_path.name),
                tag=config.image,
                buildargs=config.build_args or {},
                rm=True,
                pull=True,
            )

            build_output = ""
            for log in build_logs:
                if "stream" in log:
                    build_output += log["stream"]

            logger.info(f"✅ Built {config.image}")

            return DockerBuildResult(
                success=True,
                image_name=config.image,
                build_output=build_output,
            )

        except docker.errors.BuildError as e:
            build_log = ""
            if hasattr(e, "build_log"):
                for log in e.build_log:
                    if "stream" in log:
                        build_log += log["stream"]

            return DockerBuildResult(
                success=False,
                image_name=config.image,
                build_output=build_log,
                error_message=str(e),
            )

        except Exception as e:
            return DockerBuildResult(
                success=False,
                image_name=config.image,
                build_output="",
                error_message=str(e),
            )

    @classmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        """Cache containers on remote Docker."""
        import re

        def normalize_image_name(name: str) -> str:
            normalized = name.lower()
            normalized = re.sub(r"[^a-z0-9._-]", "_", normalized)
            normalized = normalized.strip(".-")
            if normalized.startswith("_"):
                normalized = "img" + normalized
            if len(normalized) > 200:
                normalized = normalized[:200].rstrip("_-.")
            return normalized

        cache_results = {
            "backend": "remotedocker",
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": None,
            "cached_images": {},
            "errors": [],
        }

        try:
            cache_dir_path = Path(cache_dir)
            cache_dir_path.mkdir(parents=True, exist_ok=True)

            if shared_volume_id:
                try:
                    backup_path = cache_dir_path / f"{task_name}_shared_volume.tar.gz"
                    cls._backup_remote_volume_to_tarball(
                        volume_name=shared_volume_id,
                        backup_tar_path=backup_path,
                    )
                    cache_results["shared_volume_backup"] = str(backup_path)
                except Exception as exc:
                    error = f"Failed to backup shared volume {shared_volume_id}: {exc}"
                    logger.error(error)
                    cache_results["errors"].append(error)

            client = cls._get_docker_client()
            normalized_task = normalize_image_name(task_name)

            for sandbox_type, sandbox_instance in sandbox_instances.items():
                try:
                    if (
                        not hasattr(sandbox_instance, "container_id")
                        or not sandbox_instance.container_id
                    ):
                        continue

                    container = client.containers.get(sandbox_instance.container_id)

                    normalized_type = normalize_image_name(sandbox_type)
                    repository = f"{normalized_task}_sandbox_{normalized_type}"
                    cached_image = f"{repository}:cached"

                    logger.info(f"Committing {container.id} to {cached_image}")

                    committed = container.commit(
                        repository=repository,
                        tag="cached",
                        message=f"Cached for {task_name}",
                    )

                    cache_results["cached_images"][sandbox_type] = {
                        "image_name": cached_image,
                        "image_id": committed.id,
                        "container_id": container.id,
                    }

                    logger.info(f"✅ Committed {sandbox_type}")

                except Exception as e:
                    error = f"Failed to commit {sandbox_type}: {e}"
                    logger.error(error)
                    cache_results["errors"].append(error)

            manifest_data = {
                "task_name": task_name,
                "cache_dir": str(cache_dir_path),
                "shared_volume_backup": cache_results["shared_volume_backup"],
                "sandboxes": cache_results["cached_images"],
            }
            manifest_path = cache_dir_path / "remote_docker_cache_manifest.json"
            with manifest_path.open("w", encoding="utf-8") as manifest_file:
                json.dump(manifest_data, manifest_file, indent=2)
            cache_results["metadata_path"] = str(manifest_path)
            os.environ[cls._CACHE_DIR_ENV] = str(cache_dir_path)

            global_manifest_dir = (
                Path.home() / ".cache" / "opensage" / "remote_docker_cache"
            )
            global_manifest_dir.mkdir(parents=True, exist_ok=True)
            global_manifest = global_manifest_dir / f"{normalized_task}.json"
            with global_manifest.open("w", encoding="utf-8") as global_file:
                json.dump(manifest_data, global_file, indent=2)

            return cache_results

        except Exception as e:
            error = f"Failed to cache: {e}"
            logger.error(error)
            cache_results["errors"].append(error)
            return cache_results

    @classmethod
    def _backup_remote_volume_to_tarball(
        cls,
        *,
        volume_name: str,
        backup_tar_path: Path,
    ) -> str:
        """Backup a remote Docker volume to a local ``tar.gz`` file.

        Raises:
          RuntimeError: Raised when this operation fails."""
        client = cls._get_docker_client()
        helper_container = None
        temp_tar_path = None
        try:
            try:
                client.images.get("alpine:latest")
            except ImageNotFound:
                client.images.pull("alpine:latest")

            helper_container = client.containers.create(
                "alpine:latest",
                command=["tail", "-f", "/dev/null"],
                volumes={volume_name: {"bind": "/data", "mode": "ro"}},
                detach=True,
                name=f"backup-{volume_name}-{uuid.uuid4().hex[:8]}",
            )
            helper_container.start()

            exit_code, output = helper_container.exec_run(
                ["sh", "-c", "tar -C /data -czf /tmp/shared_volume.tar.gz ."],
            )
            if exit_code != 0:
                raise RuntimeError(
                    f"Failed to archive remote volume {volume_name}: "
                    f"{output.decode('utf-8', errors='ignore')}"
                )

            stream, _ = helper_container.get_archive("/tmp/shared_volume.tar.gz")
            with tempfile.NamedTemporaryFile(delete=False) as temp_tar:
                for chunk in stream:
                    temp_tar.write(chunk)
                temp_tar_path = temp_tar.name

            backup_tar_path.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(temp_tar_path) as tar:
                members = tar.getmembers()
                if not members:
                    raise RuntimeError(
                        f"Remote archive for volume {volume_name} is empty"
                    )
                file_obj = tar.extractfile(members[0])
                if file_obj is None:
                    raise RuntimeError(
                        f"Failed to extract backup file for volume {volume_name}"
                    )
                with backup_tar_path.open("wb") as out_file:
                    out_file.write(file_obj.read())

            logger.info(f"Backed up remote volume {volume_name} to {backup_tar_path}")
            return str(backup_tar_path)
        finally:
            if temp_tar_path and os.path.exists(temp_tar_path):
                os.remove(temp_tar_path)
            if helper_container is not None:
                try:
                    helper_container.stop(timeout=5)
                except Exception:
                    pass
                try:
                    helper_container.remove(force=True)
                except Exception:
                    pass

    @classmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        """Delete shared volumes using Docker API."""
        client = cls._get_docker_client()

        for volume_id in [scripts_volume_id, data_volume_id, tools_volume_id]:
            if volume_id:
                try:
                    volume = client.volumes.get(volume_id)
                    volume.remove()
                    logger.info(f"Deleted remote volume: {volume_id}")
                except Exception as e:
                    logger.warning(f"Error deleting volume {volume_id}: {e}")

    @classmethod
    def checkpoint(cls) -> str:
        """Checkpoint the sandbox."""
        raise NotImplementedError("Checkpoint is not implemented for LocalSandbox")

    @classmethod
    def restore(cls) -> str:
        """Restore the sandbox."""
        raise NotImplementedError("Restore is not implemented for LocalSandbox")
