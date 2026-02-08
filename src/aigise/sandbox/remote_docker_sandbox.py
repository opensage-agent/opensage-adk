"""Remote Docker Sandbox implementation.

This module provides a sandbox backend that connects to remote Docker daemons
via SSH or TCP, enabling distributed execution across multiple machines.
"""

from __future__ import annotations

import io
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

from aigise.sandbox.native_docker_sandbox import (
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
      python -m aigise.evaluations ...
    """

    backend_type = "remotedocker"

    @classmethod
    def _get_docker_host_from_config(cls) -> Optional[str]:
        """Get docker_host from config if available."""
        try:
            from aigise.session.aigise_session import get_aigise_session

            sessions = (
                list(get_aigise_session._sessions.values())
                if hasattr(get_aigise_session, "_sessions")
                else []
            )
            for session in sessions:
                if hasattr(session, "config") and hasattr(session.config, "sandbox"):
                    docker_host = getattr(session.config.sandbox, "docker_host", None)
                    if docker_host:
                        return docker_host
        except Exception:
            pass
        return None

    @classmethod
    def _get_docker_client(cls, timeout: Optional[int] = None) -> docker.DockerClient:
        """Get Docker client for remote daemon."""
        timeout = timeout or 3600

        docker_host = cls._get_docker_host_from_config() or os.environ.get(
            "DOCKER_HOST"
        )

        if not docker_host:
            raise ValueError(
                "RemoteDockerSandbox requires docker_host configuration. "
                'Set in config: [sandbox] docker_host = "ssh://user@host" '
                'or environment: DOCKER_HOST="ssh://user@host"'
            )

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
    def _get_remote_host_from_config(cls) -> Optional[str]:
        """Get docker_remote_host from config if available."""
        try:
            from aigise.session.aigise_session import get_aigise_session

            sessions = (
                list(get_aigise_session._sessions.values())
                if hasattr(get_aigise_session, "_sessions")
                else []
            )
            for session in sessions:
                if hasattr(session, "config") and hasattr(session.config, "sandbox"):
                    remote_host = getattr(
                        session.config.sandbox, "docker_remote_host", None
                    )
                    if remote_host:
                        return remote_host
        except Exception:
            pass
        return None

    @classmethod
    def _get_remote_host_ip(cls) -> str:
        """Extract remote host IP from config or DOCKER_HOST."""
        remote_host = cls._get_remote_host_from_config()
        if remote_host:
            return remote_host

        remote_host = os.environ.get("DOCKER_REMOTE_HOST")
        if remote_host:
            return remote_host

        docker_host = cls._get_docker_host_from_config()
        if not docker_host:
            docker_host = os.environ.get("DOCKER_HOST", "")

        if docker_host.startswith("tcp://"):
            return docker_host.replace("tcp://", "").split(":")[0]
        elif docker_host.startswith("ssh://"):
            return docker_host.replace("ssh://", "").split("@")[-1].split(":")[0]
        elif docker_host.startswith("http://") or docker_host.startswith("https://"):
            proto = "https://" if docker_host.startswith("https://") else "http://"
            return docker_host.replace(proto, "").split(":")[0]

        raise ValueError(f"Cannot parse remote host from: {docker_host}")

    @classmethod
    def _make_tar_from_path(cls, source_path: Path) -> bytes:
        """Pack local directory/file into uncompressed tar archive."""
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
        """Create volume and populate using put_archive (remote-compatible)."""
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
        """Launch all sandbox instances on remote Docker daemon."""
        from aigise.session.aigise_session import get_aigise_session

        aigise_session = get_aigise_session(session_id)
        config = aigise_session.config

        remote_host = cls._get_remote_host_ip()
        config.default_host = remote_host
        logger.info(f"Remote Docker: default_host={remote_host}")

        for sandbox_type, container_config in sandbox_configs.items():
            if container_config.ports:
                updated_ports = {}
                for container_port in container_config.ports.keys():
                    updated_ports[container_port] = None
                container_config.ports = updated_ports

        async def launch_concurrent():
            from aigise.sandbox.factory import (
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
                # service_name already is the sandbox_type (e.g., "gdb_mcp")
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
        from aigise.utils.project_info import PROJECT_PATH

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
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": None,
            "cached_images": {},
            "errors": [],
        }

        try:
            client = cls._get_docker_client()

            for sandbox_type, sandbox_instance in sandbox_instances.items():
                try:
                    if (
                        not hasattr(sandbox_instance, "container_id")
                        or not sandbox_instance.container_id
                    ):
                        continue

                    container = client.containers.get(sandbox_instance.container_id)

                    normalized_task = normalize_image_name(task_name)
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

            return cache_results

        except Exception as e:
            error = f"Failed to cache: {e}"
            logger.error(error)
            cache_results["errors"].append(error)
            return cache_results

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
