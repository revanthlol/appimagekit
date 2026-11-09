"""
src/widgets/desktop_editor.py — Desktop entry editor dialog with live preview.

Allows users to inspect and customize Name, Comment, Executable arguments,
Icon, Categories, Terminal, and StartupWMClass metadata. Features real-time
synchronization with an in-app live .desktop text preview and atomic persistence.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango

from ..models.app import App
from ..services.desktop_entry import DesktopEntryService

logger = logging.getLogger("appimagekit.widgets.desktop_editor")


class DesktopEditorDialog(Adw.Window):
    """
    Adwaita dialog for modifying freedesktop .desktop entry properties with live preview.
    """

    def __init__(
        self,
        app: App,
        desktop_service: Optional[DesktopEntryService] = None,
        parent: Optional[Gtk.Window] = None,
        on_saved: Optional[Callable[[App], None]] = None,
    ) -> None:
        super().__init__()
        self.app = app
        self.desktop_service = desktop_service or DesktopEntryService()
        self._on_saved = on_saved

        self.set_title(f"Edit Desktop Entry — {self.app.name}")
        self.set_default_size(880, 580)
        self.set_modal(True)
        if parent:
            self.set_transient_for(parent)

        # 1. Main ToolbarView
        toolbar_view = Adw.ToolbarView()

        # HeaderBar
        header_bar = Adw.HeaderBar()
        header_bar.set_show_end_title_buttons(False)
        header_bar.set_show_start_title_buttons(False)

        # Cancel button
        btn_cancel = Gtk.Button.new_with_label("Cancel")
        btn_cancel.connect("clicked", lambda _b: self.close())
        header_bar.pack_start(btn_cancel)

        # Save button
        self.btn_save = Gtk.Button.new_with_label("Save")
        self.btn_save.add_css_class("suggested-action")
        self.btn_save.connect("clicked", self._on_save_clicked)
        header_bar.pack_end(self.btn_save)

        # Reset button
        btn_reset = Gtk.Button.new_with_label("Reset Defaults")
        btn_reset.add_css_class("flat")
        btn_reset.connect("clicked", self._on_reset_clicked)
        header_bar.pack_end(btn_reset)

        toolbar_view.add_top_bar(header_bar)

        # 2. Split Body (Form on left, Live Preview on right)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(440)
        paned.set_wide_handle(True)

        # Left Column: Form Fields
        form_scrolled = Gtk.ScrolledWindow()
        form_scrolled.set_vexpand(True)
        form_scrolled.set_hexpand(True)
        form_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        form_box.set_margin_top(16)
        form_box.set_margin_bottom(16)
        form_box.set_margin_start(16)
        form_box.set_margin_end(16)

        prefs_group = Adw.PreferencesGroup()
        prefs_group.set_title("Desktop Entry Metadata")
        prefs_group.set_description("Customize how this application appears in your application launcher")

        # Name entry row
        self.name_row = Adw.EntryRow()
        self.name_row.set_title("Application Name")
        self.name_row.set_text(self.app.name or "")
        self.name_row.connect("notify::text", self._on_field_changed)
        prefs_group.add(self.name_row)

        # Comment / description row
        self.comment_row = Adw.EntryRow()
        self.comment_row.set_title("Comment / Description")
        self.comment_row.set_text(self.app.comment or "")
        self.comment_row.connect("notify::text", self._on_field_changed)
        prefs_group.add(self.comment_row)

        # Executable arguments row
        self.exec_row = Adw.EntryRow()
        self.exec_row.set_title("Executable Arguments")
        self.exec_row.set_text(self.app.exec_args or "")
        self.exec_row.connect("notify::text", self._on_field_changed)
        prefs_group.add(self.exec_row)

        # Icon row
        self.icon_row = Adw.EntryRow()
        self.icon_row.set_title("Icon Name or Path")
        self.icon_row.set_text(self.app.icon_path or "")
        self.icon_row.connect("notify::text", self._on_field_changed)
        prefs_group.add(self.icon_row)

        # Categories row
        self.categories_row = Adw.EntryRow()
        self.categories_row.set_title("Categories (semicolon-separated)")
        cat_text = self.app.category or "Utility"
        if not cat_text.endswith(";"):
            cat_text += ";"
        self.categories_row.set_text(cat_text)
        self.categories_row.connect("notify::text", self._on_field_changed)
        prefs_group.add(self.categories_row)

        # Terminal switch row
        self.terminal_row = Adw.SwitchRow()
        self.terminal_row.set_title("Run in Terminal")
        self.terminal_row.set_subtitle("Launch application inside a terminal emulator window")
        self.terminal_row.set_active(bool(self.app.terminal))
        self.terminal_row.connect("notify::active", self._on_field_changed)
        prefs_group.add(self.terminal_row)

        # StartupWMClass row
        self.startup_wm_row = Adw.EntryRow()
        self.startup_wm_row.set_title("Startup WM Class")
        self.startup_wm_row.set_text(self.app.startup_wm_class or "")
        self.startup_wm_row.connect("notify::text", self._on_field_changed)
        prefs_group.add(self.startup_wm_row)

        form_box.append(prefs_group)
        form_scrolled.set_child(form_box)
        paned.set_start_child(form_scrolled)

        # Right Column: Live .desktop File Preview
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preview_box.set_margin_top(16)
        preview_box.set_margin_bottom(16)
        preview_box.set_margin_start(16)
        preview_box.set_margin_end(16)

        preview_header = Gtk.Label(label="LIVE .DESKTOP PREVIEW")
        preview_header.add_css_class("heading")
        preview_header.add_css_class("dim-label")
        preview_header.set_halign(Gtk.Align.START)
        preview_box.append(preview_header)

        preview_scrolled = Gtk.ScrolledWindow()
        preview_scrolled.set_vexpand(True)
        preview_scrolled.set_hexpand(True)
        preview_scrolled.add_css_class("card")

        self.preview_buffer = Gtk.TextBuffer()
        self.preview_view = Gtk.TextView.new_with_buffer(self.preview_buffer)
        self.preview_view.set_editable(False)
        self.preview_view.set_cursor_visible(False)
        self.preview_view.set_monospace(True)
        self.preview_view.set_left_margin(12)
        self.preview_view.set_right_margin(12)
        self.preview_view.set_top_margin(12)
        self.preview_view.set_bottom_margin(12)
        preview_scrolled.set_child(self.preview_view)

        preview_box.append(preview_scrolled)
        paned.set_end_child(preview_box)

        toolbar_view.set_content(paned)
        self.set_content(toolbar_view)

        # Initial preview generation
        self._update_preview()

    def _generate_preview_text(self) -> str:
        """Construct the raw Freedesktop .desktop string from current form inputs."""
        name = self.name_row.get_text().strip() or self.app.name or "App"
        comment = self.comment_row.get_text().strip()
        exec_args = self.exec_row.get_text().strip()
        icon = self.icon_row.get_text().strip()
        categories = self.categories_row.get_text().strip()
        terminal = self.terminal_row.get_active()
        startup_wm = self.startup_wm_row.get_text().strip()

        exec_line = DesktopEntryService.format_exec(self.app.install_path, exec_args)
        formatted_categories = DesktopEntryService.format_categories(categories)

        lines = [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.5",
            f"Name={name}",
        ]
        if comment:
            lines.append(f"Comment={comment}")
        lines.append(f"Exec={exec_line}")
        if icon:
            lines.append(f"Icon={icon}")
        lines.append(f"Categories={formatted_categories}")
        lines.append(f"Terminal={'true' if terminal else 'false'}")
        if startup_wm:
            lines.append(f"StartupWMClass={startup_wm}")
        if self.app.version:
            lines.append(f"X-AppImage-Version={self.app.version}")

        return "\n".join(lines) + "\n"

    def _update_preview(self) -> None:
        """Render generated preview text to the live preview buffer."""
        preview_text = self._generate_preview_text()
        self.preview_buffer.set_text(preview_text)

    def _on_field_changed(self, *_args: Any) -> None:
        """Update live preview when any form field changes."""
        self._update_preview()

    def _on_reset_clicked(self, _btn: Gtk.Button) -> None:
        """Reset form fields to app defaults."""
        self.name_row.set_text(self.app.name or "")
        self.comment_row.set_text(self.app.comment or "")
        self.exec_row.set_text(self.app.exec_args or "")
        self.icon_row.set_text(self.app.icon_path or "")
        cat_text = self.app.category or "Utility"
        if not cat_text.endswith(";"):
            cat_text += ";"
        self.categories_row.set_text(cat_text)
        self.terminal_row.set_active(bool(self.app.terminal))
        self.startup_wm_row.set_text(self.app.startup_wm_class or "")
        self._update_preview()

    def _on_save_clicked(self, _btn: Gtk.Button) -> None:
        """Commit changes to the desktop entry file and database."""
        name = self.name_row.get_text().strip() or self.app.name
        comment = self.comment_row.get_text().strip()
        exec_args = self.exec_row.get_text().strip()
        icon = self.icon_row.get_text().strip()
        categories = self.categories_row.get_text().strip()
        terminal = self.terminal_row.get_active()
        startup_wm = self.startup_wm_row.get_text().strip() or None

        updated_app = self.desktop_service.update_entry(
            app=self.app,
            name=name,
            comment=comment,
            exec_args=exec_args,
            icon=icon,
            categories=categories,
            terminal=terminal,
            startup_wm_class=startup_wm,
        )

        if self._on_saved:
            self._on_saved(updated_app)

        self.close()
