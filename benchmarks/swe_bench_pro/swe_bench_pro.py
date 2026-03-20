import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import datasets
import fire
import google.adk as adk
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models.google_llm import Gemini
from google.adk.models.lite_llm import LiteLlm
from google.adk.planners import BuiltInPlanner
from google.adk.sessions import Session
from google.genai import types

from opensage.evaluation.base import Evaluation, EvaluationTask
from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)
try:
    from langfuse import get_client
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    langfuse = get_client()
    # Verify connection
    if langfuse.auth_check():
        print("Langfuse client is authenticated and ready!")
    else:
        print("Authentication failed. Please check your credentials and host.")
    GoogleADKInstrumentor().instrument()
except ImportError:
    logger.info(
        "Langfuse not available. To enable tracing, install with: pip install opensage[langfuse]"
    )


@dataclass
class SweBenchPro(Evaluation):
    dataset_path: str = "ScaleAI/SWE-bench_Pro"
    dataset_split: str = "test"
    agent_dir: str = PROJECT_PATH / "examples/agents/swebenchpro_agent"
    config_template_path: str = str(Path(agent_dir) / "config.toml")

    # Optional override for output directory relative to project root
    predictions_filename: str = "predictions.json"
    dockerhub_username: str = "jefzda"
    # Model selection: model to use for agents
    model_name: str = "gemini-3-flash-preview"
    # Output directory in sandbox to copy (required for patch collection)
    export_dir_in_sandbox: str = "/workspace"
    # Dataset filtering
    start_idx: int = 0
    end_idx: int | None = None  # None means all samples
    task_file: str | None = None  # Path to file with task IDs to run (one per line)
    exclude_task_file: str | None = (
        None  # Path to file with task IDs to exclude (higher priority than task_file)
    )
    skip_existing: bool = False  # Skip tasks that already have a folder in output_dir
    skip_with_patch: bool = False  # Skip tasks that already have prediction.patch
    # Success tracking - automatically skip tasks that have already been solved
    successful_instances_file: str = (
        "successful_instances.txt"  # File to track solved tasks
    )
    skip_successful: bool = (
        True  # Default behavior: skip tasks already solved (accuracy=1)
    )
    # Explore agent settings
    use_explore_agent: bool = False  # Run explore agent before bench agent
    explore_max_llm_calls: int = 40  # Max LLM calls for explore agent

    def __post_init__(self):
        super().__post_init__()
        if not self.agent_dir:
            logger.warning(
                "Agent directory not specified. Make sure to provide --agent_dir when running."
            )
        # Create planner with thinking enabled (visible in langfuse)
        self.planner = BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_level=types.ThinkingLevel.HIGH,
            )
        )
        # Create model instance with retry
        if "anthropic" in self.model_name:
            self.model = LiteLlm(model=self.model_name, reasoning_effort="high")
        else:
            self.model = Gemini(
                model=self.model_name,
                retry_options=types.HttpRetryOptions(initial_delay=1, attempts=2),
            )
        logger.info(f"Created model: {self.model_name}")

        # Load explore agent function if enabled
        if self.use_explore_agent:
            self._mk_explore_agent = self._load_mk_explore_agent(self.agent_dir)
            logger.info("Explore agent enabled - will run before main agent")

    # ========= Abstract method implementations ==========

    def _get_task_id(self, sample: dict) -> str:
        """Get unique task ID for this sample."""
        return sample["instance_id"]

    def _get_first_user_message(self, sample: dict) -> str:
        """Get initial prompt for the agent (without explore context)."""
        return self._build_user_message(sample)

    def _get_export_dir_in_sandbox(self, sample: dict) -> str | tuple | None:
        return self.export_dir_in_sandbox

    # ========= Dataset filtering ==========

    def _get_dataset(self) -> datasets.Dataset:
        dataset = super()._get_dataset()

        # Load exclude task IDs if specified (highest priority - applied last)
        exclude_task_ids: set[str] = set()
        if self.exclude_task_file:
            exclude_file_path = Path(self.exclude_task_file)
            if exclude_file_path.exists():
                with open(exclude_file_path, "r") as f:
                    exclude_task_ids = set(line.strip() for line in f if line.strip())
                logger.info(
                    f"Loaded {len(exclude_task_ids)} task IDs to exclude from {self.exclude_task_file}"
                )
            else:
                logger.warning(f"Exclude task file not found: {self.exclude_task_file}")

        # Filter by task file if specified (takes priority over start_idx/end_idx)
        if self.task_file:
            task_file_path = Path(self.task_file)
            if task_file_path.exists():
                with open(task_file_path, "r") as f:
                    task_ids = set(line.strip() for line in f if line.strip())
                logger.info(f"Loaded {len(task_ids)} task IDs from {self.task_file}")

                # Filter dataset to only include samples with matching instance_id
                indices = [
                    i
                    for i, sample in enumerate(dataset)
                    if sample["instance_id"] in task_ids
                ]
                if indices:
                    dataset = dataset.select(indices)
                    logger.info(f"Filtered dataset to {len(dataset)} samples")
                else:
                    logger.warning("No matching samples found for task IDs in file")
            else:
                logger.warning(f"Task file not found: {self.task_file}")
        else:
            # Apply range filtering if specified
            if self.end_idx is not None:
                dataset = dataset.select(range(self.start_idx, self.end_idx))
            elif self.start_idx > 0:
                dataset = dataset.select(range(self.start_idx, len(dataset)))

        # Apply exclude filter (highest priority - excludes tasks regardless of task_file)
        if exclude_task_ids:
            pre_exclude_count = len(dataset)
            indices = [
                i
                for i, sample in enumerate(dataset)
                if sample["instance_id"] not in exclude_task_ids
            ]
            if indices:
                dataset = dataset.select(indices)
            else:
                # All samples were excluded - return empty dataset
                dataset = dataset.select([])
            excluded_count = pre_exclude_count - len(dataset)
            logger.info(
                f"Excluded {excluded_count} tasks, {len(dataset)} remaining after exclude filter"
            )

        # Skip tasks that already have a folder in output_dir
        if self.skip_existing and Path(self.output_dir).exists():
            existing_task_ids = set()
            for task_dir in Path(self.output_dir).iterdir():
                if task_dir.is_dir() and task_dir.name not in (
                    "results",
                    "__pycache__",
                ):
                    existing_task_ids.add(task_dir.name)

            if existing_task_ids:
                pre_skip_count = len(dataset)
                indices = [
                    i
                    for i, sample in enumerate(dataset)
                    if sample["instance_id"] not in existing_task_ids
                ]
                if indices:
                    dataset = dataset.select(indices)
                else:
                    dataset = dataset.select([])
                skipped_count = pre_skip_count - len(dataset)
                logger.info(
                    f"Skipped {skipped_count} existing tasks, {len(dataset)} remaining to run"
                )

        # Skip tasks that already have prediction.patch
        if self.skip_with_patch and Path(self.output_dir).exists():
            tasks_with_patch = set()
            for task_dir in Path(self.output_dir).iterdir():
                if task_dir.is_dir() and task_dir.name not in (
                    "results",
                    "__pycache__",
                ):
                    patch_file = (
                        task_dir / "sandbox_output" / "workspace" / "prediction.patch"
                    )
                    if patch_file.exists():
                        tasks_with_patch.add(task_dir.name)

            if tasks_with_patch:
                pre_skip_count = len(dataset)
                indices = [
                    i
                    for i, sample in enumerate(dataset)
                    if sample["instance_id"] not in tasks_with_patch
                ]
                if indices:
                    dataset = dataset.select(indices)
                else:
                    dataset = dataset.select([])
                skipped_count = pre_skip_count - len(dataset)
                logger.info(
                    f"Skipped {skipped_count} tasks with prediction.patch, {len(dataset)} remaining to run"
                )

        # Skip tasks that have been successfully solved (accuracy=1)
        if self.skip_successful and Path(self.output_dir).exists():
            successful_file = Path(self.output_dir) / self.successful_instances_file
            if successful_file.exists():
                with open(successful_file, "r") as f:
                    successful_ids = set(line.strip() for line in f if line.strip())
                if successful_ids:
                    pre_skip_count = len(dataset)
                    indices = [
                        i
                        for i, sample in enumerate(dataset)
                        if sample["instance_id"] not in successful_ids
                    ]
                    if indices:
                        dataset = dataset.select(indices)
                    else:
                        dataset = dataset.select([])
                    skipped_count = pre_skip_count - len(dataset)
                    logger.info(
                        f"Skipped {skipped_count} previously successful tasks, {len(dataset)} remaining to run"
                    )

        return dataset

    # ========= Task creation ==========

    def _create_task(self, sample: dict) -> EvaluationTask:
        """Create task with model injection."""
        return super()._create_task(sample, model=self.model)

    # ========= Agent preparation ==========

    def _load_mk_explore_agent(self, agent_dir: str):
        """Load mk_explore_agent function from agent directory."""
        import importlib
        import sys
        from pathlib import Path

        agent_path = Path(agent_dir).resolve()
        agent_file = agent_path / "agent.py"

        if not agent_file.exists():
            raise ValueError(f"agent.py not found in {agent_path}")

        # Add parent directory to sys.path for module imports
        parent_dir = str(agent_path.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        agent_name = agent_path.name

        try:
            agent_module = importlib.import_module(f"{agent_name}.agent")
        except ModuleNotFoundError as e:
            raise ValueError(f"Failed to import {agent_name}.agent: {e}") from e

        mk_explore_agent = getattr(agent_module, "mk_explore_agent", None)
        if mk_explore_agent is None:
            raise ValueError(
                f"No `mk_explore_agent` function found in {agent_file}. "
                "Please define mk_explore_agent() to use explore agent feature."
            )

        logger.info(f"Loaded mk_explore_agent from {agent_file}")
        return mk_explore_agent

    def _prepare_agent(self, task: EvaluationTask) -> adk.Agent:
        """Prepare agent with model and planner."""
        agent = self._mk_agent_original(
            opensage_session_id=task.session_id,
            model=self.model,
            planner=self.planner,
        )
        logger.info(
            f"Created agent with model={self.model_name}, planner={self.planner} "
            f"(session {task.session_id})"
        )
        return agent

    # ========= Config template ==========

    def _get_config_template_variables(self, task: EvaluationTask) -> dict:
        """Add Docker image URI to config template variables."""
        template = super()._get_config_template_variables(task)
        # Docker requires lowercase task name
        template["TASK_NAME"] = task.id.lower()
        # Docker image
        docker_image = self._get_docker_image_uri(task.sample)
        template["DEFAULT_IMAGE"] = docker_image
        logger.info(f"Selected docker image for {task.id}: {docker_image}")
        # Shared data path (relative to project root)
        if task.initial_data_dir:
            template["PROJECT_RELATIVE_SHARED_DATA_PATH"] = str(
                Path(task.initial_data_dir).relative_to(PROJECT_PATH)
            )
        else:
            template["PROJECT_RELATIVE_SHARED_DATA_PATH"] = ""
        return template

    def _get_docker_image_uri(self, sample: dict) -> str:
        """
        Derive the official Docker Hub image URI for the sample.
        Logic ported from SWE-bench_Pro-os/helper_code/image_uri.py
        """
        uid = sample["instance_id"]
        repo_name = sample.get("repo", "")

        try:
            repo_base, repo_name_only = repo_name.lower().split("/")
            hsh = uid.replace("instance_", "")

            if (
                uid
                == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
            ):
                repo_name_only = "element-web"  # Keep full name for this one case
            elif (
                "element-hq" in repo_name.lower() and "element-web" in repo_name.lower()
            ):
                repo_name_only = "element"
                if hsh.endswith("-vnan"):
                    hsh = hsh[:-5]
            # All other repos: strip -vnan suffix
            elif hsh.endswith("-vnan"):
                hsh = hsh[:-5]

            tag = f"{repo_base}.{repo_name_only}-{hsh}"
            if len(tag) > 128:
                tag = tag[:128]

            return f"{self.dockerhub_username}/sweap-images:{tag}"
        except Exception as e:
            logger.warning(
                f"Failed to generate custom docker URI: {e}. Falling back to default."
            )
            return "python:3.11"

    # ========= Environment preparation ==========

    async def _prepare_environment(self, task: EvaluationTask):
        """Prepare environment: clone repo and checkout commit."""
        try:
            await super()._prepare_environment(task)
        except RuntimeError as e:
            if "neo4j" in str(e):
                logger.warning(f"Ignored expected neo4j initialization error: {e}")
            else:
                raise e

        # Clone repo and checkout base commit
        sample = task.sample
        repo = sample.get("repo")
        base_commit = sample.get("base_commit")

        sandbox = task.opensage_session.sandboxes.get_sandbox("main")

        if repo and base_commit:
            # Format repo url (assuming github for now)
            logger.info(f"Cloning repo {repo} at commit {base_commit}")

            # Check if /app exists (pre-installed repo)
            # SWE-bench images usually have the repo at /app
            # User confirmed /app is the location for these images
            check_app_cmd = "test -d /app"
            _, app_exists_code = sandbox.run_command_in_container(check_app_cmd)

            if app_exists_code == 0:
                logger.info("Found /app directory in container. Using it directly.")
                # Configure git safe directory for /app
                setup_cmds = [
                    "git config --global --add safe.directory /app",
                    # Add /app to PYTHONPATH so agent tools can find it if not already there
                    "export PYTHONPATH=$PYTHONPATH:/app",
                ]
            else:
                logger.info("/app not found. Falling back to git clone.")
                # Original logic
                # Format repo url (assuming github for now)
                repo_url = (
                    f"https://github.com/{repo}"
                    if not repo.startswith("http")
                    else repo
                )

                # 1. Clone the repository
                # We clone into a directory named 'repo' to keep it clean
                setup_cmds = [
                    f"git clone {repo_url} repo",
                    f"cd repo && git checkout {base_commit}",
                ]

            # Execute commands
            cmd = " && ".join(setup_cmds)
            logger.info(f"Setting up repo for task {task.id}: {cmd}")
            output, exit_code = sandbox.run_command_in_container(cmd)

            if exit_code != 0:
                logger.error(f"Failed to setup repo for {task.id}: {output}")
                raise RuntimeError(f"Failed to setup repo: {output}")

            # 2. Install dependencies (Optional/Heuristic)
            pass

    # ========= Explore agent ==========

    def _build_user_message(
        self, sample: dict, explore_summary: str | None = None
    ) -> str:
        """Build user message, optionally including explore agent context."""
        msg = f"Please fix the issue described below.\n\n"
        msg += f"Problem Statement:\n{sample['problem_statement']}\n\nRequirements:\n{sample['requirements']}\n\nNew interfaces introduced:\n{sample['interface']}\n\n"

        # Add explore summary if available
        if explore_summary:
            msg += (
                f"---\n"
                f"**Context from Memory (Explore Agent Analysis):**\n"
                f"The following information was gathered by an explore agent."
                f"Use this context to help you understand "
                f"the codebase structure and relevant code:\n\n"
                f"{explore_summary}\n"
                f"---\n\n"
                f"You can also use `search_memory` to find more related information.\n"
            )

        return msg

    async def _summarize_explore_session(
        self,
        session_service,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> str | None:
        """Manually summarize explore agent session when it hits max calls limit.

        Args:
            session_service: The session service containing the session.
            app_name: Name of the app.
            user_id: User ID for the session.
            session_id: Session ID to summarize.

        Returns:
            A summary of the exploration, or None if failed.
        """
        from google import genai

        # Get the session to extract conversation history
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

        if not session or not session.events:
            logger.warning("No session or events to summarize")
            return None

        # Extract conversation content from events
        conversation_parts = []
        for event in session.events:
            if not event.content or not event.content.parts:
                continue

            role = event.content.role or "unknown"
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    # Skip thought parts
                    if getattr(part, "thought", False):
                        continue
                    conversation_parts.append(f"[{role}]: {part.text[:2000]}")
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    args_str = json.dumps(fc.args)[:500] if fc.args else "{}"
                    conversation_parts.append(f"[tool_call]: {fc.name}({args_str})")
                elif hasattr(part, "function_response") and part.function_response:
                    fr = part.function_response
                    resp_str = str(fr.response)[:1000] if fr.response else ""
                    conversation_parts.append(f"[tool_result]: {fr.name} -> {resp_str}")

        if not conversation_parts:
            logger.warning("No conversation content to summarize")
            return None

        # Build summary prompt
        conversation_text = "\n".join(conversation_parts[-50:])  # Last 50 parts
        summary_prompt = f"""You are an expert code exploration assistant. The following is the conversation history of an exploration agent that was examining a codebase to understand it. The agent was interrupted before it could provide a final summary.

Based on the conversation history, please provide a structured summary of what was explored and discovered.

## Conversation History:
{conversation_text}

## Required Summary Format:
Provide a summary in the following format:

## Exploration Summary

### Key Findings
- [List the most important discoveries about the codebase]

### Relevant Files
- [List files that were examined or are relevant, with brief descriptions]

### Code Structure
- [Describe how the relevant parts of the code are organized]

### Suggested Approach
- [Based on the exploration, suggest how to approach the task]

Please be concise but comprehensive. Focus on information that would be useful for fixing a bug or implementing a feature."""

        # Call LLM to generate summary
        try:
            client = genai.Client()
            response = await client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=summary_prompt,
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logger.warning(f"Failed to generate summary with genai: {e}")

        return None

    async def _run_explore_agent(self, task: EvaluationTask) -> str | None:
        """Run explore agent to gather codebase knowledge before main agent.

        The explore agent explores the codebase and stores relevant knowledge
        to memory, which the main bench agent can then search and use.

        Returns:
            The final summary from the explore agent, or None if failed.
        """
        from google.adk.agents.run_config import RunConfig
        from google.adk.apps.app import App
        from google.adk.runners import Runner

        from opensage.features.opensage_in_memory_session_service import (
            OpenSageInMemorySessionService,
        )
        from opensage.plugins import load_plugins

        # Set user_id with explore suffix for langfuse visibility
        instance_id = self._get_task_id(task.sample)
        explore_user_id = f"swebench_{instance_id}_explore"

        logger.warning(f"=== Phase 1: Running Explore Agent for {task.id} ===")

        # Create explore agent
        explore_agent = self._mk_explore_agent(
            opensage_session_id=task.session_id,
            model=self.model,
            planner=self.planner,
        )

        # Create separate session service for explore agent
        # Use a different session_id to keep traces separate
        explore_session_id = f"{task.session_id}_explore"
        app_name = f"{self.__class__.__name__.lower()}_explore"
        session_service = OpenSageInMemorySessionService()

        # Load plugins from config (same as main agent)
        enabled_plugins = []
        if task.opensage_session and getattr(task.opensage_session, "config", None):
            plugins_cfg = getattr(task.opensage_session.config, "plugins", None)
            enabled_plugins = getattr(plugins_cfg, "enabled", []) or []
            extra_plugin_dirs = getattr(plugins_cfg, "extra_plugin_dirs", []) or []
        plugins = load_plugins(
            enabled_plugins,
            agent_dir=self.agent_dir,
            extra_plugin_dirs=extra_plugin_dirs,
        )

        app = App(name=app_name, root_agent=explore_agent, plugins=plugins)
        runner = Runner(app=app, session_service=session_service)

        # Create session with opensage_session_id in state (same as main agent)
        await session_service.create_session(
            app_name=app_name,
            user_id=explore_user_id,
            session_id=explore_session_id,
            state={"opensage_session_id": task.session_id},
        )

        # Build explore prompt
        explore_prompt = (
            f"Explore the codebase and gather as much as possible of relevant knowledge related to this issue. "
            f"Problem Statement:\n{task.sample['problem_statement']}\n\nRequirements:\n{task.sample['requirements']}\n\nNew interfaces introduced:\n{task.sample['interface']}\n\n"
        )

        # Run explore agent with limited LLM calls
        run_config = RunConfig(max_llm_calls=self.explore_max_llm_calls)

        explore_summary = None

        try:
            async for event in runner.run_async(
                user_id=explore_user_id,
                session_id=explore_session_id,
                run_config=run_config,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=explore_prompt)]
                ),
            ):
                logger.info(f"[Explore] {event.model_dump_json(exclude_none=True)}")

                # Capture the final response as summary
                if event.is_final_response() and event.content:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            # Skip thought parts
                            if not getattr(part, "thought", False):
                                explore_summary = part.text.strip()
                                break

            logger.warning(f"=== Explore Agent completed for {task.id} ===")
            if explore_summary:
                logger.warning(
                    f"Explore summary captured ({len(explore_summary)} chars):"
                )
                logger.warning(
                    f"--- EXPLORE SUMMARY START ---\n{explore_summary}\n--- EXPLORE SUMMARY END ---"
                )
        except LlmCallsLimitExceededError as e:
            logger.warning(f"Explore agent hit max calls limit: {e}")
            # Try to manually summarize the session history
            try:
                explore_summary = await self._summarize_explore_session(
                    session_service=session_service,
                    app_name=app_name,
                    user_id=explore_user_id,
                    session_id=explore_session_id,
                )
                if explore_summary:
                    logger.warning(
                        f"Manual summary generated ({len(explore_summary)} chars):"
                    )
                    logger.warning(
                        f"--- MANUAL EXPLORE SUMMARY START ---\n{explore_summary}\n--- MANUAL EXPLORE SUMMARY END ---"
                    )
            except Exception as summary_error:
                logger.warning(f"Failed to generate manual summary: {summary_error}")
        except Exception as e:
            logger.warning(f"Explore agent failed (non-fatal): {e}")
        finally:
            await runner.close()

        return explore_summary

    # ========= Agent execution ==========

    async def _run_agent(self, task: EvaluationTask, agent: adk.Agent) -> Session:
        # Phase 1: Run explore agent if enabled
        explore_summary = None
        if self.use_explore_agent:
            explore_summary = await self._run_explore_agent(task)

        # Phase 2: Update task.first_user_message to include explore summary
        if explore_summary:
            logger.warning(f"=== Phase 2: Running Main Agent with explore context ===")
            task.first_user_message = self._build_user_message(
                task.sample, explore_summary=explore_summary
            )
        else:
            logger.warning(f"=== Running Main Agent (no explore context) ===")

        # Phase 3: Run main agent
        session = await super()._run_agent(task, agent)

        # 4.5. Generate patch
        # The agent might not create a patch file, so we force one.
        # We assume the repo is in 'repo' directory under working_dir (/workspace) or /app
        try:
            sandbox = task.opensage_session.sandboxes.get_sandbox("main")

            # Prefer patch generated by the agent if it already exists.
            _, shared_patch_exists = sandbox.run_command_in_container(
                "test -f /shared/prediction.patch"
            )
            if shared_patch_exists == 0:
                copy_cmd = "cp /shared/prediction.patch /workspace/prediction.patch"
                logger.info(
                    f"Found /shared/prediction.patch; copying to /workspace: {copy_cmd}"
                )
                output, copy_exit = sandbox.run_command_in_container(copy_cmd)
                if copy_exit != 0:
                    logger.warning(f"Failed to copy prediction.patch: {output}")
                else:
                    logger.info("Successfully copied prediction.patch from /shared")
                    return session

            # Locate repo: try /app first, then repo
            repo_path = "repo"  # Default fallback

            check_app_cmd = "test -d /app"
            _, app_exists = sandbox.run_command_in_container(check_app_cmd)

            if app_exists == 0:
                repo_path = "/app"
            else:
                # Check if repo exists
                check_repo_cmd = "test -d repo"
                _, exit_code = sandbox.run_command_in_container(check_repo_cmd)
                if exit_code == 0:
                    repo_path = "repo"
                else:
                    logger.warning(
                        "Repo directory not found, skipping patch generation"
                    )
                    repo_path = ""

            if repo_path:
                # Run git diff
                # We use SafeToAutoRun=True kind of logic implicitly
                diff_cmd = f"cd {repo_path} && git diff > /workspace/prediction.patch"
                logger.info(f"Generating patch from {repo_path}: {diff_cmd}")
                output, diff_exit = sandbox.run_command_in_container(diff_cmd)
                if diff_exit != 0:
                    logger.warning(f"Failed to generate patch: {output}")
                else:
                    logger.info("Successfully generated prediction.patch")

        except Exception as e:
            logger.warning(f"Error during patch generation: {e}")
        return session

    # ========= Results collection ==========

    def customized_modify_and_save_results(
        self,
        *,
        results: list | None,
        failed_samples: list[str] | None,
        mode: str,
    ) -> None:
        """Aggregate results and save predictions.json.

        Scans task output folders for patches rather than relying on result
        metadata, since the base class _collect_outputs does not include sample
        data in its return value.
        """
        predictions = self._collect_predictions_from_task_folders()
        if predictions:
            output_file = Path(self.output_dir) / self.predictions_filename
            with open(output_file, "w") as f:
                json.dump(predictions, f, indent=2)
            logger.warning(f"Saved {len(predictions)} predictions to {output_file}")

    def _collect_predictions_from_task_folders(self) -> list[dict]:
        """Scan all task folders and collect predictions from individual patch files.

        This allows re-evaluation after re-running some tasks, as it reads the latest
        patch files from each task folder rather than relying on a pre-aggregated file.

        Returns:
            List of prediction dicts: [{"instance_id": ..., "patch": ...}, ...]
        """
        predictions = []

        if not Path(self.output_dir).exists():
            logger.warning(f"Output directory does not exist: {self.output_dir}")
            return predictions

        # Scan all subdirectories in output_dir (each is a task folder)
        for task_dir in Path(self.output_dir).iterdir():
            if not task_dir.is_dir():
                continue

            # Skip non-task directories (like "results")
            if task_dir.name in ("results", "__pycache__"):
                continue

            instance_id = task_dir.name  # Task folder name is the instance_id
            sandbox_output = task_dir / "sandbox_output"
            patch_content = ""

            if sandbox_output.exists():
                # Try specific name first (check root and workspace subdir)
                candidate_paths = [
                    sandbox_output / "prediction.patch",
                    sandbox_output / "workspace" / "prediction.patch",
                ]

                for p in candidate_paths:
                    if p.exists():
                        patch_content = p.read_text()
                        break

                if not patch_content:
                    # Fallback: look for any .patch or .diff file recursively
                    patches = list(sandbox_output.rglob("*.patch")) + list(
                        sandbox_output.rglob("*.diff")
                    )
                    if patches:
                        # Take the first one
                        patch_content = patches[0].read_text()

            if patch_content:
                predictions.append({"instance_id": instance_id, "patch": patch_content})
                logger.debug(f"Collected patch for {instance_id}")
            else:
                logger.warning(f"No patch found for {instance_id} in {sandbox_output}")

        logger.info(f"Collected {len(predictions)} predictions from task folders")
        return predictions

    # ========= Evaluation ==========

    def evaluate(self) -> None:
        """Run the official SWE-bench Pro evaluation.

        Note: `Evaluation.run()` / `Evaluation.run_debug()` call `self.evaluate()`
        with no arguments, so this method must not require parameters.

        This method scans all task folders to collect the latest patches before
        evaluation, so re-running some tasks will be reflected in the results.
        """
        # Step 1: Collect predictions from all task folders (regenerate predictions.json)
        predictions = self._collect_predictions_from_task_folders()

        if not predictions:
            logger.error("No predictions found. Cannot evaluate.")
            return

        # Step 2: Save aggregated predictions
        predictions_path = Path(self.output_dir) / self.predictions_filename
        with open(predictions_path, "w") as f:
            json.dump(predictions, f, indent=2)
        logger.info(f"Saved {len(predictions)} predictions to {predictions_path}")

        # Step 3: Run official evaluation
        results_dir = Path(self.output_dir) / "results"
        self._evaluate_official(
            predictions_path=predictions_path, results_dir=results_dir
        )

        # Step 4: Record successful instances to file for future runs
        self._update_successful_instances(results_dir)

    def _update_successful_instances(self, results_dir: Path) -> None:
        """Read eval_results.json and append successful instances to tracking file."""
        eval_results_path = results_dir / "eval_results.json"
        if not eval_results_path.exists():
            logger.warning(f"eval_results.json not found at {eval_results_path}")
            return

        try:
            with open(eval_results_path, "r") as f:
                eval_results = json.load(f)

            successful_ids = {
                instance_id
                for instance_id, success in eval_results.items()
                if success is True
            }

            if not successful_ids:
                logger.info("No successful instances to record")
                return

            successful_file = Path(self.output_dir) / self.successful_instances_file
            existing_ids = set()
            if successful_file.exists():
                with open(successful_file, "r") as f:
                    existing_ids = set(line.strip() for line in f if line.strip())

            new_ids = successful_ids - existing_ids
            if new_ids:
                with open(successful_file, "a") as f:
                    for instance_id in sorted(new_ids):
                        f.write(f"{instance_id}\n")
                logger.info(
                    f"Recorded {len(new_ids)} new successful instances to {successful_file} "
                    f"(total: {len(existing_ids) + len(new_ids)})"
                )
            else:
                logger.info("No new successful instances to record")

        except Exception as e:
            logger.warning(f"Failed to update successful instances: {e}")

    def _evaluate_official(self, *, predictions_path: Path, results_dir: Path) -> None:
        """Run the official SWE-bench Pro evaluation with explicit paths."""
        logger.warning(f"Starting evaluation for {self.output_dir}...")

        # Convert to absolute paths since eval script runs from different cwd
        predictions_path = Path(predictions_path).resolve()
        results_dir = Path(results_dir).resolve()

        # Define paths
        third_party_dir = PROJECT_PATH / "third_party"
        swe_bench_repo_name = "SWE-bench_Pro-os"
        swe_bench_repo_path = third_party_dir / swe_bench_repo_name
        repo_url = "https://github.com/scaleapi/SWE-bench_Pro-os"

        # 1. Ensure the repository exists
        if not swe_bench_repo_path.exists():
            logger.warning(f"Cloning {swe_bench_repo_name} to {swe_bench_repo_path}...")
            third_party_dir.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(
                    ["git", "clone", repo_url, str(swe_bench_repo_path)],
                    check=True,
                    capture_output=True,
                )
                logger.warning(f"Successfully cloned {swe_bench_repo_name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone repo: {e.stderr.decode()}")
                return

        # 2. Check/Install requirements
        req_file = swe_bench_repo_path / "requirements.txt"
        if req_file.exists():
            logger.warning("Installing/Verifying dependencies for SWE-bench Pro...")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"Dependency installation warning: {e.stderr.decode()[:200]}..."
                )

        # 3. Construct the command
        eval_script = swe_bench_repo_path / "swe_bench_pro_eval.py"

        # Verify predictions file exists
        if not predictions_path.exists():
            logger.error(
                f"Predictions file not found at {predictions_path}. Cannot evaluate."
            )
            return

        # 2.5 Prepare Dataset CSV
        # The eval script expects a CSV file, but we have a HF dataset name.
        # We need to dump the dataset to CSV.
        # Use absolute path since eval script runs from different cwd
        dataset_csv_path = Path(self.output_dir).resolve() / "dataset.csv"
        if not dataset_csv_path.exists():
            logger.warning(
                f"Exporting dataset {self.dataset_path} to {dataset_csv_path}..."
            )
            try:
                import datasets
                import pandas as pd

                ds = datasets.load_dataset(self.dataset_path, split=self.dataset_split)
                # Convert to pandas and save
                df = ds.to_pandas()
                df.to_csv(dataset_csv_path, index=False)
            except Exception as e:
                logger.error(f"Failed to export dataset to CSV: {e}")
                return

        cmd = [
            sys.executable,
            str(eval_script),
            "--raw_sample_path",
            str(dataset_csv_path),
            "--patch_path",
            str(predictions_path),
            "--output_dir",
            str(results_dir),
            "--dockerhub_username",
            self.dockerhub_username,
            "--scripts_dir",
            str(swe_bench_repo_path / "run_scripts"),
            "--use_local_docker",
        ]

        # Ensure results directory exists
        results_dir.mkdir(parents=True, exist_ok=True)

        logger.warning(f"Running evaluation command: {' '.join(cmd)}")
        # 4. Execute
        try:
            # Streaming output might be better for long running processes
            with subprocess.Popen(
                cmd,
                cwd=str(
                    swe_bench_repo_path
                ),  # Run from its dir just in case of relative paths
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            ) as p:
                for line in p.stdout:
                    print(line, end="")  # Print to stdout so user sees progress

            if p.returncode != 0:
                logger.error(
                    f"Evaluation script finished with error code {p.returncode}"
                )
            else:
                logger.warning("Evaluation script finished successfully.")
        except Exception as e:
            logger.error(f"Failed to run evaluation script: {e}")


if __name__ == "__main__":
    fire.Fire(SweBenchPro)
