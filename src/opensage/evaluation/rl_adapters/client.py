"""
OpenSage Client for RL Framework Integration.

This module provides the client class for integrating OpenSage agents
with RL frameworks like slime, verl, areal, etc.

The Client handles:
- Agent loading and configuration
- LLM model setup
- Session lifecycle management
- Framework-specific adapter creation
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opensage.session import cleanup_opensage_session, get_opensage_session

from .adapters import BaseAdapter
from .benchmark_interface import BenchmarkInterface

if TYPE_CHECKING:
    from opensage.evaluation.base import Evaluation
    from opensage.session import OpenSageSession

logger = logging.getLogger(__name__)


class Client:
    """Client for OpenSage RL framework integration.

    Manages agent configuration and session creation for RL framework rollout systems.

    Usage:
        client = opensage.create("vul_agent", "secodeplt")
        with client.init_session() as session:
            sample = await session.slime_generate(args, sample, sampling_params)
    """

    def __init__(
        self,
        agent_name: str,
        benchmark_name: str,
        model_name: str | None = None,
        **eval_kwargs: Any,
    ):
        """Initialize client.

        Args:
            agent_name (str): Name of the agent (defined in opensage/agents/ or agent_library/agents/)
            benchmark_name (str): Name of the benchmark (defined in opensage/evaluations/)
            model_name (str | None): Optional model name to override the evaluation's default.
            **eval_kwargs: Extra keyword arguments passed to the Evaluation constructor
                (e.g., dataset_path for HarborEvaluation)."""
        self.agent_name = agent_name
        self.benchmark_name = benchmark_name
        self.model_name = model_name
        self._extra_eval_kwargs = eval_kwargs

        # Resolve agent directory
        self._agent_dir = self._resolve_agent_dir()

        # Load benchmark interface and create Evaluation instance
        self._benchmark, self._evaluation = self._load_benchmark()

    def _resolve_agent_dir(self) -> str:
        """Resolve agent directory from agent name.

        Searches for agent in the installed package's agent_library/agents/ directory.

        Returns:
            str: Absolute path to agent directory

        Raises:
            ValueError: If agent directory not found
        """
        from opensage.utils.project_info import find_path

        resolved = find_path("agent_library", "agents", self.agent_name)
        if resolved.exists() and (resolved / "agent.py").exists():
            logger.info(f"Resolved agent directory: {resolved}")
            return str(resolved.resolve())

        raise ValueError(
            f"Agent '{self.agent_name}' not found. Searched via find_path: {resolved}"
        )

    def _load_benchmark(self) -> tuple[BenchmarkInterface, "Evaluation"]:
        """Load benchmark interface and create Evaluation instance.

        Returns:
            tuple[BenchmarkInterface, 'Evaluation']: Tuple of (BenchmarkInterface, Evaluation instance)
        """
        try:
            benchmark = BenchmarkInterface.load(self.benchmark_name)
        except ImportError as e:
            logger.warning(
                f"Could not load benchmark '{self.benchmark_name}': {e}. "
                f"Using default benchmark interface."
            )
            benchmark = BenchmarkInterface()

        # Create Evaluation instance
        evaluation = None
        if benchmark.evaluation_class is not None:
            try:
                # Generate a default agent_id for RL rollout
                agent_id = f"rl_{self.agent_name}_{uuid.uuid4().hex[:8]}"

                # Create instance with agent_dir and agent_id (other params use defaults)
                eval_kwargs = dict(
                    dataset_path="",
                    agent_dir=self._agent_dir,
                    agent_id=agent_id,
                    **self._extra_eval_kwargs,
                )
                if self.model_name is not None:
                    eval_kwargs["model_name"] = self.model_name
                evaluation = benchmark.evaluation_class(**eval_kwargs)
                logger.info(
                    f"Created Evaluation instance: {benchmark.evaluation_class.__name__} "
                    f"with agent_id: {agent_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Could not create Evaluation instance: {e}. "
                    f"RL rollout will use adapter's built-in agent runner."
                )

        return benchmark, evaluation

    def init_session(self, session_id: str | None = None) -> "RLSession":
        """Initialize a new session.

        Args:
            session_id (str | None): Optional session ID
        Returns:
            'RLSession': RLSession instance (usable as context manager)
        """
        return RLSession(client=self, session_id=session_id)


class RLSession:
    """Session for RL framework integration.

    Wraps OpenSageSession and provides framework-specific generate methods
    through adapters.

    Supports context manager protocol for automatic resource cleanup.
    """

    def __init__(
        self,
        client: Client,
        session_id: str | None = None,
    ):
        """Initialize session.

        Args:
            client (Client): Parent Client instance
            session_id (str | None): Optional session ID (auto-generated if not provided)"""
        self.client = client
        self.session_id = session_id or str(uuid.uuid4())
        self._opensage_session: OpenSageSession | None = None
        self._adapters: dict[str, BaseAdapter] = {}
        self._closed = False

    def __enter__(self) -> "RLSession":
        """Enter context manager."""
        # Session will be created by Evaluation._register_opensage_session()
        # when adapter.generate() is called
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up session resources."""
        if not self._closed:
            cleanup_opensage_session(self.session_id)
            self._adapters.clear()
            self._closed = True

    def _get_adapter(self, framework: str) -> BaseAdapter:
        """Get or create adapter for specified framework.

        Args:
            framework (str): Framework name ("slime", "areal", "miles", etc.)
        Returns:
            BaseAdapter: Framework-specific adapter

        Raises:
            ValueError: If framework is not registered
        """
        if framework not in self._adapters:
            dummy_session = type(
                "DummySession", (), {"opensage_session_id": self.session_id}
            )()

            adapter_cls = BaseAdapter.get(framework)
            self._adapters[framework] = adapter_cls(
                opensage_session=dummy_session,
                evaluation=self.client._evaluation,
                benchmark=self.client._benchmark,
            )

        return self._adapters[framework]

    async def slime_generate(
        self,
        args: Any,
        sample: Any,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Generate using OpenSage agent for slime rollout.

                Args:
                    args (Any): Rollout arguments from slime
                    sample (Any): Sample object with prompt and metadata
                    sampling_params (dict[str, Any]): Sampling parameters

        Raises:
          RuntimeError: Raised when this operation fails.
                Returns:
                    Any: Updated Sample object with response and status
        """
        if self._closed:
            raise RuntimeError("Session has been closed")

        adapter = self._get_adapter("slime")
        return await adapter.generate(args, sample, sampling_params)

    async def miles_generate(
        self,
        base_url: str,
        prompt: Any,
        metadata: dict[str, Any] | None = None,
        sampling_params: dict[str, Any] | None = None,
        model_name: str = "",
    ) -> dict[str, Any]:
        """Generate using OpenSage agent for Miles rollout.

        Miles handles token tracking externally via TITO session server.
        The agent just uses base_url for LLM calls (standard OpenAI API).

        Args:
            base_url: Miles session server endpoint
            prompt: Task prompt
            metadata: Task metadata from Miles sample
            sampling_params: Sampling parameters
            model_name: Model name for the agent

        Returns:
            dict with {reward, exit_status, agent_metrics, eval_report}
        """
        if self._closed:
            raise RuntimeError("Session has been closed")

        adapter = self._get_adapter("miles")
        return await adapter.generate(
            base_url=base_url,
            prompt=prompt,
            metadata=metadata,
            sampling_params=sampling_params,
            model_name=model_name,
        )

    # Future framework methods (placeholders)
    async def verl_generate(
        self,
        args: Any,
        sample: Any,
        sampling_params: dict[str, Any],
    ) -> Any:
        """Generate using OpenSage agent for verl rollout.

                Args:
                    args (Any): Rollout arguments from verl
                    sample (Any): Sample object
                    sampling_params (dict[str, Any]): Sampling parameters

        Raises:
          RuntimeError: Raised when this operation fails.
                Returns:
                    Any: Updated sample object
        """
        if self._closed:
            raise RuntimeError("Session has been closed")

        adapter = self._get_adapter("verl")
        return await adapter.generate(args, sample, sampling_params)

    async def areal_generate(
        self,
        data: dict[str, Any],
        model: Any,  # BaseLlm, but avoid import for flexibility
        **kwargs,
    ) -> dict[str, Any]:
        """Generate using OpenSage agent for AReaL rollout.

                This method accepts an ADK-compatible model (ArealLlm) from AReaL.
                ArealLlm wraps ArealOpenAI, which automatically tracks token log
                probabilities and supports reward assignment for RL training.

                This design is similar to how CAMEL integrates with AReaL.

                Args:
                    data (dict[str, Any]): Dataset sample (dict format)
                    model (Any): ADK-compatible model (ArealLlm instance)
                        Created by AReaL: ArealLlm(openai_client=ArealOpenAI(...))
                        The model automatically tracks log probs for RL training.
                    **kwargs: Additional arguments passed to Evaluation

        Raises:
          RuntimeError: Raised when this operation fails.
                Returns:
                    dict[str, Any]: Result dict from Evaluation._generate_sample

                Example (from AReaL side):
                    ```python
                    from areal.experimental.adk import ArealLlm
                    from areal.experimental.openai import ArealOpenAI

                    # Create client and model
                    client = ArealOpenAI(engine=engine, tokenizer=tokenizer, ...)
                    model = ArealLlm(openai_client=client)

                    # Run agent
                    with opensage_client.init_session() as session:
                        result = await session.areal_generate(data=data, model=model)

                    # Set reward and export (on AReaL side)
                    client.set_last_reward(result.get("reward", 0.0))
                    client.apply_reward_discount(turn_discount=0.9)
                    interactions = client.export_interactions(style="individual")
                    ```
        """
        if self._closed:
            raise RuntimeError("Session has been closed")

        adapter = self._get_adapter("areal")
        return await adapter.generate(
            data=data,
            model=model,
            **kwargs,
        )


def create(
    agent_name: str,
    benchmark_name: str,
    model_name: str | None = None,
    **eval_kwargs: Any,
) -> Client:
    """Create an OpenSage client for RL framework integration.

    This is the main entry point for RL framework integration.

    Args:
        agent_name (str): Name of the agent defined in opensage/agents/ directory
        benchmark_name (str): Name of the benchmark defined in opensage/evaluations/ directory
        model_name (str | None): Optional model name to override the evaluation's default.
        **eval_kwargs: Extra keyword arguments passed to the Evaluation constructor
            (e.g., dataset_path for HarborEvaluation).
    Returns:
        Client: Client instance

    Example:
        ```python
        import opensage

        # SeCodePLT (auto-downloads from HuggingFace)
        client = opensage.create("vul_agent_static_tools", "secodeplt")

        # Harbor tasks (auto-downloads from harbor registry)
        client = opensage.create("harbor_agent", "harbor", dataset_path="swebench")

        # Harbor tasks (local directory)
        client = opensage.create("harbor_agent", "harbor",
                                 dataset_path="/data/my_tasks")
        ```
    """
    return Client(
        agent_name=agent_name,
        benchmark_name=benchmark_name,
        model_name=model_name,
        **eval_kwargs,
    )
