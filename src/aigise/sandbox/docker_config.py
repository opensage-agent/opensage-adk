from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    ports: List[str] = field(default_factory=list)  # ["9000:9000", ...]

    # Raw args passthrough for docker CLI (where applicable)
    docker_args: List[str] = field(default_factory=list)

    # SWE-ReX related toggles (if leveraged by the manager)
    remove_container: Optional[bool] = None
    remove_images: Optional[bool] = None
    python_standalone_dir: Optional[str] = None

    # Anything else
    extra: Dict[str, Any] = field(default_factory=dict)
