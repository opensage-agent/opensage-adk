"""
Configuration DataClass Definitions for AIgiSE Framework

Defines all configuration dataclasses with default values and environment variable overrides.
"""

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import toml

from aigise.utils.project_info import PROJECT_PATH


def _expand_template_variables(config_data: dict) -> dict:
    """Unified template variable expansion system.

    Rules:
    1. Top-level UPPERCASE variables automatically become template variables
    2. ${VAR_NAME} lookup order: environment variables → top-level variables → error
    3. Environment variables have highest priority and can override config defaults
    4. Undefined variables cause immediate error
    """

    # Deep copy to avoid modifying original data
    expanded_data = copy.deepcopy(config_data)

    # 1. Collect top-level UPPERCASE variables as template variables
    template_variables = {}
    for key, value in expanded_data.items():
        if key.isupper() and isinstance(value, (str, int, float, bool)):
            template_variables[key] = str(value)

    # 2. Define variable lookup function
    def get_variable_value(var_name: str) -> str:
        # First check environment variables (highest priority)
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
    neo4j_http_port: int = 7474  # Neo4j HTTP port
    _parent_config: Optional["AigiseConfig"] = field(default=None, repr=False)

    @property
    def uri(self) -> str:
        """Get Neo4j URI, dynamically constructed from parent config's default_host.

        Returns URI in format: neo4j://{default_host}:{bolt_port}
        Falls back to 127.0.0.1 if no default_host is set.
        """
        if self._parent_config and self._parent_config.default_host:
            host = self._parent_config.default_host
        else:
            host = "127.0.0.1"

        return f"neo4j://{host}:{self.bolt_port}"


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
    ports: Dict[str, Union[int, None, Tuple[str, int], List[int]]] = field(
        default_factory=dict
    )

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

    # Anything else
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxConfig:
    """Configuration for different sandbox types."""

    default_image: str = None
    sandboxes: Dict[str, ContainerConfig] = field(default_factory=dict)
    project_relative_shared_data_path: Optional[str] = None
    absolute_shared_data_path: Optional[str] = None
    backend: str = "native"

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

    max_tool_response_length: int = 1500
    max_history_summary_length: int = 60000


@dataclass
class AgentEnsembleConfig:
    """Agent ensemble configuration."""

    thread_safe_tools: Set[str] = field(default_factory=set)
    available_models_for_ensemble: List[str] = field(default_factory=list)


@dataclass
class BuildConfig:
    """Build and execution configuration."""

    poc_dir: Optional[str] = None
    compile_command: Optional[str] = None
    run_command: Optional[str] = None
    target_type: Optional[str] = None
    code_dir: Optional[str] = None


class MCPServiceConfig:
    """Single MCP service configuration with dynamic host resolution."""

    def __init__(
        self,
        sse_port: int,
        sse_host: Optional[str] = None,
        _parent_config: "AigiseConfig" = None,
    ):
        """Initialize MCP service config.

        Args:
            sse_port: SSE server port
            sse_host: Explicit SSE host. If None, will dynamically use parent config's default_host
            _parent_config: Reference to parent AigiseConfig for dynamic default_host
        """
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
        if self._parent_config and hasattr(self._parent_config, "default_host"):
            return self._parent_config.default_host

        # Final fallback
        return "127.0.0.1"


@dataclass
class MCPConfig:
    """MCP servers configuration supporting multiple services."""

    services: Dict[str, MCPServiceConfig] = field(default_factory=dict)
    _parent_config: Optional["AigiseConfig"] = field(default=None, repr=False)

    def set_parent_config(self, parent_config: "AigiseConfig") -> None:
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
class AigiseConfig:
    """Complete SecAgentFramework configuration."""

    neo4j: Neo4jConfig = None
    sandbox: SandboxConfig = None
    llm: LLMConfig = None
    history: HistoryConfig = None
    agent_ensemble: AgentEnsembleConfig = None
    build: BuildConfig = None
    mcp: MCPConfig = None
    task_name: str = None
    agent_storage_path: Optional[str] = None
    default_host: str = None

    @classmethod
    def create_default(cls) -> "AigiseConfig":
        """Create a default configuration from TOML file with environment variable overrides."""
        return cls.from_toml()

    @classmethod
    def from_toml(cls, config_path: Optional[str] = None) -> "AigiseConfig":
        """Create configuration from TOML file with template variable expansion."""
        if config_path is None:
            config_path = (
                PROJECT_PATH
                / "src"
                / "aigise"
                / "templates"
                / "configs"
                / "default_config.toml"
            )

        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load TOML data
        toml_data = toml.load(config_path)

        # Expand template variables
        expanded_data = _expand_template_variables(toml_data)

        # Helper function to create config objects
        def create_config(section_name: str, config_class, **kwargs):
            if section_name not in expanded_data:
                return None
            data = expanded_data[section_name]
            return config_class(**data, **kwargs)

        # Create configuration objects from expanded data
        neo4j = create_config("neo4j", Neo4jConfig)
        history = create_config("history", HistoryConfig)

        # Sandbox (needs special handling for nested ContainerConfigs)
        sandbox = None
        if "sandbox" in expanded_data:
            sandbox_data = expanded_data["sandbox"]
            sandboxes = {
                name: ContainerConfig(**config)
                for name, config in sandbox_data.get("sandboxes", {}).items()
            }

            sandbox = SandboxConfig(
                default_image=sandbox_data.get("default_image"),
                sandboxes=sandboxes,
                project_relative_shared_data_path=sandbox_data.get(
                    "project_relative_shared_data_path"
                ),
                absolute_shared_data_path=sandbox_data.get("absolute_shared_data_path"),
                backend=sandbox_data.get("backend", "native"),
            )

        # LLM (needs special handling for nested ModelConfigs)
        llm = None
        if "llm" in expanded_data:
            model_configs = {
                name: ModelConfig(**config)
                for name, config in expanded_data["llm"]
                .get("model_configs", {})
                .items()
            }
            llm = LLMConfig(model_configs=model_configs)

        # Agent Ensemble (needs list to set conversion and comma-separated string to list)
        agent_ensemble = None
        if "agent_ensemble" in expanded_data:
            ensemble_data = dict(expanded_data["agent_ensemble"])

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
                        if model.strip()  # Filter out empty strings
                    ]
                elif not models_value or models_value == "":
                    ensemble_data["available_models_for_ensemble"] = []

            agent_ensemble = AgentEnsembleConfig(**ensemble_data)

        # Build (needs empty string to None conversion)
        build = None
        if "build" in expanded_data:
            build_data = dict(expanded_data["build"])
            for field in ["poc_dir", "compile_command", "run_command"]:
                if build_data.get(field) == "":
                    build_data[field] = None
            build = BuildConfig(**build_data)

        # MCP (needs special handling for nested MCPServiceConfigs)
        mcp = None
        if "mcp" in expanded_data:
            services = {
                name: MCPServiceConfig(
                    sse_port=service_config.get("sse_port"),
                    sse_host=service_config.get(
                        "sse_host"
                    ),  # None if not specified -> will use default_host dynamically
                )
                for name, service_config in expanded_data["mcp"]
                .get("services", {})
                .items()
            }
            mcp = MCPConfig(services=services)

        # Create and return the complete configuration
        config = cls(
            neo4j=neo4j,
            sandbox=sandbox,
            llm=llm,
            history=history,
            agent_ensemble=agent_ensemble,
            build=build,
            mcp=mcp,
            task_name=expanded_data.get("task_name"),
            agent_storage_path=expanded_data.get("agent_storage_path"),
            default_host=expanded_data.get("default_host", None),
        )

        # Set parent config references to enable dynamic host resolution
        if config.neo4j:
            config.neo4j._parent_config = config

        if config.mcp:
            config.mcp.set_parent_config(config)

        return config

    def get_sandbox_config(self, sandbox_type: str):
        """Get sandbox configuration for a specific type.

        Args:
            sandbox_type: Type of sandbox configuration to get

        Returns:
            ContainerConfig for the specified sandbox type, or None if not found
        """
        if self.sandbox:
            return self.sandbox.get_sandbox_config(sandbox_type)
        return None

    def get_llm_config(self, model_name: str):
        """Get LLM configuration for a specific model.

        Args:
            model_name: Name of the model configuration to get

        Returns:
            ModelConfig for the specified model, or None if not found
        """
        if self.llm and model_name in self.llm.model_configs:
            return self.llm.model_configs[model_name]
        return None

    def save_to_toml(self, toml_path: str) -> None:
        """Save configuration to TOML file.

        Args:
            toml_path: Path to save TOML file
        """
        from dataclasses import asdict

        config_dict = asdict(self)

        toml_path = Path(toml_path)
        toml_path.parent.mkdir(parents=True, exist_ok=True)

        with open(toml_path, "w", encoding="utf-8") as f:
            toml.dump(config_dict, f)

    def copy(self) -> "AigiseConfig":
        """Create a deep copy of this configuration."""
        import copy

        return copy.deepcopy(self)


def load_config_from_toml(config_path: Optional[str] = None) -> AigiseConfig:
    """Convenience function to load configuration from TOML file."""
    return AigiseConfig.from_toml(config_path)
