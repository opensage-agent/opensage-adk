import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def image_exists_locally(image_name: str, container_cli: str = "docker") -> bool:
    """Check if a local container image exists."""
    try:
        result = subprocess.run(
            [container_cli, "images", "-q", image_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def can_pull_image(image_name: str, container_cli: str = "docker") -> bool:
    """Try to pull a container image and return success status."""
    try:
        result = subprocess.run(
            [container_cli, "pull", image_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def normalize_image_name(name: str) -> str:
    """Normalize name to comply with Docker image naming rules."""
    normalized = name.lower()
    normalized = re.sub(r"[^a-z0-9._-]", "_", normalized)
    normalized = normalized.strip(".-")
    if normalized.startswith("_"):
        normalized = "img" + normalized
    if len(normalized) > 200:
        normalized = normalized[:200].rstrip("_-.")
    return normalized


def load_named_cache_manifest(
    task_name: str,
    *,
    cache_dir_env: str,
    global_subdir: str,
    manifest_filename: str,
) -> tuple[dict, Optional[str], Optional[str]]:
    """Load a cache manifest from environment-specified or global cache paths.

    Searches for the manifest file in:
    1. The directory specified by the cache_dir_env environment variable
    2. The global cache directory at ~/.cache/opensage/{global_subdir}/

    Returns:
        tuple of (sandboxes_dict, cache_dir, shared_volume_backup)
    """
    manifest_paths = []
    cache_dir_value = os.getenv(cache_dir_env)
    if cache_dir_value:
        manifest_paths.append(Path(cache_dir_value) / manifest_filename)

    global_manifest = (
        Path.home()
        / ".cache"
        / "opensage"
        / global_subdir
        / f"{normalize_image_name(task_name)}.json"
    )
    manifest_paths.append(global_manifest)

    for manifest_path in manifest_paths:
        if manifest_path and manifest_path.exists():
            try:
                with manifest_path.open("r", encoding="utf-8") as manifest_file:
                    data = json.load(manifest_file)
                return (
                    data.get("sandboxes", {}),
                    data.get("cache_dir"),
                    data.get("shared_volume_backup"),
                )
            except Exception as exc:
                logger.debug(f"Failed to read cache manifest {manifest_path}: {exc}")
    return {}, None, None


@dataclass
class SandboxCacheInfo:
    """Result of resolving cache for a single sandbox."""

    found: bool
    image: Optional[str] = None
    rootfs_tar: Optional[str] = None
    base_image: Optional[str] = None
