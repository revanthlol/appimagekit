"""
AppImageKit — Native Linux AppImage installer and manager.
"""

from .cli import cli
from .main import main

__version__ = "0.1.0"

__all__ = ["cli", "main", "__version__"]
