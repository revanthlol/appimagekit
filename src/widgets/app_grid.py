"""
src/widgets/app_grid.py — Responsive application grid view for AppImageKit.

Presents installed applications in a responsive Gtk.FlowBox layout.
Supports real-time search query filtering, category classification,
update state indicators, and empty-state placeholders via Adw.StatusPage.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..models.app import App
from ..services.updater import UpdateInfo
from .app_card import AppCard

logger = logging.getLogger("appimagekit.widgets.app_grid")


class AppGridView(Gtk.Box):
    """
    Grid view displaying installed AppImage cards in an Adwaita flow layout.
    """

    def __init__(
        self,
        on_app_selected: Optional[Callable[[App], None]] = None,
        on_app_launch: Optional[Callable[[App], None]] = None,
        on_app_uninstall: Optional[Callable[[App], None]] = None,
        on_app_edit: Optional[Callable[[App], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._on_app_selected = on_app_selected
        self._on_app_launch = on_app_launch
        self._on_app_uninstall = on_app_uninstall
        self._on_edit = on_app_edit

        self._apps: List[App] = []
        self._cards: Dict[int, AppCard] = {}
        self._updates_map: Dict[int, UpdateInfo] = {}
        self._current_category: str = "all"
        self._current_search_query: str = ""

        # 1. Stack for Grid vs Empty State
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)

        # 2. Scrolled FlowBox View
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_halign(Gtk.Align.FILL)
        self.flowbox.set_hexpand(True)
        self.flowbox.set_vexpand(True)
        self.flowbox.set_min_children_per_line(1)
        self.flowbox.set_max_children_per_line(10)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_column_spacing(16)
        self.flowbox.set_row_spacing(16)
        self.flowbox.set_margin_start(24)
        self.flowbox.set_margin_end(24)
        self.flowbox.set_margin_top(20)
        self.flowbox.set_margin_bottom(20)
        self.flowbox.set_filter_func(self._filter_child)

        scrolled.set_child(self.flowbox)
        self.stack.add_named(scrolled, "grid")

        # 3. Empty State Status Page
        self.status_page = Adw.StatusPage()
        self.status_page.set_icon_name("system-software-install-symbolic")
        self.status_page.set_title("No Applications Installed")
        self.status_page.set_description("Drag and drop .AppImage files anywhere to install, or click '+' in the header.")
        self.status_page.add_css_class("compact")
        self.stack.add_named(self.status_page, "empty")

        self.append(self.stack)
        self.stack.set_visible_child_name("empty")

    def _filter_child(self, child: Gtk.FlowBoxChild) -> bool:
        """Predicate checking whether a child card satisfies current category and search filter."""
        card = child.get_child()
        if not isinstance(card, AppCard):
            return True

        app = card.app

        # 1. Category Filter
        cat = self._current_category.lower()
        if cat in ("all", "*", ""):
            pass
        elif cat == "updates":
            if not card._update_available:
                return False
        elif cat == "pinned":
            if not app.pinned:
                return False
        else:
            app_cat = (app.category or "").lower()
            if cat not in app_cat and app_cat not in cat:
                return False

        # 2. Search Query Filter
        query = self._current_search_query.strip().lower()
        if query:
            name_match = query in (app.name or "").lower()
            comment_match = query in (app.comment or "").lower()
            cat_match = query in (app.category or "").lower()
            file_match = query in (app.filename or "").lower()
            if not (name_match or comment_match or cat_match or file_match):
                return False

        return True

    def set_apps(self, apps: List[App]) -> None:
        """Populate the grid with a collection of installed applications."""
        self._apps = list(apps)

        # Clear existing children
        child = self.flowbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.flowbox.remove(child)
            child = next_child

        self._cards.clear()

        # Add cards
        for app in self._apps:
            self._create_and_append_card(app)

        self._refresh_view_state()

    def add_app(self, app: App) -> None:
        """Add a newly installed app to the grid view."""
        self._apps.append(app)
        self._create_and_append_card(app)
        self._refresh_view_state()

    def remove_app(self, app_id: int) -> None:
        """Remove an uninstalled application from the grid."""
        self._apps = [a for a in self._apps if a.id != app_id]
        card = self._cards.pop(app_id, None)
        if card:
            parent = card.get_parent()
            if parent:
                self.flowbox.remove(parent)
        self._refresh_view_state()

    def _create_and_append_card(self, app: App) -> None:
        card = AppCard(
            app=app,
            on_clicked=self._on_app_selected,
            on_launch=self._on_app_launch,
            on_uninstall=self._on_app_uninstall,
            on_edit=self._on_edit,
        )

        # Check update state
        if app.id and app.id in self._updates_map:
            uinfo = self._updates_map[app.id]
            card.set_update_available(bool(uinfo.update_available))

        if app.id is not None:
            self._cards[app.id] = card

        self.flowbox.append(card)

    def set_updates(self, updates_map: Dict[int, UpdateInfo]) -> None:
        """Update badges for all cards based on available updates map."""
        self._updates_map = dict(updates_map)
        for app_id, card in self._cards.items():
            uinfo = self._updates_map.get(app_id)
            available = bool(uinfo and uinfo.update_available)
            card.set_update_available(available)

        if self._current_category == "updates":
            self.flowbox.invalidate_filter()
        self._refresh_view_state()

    def set_filter_category(self, category: str) -> None:
        """Filter visible cards by category identifier."""
        self._current_category = category
        self.flowbox.invalidate_filter()
        self._refresh_view_state()

    def set_filter_query(self, query: str) -> None:
        """Filter visible cards by search query."""
        self._current_search_query = query
        self.flowbox.invalidate_filter()
        self._refresh_view_state()

    def _refresh_view_state(self) -> None:
        """Check visible cards count and toggle empty state placeholder."""
        if not self._apps:
            self.status_page.set_title("No Applications Installed")
            self.status_page.set_description("Drop .AppImage files here or click '+' in the header to install.")
            self.stack.set_visible_child_name("empty")
            return

        # Check if any cards match the current filters
        matching_count = sum(
            1 for app in self._apps
            if self._matches_filter_logic(app, self._cards.get(app.id or -1))
        )

        if matching_count == 0:
            if self._current_search_query:
                self.status_page.set_title("No Matching Results")
                self.status_page.set_description(f"No applications matching '{self._current_search_query}'.")
            elif self._current_category == "updates":
                self.status_page.set_title("No Updates Available")
                self.status_page.set_description("All installed applications are up to date.")
            else:
                self.status_page.set_title(f"No {self._current_category.capitalize()} Apps")
                self.status_page.set_description("No applications found in this category.")
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("grid")

    def _matches_filter_logic(self, app: App, card: Optional[AppCard]) -> bool:
        cat = self._current_category.lower()
        if cat in ("all", "*", ""):
            pass
        elif cat == "updates":
            is_upd = card._update_available if card else False
            if not is_upd:
                return False
        elif cat == "pinned":
            if not app.pinned:
                return False
        else:
            app_cat = (app.category or "").lower()
            if cat not in app_cat and app_cat not in cat:
                return False

        query = self._current_search_query.strip().lower()
        if query:
            name_match = query in (app.name or "").lower()
            comment_match = query in (app.comment or "").lower()
            cat_match = query in (app.category or "").lower()
            file_match = query in (app.filename or "").lower()
            if not (name_match or comment_match or cat_match or file_match):
                return False

        return True

    def get_apps(self) -> List[App]:
        """Return currently loaded applications."""
        return list(self._apps)
