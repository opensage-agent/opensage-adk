"""
OpenSageSandboxManager: Session-specific sandbox management

This module provides session-bound sandbox management, replacing the global
SandboxManager with session-isolated sandbox handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set

from opensage.config.config_dataclass import OpenSageConfig
from opensage.sandbox.base_sandbox import BaseSandbox, SandboxState
from opensage.sandbox.factory import (
    create_sandbox_class,
    get_backend_class,
    get_initializer_class,
)
from opensage.sandbox.utils import can_pull_image, image_exists_locally
from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)


class OpenSageSandboxManager:
    """Session-specific sandbox manager.

    Each OpenSageSession gets its own OpenSageSandboxManager instance,
    ensuring complete sandbox isolation between sessions.
    """

    def __init__(self, session):
        """Initialize OpenSageSandboxManager.

        Args:
            session: OpenSageSession instance (stores reference, not copied)"""
        self._session = session
        self.opensage_session_id = session.opensage_session_id

        if getattr(session.config, "sandbox", None) is not None:
            logger.debug(
                f"Sandbox backend for session {session.opensage_session_id}: '{session.config.sandbox.backend}'"
            )
        else:
            logger.debug(
                f"Sandbox backend for session {session.opensage_session_id}: <none configured>"
            )

        # Sandbox storage for this session
        self._sandboxes: Dict[str, BaseSandbox] = {}
        # Shared volume IDs for this session
        self._scripts_volume_id: Optional[str] = None  # Read-only scripts volume
        self._tools_volume_id: Optional[str] = None  # Read-write tools volume
        self._shared_volume_id: Optional[str] = None  # Read-write data volume
        # Track which bash_tools Skills were enabled when preparing this session.
        # Sandbox initializers can use this to run per-skill dependency installers.
        self.enabled_skills: Any = None

    @property
    def config(self) -> OpenSageConfig:
        """Get latest config from session dynamically."""
        return self._session.config

    def get_sandbox(self, sandbox_type: str) -> BaseSandbox:
        """Get the sandbox instance for the given sandbox type.

        Args:
            sandbox_type (str): Type of sandbox to get or create
        Returns:
            BaseSandbox: BaseSandbox instance for the session and type
        """
        return self._sandboxes[sandbox_type]

    def list_sandboxes(self) -> Dict[str, BaseSandbox]:
        """List all sandboxes for this session.

        Returns:
            Dict[str, BaseSandbox]: Dictionary mapping sandbox types to sandbox instances
        """
        return self._sandboxes.copy()

    def remove_sandbox(self, sandbox_type: str) -> bool:
        """Remove and cleanup a specific sandbox.

        Args:
            sandbox_type (str): Type of sandbox to remove
        Returns:
            bool: True if removed, False if not found
        """
        if sandbox_type not in self._sandboxes:
            return False

        sandbox = self._sandboxes[sandbox_type]

        try:
            # Cleanup sandbox resources
            self._cleanup_sandbox(sandbox)
            del self._sandboxes[sandbox_type]

            logger.info(
                f"Removed sandbox {sandbox_type} from session {self.opensage_session_id}"
            )
            return True

        except Exception as e:
            logger.warning(
                f"Error removing sandbox {sandbox_type} from session {self.opensage_session_id}: {e}"
            )
            return False

    def get_session_statistics(self) -> Dict:
        """Get statistics for this session's sandboxes.

        Returns:
            Dict: Dictionary with session statistics
        """
        return {
            "opensage_session_id": self.opensage_session_id,
            "total_sandboxes": len(self._sandboxes),
            "sandbox_types": list(self._sandboxes.keys()),
            "sandbox_states": {k: v.value for k, v in self._sandboxes.state.items()},
        }

    def initialize_shared_volumes(
        self,
        *,
        tools_top_roots: set[str] | None = None,
        enabled_skills: Any = None,
    ) -> None:
        """Initialize shared volumes (scripts/shared-data/tools).

        Args:
            tools_top_roots (set[str] | None): Optional set of top-level bash_tools roots to stage
                into the tools volume/PVC. If None, stage all tools.
            enabled_skills (Any): enabled_skills setting from the root agent (None, "all",
                or List[str]). Stored for sandbox initializers to conditionally run
                skill dependency installers."""
        try:
            self.enabled_skills = enabled_skills
            config = self.config
            self._add_mount_host_paths_to_all_configs()
            self._add_host_shared_mem_mount_to_all_configs()

            # Check if global sandbox config has shared data path
            try:
                # Get backend type from global sandbox config or default to native
                backend_type = getattr(config.sandbox, "backend", "native")

                logger.debug(
                    f"Initializing shared volume using backend '{backend_type}'"
                )

                # Get the backend class
                backend_class = get_backend_class(backend_type, self.config)
                logger.debug(
                    f"Resolved backend class for shared volume: {backend_class.__name__}"
                )

                # Determine the shared data path
                shared_data_path = None
                if config.sandbox.absolute_shared_data_path:
                    shared_data_path = Path(config.sandbox.absolute_shared_data_path)
                elif config.sandbox.project_relative_shared_data_path:
                    shared_data_path = (
                        Path(PROJECT_PATH)
                        / config.sandbox.project_relative_shared_data_path
                    )

                # Call class method to create three shared volumes
                scripts_volume_id, data_volume_id, tools_volume_id = (
                    backend_class.create_shared_volume(
                        self.opensage_session_id,
                        shared_data_path,
                        tools_top_roots,
                    )
                )

                # Store volume IDs
                self._scripts_volume_id = scripts_volume_id
                self._shared_volume_id = data_volume_id
                self._tools_volume_id = tools_volume_id

                # Update all sandbox configs to mount all volumes
                self._add_shared_volumes_to_all_configs(
                    scripts_volume_id, data_volume_id, tools_volume_id
                )

                logger.info(
                    f"Initialized shared volumes for session {self.opensage_session_id}: "
                    f"scripts={scripts_volume_id}, data={data_volume_id}, tools={tools_volume_id}"
                )

            except Exception as e:
                logger.error(
                    f"Failed to initialize shared volume for session {self.opensage_session_id}: {e}"
                )

        except Exception as e:
            logger.error(f"Error during shared volume initialization: {e}")

    @staticmethod
    def _normalize_mount_host_path_spec(spec: str) -> str:
        """Validate and normalize a mount_host_paths spec.

                Expected format:
                  <absolute_host_path>:<absolute_container_path>[:ro|rw]

        Raises:
          TypeError: Raised when this operation fails.
          ValueError: Raised when this operation fails."""
        if not isinstance(spec, str):
            raise TypeError(
                f"mount_host_paths entries must be strings, got: {type(spec)}"
            )
        parts = spec.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(
                "Invalid mount_host_paths entry. Expected "
                "'<abs_host_path>:<abs_container_path>[:ro|rw]': "
                f"{spec}"
            )
        host_path = parts[0].strip()
        container_path = parts[1].strip()
        mode = parts[2].strip() if len(parts) == 3 else "rw"
        if not host_path.startswith("/"):
            raise ValueError(
                f"mount_host_paths host path must be absolute: {host_path}"
            )
        if not container_path.startswith("/"):
            raise ValueError(
                f"mount_host_paths container path must be absolute: {container_path}"
            )
        if mode not in ("ro", "rw"):
            raise ValueError(f"mount_host_paths mode must be 'ro' or 'rw': {mode}")
        return f"{host_path}:{container_path}:{mode}"

    def _add_mount_host_paths_to_all_configs(self) -> None:
        """Inject global mount_host_paths into every sandbox volume list."""
        config = self.config
        if not config.sandbox or not config.sandbox.sandboxes:
            return
        mount_specs = list(getattr(config.sandbox, "mount_host_paths", []) or [])
        if not mount_specs:
            return

        normalized_specs = [
            self._normalize_mount_host_path_spec(spec) for spec in mount_specs
        ]
        for sandbox_type, sandbox_config in config.sandbox.sandboxes.items():
            if not sandbox_config.volumes:
                sandbox_config.volumes = []
            for spec in normalized_specs:
                if spec not in sandbox_config.volumes:
                    sandbox_config.volumes.append(spec)
                    logger.debug(
                        "Added mount_host_paths spec to %s: %s",
                        sandbox_type,
                        spec,
                    )

    def _add_host_shared_mem_mount_to_all_configs(self) -> None:
        """Inject host_shared_mem_dir mount into every sandbox as /mem/shared."""
        config = self.config
        if not config.sandbox or not config.sandbox.sandboxes:
            return

        host_shared_mem_dir = getattr(config.sandbox, "host_shared_mem_dir", None)
        if not host_shared_mem_dir:
            return
        if not isinstance(host_shared_mem_dir, str):
            raise TypeError(
                "sandbox.host_shared_mem_dir must be a string absolute path"
            )
        if not host_shared_mem_dir.startswith("/"):
            raise ValueError(
                "sandbox.host_shared_mem_dir must be an absolute host path"
            )

        host_path = Path(host_shared_mem_dir)
        host_path.mkdir(parents=True, exist_ok=True)
        mount_spec = f"{host_path}:/mem/shared:rw"

        for sandbox_type, sandbox_config in config.sandbox.sandboxes.items():
            if not sandbox_config.volumes:
                sandbox_config.volumes = []
            if mount_spec not in sandbox_config.volumes:
                sandbox_config.volumes.append(mount_spec)
                logger.debug(
                    "Added host_shared_mem_dir mount to %s: %s",
                    sandbox_type,
                    mount_spec,
                )

    def get_shared_volume(self) -> Optional[str]:
        """Get the shared volume ID for this session.

        Returns:
            Optional[str]: Volume ID or None if no shared volume exists
        """
        return self._shared_volume_id

    def _add_shared_volumes_to_all_configs(
        self, scripts_volume_id: str, data_volume_id: str, tools_volume_id: str
    ) -> None:
        """Add shared volume mounts to all sandbox configurations.

        Args:
            scripts_volume_id (str): The scripts volume identifier (read-only)
            data_volume_id (str): The data volume identifier (read-write)
            tools_volume_id (str): The tools volume identifier (read-write)"""
        try:
            config = self.config
            if not config.sandbox or not config.sandbox.sandboxes:
                return

            scripts_mount = f"{scripts_volume_id}:/sandbox_scripts:ro"
            data_mount = f"{data_volume_id}:/shared:rw"
            tools_mount = f"{tools_volume_id}:/bash_tools:rw"

            for sandbox_type, sandbox_config in config.sandbox.sandboxes.items():
                # Initialize volumes list if it doesn't exist
                if not sandbox_config.volumes:
                    sandbox_config.volumes = []

                # Add scripts volume mount if not already present
                if scripts_mount not in sandbox_config.volumes:
                    sandbox_config.volumes.append(scripts_mount)
                    logger.debug(
                        f"Added scripts volume mount to {sandbox_type}: {scripts_mount}"
                    )

                # Add data volume mount if not already present
                if data_mount not in sandbox_config.volumes:
                    sandbox_config.volumes.append(data_mount)
                    logger.debug(
                        f"Added data volume mount to {sandbox_type}: {data_mount}"
                    )

                # Add tools volume mount if not already present
                if tools_mount not in sandbox_config.volumes:
                    sandbox_config.volumes.append(tools_mount)
                    logger.debug(
                        f"Added tools volume mount to {sandbox_type}: {tools_mount}"
                    )

            logger.info(
                f"Updated all sandbox configs with shared volumes: "
                f"scripts={scripts_volume_id}, data={data_volume_id}, tools={tools_volume_id}"
            )

        except Exception as e:
            logger.error(f"Failed to update sandbox configs with shared volumes: {e}")

    async def launch_all_sandboxes(
        self, sandbox_types: Optional[Set[str]] = None
    ) -> None:
        """Launch configured sandbox instances based on backend type.

                This method should be called during session initialization.
                If sandboxes already exist, this method will skip to avoid conflicts.

                Args:
                    sandbox_types (Optional[Set[str]]): Optional set of sandbox types to launch.
                        If None, launches all configured sandboxes.
                        If provided, only launches sandboxes of the specified types.
                        Use collect_sandbox_dependencies() to get this from an agent.

                Example::

                    # Launch only required sandboxes
                    from opensage.toolbox.sandbox_requirements import collect_sandbox_dependencies

                    deps = collect_sandbox_dependencies(root_agent)  # {'main', 'gdb_mcp'}
                    await session.sandboxes.launch_all_sandboxes(sandbox_types=deps)

                    # Or launch all configured sandboxes
                    await session.sandboxes.launch_all_sandboxes()

        Raises:
          Exception: Raised when this operation fails."""
        # Defensive check: if any sandboxes already exist, skip launch
        if self._sandboxes:
            logger.warning(
                f"Sandboxes already exist for session {self.opensage_session_id}: "
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
            backend_class = get_backend_class(backend_type, self.config)

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
                f"Launching sandboxes for session {self.opensage_session_id} "
                f"using {backend_type} backend: {list(sandbox_configs.keys())}"
            )

            # Call backend-specific launch method (creates containers, not initialized yet)
            sandbox_instances = await backend_class.launch_all_sandboxes(
                session_id=self.opensage_session_id,
                sandbox_configs=sandbox_configs,
                shared_volume_id=self._shared_volume_id,
                scripts_volume_id=self._scripts_volume_id,
                tools_volume_id=self._tools_volume_id,
            )

            # Store sandbox instances in manager (mark as CREATED, not READY yet)
            for sandbox_type, sandbox_instance in sandbox_instances.items():
                self._sandboxes[sandbox_type] = sandbox_instance

            logger.info(
                f"Successfully launched {len(sandbox_instances)} sandboxes for session {self.opensage_session_id}, "
                f"sandbox types: {list(sandbox_instances.keys())} (not yet initialized)"
            )

        except Exception as e:
            logger.error(
                f"Failed to launch sandboxes for session {self.opensage_session_id}: {e}"
            )
            raise

    async def initialize_all_sandboxes(
        self, *, continue_on_error: bool = False
    ) -> None:
        """Initialize all created sandboxes.

                This should be called after launch_all_sandboxes() and after
                registering any hooks.

                Example:
                    # Create sandboxes
                    await opensage_session.sandboxes.launch_all_sandboxes()

                    # Initialize
                    await opensage_session.sandboxes.initialize_all_sandboxes()

        Raises:
          res: Raised when this operation fails."""
        if not self._sandboxes:
            logger.warning(
                f"No sandboxes to initialize for session {self.opensage_session_id}"
            )
            return

        # Get backend class
        backend_type = getattr(self.config.sandbox, "backend", "native")
        backend_class = get_backend_class(backend_type, self.config)

        logger.info(
            f"Initializing {len(self._sandboxes)} sandboxes for session {self.opensage_session_id}: "
            f"{list(self._sandboxes.keys())}"
        )

        # If continue_on_error is True, the backend will return a map of sandbox_type -> Exception | None instead of raising an exception.
        result_map = await backend_class.initialize_all_sandboxes(
            self._sandboxes, continue_on_error=continue_on_error
        )

        failed = []
        succeeded = []
        for sandbox_type, exc in (result_map or {}).items():
            if exc is None:
                succeeded.append(sandbox_type)
            else:
                failed.append((sandbox_type, exc))

        # Run skill dependency installers for successfully initialized sandboxes.
        # This is orchestration-level logic and intentionally lives outside
        # sandbox initializers.
        if succeeded and self.enabled_skills is not None:
            from opensage.sandbox.skill_deps import prepare_skill_deps

            async def _prep_one(sandbox_type: str) -> None:
                sandbox = self._sandboxes.get(sandbox_type)
                if sandbox is None:
                    return
                await prepare_skill_deps(sandbox, self.enabled_skills)

            prep_tasks = [asyncio.create_task(_prep_one(t)) for t in succeeded]
            prep_results = await asyncio.gather(*prep_tasks, return_exceptions=True)
            for sandbox_type, res in zip(succeeded, prep_results):
                if isinstance(res, Exception):
                    logger.error(
                        "Skill deps prep failed for sandbox '%s': %s", sandbox_type, res
                    )
                    if continue_on_error:
                        failed.append((sandbox_type, res))
                    else:
                        raise res

        if succeeded:
            logger.info(f"Successfully initialized sandboxes: {sorted(succeeded)}")
        if failed:
            for sandbox_type, exc in failed:
                logger.error(f"Sandbox '{sandbox_type}' failed to initialize: {exc}")

    async def attach_sandbox(
        self,
        sandbox_type: str,
        *,
        container_id: Optional[str] = None,
        pod_name: Optional[str] = None,
        container_name: Optional[str] = None,
    ) -> None:
        """Attach to an existing container/Pod and register it to this session,
                then call ensure_ready.

                - native (Docker): requires container_id
                - k8s: requires pod_name + container_name

        Raises:
          ValueError: Raised when this operation fails."""
        backend_type = getattr(self.config.sandbox, "backend", "native")
        backend_class = get_backend_class(backend_type, self.config)

        # Build or create a ContainerConfig entry for this sandbox based on current config
        container_config = self.config.get_sandbox_config(sandbox_type)
        if not container_config:
            from opensage.config.config_dataclass import ContainerConfig

            container_config = ContainerConfig()
            if getattr(self.config, "sandbox", None):
                self.config.sandbox.add_or_update_sandbox(
                    sandbox_type, container_config
                )

        # Inject identifiers based on backend
        if backend_type == "native":
            if not container_id:
                raise ValueError("attach(native) requires container_id")
            container_config.container_id = container_id
        elif backend_type == "k8s":
            if not pod_name or not container_name:
                raise ValueError("attach(k8s) requires pod_name and container_name")
            container_config.pod_name = pod_name
            container_config.container_name = container_name
        else:
            raise ValueError(f"Unsupported backend: {backend_type}")

        initializer_class = get_initializer_class(sandbox_type)
        sandbox_class = create_sandbox_class(backend_class, initializer_class)

        sandbox_instance: BaseSandbox = sandbox_class(
            container_config,
            session_id=self.opensage_session_id,
            backend_type=backend_type,
            sandbox_type=sandbox_type,
        )

        self._sandboxes[sandbox_type] = sandbox_instance

        # Ensure ready
        await sandbox_instance.ensure_ready()
        if self.enabled_skills is not None:
            from opensage.sandbox.skill_deps import prepare_skill_deps

            await prepare_skill_deps(sandbox_instance, self.enabled_skills)
        logger.info(
            f"Attached sandbox '{sandbox_type}' (backend={backend_type}) for session {self.opensage_session_id}"
        )

    def _cleanup_sandbox(self, sandbox: BaseSandbox) -> None:
        """Cleanup a specific sandbox instance.

        Args:
            sandbox (BaseSandbox): The sandbox instance to cleanup"""
        try:
            # Delete container for Native Docker
            if hasattr(sandbox, "delete_container"):
                sandbox.delete_container()

        except Exception as e:
            logger.warning(f"Error during sandbox cleanup: {e}")

    def cleanup(self) -> None:
        """Cleanup all sandboxes for this session."""
        logger.info("Cleaning up OpenSageSandboxManager")

        # Make a copy to avoid modifying while iterating
        sandbox_types = list(self._sandboxes.keys())
        for sandbox_type in sandbox_types:
            try:
                self.remove_sandbox(sandbox_type)
            except Exception as e:
                logger.warning(f"Error cleaning up sandbox {sandbox_type}: {e}")

        # Delete shared volumes if they exist
        if self._scripts_volume_id or self._shared_volume_id or self._tools_volume_id:
            try:
                backend_type = getattr(self.config.sandbox, "backend", "native")
                backend_class = get_backend_class(backend_type, self.config)
                backend_class.delete_shared_volumes(
                    scripts_volume_id=self._scripts_volume_id,
                    data_volume_id=self._shared_volume_id,
                    tools_volume_id=self._tools_volume_id,
                )
                logger.info("Deleted shared volumes")
            except Exception as e:
                logger.warning(f"Error deleting shared volumes: {e}")

        # Clear any remaining references
        self._sandboxes.clear()
        self._scripts_volume_id = None
        self._tools_volume_id = None
        self._shared_volume_id = None
        logger.info("Completed cleanup")

    def cache_sandboxes(
        self,
        cache_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cache current sandbox states and shared volume content.

                Args:
                    cache_dir (Optional[str]): Directory to store cache files (default: ./sandbox_cache/{task_name})

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    Dict[str, Any]: Dictionary with cache results including backup paths and cached images
        """
        try:
            config = self.config

            # Determine task_name
            task_name = config.task_name

            # Determine cache directory
            if not cache_dir:  # Handle both None and empty string
                cache_dir = f"./sandbox_cache/{task_name}"

            # Ensure cache directory exists
            os.makedirs(cache_dir, exist_ok=True)

            # Get backend type from global sandbox config
            backend_type = getattr(config.sandbox, "backend", "native")

            # Get the backend class
            backend_class = get_backend_class(backend_type, self.config)

            logger.info(
                f"Caching sandboxes for session {self.opensage_session_id} using {backend_type} backend"
            )

            # Call backend-specific cache method
            cache_result = backend_class.cache_sandboxes(
                sandbox_instances=self._sandboxes,
                shared_volume_id=self._shared_volume_id,
                cache_dir=cache_dir,
                task_name=task_name,
            )

            logger.info(
                f"Successfully cached {len(self._sandboxes)} sandboxes for session {self.opensage_session_id}"
            )
            return cache_result

        except Exception as e:
            logger.error(
                f"Failed to cache sandboxes for session {self.opensage_session_id}: {e}"
            )
            raise

    def load_sandbox_caches_to_config(self) -> list[str]:
        """Load cached sandbox images and update sandbox configurations.

                This method looks for cached images with the naming pattern:
                {normalized_task_name}_sandbox_{normalized_sandbox_type}:cached

                For each found cached image, it updates the corresponding sandbox
                configuration to use the cached image instead of the original.

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    list[str]: List of sandbox types that don't have cached images available
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
            named_manifest = {}
            shared_volume_backup = None
            if backend_type == "k8s":
                k8s_manifest, _, shared_volume_backup = self._load_k8s_cache_manifest(
                    task_name, normalize_image_name
                )
            elif backend_type == "native":
                candidate_dirs = []
                if config.sandbox.absolute_shared_data_path:
                    candidate_dirs.append(
                        Path(config.sandbox.absolute_shared_data_path)
                    )
                candidate_dirs.append(Path(f"./sandbox_cache/{task_name}"))
                for candidate_dir in candidate_dirs:
                    candidate = candidate_dir / f"{task_name}_shared_volume.tar.gz"
                    if candidate.exists():
                        shared_volume_backup = str(candidate)
                        break
            elif backend_type == "remotedocker":
                named_manifest, _, shared_volume_backup = (
                    self._load_named_cache_manifest(
                        task_name,
                        normalize_image_name,
                        cache_dir_env="OPENSAGE_REMOTE_DOCKER_CACHE_DIR",
                        global_subdir="remote_docker_cache",
                        manifest_filename="remote_docker_cache_manifest.json",
                    )
                )
            elif backend_type == "opensandbox":
                opensandbox_manifest, _, shared_volume_backup = (
                    self._load_named_cache_manifest(
                        task_name,
                        normalize_image_name,
                        cache_dir_env="OPENSAGE_OPENSANDBOX_CACHE_DIR",
                        global_subdir="opensandbox_cache",
                        manifest_filename="opensandbox_cache_manifest.json",
                    )
                )
                named_manifest = opensandbox_manifest
                if (
                    config.sandbox.opensandbox
                    and config.sandbox.opensandbox.runtime_type == "kubernetes"
                ):
                    k8s_manifest = opensandbox_manifest

            if shared_volume_backup and os.path.exists(shared_volume_backup):
                config.sandbox.absolute_shared_data_path = shared_volume_backup
                logger.info(
                    f"Using cached shared volume backup: {shared_volume_backup}"
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
                named_manifest_entry = named_manifest.get(sandbox_type, {})

                if backend_type == "opensandbox":
                    manifest_entry = k8s_manifest.get(sandbox_type, {})

                if (
                    backend_type in {"k8s", "opensandbox"}
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

                if (
                    backend_type in {"remotedocker", "opensandbox"}
                    and named_manifest_entry
                ):
                    original_image = container_config.image
                    container_config.image = named_manifest_entry.get(
                        "image_name", cached_image_name
                    )
                    container_config.using_cached = True
                    found_caches.append(sandbox_type)
                    logger.info(
                        f"Using runtime-visible cached image for {sandbox_type}: "
                        f"{container_config.image} (was: {original_image})"
                    )
                    if named_manifest_entry.get("rootfs_tar"):
                        container_config.extra = container_config.extra or {}
                        container_config.extra["cached_rootfs_tar"] = (
                            named_manifest_entry["rootfs_tar"]
                        )
                    if named_manifest_entry.get("base_image"):
                        container_config.extra = container_config.extra or {}
                        container_config.extra.setdefault(
                            "cached_base_image", named_manifest_entry["base_image"]
                        )
                    continue

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
                f"Failed to load sandbox caches for session {self.opensage_session_id}: {e}"
            )
            raise

    def _load_k8s_cache_manifest(
        self, task_name: str, normalizer
    ) -> tuple[dict, Optional[str], Optional[str]]:
        return self._load_named_cache_manifest(
            task_name,
            normalizer,
            cache_dir_env="OPENSAGE_K8S_CACHE_DIR",
            global_subdir="k8s_cache",
            manifest_filename="k8s_cache_manifest.json",
        )

    def _load_named_cache_manifest(
        self,
        task_name: str,
        normalizer,
        *,
        cache_dir_env: str,
        global_subdir: str,
        manifest_filename: str,
    ) -> tuple[dict, Optional[str], Optional[str]]:
        manifest_paths = []
        cache_dir_value = os.getenv(cache_dir_env)
        if cache_dir_value:
            manifest_paths.append(Path(cache_dir_value) / manifest_filename)

        global_manifest = (
            Path.home()
            / ".cache"
            / "opensage"
            / global_subdir
            / f"{normalizer(task_name)}.json"
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
                    logger.debug(
                        f"Failed to read cache manifest {manifest_path}: {exc}"
                    )
        return {}, None, None
