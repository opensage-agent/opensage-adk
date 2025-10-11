"""
AigiseSandboxManager: Session-specific sandbox management

This module provides session-bound sandbox management, replacing the global
SandboxManager with session-isolated sandbox handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set

from loguru import logger

from aigise.config.config_dataclass import AigiseConfig
from aigise.sandbox import BaseSandbox
from aigise.sandbox.factory import get_backend_class
from aigise.sandbox.utils import can_pull_image, image_exists_locally
from aigise.session.sandbox_state import SandboxState
from aigise.utils.project_info import PROJECT_PATH


class AigiseSandboxManager:
    """Session-specific sandbox manager.

    Each AigiseSession gets its own AigiseSandboxManager instance,
    ensuring complete sandbox isolation between sessions.
    """

    def __init__(self, session):
        """Initialize AigiseSandboxManager.

        Args:
            session: AigiseSession instance (stores reference, not copied)
        """
        self._session = session
        self.aigise_session_id = session.aigise_session_id

        if getattr(session.config, "sandbox", None) is not None:
            logger.debug(
                f"Sandbox backend for session {session.aigise_session_id}: '{session.config.sandbox.backend}'"
            )
        else:
            logger.debug(
                f"Sandbox backend for session {session.aigise_session_id}: <none configured>"
            )

        # Sandbox storage for this session
        self._sandboxes: Dict[str, BaseSandbox] = {}
        # Sandbox state tracking
        self._sandbox_states: Dict[str, SandboxState] = {}
        # Shared volume ID for this session
        self._shared_volume_id: Optional[str] = None
        # Locks for concurrent sandbox creation (per sandbox_type)
        self._sandbox_locks: Dict[str, asyncio.Lock] = {}
        # Lock to protect _sandbox_locks dictionary itself
        self._locks_lock: asyncio.Lock = asyncio.Lock()

    @property
    def config(self) -> AigiseConfig:
        """Get latest config from session dynamically."""
        return self._session.config

    async def _get_or_create_lock(self, sandbox_type: str) -> asyncio.Lock:
        """Get or create a lock for a specific sandbox type.

        Args:
            sandbox_type: Type of sandbox

        Returns:
            asyncio.Lock for the sandbox type
        """
        # Fast path: lock already exists
        if sandbox_type in self._sandbox_locks:
            return self._sandbox_locks[sandbox_type]

        # Slow path: create new lock
        async with self._locks_lock:
            # Double-check pattern
            if sandbox_type not in self._sandbox_locks:
                self._sandbox_locks[sandbox_type] = asyncio.Lock()
            return self._sandbox_locks[sandbox_type]

    def get_sandbox(self, sandbox_type: str) -> BaseSandbox:
        """Get the sandbox instance for the given sandbox type.

        Args:
            sandbox_type: Type of sandbox to get or create

        Returns:
            BaseSandbox instance for the session and type
        """
        return self._sandboxes[sandbox_type]

    def list_sandboxes(self) -> Dict[str, BaseSandbox]:
        """List all sandboxes for this session.

        Returns:
            Dictionary mapping sandbox types to sandbox instances
        """
        return self._sandboxes.copy()

    def remove_sandbox(self, sandbox_type: str) -> bool:
        """Remove and cleanup a specific sandbox.

        Args:
            sandbox_type: Type of sandbox to remove

        Returns:
            True if removed, False if not found
        """
        if sandbox_type not in self._sandboxes:
            return False

        sandbox = self._sandboxes[sandbox_type]

        try:
            # Cleanup sandbox resources
            self._cleanup_sandbox(sandbox)
            del self._sandboxes[sandbox_type]

            logger.info(
                f"Removed sandbox {sandbox_type} from session {self.aigise_session_id}"
            )
            return True

        except Exception as e:
            logger.warning(
                f"Error removing sandbox {sandbox_type} from session {self.aigise_session_id}: {e}"
            )
            return False

    def get_session_statistics(self) -> Dict:
        """Get statistics for this session's sandboxes.

        Returns:
            Dictionary with session statistics
        """
        return {
            "aigise_session_id": self.aigise_session_id,
            "total_sandboxes": len(self._sandboxes),
            "sandbox_types": list(self._sandboxes.keys()),
            "sandbox_states": {k: v.value for k, v in self._sandbox_states.items()},
        }

    def get_sandbox_state(self, sandbox_type: str) -> Optional[SandboxState]:
        """Get the state of a specific sandbox.

        Args:
            sandbox_type: Type of sandbox to check

        Returns:
            SandboxState or None if sandbox doesn't exist
        """
        return self._sandbox_states.get(sandbox_type)

    def set_sandbox_state(self, sandbox_type: str, state: SandboxState) -> None:
        """Set the state of a specific sandbox.
        Args:
            sandbox_type: Type of sandbox to set
            state: State to set
        """
        self._sandbox_states[sandbox_type] = state

    def initialize_shared_volumes(self) -> None:
        """Initialize shared volume if configured in global sandbox config."""
        try:
            config = self.config
            # no sandbox at all
            if not config.sandbox or not (
                config.sandbox.project_relative_shared_data_path
                or config.sandbox.absolute_shared_data_path
            ):
                return

            # Check if global sandbox config has shared data path
            try:
                # Get backend type from global sandbox config or default to native
                backend_type = getattr(config.sandbox, "backend", "native")

                logger.debug(
                    f"Initializing shared volume using backend '{backend_type}'"
                )

                # Get the backend class
                backend_class = get_backend_class(backend_type)
                logger.debug(
                    f"Resolved backend class for shared volume: {backend_class.__name__}"
                )

                # Create shared volume name for this session
                volume_name = f"{self.aigise_session_id}_shared"

                # Determine the shared data path
                if config.sandbox.absolute_shared_data_path:
                    shared_data_path = Path(config.sandbox.absolute_shared_data_path)
                elif config.sandbox.project_relative_shared_data_path:
                    shared_data_path = (
                        Path(PROJECT_PATH)
                        / config.sandbox.project_relative_shared_data_path
                    )
                else:
                    logger.warning(
                        "No shared data path configured, skipping shared volume initialization"
                    )
                    return

                # Call class method to create shared volume
                volume_id = backend_class.create_shared_volume(
                    volume_name,
                    shared_data_path,
                )

                # Store shared volume ID
                self._shared_volume_id = volume_id

                # Update all sandbox configs to mount the shared volume
                self._add_shared_volume_to_all_configs(volume_id)

                logger.info(
                    f"Initialized shared volume {volume_id} for session {self.aigise_session_id}"
                )

            except Exception as e:
                logger.error(
                    f"Failed to initialize shared volume for session {self.aigise_session_id}: {e}"
                )

        except Exception as e:
            logger.error(f"Error during shared volume initialization: {e}")

    def get_shared_volume(self) -> Optional[str]:
        """Get the shared volume ID for this session.

        Returns:
            Volume ID or None if no shared volume exists
        """
        return self._shared_volume_id

    def _add_shared_volume_to_all_configs(self, volume_id: str) -> None:
        """Add shared volume mount to all sandbox configurations.

        Args:
            volume_id: The volume identifier to mount to all sandboxes
        """
        try:
            config = self.config
            if not config.sandbox or not config.sandbox.sandboxes:
                return

            shared_mount = f"{volume_id}:/shared:rw"

            for sandbox_type, sandbox_config in config.sandbox.sandboxes.items():
                # Initialize volumes list if it doesn't exist
                if not sandbox_config.volumes:
                    sandbox_config.volumes = []

                # Add shared volume mount if not already present
                if shared_mount not in sandbox_config.volumes:
                    sandbox_config.volumes.append(shared_mount)
                    logger.debug(
                        f"Added shared volume mount to {sandbox_type}: {shared_mount}"
                    )

            logger.info(f"Updated all sandbox configs with shared volume: {volume_id}")

        except Exception as e:
            logger.error(f"Failed to update sandbox configs with shared volume: {e}")

    async def launch_all_sandboxes(
        self, sandbox_types: Optional[Set[str]] = None
    ) -> None:
        """Launch configured sandbox instances based on backend type.

        This method should be called during session initialization.
        If sandboxes already exist, this method will skip to avoid conflicts.

        Args:
            sandbox_types: Optional set of sandbox types to launch.
                If None, launches all configured sandboxes.
                If provided, only launches sandboxes of the specified types.
                Use collect_sandbox_dependencies() to get this from an agent.

        Example::

            # Launch only required sandboxes
            from aigise.toolbox.decorators import collect_sandbox_dependencies

            deps = collect_sandbox_dependencies(root_agent)  # {'main', 'gdb_mcp'}
            await session.sandboxes.launch_all_sandboxes(sandbox_types=deps)

            # Or launch all configured sandboxes
            await session.sandboxes.launch_all_sandboxes()
        """
        # Defensive check: if any sandboxes already exist, skip launch
        if self._sandboxes:
            logger.warning(
                f"Sandboxes already exist for session {self.aigise_session_id}: "
                f"{list(self._sandboxes.keys())}. Skipping launch_all_sandboxes "
                f"to avoid conflicts with existing sandboxes."
            )
            return

        try:
            config = self.config
            if not config.sandbox or not config.sandbox.sandboxes:
                logger.warning("No sandbox configurations found")
                return

            # Get backend type from global sandbox config
            backend_type = getattr(config.sandbox, "backend", "native")

            # Get the backend class
            backend_class = get_backend_class(backend_type)

            # Prepare sandbox configurations (filter by types if provided)
            sandbox_configs = {}
            for sandbox_type, container_config in config.sandbox.sandboxes.items():
                # If types specified, only include those types
                if sandbox_types is None or sandbox_type in sandbox_types:
                    sandbox_configs[sandbox_type] = container_config

            if not sandbox_configs:
                logger.warning(
                    f"No matching sandbox configurations found. "
                    f"Requested: {sandbox_types}, "
                    f"Available: {list(config.sandbox.sandboxes.keys())}"
                )
                return

            logger.info(
                f"Launching sandboxes for session {self.aigise_session_id} "
                f"using {backend_type} backend: {list(sandbox_configs.keys())}"
            )
            # mark all sandbox states to starting
            for sandbox_type in sandbox_configs.keys():
                self._sandbox_states[sandbox_type] = SandboxState.STARTING

            # Call backend-specific launch method (unified interface)
            sandbox_instances = await backend_class.launch_all_sandboxes(
                session_id=self.aigise_session_id,
                sandbox_configs=sandbox_configs,
                shared_volume_id=self._shared_volume_id,
            )

            # Store sandbox instances in manager
            for sandbox_type, sandbox_instance in sandbox_instances.items():
                self._sandboxes[sandbox_type] = sandbox_instance
                self._sandbox_states[sandbox_type] = SandboxState.READY

            logger.info(
                f"Successfully launched {len(sandbox_instances)} sandboxes for session {self.aigise_session_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to launch sandboxes for session {self.aigise_session_id}: {e}"
            )
            # Set all sandbox states to error
            for sandbox_type in config.sandbox.sandboxes.keys():
                self._sandbox_states[sandbox_type] = SandboxState.ERROR
            raise

    async def wait_for_ready(self, sandbox_type: str) -> None:
        """Wait for a specific sandbox to be ready."""
        while self._sandbox_states[sandbox_type] != SandboxState.READY:
            await asyncio.sleep(1)

    def _cleanup_sandbox(self, sandbox: BaseSandbox) -> None:
        """Cleanup a specific sandbox instance.

        Args:
            sandbox: The sandbox instance to cleanup
        """
        try:
            # Delete container for Native Docker
            if hasattr(sandbox, "delete_container"):
                sandbox.delete_container()

        except Exception as e:
            logger.warning(f"Error during sandbox cleanup: {e}")

    def cleanup(self) -> None:
        """Cleanup all sandboxes for this session."""
        logger.info("Cleaning up AigiseSandboxManager")

        # Make a copy to avoid modifying while iterating
        sandbox_types = list(self._sandboxes.keys())
        for sandbox_type in sandbox_types:
            # TODO: change to config option
            if sandbox_type != "neo4j":
                try:
                    self.remove_sandbox(sandbox_type)
                except Exception as e:
                    logger.warning(f"Error cleaning up sandbox {sandbox_type}: {e}")

        # Clear any remaining references
        self._sandboxes.clear()
        self._sandbox_states.clear()
        self._shared_volume_id = None
        logger.info("Completed cleanup")

    def cache_sandboxes(
        self,
        cache_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cache current sandbox states and shared volume content.

        Args:
            cache_dir: Directory to store cache files (default: ./sandbox_cache/{task_name})

        Returns:
            Dictionary with cache results including backup paths and cached images
        """
        try:
            config = self.config

            # Determine task_name
            task_name = config.task_name

            # Determine cache directory
            if cache_dir is None:
                cache_dir = f"./sandbox_cache/{task_name}"

            # Ensure cache directory exists
            os.makedirs(cache_dir, exist_ok=True)

            # Get backend type from global sandbox config
            backend_type = getattr(config.sandbox, "backend", "native")

            # Get the backend class
            backend_class = get_backend_class(backend_type)

            logger.info(
                f"Caching sandboxes for session {self.aigise_session_id} using {backend_type} backend"
            )

            # Call backend-specific cache method
            cache_result = backend_class.cache_sandboxes(
                sandbox_instances=self._sandboxes,
                shared_volume_id=self._shared_volume_id,
                cache_dir=cache_dir,
                task_name=task_name,
            )

            logger.info(
                f"Successfully cached {len(self._sandboxes)} sandboxes for session {self.aigise_session_id}"
            )
            return cache_result

        except Exception as e:
            logger.error(
                f"Failed to cache sandboxes for session {self.aigise_session_id}: {e}"
            )
            raise

    def load_sandbox_caches_to_config(self) -> list[str]:
        """Load cached sandbox images and update sandbox configurations.

        This method looks for cached images with the naming pattern:
        {normalized_task_name}_sandbox_{normalized_sandbox_type}:cached

        For each found cached image, it updates the corresponding sandbox
        configuration to use the cached image instead of the original.

        Returns:
            List of sandbox types that don't have cached images available
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

        def image_exists_or_pullable(image_name: str) -> bool:
            """Check if image exists locally or can be pulled."""
            if image_exists_locally(image_name):
                return True
            elif can_pull_image(image_name):
                logger.info(f"Successfully pulled cached image: {image_name}")
                return True
            else:
                return False

        try:
            config = self.config
            task_name = config.task_name

            if not config.sandbox or not config.sandbox.sandboxes:
                logger.warning("No sandbox configurations found")
                return []

            normalized_task_name = normalize_image_name(task_name)
            missing_caches = []
            found_caches = []

            logger.info(f"Loading sandbox caches for task '{task_name}'")

            backend_type = getattr(config.sandbox, "backend", "native")
            k8s_manifest = {}
            if backend_type == "k8s":
                k8s_manifest, _ = self._load_k8s_cache_manifest(
                    task_name, normalize_image_name
                )

            for sandbox_type, container_config in config.sandbox.sandboxes.items():
                # Generate expected cached image name
                normalized_sandbox_type = normalize_image_name(sandbox_type)
                cached_image_name = (
                    f"{normalized_task_name}_sandbox_{normalized_sandbox_type}:cached"
                )

                manifest_entry = (
                    k8s_manifest.get(sandbox_type, {}) if backend_type == "k8s" else {}
                )

                if (
                    backend_type == "k8s"
                    and manifest_entry
                    and not manifest_entry.get("commit_succeeded", False)
                ):
                    rootfs_tar = manifest_entry.get("rootfs_tar")
                    if rootfs_tar and os.path.exists(rootfs_tar):
                        container_config.extra = container_config.extra or {}
                        container_config.extra["cached_rootfs_tar"] = rootfs_tar
                        if manifest_entry.get("base_image"):
                            container_config.extra.setdefault(
                                "cached_base_image", manifest_entry["base_image"]
                            )
                        container_config.using_cached = True
                        found_caches.append(sandbox_type)
                        logger.info(
                            f"Using file-based cache for {sandbox_type} (image unchanged, applying rootfs snapshot)"
                        )
                        continue
                    else:
                        logger.info(
                            f"No filesystem snapshot found for {sandbox_type}; skipping cache load"
                        )

                # Check if cached image exists or can be pulled
                if image_exists_or_pullable(cached_image_name):
                    # Update the container config to use cached image
                    original_image = container_config.image
                    container_config.image = cached_image_name
                    container_config.using_cached = True  # Mark as using cached image

                    logger.info(
                        f"Found cached image for {sandbox_type}: {cached_image_name} (was: {original_image})"
                    )
                    found_caches.append(sandbox_type)

                    if manifest_entry.get("rootfs_tar"):
                        container_config.extra = container_config.extra or {}
                        container_config.extra["cached_rootfs_tar"] = manifest_entry[
                            "rootfs_tar"
                        ]
                        if manifest_entry.get("base_image"):
                            container_config.extra.setdefault(
                                "cached_base_image", manifest_entry["base_image"]
                            )
                else:
                    logger.info(
                        f"No cached image found for {sandbox_type}: {cached_image_name}"
                    )
                    missing_caches.append(sandbox_type)

            if found_caches:
                logger.info(f"Loaded {len(found_caches)} cached images: {found_caches}")

            return missing_caches

        except Exception as e:
            logger.error(
                f"Failed to load sandbox caches for session {self.aigise_session_id}: {e}"
            )
            raise

    def _load_k8s_cache_manifest(
        self, task_name: str, normalizer
    ) -> tuple[dict, Optional[str]]:
        manifest_paths = []
        cache_dir_env = os.getenv("AIGISE_K8S_CACHE_DIR")
        if cache_dir_env:
            manifest_paths.append(Path(cache_dir_env) / "k8s_cache_manifest.json")

        global_manifest = (
            Path.home()
            / ".cache"
            / "aigise"
            / "k8s_cache"
            / f"{normalizer(task_name)}.json"
        )
        manifest_paths.append(global_manifest)

        for manifest_path in manifest_paths:
            if manifest_path and manifest_path.exists():
                try:
                    with manifest_path.open("r", encoding="utf-8") as manifest_file:
                        data = json.load(manifest_file)
                    return data.get("sandboxes", {}), data.get("cache_dir")
                except Exception as exc:
                    logger.debug(
                        f"Failed to read k8s cache manifest {manifest_path}: {exc}"
                    )
        return {}, None
