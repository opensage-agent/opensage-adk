"""
RL Framework Adapters for OpenSage.

Each adapter provides framework-specific integration logic for a particular
RL training framework (slime, verl, areal, etc.).
"""

from .areal import ArealAdapter
from .base import BaseAdapter
from .slime import SlimeAdapter

__all__ = [
    "ArealAdapter",
    "BaseAdapter",
    "SlimeAdapter",
]
