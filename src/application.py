"""
src/application.py — Main Libadwaita Application for AppImageKit.

Manages application lifecycle, single-instance coordination, desktop file associations
(opening .AppImage files directly from the desktop shell), and application-level actions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .models.database import DatabaseManager
from .window import MainWindow

logger = logging.getLogger("appimagekit.application")


class AppImageKitApplication(Adw.Application):
    """
    Main Libadwaita Application class for AppImageKit.
    """

    def __init__(
        self,
        application_id: str = "io.github.revanthlol.appimagekit",
        db: Optional[DatabaseManager] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            application_id=application_id,
            flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE,
            **kwargs,
        )
        self.db = db
        self.win: Optional[MainWindow] = None
        self._setup_actions()

    def _setup_actions(self) -> None:
        """Register application-level action handlers."""
        if not self.lookup_action("quit"):
            self._add_action("quit", lambda *_a: self.quit(), ["<primary>q", "<primary>w"])
        if not self.lookup_action("about"):
            self._add_action("about", self._on_about_action)

    def do_startup(self) -> None:
        """Initialize application state, style provider, and actions."""
        Adw.Application.do_startup(self)
        self._setup_actions()

    def _add_action(self, name: str, callback: Any, accelerators: Optional[List[str]] = None) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if accelerators:
            self.set_accels_for_action(f"app.{name}", accelerators)

    def do_activate(self) -> None:
        """Create or present the main window."""
        if not self.win:
            self.win = MainWindow(application=self, db=self.db)
        self.win.present()

    def do_open(self, files: List[Gio.File], _hint: str) -> None:
        """Handle opening files passed via file manager or CLI open command."""
        self.do_activate()
        if not self.win:
            return

        for gio_file in files:
            path_str = gio_file.get_path()
            if path_str:
                p = Path(path_str)
                if p.is_file():
                    self.win.install_appimage(p)

    def _on_about_action(self, *_args: Any) -> None:
        if self.win:
            self.win._show_about_dialog()
