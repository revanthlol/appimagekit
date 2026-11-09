"""
src/widgets/drop_target.py — Drag-and-drop file receiver for AppImageKit.

Attaches a GTK4 DropTarget controller to any widget (such as MainWindow or AppGridView)
to accept .AppImage files dragged from file managers (GNOME Files, Dolphin, Thunar).
Validates file extensions and magic headers and notifies the application to trigger installation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk

logger = logging.getLogger("appimagekit.widgets.drop_target")


def is_appimage_file(path: Union[str, Path]) -> bool:
    """
    Validate whether a given file path points to an AppImage.
    Checks file extension and existence.
    """
    p = Path(path)
    if not p.is_file():
        return False

    name_lower = p.name.lower()
    if name_lower.endswith(".appimage") or ".appimage." in name_lower:
        return True

    # Check ELF header or AppImage magic bytes (\x7fELF... AI\x02)
    try:
        with open(p, "rb") as f:
            header = f.read(12)
            if header.startswith(b"\x7fELF"):
                return True
            if b"AI\x02" in header:
                return True
    except Exception:
        pass

    return False


class AppImageDropTarget:
    """
    Controller wrapper managing file drag-and-drop operations for AppImages.
    """

    def __init__(
        self,
        widget: Gtk.Widget,
        on_appimages_dropped: Callable[[List[Path]], None],
    ) -> None:
        self.widget = widget
        self.on_appimages_dropped = on_appimages_dropped

        # Create GTK4 DropTarget accepting Gdk.FileList
        self.controller = Gtk.DropTarget.new(type=Gdk.FileList, actions=Gdk.DragAction.COPY)
        self.controller.connect("drop", self._on_drop)
        self.controller.connect("enter", self._on_enter)
        self.controller.connect("leave", self._on_leave)

        self.widget.add_controller(self.controller)

    def _on_enter(self, _target: Gtk.DropTarget, _x: float, _y: float) -> Gdk.DragAction:
        """Visual feedback when dragging over the receiver."""
        self.widget.add_css_class("drag-hover")
        return Gdk.DragAction.COPY

    def _on_leave(self, _target: Gtk.DropTarget) -> None:
        """Remove visual feedback when drag leaves the receiver."""
        self.widget.remove_css_class("drag-hover")

    def _on_drop(self, _target: Gtk.DropTarget, value: Any, _x: float, _y: float) -> bool:
        """Handle dropped files and extract valid AppImages."""
        self.widget.remove_css_class("drag-hover")

        if not value:
            return False

        dropped_paths: List[Path] = []

        try:
            # Gdk.FileList case
            if hasattr(value, "get_files"):
                for gio_file in value.get_files():
                    raw_path = gio_file.get_path()
                    if raw_path:
                        dropped_paths.append(Path(raw_path))
            elif isinstance(value, Gio.File):
                raw_path = value.get_path()
                if raw_path:
                    dropped_paths.append(Path(raw_path))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if hasattr(item, "get_path"):
                        raw_path = item.get_path()
                        if raw_path:
                            dropped_paths.append(Path(raw_path))
                    elif isinstance(item, (str, Path)):
                        dropped_paths.append(Path(item))
        except Exception as e:
            logger.warning("Error inspecting dropped files: %s", e)
            return False

        # Filter for valid AppImage files
        valid_appimages = [p for p in dropped_paths if is_appimage_file(p)]

        if not valid_appimages:
            logger.info("Drop rejected: no valid .AppImage files among dropped items: %s", dropped_paths)
            return False

        logger.info("Accepted %d dropped AppImage(s): %s", len(valid_appimages), valid_appimages)
        self.on_appimages_dropped(valid_appimages)
        return True


def setup_drop_target(
    widget: Gtk.Widget, on_appimages_dropped: Callable[[List[Path]], None]
) -> AppImageDropTarget:
    """Helper function to attach an AppImage drop receiver to any widget."""
    return AppImageDropTarget(widget, on_appimages_dropped)
