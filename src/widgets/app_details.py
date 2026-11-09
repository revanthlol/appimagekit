"""
src/widgets/app_details.py — Full application detail view for AppImageKit.

Displays comprehensive application metadata, large extracted icon, action buttons
(Launch, Update, Edit Desktop Entry, Uninstall), and freedesktop preferences
(Autostart on login toggle, binary location, sandboxing status, and SHA256 integrity).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..models.app import App, SandboxType, UpdateSource
from ..services.updater import UpdateInfo
from .sidebar import format_size

logger = logging.getLogger("appimagekit.widgets.app_details")


class AppDetailsView(Gtk.Box):
    """
    Detailed inspection and management page for an installed AppImage.
    """

    def __init__(
        self,
        on_back: Optional[Callable[[], None]] = None,
        on_launch: Optional[Callable[[App], None]] = None,
        on_update: Optional[Callable[[App], None]] = None,
        on_edit_desktop: Optional[Callable[[App], None]] = None,
        on_uninstall: Optional[Callable[[App], None]] = None,
        on_autostart_toggle: Optional[Callable[[App, bool], None]] = None,
        on_pin_toggle: Optional[Callable[[App, bool], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._on_back = on_back
        self._on_launch = on_launch
        self._on_update = on_update
        self._on_edit_desktop = on_edit_desktop
        self._on_uninstall = on_uninstall
        self._on_autostart_toggle = on_autostart_toggle
        self._on_pin_toggle = on_pin_toggle

        self.app: Optional[App] = None
        self.update_info: Optional[UpdateInfo] = None

        # 1. Back button header bar
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        nav_box.set_margin_top(12)
        nav_box.set_margin_start(20)
        nav_box.set_margin_end(20)
        nav_box.set_margin_bottom(8)

        self.back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.back_button.set_tooltip_text("Back to Library")
        self.back_button.add_css_class("flat")
        self.back_button.connect("clicked", lambda _b: self._on_back and self._on_back())
        nav_box.append(self.back_button)

        back_label = Gtk.Label(label="Library")
        back_label.add_css_class("heading")
        nav_box.append(back_label)
        self.append(nav_box)

        # 2. Scrolled content container with Adw.Clamp
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(780)
        clamp.set_tightening_threshold(600)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(32)
        content_box.set_margin_start(20)
        content_box.set_margin_end(20)

        # 3. Application Header (Large Icon, Name, Subtitle, Description)
        header_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        header_card.set_halign(Gtk.Align.CENTER)

        self.icon_image = Gtk.Image()
        self.icon_image.set_pixel_size(96)
        header_card.append(self.icon_image)

        self.title_label = Gtk.Label()
        self.title_label.add_css_class("title-1")
        self.title_label.set_halign(Gtk.Align.CENTER)
        header_card.append(self.title_label)

        self.meta_label = Gtk.Label()
        self.meta_label.add_css_class("dim-label")
        self.meta_label.set_halign(Gtk.Align.CENTER)
        header_card.append(self.meta_label)

        self.comment_label = Gtk.Label()
        self.comment_label.set_wrap(True)
        self.comment_label.set_halign(Gtk.Align.CENTER)
        self.comment_label.set_max_width_chars(60)
        self.comment_label.add_css_class("body")
        header_card.append(self.comment_label)

        content_box.append(header_card)

        # 4. Action Buttons Bar
        actions_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions_bar.set_halign(Gtk.Align.CENTER)
        actions_bar.set_margin_top(6)
        actions_bar.set_margin_bottom(12)

        # Launch Button
        self.btn_launch = Gtk.Button.new_with_label("Launch")
        self.btn_launch.set_icon_name("media-playback-start-symbolic")
        self.btn_launch.add_css_class("suggested-action")
        self.btn_launch.add_css_class("pill")
        self.btn_launch.connect("clicked", self._handle_launch)
        actions_bar.append(self.btn_launch)

        # Update Button
        self.btn_update = Gtk.Button.new_with_label("Update")
        self.btn_update.set_icon_name("software-update-available-symbolic")
        self.btn_update.add_css_class("pill")
        self.btn_update.connect("clicked", self._handle_update)
        actions_bar.append(self.btn_update)

        # Edit Desktop Entry Button
        self.btn_edit = Gtk.Button.new_with_label("Edit Desktop Entry")
        self.btn_edit.set_icon_name("document-edit-symbolic")
        self.btn_edit.add_css_class("pill")
        self.btn_edit.connect("clicked", self._handle_edit_desktop)
        actions_bar.append(self.btn_edit)

        # Uninstall Button
        self.btn_uninstall = Gtk.Button.new_with_label("Uninstall")
        self.btn_uninstall.set_icon_name("user-trash-symbolic")
        self.btn_uninstall.add_css_class("destructive-action")
        self.btn_uninstall.add_css_class("pill")
        self.btn_uninstall.connect("clicked", self._handle_uninstall)
        actions_bar.append(self.btn_uninstall)

        content_box.append(actions_bar)

        # 5. Preferences & Metadata Group
        self.prefs_group = Adw.PreferencesGroup()
        self.prefs_group.set_title("Application Details and Integration")

        # Autostart switch row
        self.autostart_row = Adw.SwitchRow()
        self.autostart_row.set_title("Autostart on Login")
        self.autostart_row.set_subtitle("Automatically launch this application upon user login")
        self.autostart_row.connect("notify::active", self._handle_autostart_notify)
        self.prefs_group.add(self.autostart_row)

        # Pinned switch row
        self.pinned_row = Adw.SwitchRow()
        self.pinned_row.set_title("Pinned to Top")
        self.pinned_row.set_subtitle("Keep this application pinned at the top of the grid")
        self.pinned_row.connect("notify::active", self._handle_pin_notify)
        self.prefs_group.add(self.pinned_row)

        # Install Path Row
        self.path_row = Adw.ActionRow()
        self.path_row.set_title("Binary Location")
        self.path_row.set_subtitle_selectable(True)
        self.prefs_group.add(self.path_row)

        # Desktop Entry Row
        self.desktop_row = Adw.ActionRow()
        self.desktop_row.set_title("Desktop Entry")
        self.desktop_row.set_subtitle_selectable(True)
        self.prefs_group.add(self.desktop_row)

        # Sandbox Status Row
        self.sandbox_row = Adw.ActionRow()
        self.sandbox_row.set_title("Sandbox Status")
        self.prefs_group.add(self.sandbox_row)

        # Update Source Row
        self.update_source_row = Adw.ActionRow()
        self.update_source_row.set_title("Update Mechanism")
        self.prefs_group.add(self.update_source_row)

        # Checksum SHA256 Row
        self.checksum_row = Adw.ActionRow()
        self.checksum_row.set_title("SHA256 Checksum")
        self.checksum_row.set_subtitle_selectable(True)
        self.prefs_group.add(self.checksum_row)

        # Category Row
        self.category_row = Adw.ActionRow()
        self.category_row.set_title("Category")
        self.prefs_group.add(self.category_row)

        content_box.append(self.prefs_group)

        clamp.set_child(content_box)
        scrolled.set_child(clamp)
        self.append(scrolled)

    def set_app(self, app: App, update_info: Optional[UpdateInfo] = None) -> None:
        """Bind an App model and update info to this detail view."""
        self.app = app
        self.update_info = update_info
        self.refresh()

    def refresh(self) -> None:
        """Refresh all labels, icons, switches, and action buttons."""
        if not self.app:
            return

        app = self.app

        # 1. Icon
        if app.icon_path and os.path.isfile(app.icon_path):
            try:
                self.icon_image.set_from_file(app.icon_path)
            except Exception:
                self.icon_image.set_from_icon_name("application-x-executable")
        else:
            self.icon_image.set_from_icon_name("application-x-executable")

        # 2. Title & Subtitle
        self.title_label.set_text(app.name or "Unnamed Application")
        ver = f"v{app.version}" if app.version else "v1.0"
        size = format_size(app.size_bytes)
        installed = f"Installed {app.installed_at}" if app.installed_at else "Installed"
        self.meta_label.set_text(f"{ver}  •  {size}  •  {installed}")

        # 3. Comment
        self.comment_label.set_text(app.comment or "")
        self.comment_label.set_visible(bool(app.comment))

        # 4. Action button states
        if self.update_info and self.update_info.update_available:
            self.btn_update.add_css_class("suggested-action")
            latest_v = self.update_info.latest_version or "new version"
            self.btn_update.set_label(f"Update to {latest_v}")
        else:
            self.btn_update.remove_css_class("suggested-action")
            self.btn_update.set_label("Check Update")

        # 5. Switches (freeze notification during programmatic set)
        self.autostart_row.handler_block_by_func(self._handle_autostart_notify)
        self.autostart_row.set_active(bool(app.autostart))
        self.autostart_row.handler_unblock_by_func(self._handle_autostart_notify)

        self.pinned_row.handler_block_by_func(self._handle_pin_notify)
        self.pinned_row.set_active(bool(app.pinned))
        self.pinned_row.handler_unblock_by_func(self._handle_pin_notify)

        # 6. Metadata rows
        self.path_row.set_subtitle(app.install_path or "Unknown")
        self.desktop_row.set_subtitle(app.desktop_entry or "None")

        # Sandbox status
        if app.sandboxed:
            sb_name = app.sandbox_type.value.capitalize() if app.sandbox_type else "Enabled"
            self.sandbox_row.set_subtitle(f"🔒 Sandboxed ({sb_name})")
        else:
            self.sandbox_row.set_subtitle("🔓 Unsandboxed (direct host execution)")

        # Update source
        upd_src = getattr(app.update_source, "value", str(app.update_source))
        if app.update_url:
            self.update_source_row.set_subtitle(f"{upd_src} ({app.update_url})")
        elif app.zsync_url:
            self.update_source_row.set_subtitle(f"{upd_src} ({app.zsync_url})")
        else:
            self.update_source_row.set_subtitle(upd_src)

        # Checksum & Category
        self.checksum_row.set_subtitle(app.checksum_sha256 or "Unknown")
        self.category_row.set_subtitle(app.category or "Utility")

    def _handle_launch(self, _btn: Gtk.Button) -> None:
        if self.app and self._on_launch:
            self._on_launch(self.app)

    def _handle_update(self, _btn: Gtk.Button) -> None:
        if self.app and self._on_update:
            self._on_update(self.app)

    def _handle_edit_desktop(self, _btn: Gtk.Button) -> None:
        if self.app and self._on_edit_desktop:
            self._on_edit_desktop(self.app)

    def _handle_uninstall(self, _btn: Gtk.Button) -> None:
        if not self.app:
            return

        # Show Adw confirmation dialog
        root = self.get_root()
        dialog = Adw.MessageDialog(
            transient_for=root if isinstance(root, Gtk.Window) else None,
            heading=f"Uninstall {self.app.name}?",
            body=(
                f"Are you sure you want to uninstall '{self.app.name}'? "
                "This will permanently delete the binary, desktop entry, extracted icons, and settings."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("uninstall", "Uninstall")
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response == "uninstall" and self.app and self._on_uninstall:
                self._on_uninstall(self.app)

        dialog.connect("response", _on_response)
        dialog.present()

    def _handle_autostart_notify(self, _row: Adw.SwitchRow, _param: Any) -> None:
        if self.app and self._on_autostart_toggle:
            active = self.autostart_row.get_active()
            self._on_autostart_toggle(self.app, active)

    def _handle_pin_notify(self, _row: Adw.SwitchRow, _param: Any) -> None:
        if self.app and self._on_pin_toggle:
            active = self.pinned_row.get_active()
            self._on_pin_toggle(self.app, active)
