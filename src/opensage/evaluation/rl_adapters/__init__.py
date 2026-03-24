"""
OpenSage RL Framework Integration Module.

Provides seamless integration between OpenSage agents and RL framework rollout systems
(slime, verl, areal, etc.).

Architecture:
    - Client: Manages agent configuration and model setup
    - RLSession: Wraps OpenSageSession with framework-specific generate methods
    - Adapters: Framework-specific logic for sample handling

Usage:
    import opensage

    client = opensage.create(agent_name, benchmark_name)
    with client.init_session() as session:
        # For slime
        sample = await session.slime_generate(args, sample, sampling_params)
        # For verl (when implemented)
        sample = await session.verl_generate(args, sample, sampling_params)
        # For areal (when implemented)
        sample = await session.areal_generate(args, sample, sampling_params)
"""

from .adapters import BaseAdapter, SlimeAdapter
from .benchmark_interface import BenchmarkInterface
from .client import Client, RLSession, create
from .slime_llm import SlimeLlm, TokenTracker

__all__ = [
    # Main API
    "create",
    "Client",
    "RLSession",
    # Adapters
    "BaseAdapter",
    "SlimeAdapter",
    # SlimeLlm (BaseLlm for sglang routing + token tracking)
    "SlimeLlm",
    "TokenTracker",
    # Benchmark interface
    "BenchmarkInterface",
]
