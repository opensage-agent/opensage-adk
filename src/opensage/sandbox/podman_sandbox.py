"""Podman sandbox backend.

This backend uses Podman for local image and volume operations, and connects to
Podman's Docker-compatible API socket for container exec/archive operations.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import docker

from opensage.config import ContainerConfig
from opensage.sandbox.native_docker_sandbox import (
    NativeDockerSandbox,
    ensure_docker_image,
)


def _podman_pull_image_name(image: str) -> str:
    """Return an explicit image name for Podman pulls."""
    image = image.strip()
    if not image or "://" in image:
        return image

    if "/" in image:
        first_component = image.split("/", 1)[0]
        if (
            "." in first_component
            or ":" in first_component
            or first_component == "localhost"
        ):
            return image
        return f"docker.io/{image}"
    return f"docker.io/library/{image}"


class PodmanSandbox(NativeDockerSandbox):
    """Native Podman sandbox using Podman's Docker-compatible API service."""

    backend_type = "podman"
    _container_cli = "podman"
    _build_extra_args: tuple[str, ...] = ()
    _build_progress_arg = None
    _default_network = "slirp4netns"
    _HELPER_IMAGE_CANDIDATES = (
        "docker.io/library/alpine:latest",
        "docker.io/library/busybox:latest",
    )
    _cached_helper_image: str | None = None

    def __init__(
        self,
        container_config: ContainerConfig,
        session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
    ):
        if shutil.which(self._container_cli) is None:
            raise RuntimeError("Podman backend requires the 'podman' CLI on PATH.")
        super().__init__(container_config, session_id, backend_type, sandbox_type)

    @classmethod
    def _find_podman_docker_host(cls) -> str | None:
        explicit_host = os.environ.get("PODMAN_DOCKER_HOST")
        if explicit_host:
            return explicit_host

        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host and "podman" in docker_host.lower():
            return docker_host

        candidate_paths: list[Path] = []
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if xdg_runtime_dir:
            candidate_paths.append(Path(xdg_runtime_dir) / "podman" / "podman.sock")

        try:
            candidate_paths.append(
                Path("/run/user") / str(os.getuid()) / "podman" / "podman.sock"
            )
        except AttributeError:
            pass

        candidate_paths.append(Path("/run/podman/podman.sock"))

        for candidate in candidate_paths:
            if candidate.exists():
                return f"unix://{candidate}"
        return None

    @classmethod
    def _get_client(cls, timeout: int | None = None) -> docker.DockerClient:
        docker_host = cls._find_podman_docker_host()
        if not docker_host:
            raise RuntimeError(
                "Podman backend requires Podman's Docker-compatible API socket. "
                "Start it with 'systemctl --user enable --now podman.socket' "
                "or set PODMAN_DOCKER_HOST to the socket URL."
            )

        environment = dict(os.environ)
        environment["DOCKER_HOST"] = docker_host
        if timeout is None:
            return docker.from_env(environment=environment)
        return docker.from_env(environment=environment, timeout=timeout)

    @classmethod
    def _ensure_image(cls, config: ContainerConfig) -> tuple[bool, str | None]:
        if not config.image:
            return False, "No image specified in ContainerConfig"

        if cls._image_exists_locally(config.image):
            return True, None

        original_image = config.image
        qualified_image = _podman_pull_image_name(original_image)
        if qualified_image != original_image:
            if cls._image_exists_locally(qualified_image) or cls._can_pull_image(
                qualified_image
            ):
                config.image = qualified_image
                return True, None

        return ensure_docker_image(
            config,
            container_cli=cls._container_cli,
            build_extra_args=cls._build_extra_args,
            build_progress_arg=cls._build_progress_arg,
        )
