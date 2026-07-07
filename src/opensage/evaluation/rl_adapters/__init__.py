"""
OpenSage RL Framework Integration Module.

Provides seamless integration between OpenSage agents and RL framework rollout systems
(slime, AReaL, Miles, etc.).

Architecture:
    - Client: Manages agent configuration and model setup
    - RLSession: Wraps OpenSageSession with framework-specific generate methods
    - Adapters: Framework-specific logic for sample handling

Usage:
    import opensage

    client = opensage.create(agent_name, benchmark_name)
    with client.init_session() as session:
        sample = await session.slime_generate(args, sample, sampling_params)
        result = await session.areal_generate(data=data, model=areal_model)
        result = await session.miles_generate(
            base_url=base_url,
            prompt=prompt,
            metadata=metadata,
            sampling_params=sampling_params,
            model_name=model_name,
        )
"""

from .adapters import ArealAdapter, BaseAdapter, MilesAdapter, SlimeAdapter
from .benchmark_interface import BenchmarkInterface
from .client import Client, RLSession, create
from .slime_llm import SlimeLlm, TokenTracker

__all__ = [
    # Main API
    "create",
    "Client",
    "RLSession",
    # Adapters
    "ArealAdapter",
    "BaseAdapter",
    "MilesAdapter",
    "SlimeAdapter",
    # SlimeLlm (BaseLlm for sglang routing + token tracking)
    "SlimeLlm",
    "TokenTracker",
    # Benchmark interface
    "BenchmarkInterface",
]
