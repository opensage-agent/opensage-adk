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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import datasets
import fire
import google.adk as adk
import jsonpickle
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, Session
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from huggingface_hub import pause_space
from tqdm import tqdm

from aigise.config import AigiseConfig
from aigise.features.summarization import setup_summarization_callbacks
from aigise.session import get_aigise_session
from aigise.session.aigise_session import AigiseSession
from aigise.toolbox.decorators import collect_sandbox_dependencies
from aigise.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

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
    # Run async code in this process's event loop
    return asyncio.run(evaluation_instance._generate_sample(task))


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
    output_dir_in_sandbox: str | None  # Optional sandbox dir to export
    metadata: dict  # Metadata to save
    config_template_path: str = (
        PROJECT_PATH / "src/aigise/templates/configs/default_config.toml"
    )
    aigise_session: AigiseSession | None = None


@dataclass
class Evaluation(abc.ABC):
    dataset_path: str  # HuggingFace dataset name (e.g., "org/dataset") or local path
    agent_dir: str  # directory containing agent.py with mk_agent function
    dataset_hf_split: str = "train"
    output_dir: str | None = None
    input_data_path: str = ""
    cache_dir: str = ""
    max_llm_calls: int = 100
    max_workers: int = 16
    run_until_explicit_finish: bool = False
    model: str | None = (
        None  # If None, use agent's original model; if set, replace all models
    )
    output_dir_in_sandbox: str | None = None
    config_template_path: str | None = (
        PROJECT_PATH / "src/aigise/templates/configs/default_config.toml"
    )
    use_multiprocessing: bool = True  # Use multiprocessing (True) or threading (False)

    def __post_init__(self) -> None:
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
        self.user_id = str(self.output_dir).replace("/", "_")

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

    def _save_cost_info(self, task: EvaluationTask, session: "Session") -> None:
        """Calculate and save cost information for the task.

        Args:
            task: EvaluationTask instance
            session: ADK Session with events
        """
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        num_llm_calls = 0

        for event in session.events:
            if hasattr(event, "usage_metadata") and event.usage_metadata:
                usage = event.usage_metadata
                num_llm_calls += 1

                if hasattr(usage, "prompt_token_count"):
                    total_input_tokens += usage.prompt_token_count or 0
                if hasattr(usage, "candidates_token_count"):
                    total_output_tokens += usage.candidates_token_count or 0
                if hasattr(usage, "cached_content_token_count"):
                    total_cached_tokens += usage.cached_content_token_count or 0

        cost_info = {
            "session_id": task.session_id,
            "task_name": task.task_name,
            "model": self.model if self.model else "agent_default",
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
        logger.warning(f"  Model: {self.model if self.model else 'agent_default'}")
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
        self, agent: BaseAgent, model: LiteLlm, visited: set[str] | None = None
    ) -> None:
        """Recursively replace model for all agents in the agent tree.

        This method traverses the entire agent tree (sub_agents and agent_tools)
        and replaces the model of all LlmAgent instances.

        Args:
            agent: Root agent to start replacement
            model: LiteLlm instance to replace with
            visited: Set of visited agent names to avoid infinite loops
        """
        if visited is None:
            visited = set()

        # Avoid infinite recursion
        if agent.name in visited:
            return
        visited.add(agent.name)

        # Replace model if agent is LlmAgent
        if isinstance(agent, LlmAgent):
            try:
                agent.model = model
                logger.debug(
                    f"Replaced model for agent '{agent.name}', current model: {agent.model.model_name}"
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

        dataset = self._get_dataset()

        self._prepare_general_env()

        # Execute samples in parallel using process pool
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(_run_sample_in_process, self, sample): sample
                for sample in dataset
            }

            # Wait for completion with progress bar
            results = []
            for future in tqdm(
                as_completed(futures),
                total=len(dataset),
                desc="Generating samples (multiprocess)",
            ):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    sample = futures[future]
                    logger.error(
                        f"Sample {self._get_sample_id(sample)} failed with error: {e}"
                    )

        logger.warning(f"Generated {len(results)}/{len(dataset)} samples successfully")

    def generate_threaded(self) -> None:
        """Generate samples using multithreading (fallback option).

        Each sample runs in its own thread. Use this if multiprocessing
        has issues with serialization or you need shared memory.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        dataset = self._get_dataset()

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
                for sample in dataset
            }

            # Wait for completion with progress bar
            results = []
            for future in tqdm(
                as_completed(futures),
                total=len(dataset),
                desc="Generating samples (threaded)",
            ):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    sample = futures[future]
                    logger.error(
                        f"Sample {self._get_sample_id(sample)} failed with error: {e}"
                    )

        logger.warning(f"Generated {len(results)}/{len(dataset)} samples successfully")

    def generate_single_thread(self) -> None:
        """Generate samples sequentially in a single thread for debugging."""

        dataset = self._get_dataset()
        results = []
        self._prepare_general_env()
        # Keep only first sample for debugging
        dataset = dataset.select([0])
        for sample in tqdm(dataset, desc="Generating samples (single-threaded)"):
            try:
                # Create task from sample
                task = self._create_task(sample)
                # Run async code in new event loop for each sample
                result = asyncio.run(self._generate_sample(task))
                results.append(result)
            except Exception as e:
                logger.error(
                    f"Sample {self._get_sample_id(sample)} failed with error: {e}"
                )
                # Re-raise for easier debugging
                raise

        logger.warning(f"Generated {len(results)}/{len(dataset)} samples successfully")

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
            Path to cache directory
        """
        task_name = self._get_sample_id(sample)
        return str(Path(self.cache_dir) / task_name)

    def _get_output_dir_in_sandbox(self, sample: dict) -> str | None:
        """Get sandbox output directory to export.

        Default: self.output_dir_in_sandbox (class attribute)
        Override if you need sample-specific logic.

        Args:
            sample: Sample dict from dataset

        Returns:
            Path to sandbox output directory, or None
        """
        return self.output_dir_in_sandbox

    def _prepare_agent(self, task: EvaluationTask) -> None:
        agent = self._mk_agent_original(aigise_session_id=task.session_id)

        # Only replace models if user explicitly specified a model
        if self.model is not None:
            task_model = LiteLlm(model=self.model)
            self._replace_agent_models_recursive(agent, task_model)
            logger.warning(
                f"Replaced all agent models with '{self.model}' for session {task.session_id}"
            )
        else:
            logger.warning(
                f"Using agent's original model configuration for session {task.session_id}"
            )

        setup_summarization_callbacks(agent)
        logger.warning(f"Setup summarization callbacks for session {task.session_id}")
        return agent

    async def _generate_sample(self, task: EvaluationTask) -> dict:
        """Generate a single sample with automatic sandbox and Neo4j management.

        Args:
            task: EvaluationTask instance with all task data

        Returns:
            Dictionary with sample results and metadata
        """
        # === 0. Get aigise_session ===
        self._register_aigise_session(task)

        # === 1. Create Agent ===
        agent = self._prepare_agent(task)

        # === 2. Prepare Environment ===
        await self._prepare_environment(task, agent)

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

        return output_info

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

    async def _prepare_environment(
        self, task: EvaluationTask, agent: BaseAgent
    ) -> None:
        """Prepare environment: session, config, volumes, sandboxes.

        Args:
            task: EvaluationTask instance with all task data
        """
        aigise_session = task.aigise_session

        # 1. Enable Neo4j logging
        from aigise.features.agent_history_tracker import (
            enable_neo4j_logging,
            is_neo4j_logging_enabled,
        )

        if not is_neo4j_logging_enabled():
            enable_neo4j_logging()

        # Collect sandbox dependencies from agent
        sandbox_dependencies = collect_sandbox_dependencies(agent)

        # Remove sandbox configs that are not in dependencies
        if aigise_session.config.sandbox and aigise_session.config.sandbox.sandboxes:
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
        unfound_cached_sandboxes = (
            aigise_session.sandboxes.load_sandbox_caches_to_config()
        )

        # 4. Initialize shared volumes
        aigise_session.sandboxes.initialize_shared_volumes()

        # 5. Launch all sandboxes
        await aigise_session.sandboxes.launch_all_sandboxes()

        # 6. Cache sandboxes if needed
        if unfound_cached_sandboxes:
            aigise_session.sandboxes.cache_sandboxes(cache_dir=task.cache_dir)

        logger.warning(f"Environment prepared for session: {task.session_id}")

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
        session_service = InMemorySessionService()
        runner = Runner(
            agent=agent,
            app_name=app_name,
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

        # 4. Run agent with prompt
        run_config = RunConfig(max_llm_calls=self.max_llm_calls)

        all_events = []

        try:
            async for event in runner.run_async(
                user_id=self.user_id,
                session_id=task.session_id,
                run_config=run_config,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=task.prompt)]
                ),
            ):
                logger.warning(event)
                all_events.append(event)

            if self.run_until_explicit_finish:
                task_finished = False
                while not task_finished:
                    async for event in runner.run_async(
                        user_id=self.user_id,
                        session_id=task.session_id,
                        run_config=run_config,
                        new_message=types.Content(
                            role="user",
                            parts=[types.Part(text="I approve you to continue")],
                        ),
                    ):
                        logger.warning(event)
                        all_events.append(event)

                    # get the session object to check if the task is finished, get_session returns a deepcopy of the session
                    # need to call get_session to get the latest status of the session
                    session = await session_service.get_session(
                        app_name=app_name,
                        user_id=self.user_id,
                        session_id=task.session_id,
                    )

                    task_finished = session.state.get("task_finished", False)

        except LlmCallsLimitExceededError as e:
            logger.warning(
                f"Llm calls limit exceeded for session {task.session_id}: {e}"
            )

        await runner.close()
        session = await session_service.get_session(
            app_name=app_name, user_id=self.user_id, session_id=task.session_id
        )
        # set our collected events to the session object, since the original events may be lost due to summarization
        session.events = all_events

        logger.warning(f"Agent execution completed for session: {task.session_id}")

        # Calculate and save cost information
        self._save_cost_info(task, session)

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
            # # Check if under /shared
            # if not task.output_dir_in_sandbox.startswith("/shared"):
            #     raise ValueError(
            #         f"output_dir_in_sandbox must be under /shared, "
            #         f"got: {task.output_dir_in_sandbox}"
            #     )

            sandbox = aigise_session.sandboxes.get_sandbox("main")
            sandbox_output_dir = output_path / "sandbox_output"
            sandbox.copy_directory_from_container(
                src_path=task.output_dir_in_sandbox, dst_path=str(sandbox_output_dir)
            )
            logger.warning(
                f"Copied sandbox output from {task.output_dir_in_sandbox} "
                f"to {sandbox_output_dir}"
            )

        # 2. Export Neo4j history database
        await self._export_neo4j_database(aigise_session, output_path / "neo4j_history")

        # 3. Export session trace
        self._export_session_trace(session, output_path / "session_trace.json")

        # 4. Save metadata
        info = {
            "metadata": task.metadata,
            "session": session.model_dump(),
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
        # self.evaluate()


if __name__ == "__main__":
    fire.Fire(Evaluation)
