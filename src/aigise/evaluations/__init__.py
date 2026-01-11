import abc
import asyncio
import datetime
import importlib
import json
import logging
import re
import shutil
import sys
import tempfile
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import datasets
import fire
import google.adk as adk
import jsonpickle
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
from huggingface_hub import pause_space
from tqdm import tqdm

from aigise.config import AigiseConfig
from aigise.features.aigise_in_memory_session_service import (
    AigiseInMemorySessionService,
)
from aigise.plugins import load_plugins
from aigise.session import get_aigise_session
from aigise.session.aigise_session import AigiseSession
from aigise.toolbox.decorators import collect_sandbox_dependencies
from aigise.utils.bash_tools_staging import compute_bash_tools_top_roots
from aigise.utils.project_info import PROJECT_PATH, SRC_PATH

logger = logging.getLogger(__name__)

# Registry for Evaluation subclasses
_EVALUATION_REGISTRY: dict[str, type["Evaluation"]] = {}


def get_evaluation_class(name: str) -> type["Evaluation"] | None:
    """Get registered Evaluation class by name (case-insensitive).

    Args:
        name: Benchmark name (e.g., "secodeplt", "cybergym")

    Returns:
        Evaluation subclass or None if not found
    """
    return _EVALUATION_REGISTRY.get(name.lower())


def list_evaluations() -> list[str]:
    """List all registered evaluation names."""
    return list(_EVALUATION_REGISTRY.keys())


import litellm

litellm.disable_streaming_logging = True


def _run_sample_in_process(evaluation_instance: "Evaluation", sample: dict) -> dict:
    """Wrapper function to run a sample in a separate process.

    This function must be defined at module level for pickling.

    Args:
        evaluation_instance: The Evaluation instance
        sample: Sample dict from dataset

    Returns:
        Result dictionary from _generate_sample
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

    # Run async code in this process's event loop
    try:
        return asyncio.run(evaluation_instance._generate_sample(task))
    except Exception as e:
        # Convert all exceptions to RuntimeError to ensure pickle-ability
        # This prevents ProcessPool from breaking when serializing exceptions
        import traceback

        error_msg = (
            f"{e.__class__.__module__}.{e.__class__.__name__}: {str(e)}\n\n"
            f"Original traceback:\n{traceback.format_exc()}"
        )
        raise RuntimeError(error_msg) from None


@dataclass
class EvaluationTask:
    """Represents a single evaluation task instance.

    This encapsulates all data needed to run a single evaluation sample,
    making it easy to pass around and for subclasses to extend with
    custom fields.
    """

    session_id: str  # Unique AIgiSE session ID
    sample: dict  # Original sample from dataset
    task_name: str  # Unique task identifier
    input_data_path: str  # Path to input data to mount
    prompt: str  # Prompt to send to agent
    output_dir: str  # Local output directory
    cache_dir: str  # Sandbox cache directory
    output_dir_in_sandbox: str | tuple | None  # Optional sandbox dir(s) to export
    metadata: dict  # Metadata to save
    config_template_path: str | Path = (
        SRC_PATH / "templates/configs/default_config.toml"
    )
    aigise_session: AigiseSession | None = None
    model: Any = None  # Optional model override (BaseLlm instance or string model name)


@dataclass
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

    dataset_path: str  # HuggingFace dataset name (e.g., "org/dataset") or local path
    agent_dir: str  # directory containing agent.py with mk_agent function
    dataset_hf_split: str = "train"
    output_dir: str | None = None
    use_cache: bool = False  # Only load/cache sandboxes if True
    input_data_path: str = ""
    cache_dir: str = ""
    max_llm_calls: int = 100
    max_workers: int = 6
    run_until_explicit_finish: bool = False
    use_config_model: bool = (
        False  # If True, use the model specified in the config file
    )
    output_dir_in_sandbox: str | tuple | None = None
    config_template_path: str | Path | None = (
        SRC_PATH / "templates/configs/default_config.toml"
    )
    use_multiprocessing: bool = True  # Use multiprocessing (True) or threading (False)
    llm_retry_count: int = (
        3  # Number of retries for LLM API calls (e.g., for 502 errors)
    )
    llm_retry_timeout: int = 30  # Timeout in seconds for each LLM request
    log_level: str = "INFO"  # Terminal log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    neo4j_logging: bool = False  # Whether to enable Neo4j logging for this run

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
        logger.info(f"Terminal log level set to: {self.log_level}")

        if not self.output_dir:
            self.output_dir: Path = (
                PROJECT_PATH
                / Path("evals")
                / self.__class__.__name__.lower()
                / datetime.datetime.now().strftime("%y%m%d_%H%M%S")
            )
            self.output_dir.mkdir(parents=True)
        else:
            self.output_dir = Path(self.output_dir)
            if self.output_dir.exists():
                flag = (
                    input(f"{self.output_dir} already exists, continue? (y/n): ")
                    .strip()
                    .lower()
                )
                if flag != "y":
                    print("Exiting...")
                    exit(0)
            else:
                self.output_dir.mkdir(parents=True)
        self.user_id = str(self.output_dir).replace("/", "_")

        # Create master log handler - records all logs from start to finish
        # Note: Use local variable (not self._master_handler) to avoid pickle issues with multiprocessing
        master_log = self.output_dir / "evaluation_master.log"
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
        self._mk_agent_original = self._load_mk_agent(self.agent_dir)

    def _log_and_save_parameters(self) -> None:
        """Log and save evaluation parameters to output directory."""
        from dataclasses import asdict, fields

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
            import subprocess

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
        params_file = self.output_dir / "eval_params.json"
        with open(params_file, "w") as f:
            json.dump(params, f, indent=2)
        logger.warning(f"Parameters saved to: {params_file}")

    def _save_cost_info(
        self,
        task: EvaluationTask,
        session: "Session",
        *,
        num_llm_calls: int,
    ) -> None:
        """Calculate and save cost information for the task.

        Args:
            task: EvaluationTask instance
            session: ADK Session with events
        """
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
        if self.use_config_model and task.aigise_session:
            main_model_config = task.aigise_session.config.llm.model_configs.get("main")
            if main_model_config:
                model_name = main_model_config.model_name

        cost_info = {
            "session_id": task.session_id,
            "task_name": task.task_name,
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

    def _load_mk_agent(self, agent_dir: str) -> Callable:
        """Load mk_agent function from agent directory.

        Expects agent_dir to contain agent.py with mk_agent function.
        Supports both relative and absolute paths.

        Example: agent_dir = "examples/agents/poc_agent"
                 -> will load from <cwd>/examples/agents/poc_agent/agent.py

        Args:
            agent_dir: Directory containing agent.py with mk_agent function.
                      Can be relative (resolved from cwd) or absolute path.

        Returns:
            mk_agent function

        Raises:
            ValueError: If agent.py or mk_agent not found
        """
        # Convert to absolute path
        agent_path = Path(agent_dir).resolve()

        if not agent_path.exists():
            raise ValueError(
                f"Agent directory not found: {agent_dir}\nResolved to: {agent_path}"
            )

        if not agent_path.is_dir():
            raise ValueError(f"Agent path is not a directory: {agent_path}")

        agent_file = agent_path / "agent.py"
        if not agent_file.exists():
            raise ValueError(
                f"agent.py not found in {agent_path}. Expected file: {agent_file}"
            )

        # Add parent directory to sys.path for module imports
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
            agent: Root agent to start replacement
            model: BaseLlm instance to replace with (LiteLlm, ArealLlm, etc.)
            visited: Set of visited agent names to avoid infinite loops
        """
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
            dataset = datasets.load_dataset(
                self.dataset_path, split=self.dataset_hf_split
            )
        return dataset

    def _create_task(self, sample: dict) -> EvaluationTask:
        """Create task instance from sample.

        Subclasses can override this to create custom task types with
        additional fields.

        Args:
            sample: Sample dict from dataset

        Returns:
            EvaluationTask instance (or subclass)

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
        session_id = str(uuid.uuid4())
        task_name = self._get_sample_id(sample)

        return EvaluationTask(
            session_id=session_id,
            sample=sample,
            task_name=task_name,
            input_data_path=self._get_input_data_path(sample),
            prompt=self._get_user_msg_first(sample),
            output_dir=str(self.output_dir / task_name),
            cache_dir=self._get_cache_dir(sample),
            output_dir_in_sandbox=self._get_output_dir_in_sandbox(sample),
            metadata=sample,
            config_template_path=self.config_template_path,
        )

    def _prepare_general_env(self) -> None:
        """Set up general environment for all samples."""
        pass

    def generate(self) -> None:
        """Generate samples using multiprocessing for true parallelism.

        Each sample runs in its own process to bypass Python's GIL
        and enable true concurrent execution of multiple tasks.

        Note: Uses ProcessPoolExecutor by default. For threading mode,
        use generate_threaded() instead.
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed

        self.dataset = self._get_dataset()

        self._prepare_general_env()

        # Execute samples in parallel using process pool
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(_run_sample_in_process, self, sample): sample
                for sample in self.dataset
            }

            # Wait for completion with progress bar
            results = []
            failed_samples = []

            for future in tqdm(
                as_completed(futures),
                total=len(self.dataset),
                desc="Generating samples (multiprocess)",
            ):
                sample = futures[future]
                task_name = self._get_sample_id(sample)

                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"✓ Task {task_name} completed successfully")
                except Exception as e:
                    failed_samples.append(task_name)
                    logger.error(f"✗ Task {task_name} FAILED")
                    logger.error(f"  Error: {e}")
                    logger.error(f"  Traceback:\n{traceback.format_exc()}")

                    # Check if subprocess created error.json
                    error_file = self.output_dir / task_name / "error.json"
                    if error_file.exists():
                        logger.error(f"  Detailed error saved to: {error_file}")

        self.customized_modify_and_save_results(
            results=results,
            failed_samples=failed_samples,
            mode="multiprocess",
        )
        logger.warning(
            f"Generated {len(results)}/{len(self.dataset)} samples successfully"
        )
        if failed_samples:
            logger.warning(
                f"Failed samples ({len(failed_samples)}): {', '.join(failed_samples)}"
            )

    def generate_threaded(self) -> None:
        """Generate samples using multithreading (fallback option).

        Each sample runs in its own thread. Use this if multiprocessing
        has issues with serialization or you need shared memory.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self.dataset = self._get_dataset()

        self._prepare_general_env()

        # Wrapper to run async _generate_sample in a thread
        def run_sample_in_thread(sample: dict) -> dict:
            # Create task from sample
            task = self._create_task(sample)
            # Run async code in this thread's event loop
            return asyncio.run(self._generate_sample(task))

        # Execute samples in parallel using thread pool
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(run_sample_in_thread, sample): sample
                for sample in self.dataset
            }

            # Wait for completion with progress bar
            results = []
            failed_samples = []

            for future in tqdm(
                as_completed(futures),
                total=len(self.dataset),
                desc="Generating samples (threaded)",
            ):
                sample = futures[future]
                task_name = self._get_sample_id(sample)

                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"✓ Task {task_name} completed successfully")
                except Exception as e:
                    failed_samples.append(task_name)
                    logger.error(f"✗ Task {task_name} FAILED")
                    logger.error(f"  Error: {e}")
                    logger.error(f"  Traceback:\n{traceback.format_exc()}")

        self.customized_modify_and_save_results(
            results=results,
            failed_samples=failed_samples,
            mode="threaded",
        )
        logger.warning(
            f"Generated {len(results)}/{len(self.dataset)} samples successfully"
        )
        if failed_samples:
            logger.warning(
                f"Failed samples ({len(failed_samples)}): {', '.join(failed_samples)}"
            )

    def generate_single_thread(self) -> None:
        """Generate samples sequentially in a single thread for debugging."""

        self.dataset = self._get_dataset()
        results = []
        failed_samples = []
        self._prepare_general_env()

        # Keep from 50 sample for debugging
        # num_samples = len(dataset)
        # dataset = dataset.select(range(50, num_samples))
        # dataset = dataset.select(range(50))
        for sample in tqdm(self.dataset, desc="Generating samples (single-threaded)"):
            task_name = self._get_sample_id(sample)
            try:
                # Create task from sample
                task = self._create_task(sample)
                # Run async code in new event loop for each sample
                result = asyncio.run(self._generate_sample(task))
                results.append(result)
                logger.info(f"✓ Task {task_name} completed")
            except Exception as e:
                failed_samples.append(task_name)
                logger.error(f"✗ Task {task_name} FAILED")
                logger.error(f"  Error: {e}")
                logger.error(f"  Traceback:\n{traceback.format_exc()}")
                # Re-raise for easier debugging
                # raise

        self.customized_modify_and_save_results(
            results=results,
            failed_samples=failed_samples,
            mode="single_thread",
        )
        logger.warning(
            f"Generated {len(results)}/{len(self.dataset)} samples successfully"
        )
        if failed_samples:
            logger.warning(
                f"Failed samples ({len(failed_samples)}): {', '.join(failed_samples)}"
            )

    @abc.abstractmethod
    def _get_sample_id(self, sample: dict) -> str:
        """Get unique task name/ID for this sample.

        This is used for output directory naming and identification.
        Each sample should have a unique task name.

        Args:
            sample: Sample dict from dataset

        Returns:
            Unique task name/ID for this sample

        Example::
            def _get_sample_id(self, sample: dict) -> str:
                return sample["task_id"]
        """
        pass

    @abc.abstractmethod
    def _get_user_msg_first(self, sample: dict) -> str:
        """Get the initial prompt/message to send to the agent.

        Args:
            sample: Sample dict from dataset

        Returns:
            Prompt string to send to agent

        Example::
            def _get_user_msg_first(self, sample: dict) -> str:
                return sample["prompt"]
        """
        pass

    def _get_input_data_path(self, sample: dict) -> str:
        """Get input data path for this sample.

        Default: {self.input_data_path}/{task_name}
        Override if you need custom logic.

        Args:
            sample: Sample dict from dataset

        Returns:
            Path to input data directory
        """
        task_name = self._get_sample_id(sample)
        if not self.input_data_path:
            return None
        return str(Path(self.input_data_path) / task_name)

    def _get_cache_dir(self, sample: dict) -> str:
        """Get sandbox cache directory for this sample.

        Default: {self.cache_dir}/{task_name}
        Override if you need custom logic.

        Args:
            sample: Sample dict from dataset

        Returns:
            Path to cache directory, or empty string if caching is disabled
        """
        task_name = self._get_sample_id(sample)
        if not self.cache_dir:
            return ""  # Return empty string to indicate no caching
        return str(Path(self.cache_dir) / task_name)

    def _get_output_dir_in_sandbox(self, sample: dict) -> str | tuple | None:
        """Get sandbox output directory/directories to export.

        Default: self.output_dir_in_sandbox (class attribute)
        Override if you need sample-specific logic.

        Args:
            sample: Sample dict from dataset

        Returns:
            Path(s) to sandbox output directory/directories, or None
            Can be a single string or a tuple of strings
        """
        return self.output_dir_in_sandbox

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
            aigise_session = task.aigise_session
            if aigise_session and aigise_session.config.llm:
                main_model_config = aigise_session.config.llm.model_configs.get("main")
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
                    aigise_session_id=task.session_id, model=model_to_use
                )
                logger.warning(
                    f"Created agent with model from {model_source} (session {task.session_id})"
                )
            else:
                # mk_agent doesn't support model parameter - fallback to replacement
                agent = self._mk_agent_original(aigise_session_id=task.session_id)
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
            agent = self._mk_agent_original(aigise_session_id=task.session_id)
            if model_to_use is not None:
                self._replace_agent_models_recursive(agent, model_to_use)

        return agent

    async def _generate_sample(self, task: EvaluationTask) -> dict:
        """Generate a single sample with automatic sandbox and Neo4j management.

        Args:
            task: EvaluationTask instance with all task data

        Returns:
            Dictionary with sample results and metadata
        """
        # Ensure output directory exists immediately (for logging)
        output_path = Path(task.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Configure task-specific logging with two files + terminal
        # File 1: DEBUG level (all details)
        debug_log = output_path / "execution_debug.log"
        debug_handler = logging.FileHandler(debug_log, mode="w")
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        # File 2: INFO level (important info)
        info_log = output_path / "execution_info.log"
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

        # Terminal: Set ALL stderr StreamHandlers to configured log level
        # Traverse all existing loggers (root and all children)
        logging.basicConfig(level=self._terminal_log_level)
        for logger_name in list(logging.Logger.manager.loggerDict.keys()) + [""]:
            logger_obj = logging.getLogger(logger_name)
            for handler in logger_obj.handlers[:]:
                if (
                    isinstance(handler, logging.StreamHandler)
                    and handler.stream == sys.stderr
                ):
                    handler.setLevel(self._terminal_log_level)

        try:
            logger.info(f"Starting task {task.task_name} (session: {task.session_id})")

            # === 0. Get aigise_session ===
            self._register_aigise_session(task)

            # === 1. Prepare Environment ===
            await self._prepare_environment(task)

            # === 2. Prepare Agent ===
            agent = self._prepare_agent(task)

            # === 2.5 Save Config ===
            config_output_path = Path(task.output_dir) / "config_used.toml"
            task.aigise_session.config.save_to_toml(str(config_output_path))
            logger.warning(f"Config saved to {config_output_path}")

            # === 3. Run Agent ===
            session = await self._run_agent(task, agent)

            # === 4. Collect Outputs ===
            output_info = await self._collect_outputs(task, session)

            # === 5. Cleanup ===
            try:
                task.aigise_session.cleanup()
                logger.warning(f"Cleanup completed for session: {task.session_id}")
            except Exception as e:
                logger.warning(f"Cleanup failed for session {task.session_id}: {e}")

            logger.info(f"Task {task.task_name} completed successfully")
            return output_info

        except Exception as e:
            # Log exception details
            logger.error(f"Task {task.task_name} failed with exception: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")

            # Save error information to file
            error_file = output_path / "error.json"
            with open(error_file, "w") as f:
                json.dump(
                    {
                        "task_name": task.task_name,
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
                if task.aigise_session:
                    task.aigise_session.cleanup()
            except Exception as cleanup_error:
                logger.error(f"Cleanup after error failed: {cleanup_error}")

            raise

        finally:
            # Ensure logs are flushed and handlers are removed
            debug_handler.flush()
            info_handler.flush()
            root_logger.removeHandler(debug_handler)
            root_logger.removeHandler(info_handler)
            debug_handler.close()
            info_handler.close()

    def _replace_template_variables_in_config(
        self, config_path: str, template_variables: dict
    ) -> None:
        with open(config_path, "r") as f:
            content = f.read()
        for var_name, var_value in template_variables.items():
            pattern = rf"\${{\s*{re.escape(var_name)}\s*}}"
            content = re.sub(pattern, str(var_value), content)
        with open(config_path, "w") as f:
            f.write(content)

    def _before_initialize_hooks(
        self, aigise_session: AigiseSession, task: EvaluationTask
    ) -> None:
        """Run before initialize hooks.

        Args:
            aigise_session: AigiseSession instance
            task: EvaluationTask instance with all task data
        """
        pass

    def customized_modify_and_save_results(
        self,
        *,
        results: list | None,
        failed_samples: list[str] | None,
        mode: str,
    ) -> None:
        """Hook for subclasses to post-process and persist aggregated results.

        Args:
            results: Successful sample outputs collected during generation.
            failed_samples: Task identifiers that failed to complete.
            mode: Execution mode that produced the results (multiprocess, threaded,
                or single_thread).
        """
        _ = (results, failed_samples, mode)

    def _register_aigise_session(self, task: EvaluationTask):
        """Register AigiseSession with task-specific config.

        Args:
            task: EvaluationTask containing session_id and config_template_path
        Returns:
            None
        """
        # Copy config template to a temporary file for this task
        config_template = Path(task.config_template_path)
        temp_dir = tempfile.mkdtemp(prefix=f"aigise_{task.session_id}_")
        temp_config_path = Path(temp_dir) / config_template.name
        shutil.copy(config_template, temp_config_path)
        task_name = task.task_name
        input_data_path = str(Path(task.input_data_path).relative_to(PROJECT_PATH))
        template_variables = {
            "TASK_NAME": task_name,
            "PROJECT_RELATIVE_SHARED_DATA_PATH": input_data_path,
        }
        self._replace_template_variables_in_config(temp_config_path, template_variables)

        aigise_session = get_aigise_session(
            task.session_id, config_path=temp_config_path
        )
        task.aigise_session = aigise_session

        # clean up temp config file
        shutil.rmtree(temp_dir, ignore_errors=True)

    async def _prepare_environment(self, task: EvaluationTask) -> None:
        """Prepare environment: session, config, volumes, sandboxes.

        Args:
            task: EvaluationTask instance with all task data
        """
        aigise_session = task.aigise_session

        # 1. Configure Neo4j logging
        from aigise.features.agent_history_tracker import (
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

        dummy_agent = self._mk_agent_original(aigise_session_id=task.session_id)

        # Collect sandbox dependencies from agent
        sandbox_dependencies = collect_sandbox_dependencies(dummy_agent)
        tools_top_roots = compute_bash_tools_top_roots(dummy_agent)

        # Strong behavior:
        # - If dependencies mention sandboxes that are not configured, drop them and warn.
        # - If config contains sandboxes that are not needed, remove them and warn.
        if aigise_session.config.sandbox and aigise_session.config.sandbox.sandboxes:
            configured_sandboxes = set(aigise_session.config.sandbox.sandboxes.keys())

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
                for sandbox_type in aigise_session.config.sandbox.sandboxes.keys()
                if sandbox_type not in sandbox_dependencies
            ]
            for sandbox_type in sandboxes_to_remove:
                del aigise_session.config.sandbox.sandboxes[sandbox_type]
                logger.warning(
                    f"Removed unused sandbox '{sandbox_type}' from config "
                    f"(not in agent dependencies: {sandbox_dependencies})"
                )

        # 3. Load cached sandboxes
        unfound_cached_sandboxes = []
        if self.use_cache:
            unfound_cached_sandboxes = (
                aigise_session.sandboxes.load_sandbox_caches_to_config()
            )

        # 4. Initialize shared volumes
        aigise_session.sandboxes.initialize_shared_volumes(
            tools_top_roots=tools_top_roots,
            enabled_skills=getattr(dummy_agent, "_enabled_skills", None),
        )

        # 5. Launch all sandboxes (create containers only, not initialized yet)
        await aigise_session.sandboxes.launch_all_sandboxes()

        self._before_initialize_hooks(aigise_session, task)

        # 6. Initialize all sandboxes
        # continue_on_error=True is important for the evaluation to continue even if some sandboxes fail to initialize
        await aigise_session.sandboxes.initialize_all_sandboxes(continue_on_error=True)

        # 7. Cache sandboxes if needed
        if self.use_cache and unfound_cached_sandboxes:
            aigise_session.sandboxes.cache_sandboxes(cache_dir=task.cache_dir)

    async def _run_agent(self, task: EvaluationTask, agent: adk.Agent) -> "Session":
        """Run agent with the given prompt.

        Args:
            task: EvaluationTask instance with all task data
            agent: Pre-configured agent instance

        Returns:
            ADK Session object with execution history
        """
        # 2. Create runner and session service
        app_name = self.__class__.__name__.lower()
        session_service = AigiseInMemorySessionService()
        enabled_plugins = []
        if task.aigise_session and getattr(task.aigise_session, "config", None):
            enabled_plugins = (
                getattr(
                    getattr(task.aigise_session.config, "plugins", None), "enabled", []
                )
                or []
            )
        plugins = load_plugins(enabled_plugins)
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

        # 3. Create session with aigise_session_id in state
        await session_service.create_session(
            app_name=app_name,
            user_id=self.user_id,
            session_id=task.session_id,
            state={
                "aigise_session_id": task.session_id,
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
                user_id=self.user_id,
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
                user_id=self.user_id,
                session_id=task.session_id,
                run_config=_build_run_config(),
                new_message=types.Content(
                    role="user", parts=[types.Part(text=task.prompt)]
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
                        user_id=self.user_id,
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
                app_name=app_name, user_id=self.user_id, session_id=task.session_id
            )
        session = session_snapshot
        # set our collected events to the session object, since the original events may be lost due to summarization
        session.events = all_events

        logger.warning(f"Agent execution completed for session: {task.session_id}")

        # Calculate and save cost information
        self._save_cost_info(task, session, num_llm_calls=llm_calls_used_total)

        return session

    async def _collect_outputs(self, task: EvaluationTask, session: "Session") -> dict:
        """Collect outputs: sandbox files, Neo4j database, session trace.

        Args:
            task: EvaluationTask instance with all task data
            session: ADK Session object

        Returns:
            Dictionary with output information
        """
        # Get aigise_session
        aigise_session = get_aigise_session(task.session_id)

        # Create output directory
        output_path = Path(task.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 1. Copy output from sandbox (if specified)
        if task.output_dir_in_sandbox:
            sandbox = aigise_session.sandboxes.get_sandbox("main")

            # Support single string or iterable (list/tuple) of strings
            paths_to_copy = (
                [task.output_dir_in_sandbox]
                if isinstance(task.output_dir_in_sandbox, str)
                else task.output_dir_in_sandbox
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
        await self._export_neo4j_database(aigise_session, output_path / "neo4j_history")

        # 3. Export session trace
        self._export_session_trace(session, output_path / "session_trace.json")

        # 4. Save metadata
        info = {
            "metadata": task.metadata,
            "session": session.model_dump() if session else None,
        }
        with open(output_path / "metadata.json", "w") as f:
            json.dump(json.loads(jsonpickle.encode(info)), f, indent=2)

        logger.warning(f"Outputs collected to {output_path}")
        return info

    async def _export_neo4j_database(
        self, aigise_session: "AigiseSession", output_path: Path
    ) -> None:
        """Export Neo4j history database files.

        Args:
            aigise_session: AIgiSE session instance
            output_path: Local path to save database files
        """
        output_path.mkdir(parents=True, exist_ok=True)

        try:
            # Get Neo4j sandbox
            neo4j_sandbox = aigise_session.sandboxes.get_sandbox("neo4j")

            # Get database name from Neo4j client manager (reuse naming logic)
            database_name = aigise_session.neo4j._get_database_name_for_type("history")

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

    def _export_session_trace(self, session: "Session", output_path: Path) -> None:
        """Export session event trace to JSON and text formats.

        Args:
            session: ADK Session object
            output_path: Path to save trace file
        """
        if not session or not session.events:
            logger.warning(
                "Session or session events are not available. Skipping session trace export."
            )
            return

        # Save complete JSON dump
        with open(output_path, "w") as f:
            f.write(session.model_dump_json(indent=2, exclude_none=True))

        # Save formatted text trace
        output_lines = []
        for event in session.events:
            if not event.content or not event.content.parts:
                continue
            text_parts = [
                part.text.replace("\n", " ")
                for part in event.content.parts
                if part.text
            ]
            if text_parts:
                output_lines.append(
                    json.dumps(
                        {
                            "author": event.author,
                            "timestamp": str(event.timestamp),
                            "text": ".".join(text_parts),
                        }
                    )
                )

        with open(output_path.with_suffix(".txt"), "w") as f:
            f.write("\n".join(output_lines))

        logger.warning(f"Session trace exported to {output_path}")

    # ========== RL Integration Methods ==========
    # These class methods are used by BenchmarkInterface for RL framework integration.
    # Override in subclasses to provide benchmark-specific logic.

    @classmethod
    def get_prompt(cls, sample: Any) -> str:
        """Extract prompt from RL sample for agent execution.

        Override this method in subclasses to provide benchmark-specific
        prompt extraction logic.

        Args:
            sample: Sample object from RL framework

        Returns:
            Prompt string to send to agent
        """
        # Default: try common attributes
        if hasattr(sample, "prompt"):
            prompt = sample.prompt
            if isinstance(prompt, list):
                # Chat format - extract last user message
                for msg in reversed(prompt):
                    if msg.get("role") == "user":
                        return msg.get("content", "")
            return str(prompt)
        return ""

    @classmethod
    async def reward_func(cls, args: Any, sample: Any, **kwargs) -> dict:
        """Calculate reward for RL training.

        Override this method in subclasses to provide benchmark-specific
        reward calculation logic.

        Args:
            args: Rollout arguments from RL framework
            sample: Sample with agent response
            **kwargs: Additional arguments

        Returns:
            Reward dict with 'score' and optional metadata
        """
        return {"score": 0.0, "status": "not_implemented"}

    @classmethod
    def preprocess_sample(cls, sample: Any) -> Any:
        """Preprocess sample before agent execution.

        Override this method in subclasses if preprocessing is needed.

        Args:
            sample: Sample object from RL framework

        Returns:
            Preprocessed sample (may be same object)
        """
        return sample

    @classmethod
    def postprocess_response(cls, sample: Any, response: str) -> Any:
        """Postprocess agent response before reward calculation.

        Override this method in subclasses if postprocessing is needed.

        Args:
            sample: Sample object
            response: Agent response text

        Returns:
            Updated sample
        """
        return sample

    def evaluate(self) -> None:
        raise NotImplementedError

    def run(self) -> dict:
        """Run evaluation with configured parallelism mode."""
        if self.use_multiprocessing:
            self.generate()  # Uses ProcessPoolExecutor
        else:
            self.generate_threaded()  # Uses ThreadPoolExecutor
        self.evaluate()

    def run_debug(self) -> dict:
        """Run evaluation in single-threaded mode for debugging."""
        self.generate_single_thread()
        self.evaluate()


if __name__ == "__main__":
    fire.Fire(Evaluation)
