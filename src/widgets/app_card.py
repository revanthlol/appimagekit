"""
src/widgets/app_card.py — Application Card widget for AppImageKit.

Displays individual AppImage metadata (icon, application name, version,
category pill, update availability badge, and pinned indicator) in a card container.
Supports single-click navigation, double-click launch, and context menu actions.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..models.app import App

logger = logging.getLogger("appimagekit.widgets.app_card")


class AppCard(Gtk.Box):
    """
    Card representing a single installed AppImage in the application grid.
    """

    def __init__(
        self,
        app: App,
        on_clicked: Optional[Callable[[App], None]] = None,
        on_launch: Optional[Callable[[App], None]] = None,
        on_uninstall: Optional[Callable[[App], None]] = None,
        on_edit: Optional[Callable[[App], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self._on_clicked = on_clicked
        self._on_launch = on_launch
        self._on_uninstall = on_uninstall
        self._on_edit = on_edit
        self._update_available = False

        self.add_css_class("card")
        self.add_css_class("activatable")
        self.set_size_request(160, 180)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        # 1. Top Badges & Icon Overlay
        icon_overlay = Gtk.Overlay()
        icon_overlay.set_halign(Gtk.Align.CENTER)
        icon_overlay.set_valign(Gtk.Align.CENTER)
        icon_overlay.set_margin_top(12)

        self.icon_image = Gtk.Image()
        self.icon_image.set_pixel_size(64)
        self._update_icon()
        icon_overlay.set_child(self.icon_image)

        # Update available indicator badge
        self.update_badge = Gtk.Label(label="UPDATE")
        self.update_badge.add_css_class("badge")
        self.update_badge.add_css_class("accent")
        self.update_badge.set_halign(Gtk.Align.END)
        self.update_badge.set_valign(Gtk.Align.START)
        self.update_badge.set_visible(False)
        icon_overlay.add_overlay(self.update_badge)

        # Pinned indicator badge
        self.pinned_badge = Gtk.Image.new_from_icon_name("starred-symbolic")
        self.pinned_badge.set_pixel_size(14)
        self.pinned_badge.add_css_class("accent")
        self.pinned_badge.set_halign(Gtk.Align.START)
        self.pinned_badge.set_valign(Gtk.Align.START)
        self.pinned_badge.set_visible(bool(self.app.pinned))
        icon_overlay.add_overlay(self.pinned_badge)

        self.append(icon_overlay)

        # 2. Application Name
        self.name_label = Gtk.Label(label=self.app.name or "Unnamed App")
        self.name_label.add_css_class("heading")
        self.name_label.set_halign(Gtk.Align.CENTER)
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.name_label.set_max_width_chars(16)
        self.name_label.set_margin_start(8)
        self.name_label.set_margin_end(8)
        self.append(self.name_label)

        # 3. Version label
        ver_text = f"v{self.app.version}" if self.app.version else "v1.0"
        self.version_label = Gtk.Label(label=ver_text)
        self.version_label.add_css_class("caption")
        self.version_label.add_css_class("dim-label")
        self.version_label.set_halign(Gtk.Align.CENTER)
        self.version_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.version_label.set_max_width_chars(16)
        self.append(self.version_label)

        # 4. Category Badge Pill
        cat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cat_box.set_halign(Gtk.Align.CENTER)
        cat_box.set_margin_bottom(8)

        self.category_label = Gtk.Label(label=self.app.category or "Utility")
        self.category_label.add_css_class("caption")
        self.category_label.add_css_class("dim-label")
        cat_box.append(self.category_label)
        self.append(cat_box)

        # 5. Gesture Click Handling (Single click = details, Double click = launch)
        click_gesture = Gtk.GestureClick.new()
        click_gesture.set_button(0)  # Catch all buttons
        click_gesture.connect("released", self._on_gesture_released)
        self.add_controller(click_gesture)

    def _update_icon(self) -> None:
        """Load extracted icon from disk or fallback to system icon."""
        if self.app.icon_path and os.path.isfile(self.app.icon_path):
            try:
                self.icon_image.set_from_file(self.app.icon_path)
                return
            except Exception as e:
                logger.warning("Could not load icon from %s: %s", self.app.icon_path, e)

        # Fallback icon name
        self.icon_image.set_from_icon_name("application-x-executable")

    def _on_gesture_released(
        self, gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float
    ) -> None:
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_PRIMARY:
            if n_press == 2:
                # Double-click launches the app
                if self._on_launch:
                    self._on_launch(self.app)
            elif n_press == 1:
                # Single-click opens details view
                if self._on_clicked:
                    self._on_clicked(self.app)
        elif button == Gdk.BUTTON_SECONDARY:
            # Right-click context popup
            self._show_context_menu()

    def _show_context_menu(self) -> None:
        """Display right-click context menu with quick actions."""
        popover = Gtk.Popover()
        popover.set_parent(self)

        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        menu_box.set_margin_top(6)
        menu_box.set_margin_bottom(6)
        menu_box.set_margin_start(6)
        menu_box.set_margin_end(6)

        # Details button
        btn_details = Gtk.Button(label="View Details")
        btn_details.set_has_frame(False)
        btn_details.connect("clicked", lambda _b: (popover.popdown(), self._on_clicked and self._on_clicked(self.app)))
        menu_box.append(btn_details)

        # Launch button
        btn_launch = Gtk.Button(label="Launch")
        btn_launch.set_has_frame(False)
        btn_launch.connect("clicked", lambda _b: (popover.popdown(), self._on_launch and self._on_launch(self.app)))
        menu_box.append(btn_launch)

        # Edit button
        if self._on_edit:
            btn_edit = Gtk.Button(label="Edit Desktop Entry")
            btn_edit.set_has_frame(False)
            btn_edit.connect("clicked", lambda _b: (popover.popdown(), self._on_edit and self._on_edit(self.app)))
            menu_box.append(btn_edit)

        # Uninstall button
        if self._on_uninstall:
            btn_uninstall = Gtk.Button(label="Uninstall")
            btn_uninstall.add_css_class("destructive-action")
            btn_uninstall.set_has_frame(False)
            btn_uninstall.connect("clicked", lambda _b: (popover.popdown(), self._on_uninstall and self._on_uninstall(self.app)))
            menu_box.append(btn_uninstall)

        popover.set_child(menu_box)
        popover.popup()

    def set_update_available(self, available: bool) -> None:
        """Toggle update badge visibility on card."""
        self._update_available = available
        self.update_badge.set_visible(available)

    def refresh(self, app: App) -> None:
        """Update card state with new App model properties."""
        self.app = app
        self.name_label.set_text(app.name or "Unnamed App")
        ver_text = f"v{app.version}" if app.version else "v1.0"
        self.version_label.set_text(ver_text)
        self.category_label.set_text(app.category or "Utility")
        self.pinned_badge.set_visible(bool(app.pinned))
        self._update_icon()
