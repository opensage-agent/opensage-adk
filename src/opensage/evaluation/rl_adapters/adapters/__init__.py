"""
RL Framework Adapters for OpenSage.

Each adapter provides framework-specific integration logic for a particular
RL training framework (slime, AReaL, Miles, etc.).
"""

from .areal import ArealAdapter
from .base import BaseAdapter
from .miles import MilesAdapter
from .slime import SlimeAdapter

__all__ = [
    "ArealAdapter",
    "BaseAdapter",
    "MilesAdapter",
    "SlimeAdapter",
]
