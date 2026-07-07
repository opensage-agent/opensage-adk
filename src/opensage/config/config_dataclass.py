"""
Configuration DataClass Definitions for OpenSage Framework

Defines all configuration dataclasses with default values and environment variable overrides.
"""

from __future__ import annotations

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
    """Lightweight config for container-backed sandboxes.

    This is an internal convenience type to keep sandbox code tidy and typed.
    It intentionally mirrors common Docker-compatible run options that we may support.
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
    cap_drop: List[str] = field(default_factory=list)
    devices: List[str] = field(default_factory=list)  # e.g., ["/dev/kvm"]
    gpus: Optional[str] = None  # e.g., "all" or "device=GPU-UUID"
    shm_size: Optional[str] = None
    mem_limit: Optional[str] = None
    # Docker-compatible CPU quota, equivalent to `docker run --cpus N`.
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

    # Raw args passthrough for Docker-compatible CLI backends (where applicable)
    docker_args: List[str] = field(default_factory=list)

    # Build configuration
    project_relative_dockerfile_path: Optional[str] = (
        None  # Path to Dockerfile relative to project root
    )
    agent_relative_dockerfile_path: Optional[str] = (
        None  # Path to Dockerfile relative to the agent directory
    )
    absolute_dockerfile_path: Optional[str] = None  # Path to Dockerfile as given
    build_args: Dict[str, str] = field(
        default_factory=dict
    )  # Build arguments for container image build

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
class SandboxPathsConfig:
    """Configurable paths inside the sandbox (or host, for local backend).

    Container backends mount volumes at these paths inside the container.
    The local backend can override them to point at host directories.
    """

    mem_root: str = "/mem"
    shared: str = "/shared"
    bash_tools: str = "/bash_tools"
    sandbox_scripts: str = "/sandbox_scripts"
    src: str = "/src"


@dataclass
class SandboxConfig:
    """Configuration for different sandbox types."""

    default_image: str = None
    sandboxes: Dict[str, ContainerConfig] = field(default_factory=dict)
    project_relative_shared_data_path: Optional[str] = None
    absolute_shared_data_path: Optional[str] = None
    # Global host bind mounts injected into every sandbox config as
    # "<abs_host_path>:<abs_container_path>:<ro|rw>" entries.
    mount_host_paths: List[str] = field(default_factory=list)
    backend: str = "native"
    paths: SandboxPathsConfig = field(default_factory=SandboxPathsConfig)
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

    @property
    def summarize_model(self) -> Optional[str]:
        """Get drop/summarize model name."""
        drop_config = self.model_configs.get("summarize")
        return drop_config.model_name if drop_config else None


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
class ModelEntry:
    """One model entry under ``config.model.available_models``.

    The ``model`` field doubles as the registry key (LLM-facing name) and the
    provider id passed to ``LiteLlm(model=...)``. No aliasing — duplicates raise.
    """

    model: str
    api_key_env: str
    base_url: Optional[str] = None


@dataclass
class ModelPrice:
    """Custom runtime budget price for one model.

    Prices are USD per 1M tokens. ``cached_per_million`` defaults to the
    prompt price when omitted.
    """

    model: str
    prompt_per_million: float
    completion_per_million: float
    cached_per_million: Optional[float] = None


@dataclass
class ModelRegistryConfig:
    """Model registry configuration (corresponds to the ``[model]`` TOML section).

    Two mutually-exclusive sources:
    - ``available_models``: declarative list parsed from TOML (simple LiteLlm setups)
    - ``models_python_file``: path to a Python file exporting ``models: list[BaseLlm]``
      (for cases that need custom LiteLlm kwargs like ``cache_control_injection_points``)

    Configuring both raises at registry load time.

    Optional override fields are framework-specific shortcuts and reference a
    registry key (i.e. the ``model`` field of one of ``available_models``).
    """

    available_models: List[ModelEntry] = field(default_factory=list)
    models_python_file: Optional[str] = None
    # Runtime budget shared by every model call in one OpenSage session.
    # 0 or omitted means unlimited.
    budget: float = 0.0
    prices: List[ModelPrice] = field(default_factory=list)

    # When set, ``Evaluation._prepare_agent`` walks the agent tree and
    # replaces every LlmAgent.model with the registered model under this
    # name. None = use the agents' declared models. Validated against the
    # LlmRegistry at the moment of use; not at config-load time, since the
    # registry is built from the same config.
    evaluation_replace_all_models_with_model_name: Optional[str] = None


@dataclass
class AutoInsertPromptFileConfig:
    """Path to a markdown file whose content is appended to every agent's
    instruction at invocation time (covers static + dynamic agents alike).

    Two mutually-exclusive sources:
    - ``agent_relative_path``: resolved against the agent_dir (the directory
      that contains this agent's ``agent.py`` / ``config.toml``) at load time.
    - ``absolute_path``: used as-is.

    Note: this differs from ``project_relative_dockerfile_path`` elsewhere,
    which is relative to the OpenSage repo root. We deliberately use a
    different prefix here to avoid that confusion.

    Both unset ⇒ a built-in default template under
    ``src/opensage/templates/auto_insert_prompts/default.md`` is used; the
    default describes long-term memory conventions and what kinds of
    knowledge to persist there.

    The resolved file's content is injected at runtime by the
    ``BaseAgent.run_async`` patch (with a marker so it is restored after
    the invocation finishes). Empty/missing file ⇒ nothing is injected.
    """

    agent_relative_path: Optional[str] = None
    absolute_path: Optional[str] = None


@dataclass
class BuildConfig:
    """Build and execution configuration."""

    poc_dir: Optional[str] = None
    compile_command: Optional[str] = None
    run_command: Optional[str] = None
    target_type: Optional[str] = None
    target_binary: Optional[str] = None


@dataclass
class FakeUserConfig:
    """Configuration for fake-user (user-simulator) callbacks.

    Corresponds to the ``[fake_user]`` TOML section.  Points at a Python
    file that exports an async function named ``fake_user`` with signature
    ``async (Session) -> str | None``.

    Relative paths are resolved against the agent directory.
    """

    python_file: Optional[str] = None
    """Path to a Python file exporting ``async def fake_user(session) -> str | None``."""


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
    """Complete OpenSage-ADK configuration."""

    neo4j: Neo4jConfig = None
    sandbox: SandboxConfig = None
    llm: LLMConfig = field(default_factory=LLMConfig)
    history: HistoryConfig = None
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    model: ModelRegistryConfig = field(default_factory=ModelRegistryConfig)
    auto_insert_prompt_file: AutoInsertPromptFileConfig = field(
        default_factory=AutoInsertPromptFileConfig
    )
    fake_user: FakeUserConfig = None
    build: BuildConfig = None
    mcp: MCPConfig = None
    task_name: str = None
    src_dir_in_sandbox: str = None
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

        # Resolve relative sandbox paths against the config file's directory
        if config.sandbox and config.sandbox.paths:
            paths = config.sandbox.paths
            for field in ("mem_root", "shared", "bash_tools", "sandbox_scripts", "src"):
                val = getattr(paths, field, None)
                if val and not os.path.isabs(val):
                    setattr(paths, field, str(Path.cwd() / val))

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
        - build: empty string → None
        - mcp: convert to MCPServiceConfig with proper initialization
        """
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
                dockerfile_fields = [
                    "absolute_dockerfile_path",
                    "agent_relative_dockerfile_path",
                    "project_relative_dockerfile_path",
                ]
                for field in dockerfile_fields:
                    if sandbox_cfg.get(field) == "":
                        sandbox_cfg[field] = None
                configured_dockerfile_fields = [
                    field for field in dockerfile_fields if sandbox_cfg.get(field)
                ]
                if len(configured_dockerfile_fields) > 1:
                    raise ValueError(
                        f"Sandbox '{sandbox_name}' configures multiple Dockerfile "
                        f"path fields: {', '.join(configured_dockerfile_fields)}. "
                        "Use exactly one of absolute_dockerfile_path, "
                        "agent_relative_dockerfile_path, or "
                        "project_relative_dockerfile_path."
                    )

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
