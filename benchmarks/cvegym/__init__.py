"""CVEGym benchmark integration for OpenSage.

CVEGym evaluates an agent's ability to patch known CVEs in open-source
projects. The dataset is loaded from HuggingFace (scale-env/cve-dockerfile-benchmark).
Each task places the agent in a Docker container with the vulnerable codebase
and asks it to fix the vulnerability so the project's test suite passes.
"""

from .cvegym_bench import CVEGym

__all__ = ["CVEGym"]
