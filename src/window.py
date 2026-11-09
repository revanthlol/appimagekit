"""
src/window.py — Main Libadwaita application window for AppImageKit.

Composes the modern Adwaita layout:
- Adw.ToolbarView with HeaderBar and SearchBar
- Adw.OverlaySplitView with Sidebar and ViewStack
- Drag-and-drop receiver for in-window .AppImage installation
- Background update checking and asynchronous task execution
- Modal dialogs for desktop entry editing, uninstallation confirmation, and file pickers
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .models.app import App
from .models.database import DatabaseManager
from .services.autostart import AutostartManager
from .services.desktop_entry import DesktopEntryService
from .services.installer import InstallerService
from .services.sandbox import SandboxDetectorService
from .services.updater import UpdateInfo, UpdaterService
from .utils.process import AsyncWorker, get_default_worker
from .widgets.app_details import AppDetailsView
from .widgets.app_grid import AppGridView
from .widgets.desktop_editor import DesktopEditorDialog
from .widgets.drop_target import setup_drop_target
from .widgets.sidebar import Sidebar

logger = logging.getLogger("appimagekit.window")


class MainWindow(Adw.ApplicationWindow):
    """
    Main Libadwaita window managing views, navigation, background workers, and actions.
    """

    def __init__(
        self,
        *args: Any,
        db: Optional[DatabaseManager] = None,
        installer: Optional[InstallerService] = None,
        updater: Optional[UpdaterService] = None,
        desktop_entry_service: Optional[DesktopEntryService] = None,
        autostart_mgr: Optional[AutostartManager] = None,
        sandbox_service: Optional[SandboxDetectorService] = None,
        worker: Optional[AsyncWorker] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.set_title("AppImageKit")
        self.set_default_size(1080, 720)
        self.set_size_request(680, 480)

        # 1. Services
        self.db = db if db is not None else DatabaseManager()
        self.installer = installer if installer is not None else InstallerService(db=self.db)
        self.updater = updater if updater is not None else UpdaterService()
        self.desktop_entry_service = (
            desktop_entry_service if desktop_entry_service is not None else DesktopEntryService(db=self.db)
        )
        self.autostart_mgr = autostart_mgr if autostart_mgr is not None else AutostartManager(db=self.db)
        self.sandbox_service = sandbox_service if sandbox_service is not None else SandboxDetectorService()
        self.worker = worker if worker is not None else get_default_worker()

        self._updates_map: Dict[int, UpdateInfo] = {}

        # 2. Toast Overlay (for floating feedback banners)
        self.toast_overlay = Adw.ToastOverlay()

        # 3. ToolbarView
        self.toolbar_view = Adw.ToolbarView()

        # 4. HeaderBar
        self.header_bar = Adw.HeaderBar()

        # Left controls
        self.btn_toggle_sidebar = Gtk.ToggleButton.new()
        self.btn_toggle_sidebar.set_icon_name("sidebar-show-symbolic")
        self.btn_toggle_sidebar.set_tooltip_text("Toggle Sidebar")
        self.btn_toggle_sidebar.set_active(True)
        self.btn_toggle_sidebar.connect("toggled", self._on_toggle_sidebar)
        self.header_bar.pack_start(self.btn_toggle_sidebar)

        self.btn_back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.btn_back.set_tooltip_text("Back to Library")
        self.btn_back.set_visible(False)
        self.btn_back.connect("clicked", lambda _b: self.show_grid())
        self.header_bar.pack_start(self.btn_back)

        # Right controls
        # Search toggle button
        self.btn_search = Gtk.ToggleButton.new()
        self.btn_search.set_icon_name("system-search-symbolic")
        self.btn_search.set_tooltip_text("Search Applications")
        self.btn_search.connect("toggled", self._on_search_toggled)
        self.header_bar.pack_end(self.btn_search)

        # Primary menu button (hamburger menu)
        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_icon_name("open-menu-symbolic")
        self.menu_button.set_tooltip_text("Main Menu")
        self._setup_primary_menu()
        self.header_bar.pack_end(self.menu_button)

        # Update all button
        self.btn_update_all = Gtk.Button.new_from_icon_name("software-update-available-symbolic")
        self.btn_update_all.set_tooltip_text("Check All Updates")
        self.btn_update_all.connect("clicked", lambda _b: self.check_all_updates())
        self.header_bar.pack_end(self.btn_update_all)

        # Install AppImage button
        self.btn_install = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.btn_install.set_tooltip_text("Install AppImage from File")
        self.btn_install.connect("clicked", lambda _b: self.open_file_picker())
        self.header_bar.pack_end(self.btn_install)

        self.toolbar_view.add_top_bar(self.header_bar)

        # 5. SearchBar
        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.toolbar_view.add_top_bar(self.search_bar)

        # 6. SplitView & Body
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_show_sidebar(True)
        self.split_view.set_min_sidebar_width(220)
        self.split_view.set_max_sidebar_width(320)

        # Content ViewStack with smooth horizontal slide transitions
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.view_stack.set_transition_duration(200)

        # Grid view
        self.app_grid = AppGridView(
            on_app_selected=self.show_app_details,
            on_app_launch=self.launch_app,
            on_app_uninstall=self.uninstall_app,
            on_app_edit=self.open_desktop_editor,
        )
        self.view_stack.add_named(self.app_grid, "grid")

        # Details view
        self.app_details = AppDetailsView(
            on_back=self.show_grid,
            on_launch=self.launch_app,
            on_update=self.update_app,
            on_edit_desktop=self.open_desktop_editor,
            on_uninstall=self.uninstall_app,
            on_autostart_toggle=self.toggle_autostart,
            on_pin_toggle=self.toggle_pin,
        )
        self.view_stack.add_named(self.app_details, "details")

        # Sidebar
        self.sidebar = Sidebar(on_category_selected=self._on_category_selected)
        self.split_view.set_sidebar(self.sidebar)

        self.split_view.set_content(self.view_stack)
        self.toolbar_view.set_content(self.split_view)
        self.toast_overlay.set_child(self.toolbar_view)
        self.set_content(self.toast_overlay)

        # 7. Drag-and-Drop Receiver
        setup_drop_target(self, self._on_appimages_dropped)

        # 8. Initial Data Load
        self.load_apps()

    def _setup_primary_menu(self) -> None:
        """Construct the main header hamburger menu model."""
        menu = Gio.Menu()
        menu.append("Refresh Library", "win.refresh")
        menu.append("Check for Updates", "win.check_updates")
        menu.append("Install from Hub...", "win.hub")
        menu.append("Backup Applications...", "win.backup")
        menu.append("Verify All Checksums...", "win.verify")
        menu.append("About AppImageKit", "win.about")
        self.menu_button.set_menu_model(menu)

        # Install action entries
        self._add_action("refresh", lambda *_a: self.load_apps(show_toast=True))
        self._add_action("check_updates", lambda *_a: self.check_all_updates())
        self._add_action("about", lambda *_a: self._show_about_dialog())
        self._add_action("verify", lambda *_a: self._run_verify_all())

    def _add_action(self, name: str, callback: Any) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def show_toast(self, message: str, timeout: int = 3) -> None:
        """Display a Libadwaita floating toast notification."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def load_apps(self, show_toast: bool = False) -> None:
        """Query SQLite database and refresh grid and sidebar counters."""
        apps = self.db.list_apps()
        self.app_grid.set_apps(apps)

        total_apps = len(apps)
        total_bytes = self.db.get_total_disk_usage()
        active_updates = sum(1 for u in self._updates_map.values() if u.update_available)

        self.sidebar.update_summary(total_apps, total_bytes, updates_count=active_updates)

        if show_toast:
            self.show_toast(f"Library refreshed: {total_apps} apps found")

    def show_grid(self) -> None:
        """Switch view stack to the application grid."""
        self.view_stack.set_visible_child_name("grid")
        self.btn_back.set_visible(False)
        self.header_bar.set_title_widget(None)

    def show_app_details(self, app: App) -> None:
        """Switch view stack to the detail view for the selected application."""
        uinfo = self._updates_map.get(app.id or -1)
        self.app_details.set_app(app, uinfo)
        self.view_stack.set_visible_child_name("details")
        self.btn_back.set_visible(True)

    def _on_category_selected(self, category_id: str) -> None:
        """Filter grid items when a sidebar category is clicked."""
        self.app_grid.set_filter_category(category_id)
        if self.view_stack.get_visible_child_name() != "grid":
            self.show_grid()

    def _on_search_toggled(self, btn: Gtk.ToggleButton) -> None:
        active = btn.get_active()
        self.search_bar.set_search_mode(active)
        if not active:
            self.search_entry.set_text("")
            self.app_grid.set_filter_query("")

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text()
        self.app_grid.set_filter_query(query)
        if query and self.view_stack.get_visible_child_name() != "grid":
            self.show_grid()

    def _on_toggle_sidebar(self, btn: Gtk.ToggleButton) -> None:
        self.split_view.set_show_sidebar(btn.get_active())

    def _on_appimages_dropped(self, paths: List[Path]) -> None:
        """Handle files dropped directly onto the window."""
        self.show_toast(f"Processing {len(paths)} dropped AppImage(s)...")
        for path in paths:
            self.install_appimage(path)

    def open_file_picker(self) -> None:
        """Show native file chooser to select an AppImage for installation."""
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Select AppImage to Install")

        filter_appimage = Gtk.FileFilter.new()
        filter_appimage.set_name("AppImage Packages (*.AppImage, *.appimage)")
        filter_appimage.add_pattern("*.AppImage")
        filter_appimage.add_pattern("*.appimage")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_appimage)
        dialog.set_filters(filters)
        dialog.set_default_filter(filter_appimage)

        def _on_file_selected(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                gfile = dialog.open_finish(result)
                if gfile and gfile.get_path():
                    self.install_appimage(Path(gfile.get_path()))
            except GLib.Error as e:
                # User cancelled dialog
                logger.debug("File picker cancelled or error: %s", e)

        dialog.open(self, None, _on_file_selected)

    def install_appimage(self, path: Path) -> None:
        """Install an AppImage asynchronously in the background."""
        self.show_toast(f"Installing {path.name}...")

        def _do_install() -> App:
            return self.installer.install(path)

        def _on_success(app: App) -> None:
            self.show_toast(f"Successfully installed {app.name}!")
            self.load_apps()
            self.show_app_details(app)

        def _on_error(exc: Exception) -> None:
            logger.error("Failed to install %s: %s", path, exc)
            self.show_toast(f"Installation failed: {exc}", timeout=5)

        self.worker.run_async(_do_install, on_success=_on_success, on_error=_on_error)

    def check_all_updates(self) -> None:
        """Check all registered applications for updates in the background."""
        apps = self.db.list_apps()
        if not apps:
            self.show_toast("No applications installed to check.")
            return

        self.show_toast("Checking for application updates...")

        def _do_check() -> Dict[int, UpdateInfo]:
            return self.updater.check_all_updates(apps)

        def _on_success(results: Dict[int, UpdateInfo]) -> None:
            self._updates_map = results
            self.app_grid.set_updates(results)
            upd_count = sum(1 for u in results.values() if u.update_available)

            self.sidebar.set_update_count(upd_count)
            if upd_count > 0:
                self.show_toast(f"Update check finished: {upd_count} update(s) available!")
            else:
                self.show_toast("All applications are up to date.")

            # Refresh details view if open
            if self.app_details.app and self.app_details.app.id in results:
                self.app_details.update_info = results[self.app_details.app.id]
                self.app_details.refresh()

        def _on_error(exc: Exception) -> None:
            logger.error("Update check failed: %s", exc)
            self.show_toast(f"Update check failed: {exc}")

        self.worker.run_async(_do_check, on_success=_on_success, on_error=_on_error)

    def launch_app(self, app: App) -> None:
        """Launch an AppImage process detached from the GUI."""
        try:
            self.installer.launch(app)
            self.db.record_launch(app.id or 0)
            self.show_toast(f"Launched {app.name}")
        except Exception as e:
            logger.error("Failed to launch %s: %s", app.name, e)
            self.show_toast(f"Failed to launch {app.name}: {e}")

    def update_app(self, app: App, local_file: Optional[Path] = None) -> None:
        """Perform update for a single application."""
        if local_file:
            self.show_toast(f"Updating {app.name} in-place from local file...")
            def _do_local_update() -> App:
                return self.installer.update_in_place(app, local_file)

            def _on_local_success(updated: App) -> None:
                self.show_toast(f"Updated {updated.name} to v{updated.version}!")
                self.load_apps()
                self.show_app_details(updated)

            def _on_local_error(exc: Exception) -> None:
                self.show_toast(f"Update failed: {exc}")

            self.worker.run_async(_do_local_update, on_success=_on_local_success, on_error=_on_local_error)
            return

        # Online update check and apply
        uinfo = self._updates_map.get(app.id or -1)
        if not uinfo or not uinfo.update_available:
            self.show_toast(f"Checking update for {app.name}...")
            def _check_and_apply() -> Tuple[App, bool]:
                info = self.updater.check_update(app)
                if info.update_available:
                    updated = self.updater.apply_update(app, info)
                    return updated, True
                return app, False

            def _on_done(result: Tuple[App, bool]) -> None:
                updated_app, was_updated = result
                if was_updated:
                    self.show_toast(f"Updated {updated_app.name} to v{updated_app.version}!")
                    self.load_apps()
                    self.show_app_details(updated_app)
                else:
                    self.show_toast(f"{app.name} is already up to date.")

            def _on_err(exc: Exception) -> None:
                self.show_toast(f"Update failed: {exc}")

            self.worker.run_async(_check_and_apply, on_success=_on_done, on_error=_on_err)
        else:
            self.show_toast(f"Downloading and applying update for {app.name}...")
            def _apply() -> App:
                return self.updater.apply_update(app, uinfo)

            def _on_applied(updated: App) -> None:
                self.show_toast(f"Updated {updated.name} to v{updated.version}!")
                self.load_apps()
                self.show_app_details(updated)

            def _on_apply_err(exc: Exception) -> None:
                self.show_toast(f"Failed to apply update: {exc}")

            self.worker.run_async(_apply, on_success=_on_applied, on_error=_on_apply_err)

    def uninstall_app(self, app: App) -> None:
        """Uninstall an application and purge its files."""
        try:
            self.installer.uninstall(app)
            self.show_toast(f"Uninstalled {app.name}")
            self.load_apps()
            self.show_grid()
        except Exception as e:
            logger.error("Failed to uninstall %s: %s", app.name, e)
            self.show_toast(f"Uninstall failed: {e}")

    def open_desktop_editor(self, app: App) -> None:
        """Open the Desktop Entry Editor dialog for the specified application."""
        def _on_saved(updated_app: App) -> None:
            self.show_toast(f"Desktop entry updated for {updated_app.name}")
            self.load_apps()
            if self.view_stack.get_visible_child_name() == "details":
                self.show_app_details(updated_app)

        dialog = DesktopEditorDialog(
            app=app,
            desktop_service=self.desktop_entry_service,
            parent=self,
            on_saved=_on_saved,
        )
        dialog.present()

    def toggle_autostart(self, app: App, enable: bool) -> None:
        """Enable or disable autostart desktop entry for the application."""
        try:
            if enable:
                self.autostart_mgr.enable(app)
                app.autostart = True
            else:
                self.autostart_mgr.disable(app)
                app.autostart = False

            self.db.update_app(app)
            self.show_toast(f"Autostart {'enabled' if enable else 'disabled'} for {app.name}")
        except Exception as e:
            logger.error("Error toggling autostart for %s: %s", app.name, e)
            self.show_toast(f"Failed to configure autostart: {e}")

    def toggle_pin(self, app: App, pin: bool) -> None:
        """Pin or unpin application in the grid."""
        try:
            app.pinned = pin
            self.db.update_app(app)
            self.load_apps()
            self.show_toast(f"Application {'pinned' if pin else 'unpinned'}")
        except Exception as e:
            logger.error("Error updating pin state: %s", e)

    def _show_about_dialog(self) -> None:
        """Show the Libadwaita About dialog."""
        about = Adw.AboutWindow(
            transient_for=self,
            application_name="AppImageKit",
            application_icon="application-x-executable",
            developer_name="AppImageKit Team",
            version="1.0.0",
            copyright="© 2026 AppImageKit Authors",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/revanthlol/appimagekit",
            issue_url="https://github.com/revanthlol/appimagekit/issues",
        )
        about.present()

    def _run_verify_all(self) -> None:
        """Verify SHA256 integrity across all installed binaries."""
        from .services.integrity import IntegrityVerifierService
        verifier = IntegrityVerifierService(db=self.db)
        results = verifier.verify_all()
        tampered = [name for name, status in results.items() if not status.passed]
        if tampered:
            self.show_toast(f"Warning: {len(tampered)} apps failed integrity check!", timeout=6)
        else:
            self.show_toast(f"Integrity check passed: all {len(results)} applications verified.")
