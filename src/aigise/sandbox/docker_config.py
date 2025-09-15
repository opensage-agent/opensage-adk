from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass
class DockerConfig:
    """Lightweight config for Docker-backed sandboxes.

    This is an internal convenience type to keep sandbox code tidy and typed.
    It intentionally mirrors common docker SDK/run options that we may support.
    Any unsupported fields can be kept in extra for forward-compat.
    """

    # General
    image: Optional[str] = None
    timeout: int = 300

    # Runtime/engine options
    platform: Optional[str] = None
    network: Optional[str] = None
    privileged: bool = False
    security_opt: List[str] = field(default_factory=list)
    cap_add: List[str] = field(default_factory=list)
    gpus: Optional[str] = None  # e.g., "all" or "device=GPU-UUID"
    shm_size: Optional[str] = None
    mem_limit: Optional[str] = None
    cpus: Optional[str] = None
    user: Optional[str] = None
    working_dir: Optional[str] = None

    # Env/volumes/ports
    environment: Dict[str, Any] = field(default_factory=dict)
    volumes: List[str] = field(default_factory=list)  # ["/host:/cont:ro", ...]
    mounts: List[str] = field(
        default_factory=list
    )  # ["type=bind,source=...,target=..."]
    ports: Dict[str, Union[int, None, Tuple[str, int], List[int]]] = field(
        default_factory=dict
    )
    """Ports to bind inside the container.
    The keys are ports to bind inside the container (e.g. '2222/tcp', '80/udp', or just '8080').
    The values can be:
    - An integer for the host port (e.g. {'2222/tcp': 3333} maps container port 2222 to host port 3333)
    - None to assign a random host port (e.g. {'2222/tcp': None})
    - A tuple of (host_ip, host_port) to specify the host interface and port (e.g. {'1111/tcp': ('127.0.0.1', 1111)} where '127.0.0.1' is the host_ip and 1111 is the host_port)
    - A list of integers to bind multiple host ports (e.g. {'1111/tcp': [1234, 4567]})
    """

    # Raw args passthrough for docker CLI (where applicable)
    docker_args: List[str] = field(default_factory=list)

    # SWE-ReX related toggles (if leveraged by the manager)
    remove_container: Optional[bool] = None
    remove_images: Optional[bool] = None
    python_standalone_dir: Optional[str] = None

    # Template fallback configuration
    dockerfile_template_path: Optional[str] = None
    template_variables: Dict[str, Any] = field(default_factory=dict)

    # Command override - if None, defaults to "bash"; if empty string, uses Dockerfile's default CMD
    command: Optional[str] = None

    # Anything else
    extra: Dict[str, Any] = field(default_factory=dict)
