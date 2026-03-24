from __future__ import annotations

import abc
import asyncio
import datetime
import importlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import datasets
import fire
import google.adk as adk
import jsonpickle
import litellm
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App
from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from tqdm import tqdm

from opensage import get_opensage_session
from opensage.features.opensage_in_memory_session_service import (
    OpenSageInMemorySessionService,
)
from opensage.plugins import load_plugins
from opensage.session.opensage_session import OpenSageSession
from opensage.toolbox.sandbox_requirements import collect_sandbox_dependencies
from opensage.utils.bash_tools_staging import compute_bash_tools_top_roots
from opensage.utils.project_info import PROJECT_PATH, SRC_PATH

# TODO: incompatibility between litellm and multiple async event loops
litellm.disable_streaming_logging = True

logger = logging.getLogger(__name__)

# Registry for Evaluation subclasses
_EVALUATION_REGISTRY: dict[str, type[Evaluation]] = {}


def get_evaluation_class(name: str) -> type[Evaluation] | None:
    """Get registered Evaluation class by name (case-insensitive).

    Args:
        name (str): Benchmark name (e.g., "secodeplt", "cybergym")
    Returns:
        type[Evaluation] | None: Evaluation subclass or None if not found
    """
    return _EVALUATION_REGISTRY.get(name.lower())


def list_evaluations() -> list[str]:
    """List all registered evaluation names."""
    return list(_EVALUATION_REGISTRY.keys())


def _run_sample_in_process(evaluation_instance: Evaluation, sample: dict) -> dict:
    """Wrapper function to run a sample in a separate process.

        This function must be defined at module level for pickling.

        Args:
            evaluation_instance (Evaluation): The Evaluation instance
            sample (dict): Sample dict from dataset

    Raises:
      RuntimeError: Raised when this operation fails.
        Returns:
            dict: Result dictionary from _generate_one
    """
    # Create task from sample
    task = evaluation_instance._create_task(sample)

    # Re-configure litellm in subprocess to avoid event loop issues
    import litellm

    litellm.disable_streaming_logging = True
    litellm.success_callback = []
    litellm.failure_callback = []

    # Configure retry settings in subprocess
    litellm.num_retries = evaluation_instance.llm_retry_count
    litellm.request_timeout = evaluation_instance.llm_retry_timeout

    # Ensure task output directory exists before logging to files.
    task_output_dir = Path(task.output_dir)
    task_output_dir.mkdir(parents=True, exist_ok=True)

    # Configure task-specific logging with two files + terminal
    # File 1: DEBUG level (all details)
    debug_log = task_output_dir / "execution_debug.log"
    debug_handler = logging.FileHandler(debug_log, mode="w")
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # File 2: INFO level (important info)
    info_log = task_output_dir / "execution_info.log"
    info_handler = logging.FileHandler(info_log, mode="w")
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Accept all levels
    root_logger.addHandler(debug_handler)
    root_logger.addHandler(info_handler)
    added_handlers = [debug_handler, info_handler]

    # Configure terminal log level in subprocess
    # This ensures the subprocess respects the parent's log_level setting
    terminal_log_level = evaluation_instance._terminal_log_level
    logging.basicConfig(level=terminal_log_level)
    for logger_name in list(logging.Logger.manager.loggerDict.keys()) + [""]:
        logger_obj = logging.getLogger(logger_name)
        for handler in logger_obj.handlers[:]:
            if (
                isinstance(handler, logging.StreamHandler)
                and handler.stream == sys.stderr
            ):
                handler.setLevel(terminal_log_level)

    try:
        # Run async code in this process's event loop
        return asyncio.run(evaluation_instance._generate_one(task))
    except Exception as e:
        # Convert all exceptions to RuntimeError to ensure pickle-ability
        # This prevents ProcessPool from breaking when serializing exceptions
        import traceback

        error_msg = (
            f"{e.__class__.__module__}.{e.__class__.__name__}: {str(e)}\n\n"
            f"Original traceback:\n{traceback.format_exc()}"
        )
        raise RuntimeError(error_msg) from None
    finally:
        # Clean up handlers to avoid duplicate logs and open file handles
        for h in added_handlers:
            try:
                root_logger.removeHandler(h)
            except Exception:
                pass
            try:
                h.close()
            except Exception:
                pass


@dataclass
class EvaluationTask:
    """Represents a single evaluation task instance.

    This encapsulates all data needed to run a single evaluation sample,
    making it easy to pass around and for subclasses to extend with
    custom fields.
    """

    id: str
    """Unique task identifier"""

    sample: dict
    """Original sample from dataset"""

    first_user_message: str
    """First user message for the agent"""

    output_dir: str
    """Local output directory for this task"""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique OpenSage session ID"""

    # For sandbox
    initial_data_dir: str | None = None
    """Path to input data to be copied into workspace"""

    sandbox_cache_dir: str | None = None
    """Sandbox cache directory"""

    export_dir_in_sandbox: str | list[str] | None = None
    """Optional sandbox dir(s) to export"""

    model: str | BaseLlm | None = None
    """Optional model override (BaseLlm instance or string model name)"""

    @property
    def opensage_session(self) -> OpenSageSession:
        """Get or create OpenSage session for this task."""
        return get_opensage_session(self.session_id, create_if_missing=False)


@dataclass(kw_only=True)
class Evaluation(abc.ABC):
    """Base class for all evaluation benchmarks.

    Subclasses are automatically registered and can be looked up by name
    using get_evaluation_class(). Registration uses the lowercase class name.

    Example:
        class SeCodePLT(Evaluation):  # Registered as "secodeplt"
            ...

        # Later, retrieve with:
        cls = get_evaluation_class("secodeplt")
    """

    # Agent (Required)
    agent_dir: str
    """directory containing agent.py with mk_agent function"""

    # Dataset (Required)
    dataset_path: str
    """HuggingFace dataset name (e.g., "org/dataset") or local path"""

    # Evaluation
    name: str = "base_evaluation"
    output_dir: str | None = None
    """If None, will create by default as evals/{name}/{timestamp}"""

    max_workers: int = 6

    run_until_explicit_finish: bool = False

    runner_type: str = "native"
    """Execution backend: "native" (threading/multiprocessing) or "ray" (distributed)"""

    log_level: str = "INFO"
    """Console log level: DEBUG, INFO, WARNING, ERROR, CRITICAL"""

    # TODO: better priority system for which model to use
    use_config_model: bool = False
    """Override the model use the model specified in the config file if True"""

    # Agent

    config_template_path: str | None = None
    """Search in agent_dir if not provided"""

    max_llm_calls: int = 100

    llm_retry_count: int = 3
    """Number of retries for LLM API calls (e.g., for 502 errors), currently only applies to LiteLLM"""

    llm_retry_timeout: int = 30
    """Timeout in seconds for each LLM request, currently only applies to LiteLLM"""

    neo4j_logging: bool = False
    """Whether to enable Neo4j logging for this run"""

    # Dataset
    dataset_split: str = "train"

    # Sandbox and execution
    use_sandbox_cache: bool = False
    """Load/cache sandboxes if True"""

    sandbox_cache_dir: str | None = None

    def __init_subclass__(cls, **kwargs):
        """Auto-register Evaluation subclasses."""
        super().__init_subclass__(**kwargs)
        # Register by lowercase class name
        name = cls.__name__.lower()
        _EVALUATION_REGISTRY[name] = cls
        logger.debug(f"Registered evaluation: {name} -> {cls.__name__}")

    def __post_init__(self) -> None:
        # Validate and convert log level
        self.log_level = self.log_level.upper()
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_levels:
            raise ValueError(
                f"Invalid log_level '{self.log_level}'. Must be one of: {valid_levels}"
            )
        self._terminal_log_level = getattr(logging, self.log_level)

        # Configure terminal log level immediately
        logging.basicConfig(
            level=self._terminal_log_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Update existing handlers to use the configured log level
        for handler in logging.getLogger().handlers[:]:
            if (
                isinstance(handler, logging.StreamHandler)
                and handler.stream == sys.stderr
            ):
                handler.setLevel(self._terminal_log_level)

        # Configure LiteLLM global retry settings
        litellm.num_retries = self.llm_retry_count
        litellm.request_timeout = self.llm_retry_timeout
        logger.info(
            f"Configured LiteLLM retry: num_retries={self.llm_retry_count}, "
            f"request_timeout={self.llm_retry_timeout}"
        )
        logger.info(f"Terminal log level set to: {self._terminal_log_level}")

        if not self.output_dir:
            self.output_dir = str(
                PROJECT_PATH
                / "evals"
                / self.__class__.__name__.lower()
                / datetime.datetime.now().strftime("%y%m%d_%H%M%S")
            )
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        else:
            if Path(self.output_dir).exists():
                flag = (
                    input(f"{self.output_dir} already exists, continue? (y/n): ")
                    .strip()
                    .lower()
                )
                if flag != "y" and flag != "" and flag != "yes":
                    print("Exiting...")
                    exit(0)
            else:
                Path(self.output_dir).mkdir(parents=True)

        # Create master log handler - records all logs from start to finish
        # Note: Use local variable (not self._master_handler) to avoid pickle issues with multiprocessing
        master_log = Path(self.output_dir) / "evaluation_master.log"
        master_handler = logging.FileHandler(master_log, mode="w")
        master_handler.setLevel(logging.INFO)
        master_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logging.getLogger().addHandler(master_handler)
        logger.info(f"Master log handler created: {master_log}")

        # Log and save evaluation parameters
        self._log_and_save_parameters()

        # Load mk_agent function from agent_path
        self._mk_agent_original = self._load_mk_agent()

    def _log_and_save_parameters(self) -> None:
        """Log and save evaluation parameters to output directory."""

        # Collect all dataclass fields
        params = {}
        for field in fields(self):
            value = getattr(self, field.name)
            # Convert Path objects to strings
            if isinstance(value, Path):
                params[field.name] = str(value)
            elif value is not None:
                params[field.name] = value

        # Add timestamp
        params["timestamp"] = datetime.datetime.now().isoformat()
        params["evaluation_class"] = self.__class__.__name__

        # Add git commit information
        try:
            git_commit = (
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.output_dir.parent.parent,
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            git_branch = (
                subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=self.output_dir.parent.parent,
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            params["git_commit"] = git_commit
            params["git_branch"] = git_branch
        except Exception:
            params["git_commit"] = "unknown"
            params["git_branch"] = "unknown"

        # Log parameters
        logger.warning("=" * 80)
        logger.warning("Evaluation parameters:")
        logger.warning("=" * 80)
        for key, value in params.items():
            logger.warning(f"  {key:30s}: {value}")
        logger.warning("=" * 80)

        # Save to output directory
        params_file = Path(self.output_dir) / "eval_params.json"
        with open(params_file, "w") as f:
            json.dump(params, f, indent=2)
        logger.warning(f"Parameters saved to: {params_file}")

    def _save_cost_info(
        self,
        task: EvaluationTask,
        session: Session,
        *,
        num_llm_calls: int,
    ) -> None:
        """Calculate and save cost information for the task.

        Args:
            task (EvaluationTask): EvaluationTask instance
            session (Session): ADK Session with events"""
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0

        for event in session.events:
            if hasattr(event, "usage_metadata") and event.usage_metadata:
                usage = event.usage_metadata

                if hasattr(usage, "prompt_token_count"):
                    total_input_tokens += usage.prompt_token_count or 0
                if hasattr(usage, "candidates_token_count"):
                    total_output_tokens += usage.candidates_token_count or 0
                if hasattr(usage, "cached_content_token_count"):
                    total_cached_tokens += usage.cached_content_token_count or 0

        # Determine model name for logging
        model_name = "agent_default"
        if self.use_config_model and task.opensage_session:
            main_model_config = task.opensage_session.config.llm.model_configs.get(
                "main"
            )
            if main_model_config:
                model_name = main_model_config.model_name

        cost_info = {
            "session_id": task.session_id,
            "task_name": task.id,
            "model": model_name,
            "use_config_model": self.use_config_model,
            "timestamp": datetime.datetime.now().isoformat(),
            "token_usage": {
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_cached_tokens": total_cached_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            },
            "num_llm_calls": num_llm_calls,
        }

        logger.warning("=" * 80)
        logger.warning(f"Cost info for session {task.session_id}:")
        logger.warning(f"  Model: {model_name}")
        logger.warning(f"  LLM calls: {num_llm_calls}")
        logger.warning(f"  Input tokens: {total_input_tokens:,}")
        logger.warning(f"  Output tokens: {total_output_tokens:,}")
        logger.warning(f"  Cached tokens: {total_cached_tokens:,}")
        logger.warning(f"  Total tokens: {total_input_tokens + total_output_tokens:,}")
        logger.warning("=" * 80)

        # Ensure output directory exists
        output_dir = Path(task.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        cost_file = output_dir / "cost_info.json"
        with open(cost_file, "w") as f:
            json.dump(cost_info, f, indent=2)
        logger.warning(f"Cost info saved to: {cost_file}")

    def _load_mk_agent(self) -> callable:
        """Load mk_agent function from agent directory.

        Expects agent_dir to contain agent.py with mk_agent function.
        Supports both relative and absolute paths.

        Example: agent_dir = "examples/agents/poc_agent"
                 -> will load from <cwd>/examples/agents/poc_agent/agent.py

        Args:
            agent_dir: Directory containing agent.py with mk_agent function.
                      Can be relative (resolved from cwd) or absolute path.
        Returns:
            callable: mk_agent function

        Raises:
            ValueError: If agent.py or mk_agent not found
        """
        # Convert to absolute path
        agent_path = Path(self.agent_dir).resolve()

        if not agent_path.exists():
            raise ValueError(
                f"Agent directory not found: {self.agent_dir}\nResolved to: {agent_path}"
            )

        if not agent_path.is_dir():
            raise ValueError(f"Agent path is not a directory: {agent_path}")

        agent_file = agent_path / "agent.py"
        if not agent_file.exists():
            raise ValueError(
                f"agent.py not found in {agent_path}. Expected file: {agent_file}"
            )

        # Add parent directory to sys.path for module imports
        # https://github.com/google/adk-python/blob/223d9a7ff52d8da702f1f436bd22e94ad78bd5da/src/google/adk/cli/utils/agent_loader.py#L216
        parent_dir = str(agent_path.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        # Import as module: {agent_name}.agent
        agent_name = agent_path.name

        try:
            agent_module = importlib.import_module(f"{agent_name}.agent")
        except ModuleNotFoundError as e:
            raise ValueError(
                f"Failed to import {agent_name}.agent from {agent_path}. Error: {e}"
            ) from e

        # Get mk_agent function
        mk_agent = getattr(agent_module, "mk_agent", None)
        if mk_agent is None:
            raise ValueError(
                f"No `mk_agent` function found in {agent_file}. "
                f"Available: {[name for name in dir(agent_module) if not name.startswith('_')]}"
            )

        logger.debug(f"Loaded mk_agent from {agent_file}")
        return mk_agent

    def _replace_agent_models_recursive(
        self, agent: BaseAgent, model: BaseLlm, visited: set[str] | None = None
    ) -> None:
        """Recursively replace model for all agents in the agent tree.

        This method traverses the entire agent tree (sub_agents and agent_tools)
        and replaces the model of all LlmAgent instances.

        Args:
            agent (BaseAgent): Root agent to start replacement
            model (BaseLlm): BaseLlm instance to replace with (LiteLlm, ArealLlm, etc.)
            visited (set[str] | None): Set of visited agent names to avoid infinite loops"""
        if visited is None:
            visited = set()

        # Avoid infinite recursion
        if agent.name in visited:
            return
        visited.add(agent.name)

        # Get model name for logging (handle different model types)
        model_name = getattr(model, "model_name", None) or getattr(
            model, "model", "unknown"
        )

        # Replace model if agent is LlmAgent
        if isinstance(agent, LlmAgent):
            try:
                agent.model = model
                logger.debug(
                    f"Replaced model for agent '{agent.name}', current model: {model_name}"
                )
            except Exception:
                # Fallback for frozen Pydantic models
                object.__setattr__(agent, "model", model)
                logger.debug(
                    f"Replaced model for frozen agent '{agent.name}' using setattr"
                )

        # Recursively replace in sub_agents
        if hasattr(agent, "sub_agents") and agent.sub_agents:
            for sub_agent in agent.sub_agents:
                self._replace_agent_models_recursive(sub_agent, model, visited)

        # Recursively replace in agent_tools
        if hasattr(agent, "tools") and agent.tools:
            for tool in agent.tools:
                if isinstance(tool, AgentTool):
                    self._replace_agent_models_recursive(tool.agent, model, visited)

    def _get_dataset(self) -> datasets.Dataset:
        if Path(self.dataset_path).exists():
            if Path(self.dataset_path).is_dir():
                dataset = datasets.load_from_disk(str(self.dataset_path))
            else:
                dataset = datasets.load_dataset(
                    "json", data_files=str(self.dataset_path), split="train"
                )
        else:
            dataset = datasets.load_dataset(self.dataset_path, split=self.dataset_split)
        return dataset

    def _create_task(
        self, sample: dict, model: str | BaseLlm | None = None
    ) -> EvaluationTask:
        """Create task instance from sample.

        Subclasses can override this to create custom task types with
        additional fields.

        Args:
            sample (dict): Sample dict from dataset
        Returns:
            EvaluationTask: EvaluationTask instance (or subclass)

        Example::
            @dataclass
            class MyTask(EvaluationTask):
                custom_field: str

            class MyEvaluation(Evaluation):
                def _create_task(self, sample: dict) -> MyTask:
                    base_task = super()._create_task(sample)
                    return MyTask(
                        **asdict(base_task),
                        custom_field=sample["custom"]
                    )
        """
        task_id = self._get_task_id(sample)
        task = EvaluationTask(
            id=task_id,
            sample=sample,
            initial_data_dir=self._get_initial_data_dir(sample),
            first_user_message=self._get_first_user_message(sample),
            output_dir=str(Path(self.output_dir) / task_id),
            sandbox_cache_dir=self._get_sandbox_cache_dir(sample),
            export_dir_in_sandbox=self._get_export_dir_in_sandbox(sample),
            model=model,
        )
        return task

    def _get_sandbox_cache_dir(self, sample: dict) -> str:
        """Get sandbox cache directory for this sample.

        Default: {self.sandbox_cache_dir}/{task_name}
        Override if you need custom logic.

        Args:
            sample (dict): Sample dict from dataset
        Returns:
            str: Path to cache directory, or empty string if caching is disabled
        """
        if not self.use_sandbox_cache:
            return ""  # Return empty string to indicate no caching
        task_id = self._get_task_id(sample)
        return str(Path(self.sandbox_cache_dir) / task_id)

    def _prepare_agent(self, task: EvaluationTask) -> BaseAgent | None:
        """Prepare agent with the correct model.

        Model selection priority:
        1. task.model (RL integration or explicit override)
        2. self.use_config_model (from config file)
        3. Agent's default model (specified in mk_agent)
        """
        # Determine which model to use
        model_to_use = None
        model_source = "agent default"

        if task.model is not None:
            # Priority 1: task.model (RL integration or explicit override)
            model_to_use = task.model
            model_source = "task.model (RL integration)"
        elif self.use_config_model:
            # Priority 2: config model
            opensage_session = task.opensage_session
            if opensage_session and opensage_session.config.llm:
                main_model_config = opensage_session.config.llm.model_configs.get(
                    "main"
                )
                if main_model_config:
                    # Convert config to dict and extract all parameters
                    config_dict = (
                        main_model_config.model_dump()
                        if hasattr(main_model_config, "model_dump")
                        else vars(main_model_config)
                    )

                    # LiteLlm expects 'model' not 'model_name'
                    if "model_name" in config_dict:
                        config_dict["model"] = config_dict.pop("model_name")

                    # Create LiteLlm instance with all config parameters
                    model_to_use = LiteLlm(**config_dict)
                    model_source = (
                        f"config model '{config_dict.get('model', 'unknown')}'"
                    )

        # Try to create agent with model parameter
        try:
            import inspect

            sig = inspect.signature(self._mk_agent_original)
            if "model" in sig.parameters:
                # mk_agent supports model parameter - use it
                agent = self._mk_agent_original(
                    opensage_session_id=task.session_id, model=model_to_use
                )
                logger.warning(
                    f"Created agent with model from {model_source} (session {task.session_id})"
                )
            else:
                # mk_agent doesn't support model parameter - fallback to replacement
                agent = self._mk_agent_original(opensage_session_id=task.session_id)
                if model_to_use is not None:
                    self._replace_agent_models_recursive(agent, model_to_use)
                    logger.warning(
                        f"Replaced agent models with {model_source} via recursive replacement "
                        f"(session {task.session_id})"
                    )
                else:
                    logger.warning(
                        f"Using agent's default model (session {task.session_id})"
                    )
        except Exception as e:
            # Fallback: try without model parameter
            logger.warning(
                f"Failed to create agent with model parameter, falling back: {e}"
            )
            agent = self._mk_agent_original(opensage_session_id=task.session_id)
            if model_to_use is not None:
                self._replace_agent_models_recursive(agent, model_to_use)

        return agent

    async def _generate_one(self, task: EvaluationTask) -> dict:
        """Generate result for a single task with automatic sandbox and Neo4j management.

                Args:
                    task (EvaluationTask): EvaluationTask instance with all task data

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    dict: Dictionary with sample results and metadata
        """
        # Ensure output directory exists immediately (for logging)
        output_path = Path(task.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        try:
            logger.info(f"Starting task {task.id} (session: {task.session_id})")

            self._before_generate_one_callback(task)

            # === 0. Get opensage_session ===
            self._register_opensage_session(task)

            # === 1. Prepare Environment ===
            await self._prepare_environment(task)

            # === 2. Prepare Agent ===
            agent = self._prepare_agent(task)

            # === 2.5 Save Config ===
            config_output_path = Path(task.output_dir) / "config_used.toml"
            task.opensage_session.config.save_to_toml(str(config_output_path))
            logger.warning(f"Config saved to {config_output_path}")

            # === 3. Run Agent ===
            session = await self._run_agent(task, agent)

            # === 4. Collect Outputs ===
            output_info = await self._collect_outputs(task, session)

            # === 5. Cleanup ===
            try:
                task.opensage_session.cleanup()
                logger.warning(f"Cleanup completed for session: {task.session_id}")
            except Exception as e:
                logger.warning(f"Cleanup failed for session {task.session_id}: {e}")

            logger.info(f"Task {task.id} completed successfully")
            return output_info

        except Exception as e:
            # Log exception details
            logger.error(f"Task {task.id} failed with exception: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")

            # Save error information to file
            error_file = output_path / "error.json"
            with open(error_file, "w") as f:
                json.dump(
                    {
                        "task_name": task.id,
                        "session_id": task.session_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "traceback": traceback.format_exc(),
                        "timestamp": datetime.datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )

            # Try to cleanup even on error
            try:
                if task.opensage_session:
                    task.opensage_session.cleanup()
            except Exception as cleanup_error:
                logger.error(f"Cleanup after error failed: {cleanup_error}")

            raise

    def _replace_template_variables_in_config(
        self, config_path: str, template_variables: dict
    ) -> None:
        # TODO: probably merge to the config loading code
        with open(config_path, "r") as f:
            content = f.read()
        for var_name, var_value in template_variables.items():
            pattern = rf"\${{\s*{re.escape(var_name)}\s*}}"
            content = re.sub(pattern, str(var_value), content)
        with open(config_path, "w") as f:
            f.write(content)

    def customized_modify_and_save_results(
        self,
        *,
        results: list | None,
        failed_samples: list[str] | None,
        mode: str,
    ) -> None:
        """Hook for subclasses to post-process and persist aggregated results.

        Args:
            results (list | None): Successful sample outputs collected during generation.
            failed_samples (list[str] | None): Task identifiers that failed to complete.
            mode (str): Execution mode that produced the results (multiprocess, threaded,
                or single_thread)."""
        _ = (results, failed_samples, mode)

    def _register_opensage_session(self, task: EvaluationTask):
        """Register OpenSageSession with task-specific config.

        Args:
            task (EvaluationTask): EvaluationTask containing session_id and config_template_path
        Returns:
            None
        """
        # Copy config template to a temporary file for this task
        config_template = Path(self.config_template_path)
        temp_dir = tempfile.mkdtemp(prefix=f"opensage_{task.session_id}_")
        temp_config_path = Path(temp_dir) / config_template.name
        shutil.copy(config_template, temp_config_path)
        template_variables = self._get_config_template_variables(task)
        self._replace_template_variables_in_config(temp_config_path, template_variables)

        get_opensage_session(task.session_id, config_path=temp_config_path)

        # clean up temp config file
        shutil.rmtree(temp_dir, ignore_errors=True)

    async def _prepare_environment(self, task: EvaluationTask) -> None:
        """Prepare environment: session, config, volumes, sandboxes.

        Args:
            task (EvaluationTask): EvaluationTask instance with all task data"""
        opensage_session = task.opensage_session

        # 1. Configure Neo4j logging
        from opensage.features.agent_history_tracker import (
            disable_neo4j_logging,
            enable_neo4j_logging,
            is_neo4j_logging_enabled,
        )

        if self.neo4j_logging:
            if not is_neo4j_logging_enabled():
                enable_neo4j_logging()
                logger.warning("Neo4j logging enabled (neo4j_logging=True).")
        else:
            if is_neo4j_logging_enabled():
                disable_neo4j_logging()
                logger.warning("Neo4j logging disabled (neo4j_logging=False).")

        dummy_agent = self._mk_agent_original(opensage_session_id=task.session_id)

        # Collect sandbox dependencies from agent
        sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
        tools_top_roots = compute_bash_tools_top_roots(dummy_agent)

        # Strong behavior:
        # - If dependencies mention sandboxes that are not configured, drop them and warn.
        # - If config contains sandboxes that are not needed, remove them and warn.
        if (
            opensage_session.config.sandbox
            and opensage_session.config.sandbox.sandboxes
        ):
            configured_sandboxes = set(opensage_session.config.sandbox.sandboxes.keys())

            missing_in_config = sorted(
                sb for sb in sandbox_dependencies if sb not in configured_sandboxes
            )
            if missing_in_config:
                sandbox_dependencies = set(sandbox_dependencies) - set(
                    missing_in_config
                )
                logger.warning(
                    "Removed sandbox dependencies not present in config: %s. "
                    "Configured sandboxes: %s",
                    missing_in_config,
                    sorted(configured_sandboxes),
                )

            sandboxes_to_remove = [
                sandbox_type
                for sandbox_type in opensage_session.config.sandbox.sandboxes.keys()
                if sandbox_type not in sandbox_dependencies
            ]
            for sandbox_type in sandboxes_to_remove:
                del opensage_session.config.sandbox.sandboxes[sandbox_type]
                logger.warning(
                    f"Removed unused sandbox '{sandbox_type}' from config "
                    f"(not in agent dependencies: {sandbox_dependencies})"
                )

        # 3. Load cached sandboxes
        unfound_cached_sandboxes = []
        if self.use_sandbox_cache:
            unfound_cached_sandboxes = (
                opensage_session.sandboxes.load_sandbox_caches_to_config()
            )

        # 4. Initialize shared volumes
        opensage_session.sandboxes.initialize_shared_volumes(
            tools_top_roots=tools_top_roots,
            enabled_skills=getattr(dummy_agent, "_enabled_skills", None),
        )

        # 5. Launch all sandboxes (create containers only, not initialized yet)
        await opensage_session.sandboxes.launch_all_sandboxes()

        await self._before_initialize_callback(task)

        # 6. Initialize all sandboxes
        # continue_on_error=True is important for the evaluation to continue even if some sandboxes fail to initialize
        await opensage_session.sandboxes.initialize_all_sandboxes(
            continue_on_error=True
        )

        await self._after_initialize_callback(task)

        # 7. Cache sandboxes if needed
        if self.use_sandbox_cache and unfound_cached_sandboxes:
            opensage_session.sandboxes.cache_sandboxes(cache_dir=task.sandbox_cache_dir)

    async def _run_agent(self, task: EvaluationTask, agent: adk.Agent) -> Session:
        """Run agent with the given prompt.

        Args:
            task (EvaluationTask): EvaluationTask instance with all task data
            agent (adk.Agent): Pre-configured agent instance
        Returns:
            Session: ADK Session object with execution history
        """
        # 2. Create runner and session service
        user_id = self.output_dir.replace("/", "_")
        app_name = Path(self.agent_dir).resolve().parent.name
        session_service = OpenSageInMemorySessionService()
        enabled_plugins = []
        plugin_params = {}
        if task.opensage_session and getattr(task.opensage_session, "config", None):
            plugins_cfg = getattr(task.opensage_session.config, "plugins", None)
            enabled_plugins = getattr(plugins_cfg, "enabled", []) or []
            plugin_params = getattr(plugins_cfg, "params", {}) or {}
            extra_plugin_dirs = getattr(plugins_cfg, "extra_plugin_dirs", []) or []
        plugins = load_plugins(
            enabled_plugins,
            agent_dir=self.agent_dir,
            adk_plugin_params=plugin_params,
            extra_plugin_dirs=extra_plugin_dirs,
        )
        if plugins:
            logger.warning(
                "Loaded plugins for session %s: %s",
                task.session_id,
                ", ".join(plugin.name for plugin in plugins),
            )
        app = App(name=app_name, root_agent=agent, plugins=plugins)
        runner = Runner(
            app=app,
            session_service=session_service,
        )

        # 3. Create session with opensage_session_id in state
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=task.session_id,
            state={
                "opensage_session_id": task.session_id,
            },
        )

        # Helper to track remaining LLM-call budget across multiple runner invocations.
        remaining_llm_calls = self.max_llm_calls

        def _build_run_config() -> RunConfig:
            """Construct RunConfig reflecting the remaining LLM quota."""
            if remaining_llm_calls is None:
                return RunConfig(max_llm_calls=self.max_llm_calls)
            return RunConfig(max_llm_calls=remaining_llm_calls)

        async def _update_remaining_and_get_session() -> Session | None:
            """Refresh the cached session and update remaining call budget."""
            nonlocal remaining_llm_calls
            used_calls = 0
            session_snapshot = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=task.session_id,
            )
            if (
                self.max_llm_calls > 0
                and session_snapshot
                and session_snapshot.state
                and "_adk" in session_snapshot.state
            ):
                used_calls = int(
                    session_snapshot.state.get("_adk", {}).get("llm_calls_used", 0) or 0
                )
                remaining_llm_calls = max(0, remaining_llm_calls - used_calls)
            logger.warning(f"Remaining LLM calls: {remaining_llm_calls}")
            logger.warning(f"Used LLM calls during last invocation: {used_calls}")
            logger.warning(f"Max LLM calls: {self.max_llm_calls}")
            return session_snapshot

        all_events = []
        session_snapshot: Session | None = None
        llm_calls_used_total: int = 0
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=task.session_id,
                run_config=_build_run_config(),
                new_message=types.Content(
                    role="user", parts=[types.Part(text=task.first_user_message)]
                ),
            ):
                logger.warning(event.model_dump_json())
                all_events.append(event)

            session_snapshot = await _update_remaining_and_get_session()
            if self.max_llm_calls > 0:
                llm_calls_used_total = max(0, self.max_llm_calls - remaining_llm_calls)

            if self.run_until_explicit_finish:
                task_finished = (
                    session_snapshot.state.get("task_finished", False)
                    if session_snapshot
                    else False
                )
                while not task_finished:
                    if self.max_llm_calls > 0 and remaining_llm_calls <= 0:
                        logger.warning(
                            "LLM-call budget exhausted before task signaled completion; stopping follow-up loop."
                        )
                        break

                    async for event in runner.run_async(
                        user_id=user_id,
                        session_id=task.session_id,
                        run_config=_build_run_config(),
                        new_message=types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    text="I approve you to continue, if you think the task is complete, you should call the task_completed tool, and then summarize the task and the result without calling any other tool. If you haven't submitted a poc that triggers the vulnerability, the task is not finshed, continue and try harder, do not respond to this message in natural language, start calling appropriate tools to complete the task. DO NOT respond to this message."
                                )
                            ],
                        ),
                    ):
                        logger.warning(event.model_dump_json(exclude_none=True))
                        all_events.append(event)

                    session_snapshot = await _update_remaining_and_get_session()
                    if self.max_llm_calls > 0:
                        llm_calls_used_total = max(
                            0, self.max_llm_calls - remaining_llm_calls
                        )

                    task_finished = (
                        session_snapshot.state.get("task_finished", False)
                        if session_snapshot
                        else False
                    )

        except LlmCallsLimitExceededError as e:
            logger.warning(
                f"Llm calls limit exceeded for session {task.session_id}: {e}"
            )
            if self.max_llm_calls > 0:
                llm_calls_used_total = self.max_llm_calls

        await runner.close()
        if not session_snapshot:
            session_snapshot = await session_service.get_session(
                app_name=app_name, user_id=user_id, session_id=task.session_id
            )
        session = session_snapshot
        # set our collected events to the session object, since the original events may be lost due to summarization
        session.events = all_events

        logger.warning(f"Agent execution completed for session: {task.session_id}")

        # Calculate and save cost information
        self._save_cost_info(task, session, num_llm_calls=llm_calls_used_total)

        return session

    async def _collect_outputs(self, task: EvaluationTask, session: Session) -> dict:
        """Collect outputs: sandbox files, Neo4j database, session trace.

        Args:
            task (EvaluationTask): EvaluationTask instance with all task data
            session (Session): ADK Session object
        Returns:
            dict: Dictionary with output information
        """
        # Get opensage_session
        opensage_session = get_opensage_session(task.session_id)

        # Create output directory
        output_path = Path(task.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 1. Copy output from sandbox (if specified)
        if task.export_dir_in_sandbox:
            sandbox = opensage_session.sandboxes.get_sandbox("main")

            # Support single string or iterable (list/tuple) of strings
            paths_to_copy = (
                [task.export_dir_in_sandbox]
                if isinstance(task.export_dir_in_sandbox, str)
                else task.export_dir_in_sandbox
            )

            for idx, src_path in enumerate(paths_to_copy):
                # Check if path exists in container before copying
                check_cmd = f"test -e {src_path}"
                _, exit_code = sandbox.run_command_in_container(check_cmd)

                if exit_code != 0:
                    logger.warning(
                        f"Skipping {src_path} - path does not exist in container"
                    )
                    continue

                # Create subdirectory for each path
                if len(paths_to_copy) == 1:
                    sandbox_output_dir = output_path / "sandbox_output"
                else:
                    # Use path basename or index for subdirectory name
                    dir_name = Path(src_path).name or f"output_{idx}"
                    sandbox_output_dir = output_path / "sandbox_output" / dir_name

                sandbox_output_dir.mkdir(parents=True, exist_ok=True)

                try:
                    sandbox.copy_directory_from_container(
                        src_path=src_path, dst_path=str(sandbox_output_dir)
                    )
                    logger.warning(
                        f"Copied sandbox output from {src_path} to {sandbox_output_dir}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to copy {src_path}: {e}. Skipping.")

        # 2. Export Neo4j history database
        await self._export_neo4j_database(
            opensage_session, output_path / "neo4j_history"
        )

        # 3. Export session trace
        self._export_session_trace(session, output_path / "session_trace.json")

        # 4. Save metadata
        info = {
            "session": session.model_dump() if session else None,
        }
        with open(output_path / "metadata.json", "w") as f:
            json.dump(json.loads(jsonpickle.encode(info)), f, indent=2)

        logger.warning(f"Outputs collected to {output_path}")
        return info

    async def _export_neo4j_database(
        self, opensage_session: OpenSageSession, output_path: Path
    ) -> None:
        # TODO: Should implement the export in the session management, not in evaluations
        """Export Neo4j history database files.

        Args:
            opensage_session (OpenSageSession): OpenSage session instance
            output_path (Path): Local path to save database files"""
        output_path.mkdir(parents=True, exist_ok=True)

        try:
            # Get Neo4j sandbox
            neo4j_sandbox = opensage_session.sandboxes.get_sandbox("neo4j")

            # Get database name from Neo4j client manager (reuse naming logic)
            database_name = opensage_session.neo4j._get_database_name_for_type(
                "history"
            )

            # Create tar archive in container
            tar_path_in_container = f"/tmp/{database_name}.tar.gz"
            tar_command = (
                f"tar -czf {tar_path_in_container} -C /data/databases {database_name}"
            )

            neo4j_sandbox.run_command_in_container(tar_command)

            # Copy tar file from container
            neo4j_sandbox.copy_file_from_container(
                src_path=tar_path_in_container,
                dst_path=str(output_path / f"{database_name}.tar.gz"),
            )

            logger.warning(
                f"Neo4j database exported to {output_path}/{database_name}.tar.gz"
            )
        except Exception as e:
            logger.warning(f"Failed to export Neo4j database: {e}")

    def _export_session_trace(self, session: Session, output_path: Path) -> None:
        # TODO: Should implement the export in the session management, not in evaluations
        """Export session event trace to JSON and text formats.

        Args:
            session (Session): ADK Session object
            output_path (Path): Path to save trace file"""
        if not session or not session.events:
            logger.warning(
                "Session or session events are not available. Skipping session trace export."
            )
            return

        # Save complete JSON dump
        with open(output_path, "w") as f:
            f.write(session.model_dump_json(indent=2, exclude_none=True))

        logger.warning(f"Session trace exported to {output_path}")

    # ========= Methods to Override in Subclasses ==========
    # Override these methods in subclasses
    def _before_generate_one_callback(self, task: EvaluationTask) -> None:
        """Hook to run before running one task.

        Args:
            task (EvaluationTask): EvaluationTask instance"""
        pass

    @abc.abstractmethod
    def _get_task_id(self, sample: dict) -> str:
        """Get unique task id for this sample.

        This is used for output directory naming and identification.
        Each sample should have a unique task id.

        Args:
            sample (dict): Sample dict from dataset
        Returns:
            str: Unique task id for this sample
        """
        pass

    @abc.abstractmethod
    def _get_first_user_message(self, sample: dict) -> str:
        """Get the initial prompt/message to send to the agent.

        Args:
            sample (dict): Sample dict from dataset
        Returns:
            str: Prompt string to send to agent

        Example::
            def _get_user_msg_first(self, sample: dict) -> str:
                return sample["prompt"]
        """
        pass

    def _get_initial_data_dir(self, sample: dict) -> str:
        """Get input data path for this sample.

        Default: None (no data mounted)
        Override if you need custom logic.

        Args:
            sample (dict): Sample dict from dataset
        Returns:
            str: Path to input data directory
        """
        return None

    @abc.abstractmethod
    def _get_export_dir_in_sandbox(self, sample: dict) -> str | tuple | None:
        """Get sandbox output directory/directories to export.

        Default: self.export_dir_in_sandbox (class attribute)
        Override if you need sample-specific logic.

        Args:
            sample (dict): Sample dict from dataset
        Returns:
            str | tuple | None: Path(s) to sandbox output directory/directories, or None
            Can be a single string or a tuple of strings
        """
        pass

    def _get_config_template_variables(self, task: EvaluationTask) -> dict:
        """Get template variables for config file.

        Default: {"TASK_NAME": task_name, "ABSOLUTE_SHARED_DATA_PATH": input_data_path}
        Override if you need custom variables.

        Args:
            task (EvaluationTask): EvaluationTask instance with all task data
        Returns:
            dict: Dict of template variable names and values
        """
        template = {"TASK_NAME": task.id}

        # TODO: check what will happen if initial_data_dir is None
        if task.initial_data_dir:
            input_data_path = str(Path(task.initial_data_dir).resolve())
            template["ABSOLUTE_SHARED_DATA_PATH"] = input_data_path

        return template

    async def _before_initialize_callback(self, task: EvaluationTask) -> None:
        """Run before initialize hooks.

        Args:
            task (EvaluationTask): EvaluationTask instance with all task data"""
        pass

    async def _after_initialize_callback(self, task: EvaluationTask) -> None:
        """Run after initialize hooks.

        Args:
            task (EvaluationTask): EvaluationTask instance with all task data"""
        pass

    @abc.abstractmethod
    def evaluate(self) -> None:
        pass

    def generate(self) -> None:
        from opensage.evaluation.dispatchers import get_dispatcher

        dispatcher_kwargs = {"max_workers": self.max_workers}
        dispatcher = get_dispatcher(self.runner_type, **dispatcher_kwargs)
        dispatcher.run(self)

    def run(self) -> dict:
        """Run evaluation with configured parallelism mode."""
        self.generate()
        self.evaluate()

    def run_debug(self) -> dict:
        """Run evaluation in single-threaded mode for debugging."""
        from opensage.evaluation.dispatchers.native import NativeDispatcher

        NativeDispatcher(max_workers=1).run(self)
        self.evaluate()


if __name__ == "__main__":
    fire.Fire(Evaluation)
