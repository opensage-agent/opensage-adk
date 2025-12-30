"""
AIgiSE Client for RL Framework Integration.

This module provides the client class for integrating AIgiSE agents
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

from aigise.session import cleanup_aigise_session, get_aigise_session

from .adapters import ArealAdapter, BaseAdapter, SlimeAdapter
from .benchmark_interface import BenchmarkInterface

if TYPE_CHECKING:
    from aigise.evaluations import Evaluation
    from aigise.session import AigiseSession

logger = logging.getLogger(__name__)


class Client:
    """Client for AIgiSE RL framework integration.

    Manages agent configuration and session creation for RL framework rollout systems.

    Usage:
        client = aigise.create("vul_agent", "secodeplt")
        with client.init_session() as session:
            sample = await session.slime_generate(args, sample, sampling_params)
    """

    def __init__(
        self,
        agent_name: str,
        benchmark_name: str,
    ):
        """Initialize client.

        Args:
            agent_name: Name of the agent (defined in aigise/agents/ or examples/agents/)
            benchmark_name: Name of the benchmark (defined in aigise/evaluations/)
        """
        self.agent_name = agent_name
        self.benchmark_name = benchmark_name

        # Resolve agent directory
        self._agent_dir = self._resolve_agent_dir()

        # Load benchmark interface and create Evaluation instance
        self._benchmark, self._evaluation = self._load_benchmark()

    def _resolve_agent_dir(self) -> str:
        """Resolve agent directory from agent name.

        Searches for agent in the installed package's examples/agents/ directory.

        Returns:
            Absolute path to agent directory

        Raises:
            ValueError: If agent directory not found
        """
        # Package-installed path: aigise/examples/agents/<agent_name>
        # __file__ = aigise/rl_integration/client.py
        # parent.parent = aigise/
        package_path = (
            Path(__file__).parent.parent / "examples" / "agents" / self.agent_name
        )

        if package_path.exists() and (package_path / "agent.py").exists():
            logger.info(f"Resolved agent directory: {package_path}")
            return str(package_path.resolve())

        raise ValueError(
            f"Agent '{self.agent_name}' not found. Expected at: {package_path}"
        )

    def _load_benchmark(self) -> tuple[BenchmarkInterface, "Evaluation"]:
        """Load benchmark interface and create Evaluation instance.

        Returns:
            Tuple of (BenchmarkInterface, Evaluation instance)
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
                evaluation = benchmark.evaluation_class(
                    dataset_path="",  # Not used for RL rollout
                    agent_dir=self._agent_dir,
                    agent_id=agent_id,
                )
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
            session_id: Optional session ID

        Returns:
            RLSession instance (usable as context manager)
        """
        return RLSession(client=self, session_id=session_id)


class RLSession:
    """Session for RL framework integration.

    Wraps AigiseSession and provides framework-specific generate methods
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
            client: Parent Client instance
            session_id: Optional session ID (auto-generated if not provided)
        """
        self.client = client
        self.session_id = session_id or str(uuid.uuid4())
        self._aigise_session: AigiseSession | None = None
        self._adapters: dict[str, BaseAdapter] = {}
        self._closed = False

    def __enter__(self) -> "RLSession":
        """Enter context manager."""
        # Session will be created by Evaluation._register_aigise_session()
        # when adapter.generate() is called
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up session resources."""
        if not self._closed:
            cleanup_aigise_session(self.session_id)
            self._adapters.clear()
            self._closed = True

    def _get_adapter(self, framework: str) -> BaseAdapter:
        """Get or create adapter for specified framework.

        Args:
            framework: Framework name ("slime", "verl", "areal", etc.)

        Returns:
            Framework-specific adapter

        Raises:
            ValueError: If framework is not supported
        """
        if framework not in self._adapters:
            # Create a temporary dummy session for adapter initialization
            # The actual session with proper config will be created by
            # Evaluation._register_aigise_session() when needed
            from aigise.session import AigiseSession

            dummy_session = type(
                "DummySession", (), {"aigise_session_id": self.session_id}
            )()

            if framework == "slime":
                self._adapters[framework] = SlimeAdapter(
                    aigise_session=dummy_session,
                    evaluation=self.client._evaluation,
                    benchmark=self.client._benchmark,
                )
            elif framework == "verl":
                # TODO: Implement VerlAdapter
                raise NotImplementedError("verl adapter not yet implemented")
            elif framework == "areal":
                self._adapters[framework] = ArealAdapter(
                    aigise_session=dummy_session,
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
        """Generate using AIgiSE agent for slime rollout.

        Args:
            args: Rollout arguments from slime
            sample: Sample object with prompt and metadata
            sampling_params: Sampling parameters

        Returns:
            Updated Sample object with response and status
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
        """Generate using AIgiSE agent for verl rollout.

        Args:
            args: Rollout arguments from verl
            sample: Sample object
            sampling_params: Sampling parameters

        Returns:
            Updated sample object
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
        """Generate using AIgiSE agent for AReaL rollout.

        This method accepts an ADK-compatible model (ArealLlm) from AReaL.
        ArealLlm wraps ArealOpenAI, which automatically tracks token log
        probabilities and supports reward assignment for RL training.

        This design is similar to how CAMEL integrates with AReaL.

        Args:
            data: Dataset sample (dict format)
            model: ADK-compatible model (ArealLlm instance)
                Created by AReaL: ArealLlm(openai_client=ArealOpenAI(...))
                The model automatically tracks log probs for RL training.
            **kwargs: Additional arguments passed to Evaluation

        Returns:
            Result dict from Evaluation._generate_sample

        Example (from AReaL side):
            ```python
            from areal.experimental.adk import ArealLlm
            from areal.experimental.openai import ArealOpenAI

            # Create client and model
            client = ArealOpenAI(engine=engine, tokenizer=tokenizer, ...)
            model = ArealLlm(openai_client=client)

            # Run agent
            with aigise_client.init_session() as session:
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
) -> Client:
    """Create an AIgiSE client for RL framework integration.

    This is the main entry point for RL framework integration.

    Args:
        agent_name: Name of the agent defined in aigise/agents/ directory
        benchmark_name: Name of the benchmark defined in aigise/evaluations/ directory

    Returns:
        Client instance

    Example:
        ```python
        import aigise

        # Create client
        client = aigise.create("vul_agent_static_tools", "secodeplt")

        # For slime
        with client.init_session() as session:
            sample = await session.slime_generate(args, sample, sampling_params)

        # For AReaL
        with client.init_session() as session:
            result = await session.areal_generate(data, model)
        ```
    """
    return Client(
        agent_name=agent_name,
        benchmark_name=benchmark_name,
    )
