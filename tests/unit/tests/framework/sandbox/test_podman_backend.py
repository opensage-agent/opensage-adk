from __future__ import annotations

from pathlib import Path

from opensage.config import ContainerConfig


def test_podman_backend_prefers_explicit_host(monkeypatch):
    from opensage.sandbox.podman_sandbox import PodmanSandbox

    monkeypatch.setenv("PODMAN_DOCKER_HOST", "unix:///tmp/podman.sock")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    assert PodmanSandbox._find_podman_docker_host() == "unix:///tmp/podman.sock"


def test_podman_backend_does_not_use_plain_docker_host(monkeypatch):
    from opensage.sandbox.podman_sandbox import PodmanSandbox

    monkeypatch.delenv("PODMAN_DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(Path, "exists", lambda _path: False)

    assert PodmanSandbox._find_podman_docker_host() is None


def test_podman_backend_discovers_xdg_socket(monkeypatch):
    from opensage.sandbox.podman_sandbox import PodmanSandbox

    socket_path = Path("/tmp/opensage-podman-runtime/podman/podman.sock")

    monkeypatch.delenv("PODMAN_DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/opensage-podman-runtime")
    monkeypatch.setattr(Path, "exists", lambda path: path == socket_path)

    assert PodmanSandbox._find_podman_docker_host() == f"unix://{socket_path}"


def test_podman_pull_image_name_qualifies_docker_hub_short_names():
    from opensage.sandbox.podman_sandbox import _podman_pull_image_name

    assert (
        _podman_pull_image_name("jefzda/sweap-images:tag")
        == "docker.io/jefzda/sweap-images:tag"
    )
    assert _podman_pull_image_name("python:3.11") == "docker.io/library/python:3.11"
    assert _podman_pull_image_name("ghcr.io/org/image:tag") == "ghcr.io/org/image:tag"
    assert (
        _podman_pull_image_name("localhost:5000/org/image:tag")
        == "localhost:5000/org/image:tag"
    )


def test_podman_ensure_image_keeps_existing_local_short_name(monkeypatch):
    from opensage.sandbox.podman_sandbox import PodmanSandbox

    config = ContainerConfig(image="jefzda/sweap-images:tag")

    monkeypatch.setattr(
        PodmanSandbox,
        "_image_exists_locally",
        classmethod(lambda _cls, image: image == "jefzda/sweap-images:tag"),
    )
    monkeypatch.setattr(
        PodmanSandbox,
        "_can_pull_image",
        classmethod(lambda _cls, _image: False),
    )

    assert PodmanSandbox._ensure_image(config) == (True, None)
    assert config.image == "jefzda/sweap-images:tag"


def test_podman_ensure_image_uses_qualified_name_for_remote_pull(monkeypatch):
    import opensage.sandbox.podman_sandbox as podman_sandbox
    from opensage.sandbox.podman_sandbox import PodmanSandbox

    config = ContainerConfig(image="jefzda/sweap-images:tag")
    pull_attempts: list[str] = []

    monkeypatch.setattr(
        PodmanSandbox,
        "_image_exists_locally",
        classmethod(lambda _cls, _image: False),
    )

    def can_pull(_cls, image: str) -> bool:
        pull_attempts.append(image)
        return image == "docker.io/jefzda/sweap-images:tag"

    monkeypatch.setattr(PodmanSandbox, "_can_pull_image", classmethod(can_pull))

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("qualified pull should avoid dockerfile fallback")

    monkeypatch.setattr(podman_sandbox, "ensure_docker_image", fail_ensure)

    assert PodmanSandbox._ensure_image(config) == (True, None)
    assert pull_attempts == ["docker.io/jefzda/sweap-images:tag"]
    assert config.image == "docker.io/jefzda/sweap-images:tag"


def test_podman_ensure_image_falls_back_with_original_name(monkeypatch):
    import opensage.sandbox.podman_sandbox as podman_sandbox
    from opensage.sandbox.podman_sandbox import PodmanSandbox

    config = ContainerConfig(image="jefzda/sweap-images:tag")
    fallback_images: list[str] = []

    monkeypatch.setattr(
        PodmanSandbox,
        "_image_exists_locally",
        classmethod(lambda _cls, _image: False),
    )
    monkeypatch.setattr(
        PodmanSandbox,
        "_can_pull_image",
        classmethod(lambda _cls, _image: False),
    )

    def fake_ensure(config: ContainerConfig, **_kwargs):
        fallback_images.append(config.image)
        return False, "missing"

    monkeypatch.setattr(podman_sandbox, "ensure_docker_image", fake_ensure)

    assert PodmanSandbox._ensure_image(config) == (False, "missing")
    assert fallback_images == ["jefzda/sweap-images:tag"]
    assert config.image == "jefzda/sweap-images:tag"
