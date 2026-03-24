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

from .adapters import ArealAdapter, BaseAdapter, SlimeAdapter
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
    ):
        """Initialize client.

        Args:
            agent_name (str): Name of the agent (defined in opensage/agents/ or examples/agents/)
            benchmark_name (str): Name of the benchmark (defined in opensage/evaluations/)
            model_name (str | None): Optional model name to override the evaluation's default.
                When provided, this is passed to the evaluation class constructor
                so that prompt formatting and model-specific logic use the correct
                model identity (e.g., "qwen3-8b" instead of default "gemini-3-pro-preview")."""
        self.agent_name = agent_name
        self.benchmark_name = benchmark_name
        self.model_name = model_name

        # Resolve agent directory
        self._agent_dir = self._resolve_agent_dir()

        # Load benchmark interface and create Evaluation instance
        self._benchmark, self._evaluation = self._load_benchmark()

    def _resolve_agent_dir(self) -> str:
        """Resolve agent directory from agent name.

        Searches for agent in the installed package's examples/agents/ directory.

        Returns:
            str: Absolute path to agent directory

        Raises:
            ValueError: If agent directory not found
        """
        from opensage.utils.project_info import find_path

        resolved = find_path("examples", "agents", self.agent_name)
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
                    dataset_path="",  # Not used for RL rollout
                    agent_dir=self._agent_dir,
                    agent_id=agent_id,
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
            framework (str): Framework name ("slime", "verl", "areal", etc.)
        Returns:
            BaseAdapter: Framework-specific adapter

        Raises:
            ValueError: If framework is not supported
        """
        if framework not in self._adapters:
            # Create a temporary dummy session for adapter initialization
            # The actual session with proper config will be created by
            # Evaluation._register_opensage_session() when needed
            from opensage.session import OpenSageSession

            dummy_session = type(
                "DummySession", (), {"opensage_session_id": self.session_id}
            )()

            if framework == "slime":
                self._adapters[framework] = SlimeAdapter(
                    opensage_session=dummy_session,
                    evaluation=self.client._evaluation,
                    benchmark=self.client._benchmark,
                )
            elif framework == "verl":
                # TODO: Implement VerlAdapter
                raise NotImplementedError("verl adapter not yet implemented")
            elif framework == "areal":
                self._adapters[framework] = ArealAdapter(
                    opensage_session=dummy_session,
                    evaluation=self.client._evaluation,
                    benchmark=self.client._benchmark,
                )
            else:
                raise ValueError(f"Unsupported framework: {framework}")

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
) -> Client:
    """Create an OpenSage client for RL framework integration.

    This is the main entry point for RL framework integration.

    Args:
        agent_name (str): Name of the agent defined in opensage/agents/ directory
        benchmark_name (str): Name of the benchmark defined in opensage/evaluations/ directory
        model_name (str | None): Optional model name to override the evaluation's default.
            When using RL integration (e.g., AReaL), the actual inference model
            may differ from the evaluation's default. Passing model_name ensures
            prompt formatting and model-specific logic use the correct identity.
    Returns:
        Client: Client instance

    Example:
        ```python
        import opensage

        # Create client
        client = opensage.create("vul_agent_static_tools", "secodeplt")

        # For slime
        with client.init_session() as session:
            sample = await session.slime_generate(args, sample, sampling_params)

        # For AReaL (with model_name override)
        client = opensage.create("vul_agent_static_tools", "secodeplt", model_name="qwen3-8b")
        with client.init_session() as session:
            result = await session.areal_generate(data, model)
        ```
    """
    return Client(
        agent_name=agent_name,
        benchmark_name=benchmark_name,
        model_name=model_name,
    )
