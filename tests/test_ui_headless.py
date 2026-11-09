"""
tests/test_ui_headless.py — Comprehensive Headless UI Tests for AppImageKit.

Tests controller logic, model/view data binding, category filtering,
disk usage aggregation, drag-and-drop validation, desktop editor dialogs,
and GTK4/Libadwaita application window state management.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

# Ensure Adwaita is initialized
Adw.init()

from src.models.app import App, InstallMode, SandboxType, UpdateSource
from src.models.database import DatabaseManager
from src.services.updater import UpdateInfo
from src.utils.process import AsyncWorker
from src.widgets.app_card import AppCard
from src.widgets.app_details import AppDetailsView
from src.widgets.app_grid import AppGridView
from src.widgets.desktop_editor import DesktopEditorDialog
from src.widgets.drop_target import AppImageDropTarget, is_appimage_file, setup_drop_target
from src.widgets.sidebar import Sidebar, format_size
from src.window import MainWindow
from src.application import AppImageKitApplication


# ---------------------------------------------------------------------------
# 1. Format Size & Utilities
# ---------------------------------------------------------------------------

def test_format_size_edge_cases():
    """Verify human-readable binary size string conversions."""
    assert format_size(-100) == "0 B"
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1536) == "1.5 KB"
    assert format_size(1048576) == "1.0 MB"
    assert format_size(1048576 * 150) == "150.0 MB"
    assert format_size(1073741824) == "1.0 GB"
    assert format_size(1073741824 * 3) == "3.0 GB"


def test_async_worker_execution_and_idle_dispatch():
    """Verify AsyncWorker executes background work and dispatches callbacks."""
    worker = AsyncWorker(max_workers=2, sync_dispatch=True)
    results: List[int] = []
    errors: List[Exception] = []

    # Successful execution
    fut = worker.run_async(lambda x, y: x + y, on_success=lambda r: results.append(r), x=10, y=32)
    assert fut.result(timeout=2) == 42
    assert results == [42]

    # Error handling
    def _fail():
        raise ValueError("Simulated task failure")

    fut_err = worker.run_async(_fail, on_error=lambda err: errors.append(err))
    with pytest.raises(ValueError):
        fut_err.result(timeout=2)

    assert len(errors) == 1
    assert "Simulated task failure" in str(errors[0])
    worker.shutdown(wait=True)


# ---------------------------------------------------------------------------
# 2. Sidebar Navigation & Disk Usage
# ---------------------------------------------------------------------------

def test_sidebar_categories_and_selection():
    """Verify Sidebar initializes with standard categories and emits selection events."""
    selected_cats: List[str] = []
    sidebar = Sidebar(on_category_selected=lambda cat: selected_cats.append(cat))

    assert sidebar.get_selected_category() == "all"
    assert "all" in sidebar._rows
    assert "updates" in sidebar._rows
    assert "Development" in sidebar._rows
    assert "pinned" in sidebar._rows

    # Programmatic selection
    sidebar.select_category("Development")
    assert sidebar.get_selected_category() == "Development"
    assert "Development" in selected_cats


def test_sidebar_disk_usage_and_update_counters():
    """Verify Sidebar calculates and formats aggregate storage and badges."""
    sidebar = Sidebar()

    # Initial state
    assert "0 installed apps" in sidebar.apps_count_label.get_text()
    assert "0 B" in sidebar.disk_usage_label.get_text()

    # Update summary with 4 apps, 250 MB total, 2 updates
    sidebar.update_summary(total_apps=4, total_bytes=262144000, updates_count=2)

    assert "4 apps installed" in sidebar.apps_count_label.get_text()
    assert "250.0 MB" in sidebar.disk_usage_label.get_text()
    assert "2 update(s) available" in sidebar.updates_status_label.get_text()
    assert sidebar._badges["updates"].get_visible() is True
    assert sidebar._badges["updates"].get_text() == "2"

    # Reset updates
    sidebar.set_update_count(0)
    assert "All apps up to date" in sidebar.updates_status_label.get_text()
    assert sidebar._badges["updates"].get_visible() is False


# ---------------------------------------------------------------------------
# 3. AppCard Presentation & Gestures
# ---------------------------------------------------------------------------

def test_app_card_presentation_and_badging(tmp_path):
    """Verify AppCard renders metadata, fallback/custom icons, and update badges."""
    # Create fake icon
    fake_icon = tmp_path / "app.png"
    fake_icon.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    app = App(
        id=42,
        name="Blender 3D",
        version="4.1.0",
        category="Graphics",
        icon_path=str(fake_icon),
        pinned=True,
    )

    clicked_apps: List[App] = []
    card = AppCard(app=app, on_clicked=lambda a: clicked_apps.append(a))

    assert card.name_label.get_text() == "Blender 3D"
    assert card.version_label.get_text() == "v4.1.0"
    assert card.category_label.get_text() == "Graphics"
    assert card.pinned_badge.get_visible() is True
    assert card.update_badge.get_visible() is False

    # Toggle update badge
    card.set_update_available(True)
    assert card.update_badge.get_visible() is True

    # Refresh with new version
    app.version = "4.2.0"
    card.refresh(app)
    assert card.version_label.get_text() == "v4.2.0"


# ---------------------------------------------------------------------------
# 4. AppGridView Filtering & View State
# ---------------------------------------------------------------------------

def test_app_grid_filtering_and_empty_state():
    """Verify AppGridView FlowBox filtering by category and search query."""
    grid = AppGridView()

    apps = [
        App(id=1, name="Godot Engine", category="Development", version="4.2", comment="Game Engine"),
        App(id=2, name="SuperTuxKart", category="Game", version="1.4", comment="Kart Racing Game"),
        App(id=3, name="VLC Media Player", category="AudioVideo", version="3.0.20", comment="Media Player"),
        App(id=4, name="LibreOffice", category="Office", version="7.6", comment="Office Suite", pinned=True),
    ]
    grid.set_apps(apps)
    assert len(grid.get_apps()) == 4
    assert grid.stack.get_visible_child_name() == "grid"

    # Category filter: Game
    grid.set_filter_category("Game")
    assert grid.stack.get_visible_child_name() == "grid"

    # Search filter: "Tux"
    grid.set_filter_query("Tux")
    assert grid.stack.get_visible_child_name() == "grid"

    # Search filter: Non-matching query triggers empty state
    grid.set_filter_query("NonExistentTermXYZ")
    assert grid.stack.get_visible_child_name() == "empty"

    # Clear filters
    grid.set_filter_query("")
    grid.set_filter_category("all")
    assert grid.stack.get_visible_child_name() == "grid"

    # Pinned filter
    grid.set_filter_category("pinned")
    assert grid.stack.get_visible_child_name() == "grid"

    # Updates filter (initially 0 updates -> empty)
    grid.set_filter_category("updates")
    assert grid.stack.get_visible_child_name() == "empty"

    # Provide update for Godot Engine
    grid.set_updates({1: UpdateInfo(available=True, latest_version="4.3")})
    assert grid.stack.get_visible_child_name() == "grid"


def test_app_grid_dynamic_add_and_remove():
    """Verify adding and removing cards updates grid state dynamically."""
    grid = AppGridView()
    app1 = App(id=10, name="AppTen", category="Utility")
    grid.add_app(app1)

    assert len(grid.get_apps()) == 1
    assert grid.stack.get_visible_child_name() == "grid"

    grid.remove_app(10)
    assert len(grid.get_apps()) == 0
    assert grid.stack.get_visible_child_name() == "empty"


# ---------------------------------------------------------------------------
# 5. AppDetailsView Binding & Actions
# ---------------------------------------------------------------------------

def test_app_details_view_binding_and_switches():
    """Verify AppDetailsView correctly binds App properties and reflects state."""
    app = App(
        id=99,
        name="Firefox Nightly",
        version="128.0a1",
        size_bytes=150 * 1024 * 1024,
        installed_at="2026-11-09",
        comment="Fast, Private & Safe Web Browser",
        install_path="/home/rev/.local/share/appimages/firefox.AppImage",
        desktop_entry="/home/rev/.local/share/applications/firefox.desktop",
        sandboxed=True,
        sandbox_type=SandboxType.BUBBLEWRAP,
        autostart=True,
        pinned=True,
        update_source=UpdateSource.GITHUB,
        update_url="https://github.com/mozilla/firefox",
        checksum_sha256="abcdef1234567890",
        category="Network",
    )

    autostart_events = []
    pin_events = []

    details = AppDetailsView(
        on_autostart_toggle=lambda a, active: autostart_events.append((a, active)),
        on_pin_toggle=lambda a, active: pin_events.append((a, active)),
    )
    details.set_app(app)

    assert details.title_label.get_text() == "Firefox Nightly"
    assert "150.0 MB" in details.meta_label.get_text()
    assert "128.0a1" in details.meta_label.get_text()
    assert details.comment_label.get_text() == "Fast, Private & Safe Web Browser"
    assert details.autostart_row.get_active() is True
    assert details.pinned_row.get_active() is True
    assert "/firefox.AppImage" in details.path_row.get_subtitle()
    assert "Bubblewrap" in details.sandbox_row.get_subtitle()
    assert "abcdef1234567890" in details.checksum_row.get_subtitle()

    # Update notification on button
    update_info = UpdateInfo(available=True, latest_version="129.0a1")
    details.set_app(app, update_info)
    assert "Update to 129.0a1" in details.btn_update.get_label()


# ---------------------------------------------------------------------------
# 6. DesktopEditorDialog & Live Preview
# ---------------------------------------------------------------------------

def test_desktop_editor_live_preview_and_save(tmp_path):
    """Verify DesktopEditorDialog generates live preview and saves changes."""
    app = App(
        id=7,
        name="OriginalName",
        comment="Original description",
        install_path="/path/to/app.AppImage",
        category="Utility",
        version="1.0.0",
    )

    saved_apps: List[App] = []
    mock_service = MagicMock()
    mock_service.update_entry.side_effect = lambda app, name, comment, exec_args, icon, categories, terminal, startup_wm_class: App(
        id=app.id,
        name=name,
        comment=comment,
        exec_args=exec_args,
        category=categories,
        terminal=terminal,
        install_path=app.install_path,
        version=app.version,
    )

    dlg = DesktopEditorDialog(
        app=app,
        desktop_service=mock_service,
        on_saved=lambda a: saved_apps.append(a),
    )

    # Initial live preview check
    buf = dlg.preview_buffer
    initial_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert "Name=OriginalName" in initial_text
    assert "Comment=Original description" in initial_text
    assert "Terminal=false" in initial_text

    # Modify form fields
    dlg.name_row.set_text("CustomName")
    dlg.comment_row.set_text("New edited comment")
    dlg.terminal_row.set_active(True)

    # Live preview should immediately update
    updated_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert "Name=CustomName" in updated_text
    assert "Comment=New edited comment" in updated_text
    assert "Terminal=true" in updated_text

    # Click Save
    dlg.btn_save.emit("clicked")
    assert len(saved_apps) == 1
    assert saved_apps[0].name == "CustomName"
    assert saved_apps[0].terminal is True


# ---------------------------------------------------------------------------
# 7. Drag-and-Drop Ingestion Target
# ---------------------------------------------------------------------------

def test_drop_target_appimage_validation(tmp_path):
    """Verify is_appimage_file accurately detects AppImages and rejects others."""
    # 1. Valid .AppImage filename
    valid_ext = tmp_path / "app.AppImage"
    valid_ext.write_bytes(b"#!/bin/bash\nexit 0")
    assert is_appimage_file(valid_ext) is True

    # 2. Lowercase .appimage filename
    valid_lower = tmp_path / "tool.appimage"
    valid_lower.write_bytes(b"#!/bin/bash\nexit 0")
    assert is_appimage_file(valid_lower) is True

    # 3. ELF binary magic header without extension
    elf_file = tmp_path / "standalone_binary"
    elf_file.write_bytes(b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00")
    assert is_appimage_file(elf_file) is True

    # 4. Invalid text file
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Hello world")
    assert is_appimage_file(txt_file) is False

    # 5. Non-existent path
    assert is_appimage_file(tmp_path / "missing.AppImage") is False

    # 6. Directory
    assert is_appimage_file(tmp_path) is False


def test_drop_target_controller_attachment():
    """Verify setup_drop_target attaches Gtk.DropTarget to a widget."""
    box = Gtk.Box()
    dropped_result: List[List[Path]] = []
    target = setup_drop_target(box, lambda paths: dropped_result.append(paths))

    assert isinstance(target, AppImageDropTarget)
    assert target.controller is not None


# ---------------------------------------------------------------------------
# 8. MainWindow Integration
# ---------------------------------------------------------------------------

def test_main_window_navigation_and_views(tmp_path):
    """Verify MainWindow initializes view stack, handles toasts, and view switching."""
    db = DatabaseManager(":memory:")
    # Seed sample app
    app1 = App(id=1, name="TestTool", category="Utility", version="1.0", size_bytes=1048576)
    db.add_app(app1)

    win = MainWindow(db=db)
    assert win.get_title() == "AppImageKit"

    # Default view is grid
    assert win.view_stack.get_visible_child_name() == "grid"
    assert len(win.app_grid.get_apps()) == 1

    # Switch to details view
    win.show_app_details(app1)
    assert win.view_stack.get_visible_child_name() == "details"
    assert win.btn_back.get_visible() is True

    # Back button switches back to grid
    win.show_grid()
    assert win.view_stack.get_visible_child_name() == "grid"
    assert win.btn_back.get_visible() is False

    # Toast banner test
    win.show_toast("Operation completed successfully")


# ---------------------------------------------------------------------------
# 9. AppImageKitApplication Creation
# ---------------------------------------------------------------------------

def test_application_lifecycle():
    """Verify AppImageKitApplication instantiates with correct ID and actions."""
    app = AppImageKitApplication(application_id="io.github.revanthlol.appimagekit.test")
    assert app.get_application_id() == "io.github.revanthlol.appimagekit.test"
    assert app.lookup_action("quit") is not None
    assert app.lookup_action("about") is not None
