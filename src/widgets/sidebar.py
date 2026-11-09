"""
src/widgets/sidebar.py — Sidebar navigation and disk usage summary for AppImageKit.

Provides category filtering (All, Updates Available, Development, Media, Games,
Utilities, Graphics, Office, Pinned) and a real-time aggregate disk usage and
installed application count summary at the bottom of the navigation drawer.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango

logger = logging.getLogger("appimagekit.widgets.sidebar")


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable binary prefixes (B, KB, MB, GB, TB)."""
    if size_bytes < 0:
        size_bytes = 0
    if size_bytes < 1024:
        return f"{size_bytes} B"

    units = ["KB", "MB", "GB", "TB"]
    size = float(size_bytes) / 1024.0
    for unit in units[:-1]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


CATEGORY_DEFINITIONS: List[Tuple[str, str, str]] = [
    ("all", "All Applications", "view-grid-symbolic"),
    ("updates", "Updates Available", "software-update-available-symbolic"),
    ("Development", "Development", "applications-development-symbolic"),
    ("AudioVideo", "Media & Audio", "applications-multimedia-symbolic"),
    ("Game", "Games", "applications-games-symbolic"),
    ("Utility", "Utilities", "applications-utilities-symbolic"),
    ("Graphics", "Graphics & Design", "applications-graphics-symbolic"),
    ("Office", "Office", "x-office-document-symbolic"),
    ("pinned", "Pinned Apps", "starred-symbolic"),
]


class Sidebar(Gtk.Box):
    """
    GTK4 / Libadwaita sidebar widget with category filters and disk usage summary.
    """

    def __init__(self, on_category_selected: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("sidebar")
        self.set_size_request(240, -1)

        self._on_category_selected: Optional[Callable[[str], None]] = on_category_selected
        self._selected_category: str = "all"
        self._rows: Dict[str, Gtk.ListBoxRow] = {}
        self._badges: Dict[str, Gtk.Label] = {}

        # 1. Top Section Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.set_margin_top(12)
        header_box.set_margin_start(16)
        header_box.set_margin_end(16)
        header_box.set_margin_bottom(6)

        title_label = Gtk.Label(label="LIBRARY")
        title_label.add_css_class("dim-label")
        title_label.add_css_class("heading")
        title_label.set_halign(Gtk.Align.START)
        header_box.append(title_label)
        self.append(header_box)

        # 2. Scrollable Categories ListBox
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self._on_row_selected)

        for cat_id, title, icon_name in CATEGORY_DEFINITIONS:
            row = self._create_category_row(cat_id, title, icon_name)
            self._rows[cat_id] = row
            self.listbox.append(row)

        scrolled.set_child(self.listbox)
        self.append(scrolled)

        # Select "all" by default
        if "all" in self._rows:
            self.listbox.select_row(self._rows["all"])

        # 3. Bottom Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 4. Disk Usage & Status Summary Footer
        self.summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.summary_box.set_margin_top(10)
        self.summary_box.set_margin_bottom(14)
        self.summary_box.set_margin_start(16)
        self.summary_box.set_margin_end(16)

        # App count row
        apps_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        apps_icon = Gtk.Image.new_from_icon_name("package-x-generic-symbolic")
        apps_icon.set_pixel_size(14)
        apps_icon.add_css_class("dim-label")
        apps_row.append(apps_icon)
        self.apps_count_label = Gtk.Label(label="0 installed apps")
        self.apps_count_label.set_halign(Gtk.Align.START)
        self.apps_count_label.add_css_class("caption")
        self.apps_count_label.add_css_class("dim-label")
        apps_row.append(self.apps_count_label)
        self.summary_box.append(apps_row)

        # Disk usage row
        disk_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        disk_icon = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        disk_icon.set_pixel_size(14)
        disk_icon.add_css_class("dim-label")
        disk_row.append(disk_icon)
        self.disk_usage_label = Gtk.Label(label="0 B storage used")
        self.disk_usage_label.set_halign(Gtk.Align.START)
        self.disk_usage_label.add_css_class("caption")
        self.disk_usage_label.add_css_class("dim-label")
        disk_row.append(self.disk_usage_label)
        self.summary_box.append(disk_row)

        # Updates count row
        updates_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.updates_status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.updates_status_icon.set_pixel_size(14)
        self.updates_status_icon.add_css_class("dim-label")
        updates_row.append(self.updates_status_icon)
        self.updates_status_label = Gtk.Label(label="All apps up to date")
        self.updates_status_label.set_halign(Gtk.Align.START)
        self.updates_status_label.add_css_class("caption")
        self.updates_status_label.add_css_class("dim-label")
        updates_row.append(self.updates_status_label)
        self.summary_box.append(updates_row)

        self.append(self.summary_box)

    def _create_category_row(self, cat_id: str, title: str, icon_name: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._category_id = cat_id  # type: ignore[attr-defined]

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        box.append(icon)

        label = Gtk.Label(label=title)
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(label)

        # Badge for counters (e.g. update count)
        badge = Gtk.Label(label="")
        badge.add_css_class("badge")
        badge.add_css_class("accent")
        badge.set_visible(False)
        box.append(badge)
        self._badges[cat_id] = badge

        row.set_child(box)
        return row

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is None:
            return
        cat_id = getattr(row, "_category_id", "all")
        self._selected_category = cat_id
        if self._on_category_selected:
            self._on_category_selected(cat_id)

    def set_on_category_selected(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked when a sidebar category is selected."""
        self._on_category_selected = callback

    def get_selected_category(self) -> str:
        """Return the currently selected category id."""
        return self._selected_category

    def select_category(self, cat_id: str) -> None:
        """Programmatically select a category row by id."""
        row = self._rows.get(cat_id)
        if row:
            self.listbox.select_row(row)
            self._selected_category = cat_id

    def update_summary(self, total_apps: int, total_bytes: int, updates_count: int = 0) -> None:
        """Update aggregate installed apps count, total storage, and update count."""
        # 1. Total apps
        app_text = "1 app installed" if total_apps == 1 else f"{total_apps} apps installed"
        self.apps_count_label.set_text(app_text)

        # 2. Disk usage
        formatted_size = format_size(total_bytes)
        self.disk_usage_label.set_text(f"{formatted_size} storage used")

        # 3. Updates count & badge
        self.set_update_count(updates_count)

    def set_update_count(self, updates_count: int) -> None:
        """Update the updates available indicator and badge."""
        updates_badge = self._badges.get("updates")
        if updates_count > 0:
            if updates_badge:
                updates_badge.set_text(str(updates_count))
                updates_badge.set_visible(True)
            self.updates_status_icon.set_from_icon_name("software-update-available-symbolic")
            self.updates_status_icon.remove_css_class("dim-label")
            self.updates_status_icon.add_css_class("accent")
            self.updates_status_label.set_text(f"{updates_count} update(s) available")
            self.updates_status_label.remove_css_class("dim-label")
            self.updates_status_label.add_css_class("accent")
        else:
            if updates_badge:
                updates_badge.set_text("")
                updates_badge.set_visible(False)
            self.updates_status_icon.set_from_icon_name("emblem-ok-symbolic")
            self.updates_status_icon.remove_css_class("accent")
            self.updates_status_icon.add_css_class("dim-label")
            self.updates_status_label.set_text("All apps up to date")
            self.updates_status_label.remove_css_class("accent")
            self.updates_status_label.add_css_class("dim-label")
