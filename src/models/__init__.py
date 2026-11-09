"""
src/models/__init__.py — AppImageKit Domain & Persistence Models.
"""

from .app import App, InstallMode, UpdateSource, SandboxType
from .database import DatabaseManager

__all__ = [
    "App",
    "InstallMode",
    "UpdateSource",
    "SandboxType",
    "DatabaseManager",
]
