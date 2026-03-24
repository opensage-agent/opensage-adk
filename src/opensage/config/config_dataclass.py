"""
Configuration DataClass Definitions for OpenSage Framework

Defines all configuration dataclasses with default values and environment variable overrides.
"""

import copy
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import toml
from dacite import Config as DaciteConfig
from dacite import from_dict

from opensage.utils.project_info import PROJECT_PATH, SRC_PATH


def _expand_template_variables(config_data: dict) -> dict:
    """Unified template variable expansion system.

        Rules:
        1. Top-level UPPERCASE variables automatically become template variables
        2. ${VAR_NAME} lookup order: environment variables → top-level variables → error
        3. Environment variables have highest priority and can override config defaults
        4. Undefined variables cause immediate error

    Raises:
      KeyError: Raised when this operation fails."""

    # Deep copy to avoid modifying original data
    expanded_data = copy.deepcopy(config_data)

    # 1. Collect top-level UPPERCASE variables as template variables
    template_variables = {}
    for key, value in expanded_data.items():
        if key.isupper() and isinstance(value, (str, int, float, bool)):
            template_variables[key] = str(value)

    # 2. Define variable lookup function
    def get_variable_value(var_name: str) -> str:
        # # First check environment variables (highest priority)
        env_value = os.getenv(var_name)
        if env_value is not None:
            return env_value

        # Then check top-level variables (fallback)
        if var_name in template_variables:
            return template_variables[var_name]

        # Not found - raise error
        raise KeyError(
            f"Template variable '{var_name}' not found in config or environment"
        )

    # 3. Recursive replacement function
    def replace_vars_recursive(obj):
        if isinstance(obj, str):
            if "${" in obj:
                # Find all ${VAR_NAME} patterns
                for match in re.finditer(r"\$\{([A-Z0-9_]+)\}", obj):
                    var_name = match.group(1)
                    var_value = get_variable_value(var_name)
                    obj = obj.replace(f"${{{var_name}}}", var_value)

                # If the entire string was a single template variable, try to convert to appropriate type
                obj_stripped = obj.strip()
                try:
                    # Try integer first
                    return int(obj_stripped)
                except ValueError:
                    try:
                        # Try float
                        return float(obj_stripped)
                    except ValueError:
                        # Try boolean
                        if obj_stripped.lower() in ("true", "false"):
                            return obj_stripped.lower() == "true"
                        # Return as string if no conversion possible
            return obj
        elif isinstance(obj, dict):
            return {k: replace_vars_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [replace_vars_recursive(item) for item in obj]
        else:
            return obj

    return replace_vars_recursive(expanded_data)


@dataclass
class MemoryConfig:
    """Memory module configuration for graph-based knowledge storage."""

    # Whether memory module is enabled (default: disabled)
    enabled: bool = False

    # LLM model for internal memory operations (strategy selection, entity extraction, etc.)
    llm_model: str = "gemini/gemini-2.5-flash-lite"

    # Embedding model for vector similarity search
    embedding_model: str = "gemini/gemini-embedding-001"

    # Whether to use LLM for search strategy selection
    use_llm_selection: bool = True

    # Whether to use LLM for operation decisions (ADD/UPDATE/DELETE/NONE)
    use_llm_decision: bool = False

    # Max iterations for search refinement
    search_max_iterations: int = 3

    # Similarity threshold for relationship discovery
    similarity_threshold: float = 0.7


@dataclass
class Neo4jConfig:
    """Neo4j database configuration with dynamic URI construction."""

    user: Optional[str] = None
    password: Optional[str] = None
    bolt_port: int = 7687  # Neo4j bolt port
    host: Optional[str] = None  # override host if needed
    neo4j_http_port: int = 7474  # Neo4j HTTP port
    _parent_config: Optional["OpenSageConfig"] = field(default=None, repr=False)

    @property
    def uri(self) -> str:
        """Get Neo4j URI, dynamically constructed from parent config's default_host.

        Returns URI in format: bolt://{default_host}:{bolt_port}
        Falls back to 127.0.0.1 if no default_host is set.
        """
        if self._parent_config and self._parent_config.default_host:
            host = self._parent_config.default_host
        else:
            host = self.host or "127.0.0.1"

        return f"bolt://{host}:{self.bolt_port}"


@dataclass
class ContainerConfig:
    """Lightweight config for Docker-backed sandboxes.

    This is an internal convenience type to keep sandbox code tidy and typed.
    It intentionally mirrors common docker SDK/run options that we may support.
    Any unsupported fields can be kept in extra for forward-compat.
    """

    # General
    image: Optional[str] = None
    container_id: Optional[str] = (
        None  # If provided, connect to existing container instead of creating new one
    )
    timeout: int = 300

    # K8s-specific fields for connecting to existing resources
    pod_name: Optional[str] = None  # If provided, connect to existing Pod
    container_name: Optional[str] = None  # Name of container within the Pod

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
    ports: Dict[str, Union[int, None, Dict[str, Any]]] = field(default_factory=dict)

    # Raw args passthrough for docker CLI (where applicable)
    docker_args: List[str] = field(default_factory=list)

    # Build configuration
    project_relative_dockerfile_path: Optional[str] = (
        None  # Path to Dockerfile relative to project root
    )
    absolute_dockerfile_path: Optional[str] = None
    build_args: Dict[str, str] = field(
        default_factory=dict
    )  # Build arguments for Docker build

    # Command override - if None, defaults to "bash"; if empty string, uses Dockerfile's default CMD
    command: Optional[str] = None

    # Cache management
    using_cached: bool = (
        False  # Flag to indicate if this sandbox is currently using a cached image
    )

    # MCP services
    #
    # List of MCP service names this sandbox depends on / should wait for.
    # Each name must exist in `OpenSageConfig.mcp.services`.
    mcp_services: List[str] = field(default_factory=list)

    # Anything else
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxConfig:
    """Configuration for different sandbox types."""

    default_image: str = None
    sandboxes: Dict[str, ContainerConfig] = field(default_factory=dict)
    project_relative_shared_data_path: Optional[str] = None
    absolute_shared_data_path: Optional[str] = None
    # Optional absolute host directory mounted to /mem/shared in all sandboxes.
    host_shared_mem_dir: Optional[str] = None
    # Global host bind mounts injected into every sandbox config as
    # "<abs_host_path>:<abs_container_path>:<ro|rw>" entries.
    mount_host_paths: List[str] = field(default_factory=list)
    backend: str = "native"
    opensandbox: Optional["OpenSandboxConfig"] = None
    # Global tolerations applied to all k8s pods (init/chmod/session). If set,
    # overrides/augments any per-container tolerations in ContainerConfig.extra.
    tolerations: Optional[list[dict]] = None
    # Remote Docker configuration (for remotedocker backend)
    docker_host: Optional[str] = (
        None  # Docker daemon URL (e.g., ssh://user@host, tcp://host:2376)
    )
    docker_remote_host: Optional[str] = None  # Remote host IP for service connections

    def get_sandbox_config(self, sandbox_type: str) -> Optional[ContainerConfig]:
        """Get configuration for a specific sandbox type."""
        return self.sandboxes.get(sandbox_type)

    def add_or_update_sandbox(self, sandbox_type: str, config: ContainerConfig) -> None:
        """Add a new sandbox configuration."""
        self.sandboxes[sandbox_type] = config


@dataclass
class ModelConfig:
    """Single model configuration."""

    model_name: str
    temperature: float = None
    max_tokens: int = None
    rpm: int = None  # requests per minute
    tpm: int = None  # tokens per minute


@dataclass
class LLMConfig:
    """LLM model configuration supporting multiple models."""

    model_configs: Dict[str, ModelConfig] = field(default_factory=dict)

    def get_model_config(self, model_type: str) -> Optional[ModelConfig]:
        """Get configuration for a specific model type."""
        return self.model_configs.get(model_type)

    def add_model(self, name: str, config: ModelConfig) -> None:
        """Add a new model configuration."""
        self.model_configs[name] = config

    # Backward compatibility properties
    @property
    def model_name(self) -> Optional[str]:
        """Get main model name for backward compatibility."""
        main_config = self.model_configs.get("main")
        return main_config.model_name if main_config else None

    @property
    def summarize_model(self) -> Optional[str]:
        """Get drop/summarize model name for backward compatibility."""
        drop_config = self.model_configs.get("summarize")
        return drop_config.model_name if drop_config else None

    @property
    def flag_claims_model(self) -> Optional[str]:
        """Get flag claims model name for backward compatibility."""
        flag_config = self.model_configs.get("flag_claims")
        return flag_config.model_name if flag_config else None


@dataclass
class HistoryConfig:
    """Tool configuration."""

    # Maximum length of a single tool response before special handling (other features may use this)
    max_tool_response_length: int = 10000
    # Whether to show remaining LLM call quota after each tool response (non-live)
    enable_quota_countdown: bool = False

    # Events compaction-based history summarization settings
    @dataclass
    class EventsCompactionConfig:
        max_history_summary_length: Optional[int] = (
            100000  # Character budget threshold for compaction
        )
        compaction_percent: int = 50

    events_compaction: EventsCompactionConfig = field(
        default_factory=EventsCompactionConfig
    )


@dataclass
class PluginsConfig:
    """Configuration for OpenSage plugins.

    The ``enabled`` list can contain:

    - **Python plugin names** (e.g. ``"doom_loop_detector_plugin"``) — loaded from
      the corresponding ``.py`` file in ``opensage/plugins/``.
    - **Claude Code hook names** (e.g. ``"careful_edit"``) — loaded from
      the corresponding ``.json`` file in ``opensage/plugins/default/claude_code_hooks/``.
    - **Regex patterns** (e.g. ``".*_plugin"``) — auto-detected by metacharacters
      and matched via ``re.fullmatch`` against all discovered plugin names.

    Plugins are searched in order (later entries shadow earlier ones):

    1. Default ADK plugins: ``opensage/plugins/default/adk_plugins/``
    2. Default Claude Code hooks: ``opensage/plugins/default/claude_code_hooks/``
    3. User-local defaults: ``~/.local/opensage/plugins/`` (both ``.py`` and
       ``.json``)
    4. Custom directories: paths listed in ``extra_plugin_dirs`` (both ``.py``
       and ``.json``)
    5. Agent-local: ``{agent_dir}/plugins/`` (both ``.py`` and ``.json``)

    Per-plugin parameters can be set via the ``params`` dict, keyed by plugin
    name.  The values are passed as ``**kwargs`` to the plugin constructor.

    Example::

        [plugins]
        enabled = ["doom_loop_detector_plugin", "careful_edit"]
        extra_plugin_dirs = ["/path/to/shared/plugins"]

        [plugins.params.doom_loop_detector_plugin]
        threshold = 5
    """

    enabled: List[str] = field(default_factory=list)
    extra_plugin_dirs: List[str] = field(default_factory=list)
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class AgentEnsembleConfig:
    """Agent ensemble configuration."""

    # If True, only agents whose tools are all listed in thread_safe_tools are
    # considered "safe" and allowed for ensemble execution. If False, the
    # thread_safe_tools filtering is disabled (all discovered agents are treated
    # as safe).
    enforce_thread_safe_tools: bool = False
    thread_safe_tools: Set[str] = field(default_factory=set)
    available_models_for_ensemble: List[str] = field(default_factory=list)


@dataclass
class BuildConfig:
    """Build and execution configuration."""

    poc_dir: Optional[str] = None
    compile_command: Optional[str] = None
    run_command: Optional[str] = None
    target_type: Optional[str] = None
    target_binary: Optional[str] = None


@dataclass
class OpenSandboxConfig:
    """Configuration for OpenSandbox-backed sandboxes.

    These settings are consumed by the OpenSage ``opensandbox`` backend.
    They describe both how to reach the OpenSandbox control plane and how
    OpenSage should provision runtime-native shared storage for that backend.
    """

    domain: Optional[str] = None
    protocol: str = "http"
    api_key: Optional[str] = None
    request_timeout_sec: int = 30
    use_server_proxy: bool = False

    # OpenSandbox runtime type used by the target server.
    runtime_type: str = "docker"  # docker | kubernetes

    # Remote Docker settings used when runtime_type == "docker".
    docker_host: Optional[str] = None
    docker_remote_host: Optional[str] = None

    # Kubernetes settings used when runtime_type == "kubernetes".
    namespace: Optional[str] = None
    context: Optional[str] = None
    kubeconfig: Optional[str] = None

    # Sandbox defaults.
    default_timeout_sec: int = 1800
    execd_port: int = 44772
    request_working_directory: Optional[str] = None


class MCPServiceConfig:
    """Single MCP service configuration with dynamic host resolution."""

    def __init__(
        self,
        sse_port: int,
        sse_host: Optional[str] = None,
        _parent_config: "OpenSageConfig" = None,
    ):
        """Initialize MCP service config.

        Args:
            sse_port (int): SSE server port
            sse_host (Optional[str]): Explicit SSE host. If None, will dynamically use parent config's default_host
            _parent_config ('OpenSageConfig'): Reference to parent OpenSageConfig for dynamic default_host"""
        self._sse_port = sse_port
        self._sse_host = sse_host  # None means "use default_host dynamically"
        self._parent_config = _parent_config

    @property
    def sse_port(self) -> int:
        """Get SSE port."""
        return self._sse_port

    @property
    def sse_host(self) -> str:
        """Get SSE host with dynamic resolution.

        Priority:
        1. If sse_host was explicitly set (not None), use that fixed value
        2. Otherwise, dynamically get from parent config's default_host
        3. Fallback to "127.0.0.1" if no parent config
        """
        # If explicitly set, use it (allows override)
        if self._sse_host is not None:
            return self._sse_host

        # Otherwise, dynamically get from parent config
        if (
            self._parent_config
            and hasattr(self._parent_config, "default_host")
            and self._parent_config.default_host
        ):
            return self._parent_config.default_host

        # Final fallback
        return "127.0.0.1"


@dataclass
class MCPConfig:
    """MCP servers configuration supporting multiple services."""

    services: Dict[str, MCPServiceConfig] = field(default_factory=dict)
    _parent_config: Optional["OpenSageConfig"] = field(default=None, repr=False)

    def set_parent_config(self, parent_config: "OpenSageConfig") -> None:
        """Set parent config reference for all services."""
        self._parent_config = parent_config
        for service in self.services.values():
            service._parent_config = parent_config

    def get_service_config(self, service_name: str) -> Optional[MCPServiceConfig]:
        """Get configuration for a specific MCP service."""
        return self.services.get(service_name)

    def add_service(self, name: str, config: MCPServiceConfig) -> None:
        """Add a new MCP service configuration."""
        self.services[name] = config
        if self._parent_config:
            config._parent_config = self._parent_config


@dataclass
class OpenSageConfig:
    """Complete SecAgentFramework configuration."""

    neo4j: Neo4jConfig = None
    sandbox: SandboxConfig = None
    llm: LLMConfig = None
    history: HistoryConfig = None
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    agent_ensemble: AgentEnsembleConfig = None
    build: BuildConfig = None
    mcp: MCPConfig = None
    memory: MemoryConfig = None
    task_name: str = None
    src_dir_in_sandbox: str = None
    agent_storage_path: Optional[str] = None
    load_dynamic_agents: bool = False
    default_host: str = None

    auto_cleanup: bool = True

    @classmethod
    def create_default(cls) -> "OpenSageConfig":
        """Create a default configuration from TOML file with environment variable overrides."""
        return cls.from_toml()

    @classmethod
    def from_toml(cls, config_path: Optional[str] = None) -> "OpenSageConfig":
        """Create configuration from TOML file with template variable expansion.

        Raises:
          FileNotFoundError: Raised when this operation fails."""
        if config_path is None:
            config_path = SRC_PATH / "templates/configs/default_config.toml"

        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load TOML data
        toml_data = toml.load(config_path)

        # Expand template variables
        expanded_data = _expand_template_variables(toml_data)

        # Preprocess special fields before dacite conversion
        cls._preprocess_config_data(expanded_data)

        # Use dacite to automatically convert dict to nested dataclasses
        config = from_dict(
            data_class=cls,
            data=expanded_data,
            config=DaciteConfig(
                type_hooks={
                    Path: lambda x: Path(x) if x else None,
                },
                cast=[int, str, float, bool],
                check_types=False,
            ),
        )

        # Set parent config references to enable dynamic host resolution
        if config.neo4j:
            config.neo4j._parent_config = config

        if config.mcp:
            config.mcp.set_parent_config(config)

        return config

    @classmethod
    def _preprocess_config_data(cls, data: dict) -> None:
        """Preprocess config data for special conversions before dacite.

        Modifies data dict in-place to handle:
        - agent_ensemble: list → set, comma-separated string → list
        - build: empty string → None
        - mcp: convert to MCPServiceConfig with proper initialization
        """
        # Agent Ensemble: list → set, comma-separated string → list
        if "agent_ensemble" in data:
            ensemble_data = data["agent_ensemble"]

            # Convert thread_safe_tools list to set
            if "thread_safe_tools" in ensemble_data:
                ensemble_data["thread_safe_tools"] = set(
                    ensemble_data["thread_safe_tools"]
                )

            # Handle comma-separated available_models_for_ensemble string
            if "available_models_for_ensemble" in ensemble_data:
                models_value = ensemble_data["available_models_for_ensemble"]
                if isinstance(models_value, str) and models_value.strip():
                    # Split comma-separated string and clean up whitespace
                    ensemble_data["available_models_for_ensemble"] = [
                        model.strip()
                        for model in models_value.split(",")
                        if model.strip()
                    ]
                elif not models_value or models_value == "":
                    ensemble_data["available_models_for_ensemble"] = []

        # Build: empty string → None
        if "build" in data:
            build_data = data["build"]
            for field in ["poc_dir", "compile_command", "run_command"]:
                if build_data.get(field) == "":
                    build_data[field] = None

        # Sandbox: empty string → None for optional paths
        if "sandbox" in data:
            sandbox_data = data["sandbox"] or {}
            for field in [
                "project_relative_shared_data_path",
                "absolute_shared_data_path",
                "host_shared_mem_dir",
            ]:
                if sandbox_data.get(field) == "":
                    sandbox_data[field] = None
            opensandbox_data = sandbox_data.get("opensandbox") or {}
            for field in [
                "domain",
                "api_key",
                "docker_host",
                "docker_remote_host",
                "namespace",
                "context",
                "kubeconfig",
                "request_working_directory",
            ]:
                if opensandbox_data.get(field) == "":
                    opensandbox_data[field] = None

            # Sandbox ports: only allow int/None or {host, port}.
            sandboxes_data = sandbox_data.get("sandboxes") or {}
            for sandbox_name, sandbox_cfg in sandboxes_data.items():
                ports_data = sandbox_cfg.get("ports")
                if not isinstance(ports_data, dict):
                    continue
                normalized_ports: Dict[str, Union[int, None, Dict[str, Any]]] = {}
                for container_port, host_binding in ports_data.items():
                    if isinstance(host_binding, int) or host_binding is None:
                        normalized_ports[container_port] = host_binding
                    elif isinstance(host_binding, dict):
                        if "host" not in host_binding or "port" not in host_binding:
                            raise ValueError(
                                f"Invalid ports[{container_port}] for sandbox "
                                f"'{sandbox_name}': dict binding must contain "
                                "'host' and 'port'."
                            )
                        normalized_ports[container_port] = {
                            "host": str(host_binding["host"]),
                            "port": int(host_binding["port"]),
                        }
                    else:
                        raise ValueError(
                            f"Invalid ports[{container_port}] for sandbox "
                            f"'{sandbox_name}': expected int, null, or "
                            "{{host, port}} dict."
                        )
                sandbox_cfg["ports"] = normalized_ports

        # MCP: Manually create MCPServiceConfig instances (can't be auto-converted)
        if "mcp" in data and "services" in data["mcp"]:
            services_data = data["mcp"]["services"]
            services = {}
            for name, service_config in services_data.items():
                services[name] = MCPServiceConfig(
                    sse_port=service_config.get("sse_port"),
                    sse_host=service_config.get("sse_host"),  # None = use default_host
                )
            data["mcp"] = MCPConfig(services=services)

    def get_sandbox_config(self, sandbox_type: str):
        """Get sandbox configuration for a specific type.

        Args:
            sandbox_type (str): Type of sandbox configuration to get
        Returns:
            ContainerConfig for the specified sandbox type, or None if not found
        """
        if self.sandbox:
            return self.sandbox.get_sandbox_config(sandbox_type)
        return None

    def get_llm_config(self, model_name: str):
        """Get LLM configuration for a specific model.

        Args:
            model_name (str): Name of the model configuration to get
        Returns:
            ModelConfig for the specified model, or None if not found
        """
        if self.llm and model_name in self.llm.model_configs:
            return self.llm.model_configs[model_name]
        return None

    def save_to_toml(self, toml_path: str) -> None:
        """Save configuration to TOML file.

        Args:
            toml_path (str): Path to save TOML file"""
        import inspect
        from dataclasses import fields, is_dataclass

        def to_dict(obj, seen=None):
            """Recursively convert dataclass to dict, excluding circular references."""
            if seen is None:
                seen = set()

            # Handle None and basic types first
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj

            # Avoid infinite recursion
            obj_id = id(obj)
            if obj_id in seen:
                return None

            if is_dataclass(obj):
                seen.add(obj_id)
                result = {}
                for field in fields(obj):
                    # Skip private fields (starting with _)
                    if field.name.startswith("_"):
                        continue
                    value = getattr(obj, field.name)
                    result[field.name] = to_dict(value, seen)
                seen.remove(obj_id)
                return result
            elif isinstance(obj, dict):
                return {k: to_dict(v, seen) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [to_dict(item, seen) for item in obj]
            elif isinstance(obj, Path):
                return str(obj)
            elif hasattr(obj, "__dict__") or hasattr(type(obj), "__dict__"):
                # Handle objects with properties and attributes
                seen.add(obj_id)
                result = {}

                # Get all public attributes including @property
                for name in dir(obj):
                    # Skip private/magic attributes
                    if name.startswith("_"):
                        continue

                    try:
                        # Check if it's a callable method (not property)
                        attr = getattr(type(obj), name, None)
                        if callable(attr) and not isinstance(attr, property):
                            # Skip methods
                            continue

                        # Get value (works for both regular attributes and @property)
                        value = getattr(obj, name)
                        if not callable(value):  # Skip bound methods
                            result[name] = to_dict(value, seen)
                    except Exception:
                        # Skip attributes that raise errors when accessed
                        continue

                seen.remove(obj_id)
                # Only return dict if we got valid results
                return result if result else None
            else:
                return obj

        config_dict = to_dict(self)

        toml_path = Path(toml_path)
        toml_path.parent.mkdir(parents=True, exist_ok=True)

        with open(toml_path, "w", encoding="utf-8") as f:
            toml.dump(config_dict, f)

    def copy(self) -> "OpenSageConfig":
        """Create a deep copy of this configuration."""
        import copy

        return copy.deepcopy(self)


def load_config_from_toml(config_path: Optional[str] = None) -> OpenSageConfig:
    """Convenience function to load configuration from TOML file."""
    return OpenSageConfig.from_toml(config_path)
