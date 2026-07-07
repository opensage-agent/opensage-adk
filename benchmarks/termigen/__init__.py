"""TermiGen benchmark integration for OpenSage.

TermiGen (https://github.com/ucsb-mlsec/terminal-bench-env) ships 3,500+
Docker-based terminal tasks in Harbor 2.0 format, reused here by wrapping
HarborEvaluation with a small loader that clones the upstream repo.
"""

from .termigen_bench import TermigenBench

__all__ = ["TermigenBench"]
