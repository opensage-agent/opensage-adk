"""
SeCodePLT Evaluation Module.

Provides vulnerability detection and PoC generation evaluation capabilities
with RL framework integration support.

Usage:
    # For slime integration (unified interface)
    import aigise

    client = aigise.create("vul_agent_static_tools", "secodeplt", api_base, model_name)
    with client.init_session() as session:
        sample = await session.slime_generate(args, sample, sampling_params)

    # For reward calculation
    from evaluations.secodeplt import reward_func
    reward = await reward_func(args, sample)

    # For data models
    from evaluations.secodeplt import VulFinding, PoCFinding, Vulnerability

    # For full evaluation
    from evaluations.secodeplt import SeCodePLT
"""

from .vul_detection import (
    PoCFinding,
    SeCodePLT,
    VulComparisonResult,
    VulFinding,
    Vulnerability,
    mk_poc_agent,
    mk_vul_agent,
    reward_func,
)

__all__ = [
    # Evaluation class
    "SeCodePLT",
    # Data models
    "VulFinding",
    "PoCFinding",
    "Vulnerability",
    "VulComparisonResult",
    # Agent factories
    "mk_vul_agent",
    "mk_poc_agent",
    # Reward function (for RL frameworks)
    "reward_func",
]
