"""
src/widgets/__init__.py — UI widgets package for AppImageKit.
"""

from .app_card import AppCard
from .app_details import AppDetailsView
from .app_grid import AppGridView
from .desktop_editor import DesktopEditorDialog
from .drop_target import AppImageDropTarget, is_appimage_file, setup_drop_target
from .sidebar import Sidebar, format_size

__all__ = [
    "Sidebar",
    "format_size",
    "AppCard",
    "AppGridView",
    "AppDetailsView",
    "DesktopEditorDialog",
    "AppImageDropTarget",
    "is_appimage_file",
    "setup_drop_target",
]
