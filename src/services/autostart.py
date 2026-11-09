"""
src/services/autostart.py — Freedesktop.org Autostart Management.
Manages automatic startup desktop entries in ~/.config/autostart/<app_name>.desktop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from ..models.app import App
from ..models.database import DatabaseManager
from ..utils.paths import Paths

logger = logging.getLogger("appimagekit.autostart")


class AutostartManager:
    """
    Manages desktop entries in Freedesktop.org autostart locations.
    Enables, disables, toggles, and inspects startup status for installed AppImages.
    """

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        paths: Optional[Paths] = None,
    ) -> None:
        self.db = db
        self.paths = paths or Paths()

    @property
    def autostart_dir(self) -> Path:
        """Resolve the autostart directory (~/.config/autostart)."""
        return self.paths.get_autostart_dir()

    def get_autostart_path(self, app: App) -> Path:
        """Return the target autostart .desktop file path for the given app."""
        return self.autostart_dir / f"{app.name}.desktop"

    def is_enabled(self, app: App) -> bool:
        """Check whether the app is currently configured for autostart."""
        autostart_file = self.get_autostart_path(app)
        if not autostart_file.exists():
            return False

        try:
            content = autostart_file.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("Hidden=true") or line.startswith("X-GNOME-Autostart-enabled=false"):
                    return False
            return True
        except Exception as e:
            logger.warning("Error reading autostart file %s: %s", autostart_file, e)
            return False

    def enable(self, app: App) -> Path:
        """
        Enable autostart for the given App by writing a .desktop file to autostart_dir.
        Updates app.autostart attribute and registry record if DB is attached.
        """
        self.autostart_dir.mkdir(parents=True, exist_ok=True)
        autostart_file = self.get_autostart_path(app)

        # If a valid desktop entry exists, derive autostart entry from it
        if app.desktop_entry and Path(app.desktop_entry).is_file():
            try:
                base_content = Path(app.desktop_entry).read_text(encoding="utf-8", errors="replace")
                lines = []
                has_autostart_key = False
                for line in base_content.splitlines():
                    if line.strip().startswith("Hidden="):
                        continue
                    if line.strip().startswith("X-GNOME-Autostart-enabled="):
                        lines.append("X-GNOME-Autostart-enabled=true")
                        has_autostart_key = True
                    else:
                        lines.append(line)
                if not has_autostart_key:
                    lines.append("X-GNOME-Autostart-enabled=true")
                content = "\n".join(lines).strip() + "\n"
                autostart_file.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to derive autostart from %s: %s. Generating standard.", app.desktop_entry, e)
                content = self._generate_desktop_content(app)
                autostart_file.write_text(content, encoding="utf-8")
        else:
            content = self._generate_desktop_content(app)
            autostart_file.write_text(content, encoding="utf-8")

        app.autostart = True
        if self.db:
            try:
                self.db.update_app(app)
            except Exception as e:
                logger.warning("Could not update database autostart status: %s", e)

        return autostart_file

    def disable(self, app: App) -> bool:
        """
        Disable autostart for the given App by removing its .desktop file.
        Updates app.autostart attribute and registry record if DB is attached.
        """
        autostart_file = self.get_autostart_path(app)
        removed = False
        if autostart_file.exists():
            try:
                autostart_file.unlink()
                removed = True
            except OSError as e:
                logger.warning("Could not delete autostart file %s: %s", autostart_file, e)

        app.autostart = False
        if self.db:
            try:
                self.db.update_app(app)
            except Exception as e:
                logger.warning("Could not update database autostart status: %s", e)

        return True

    def toggle(self, app: App) -> bool:
        """Toggle autostart status for the given App. Returns new active state."""
        if self.is_enabled(app):
            self.disable(app)
            return False
        else:
            self.enable(app)
            return True

    def _generate_desktop_content(self, app: App) -> str:
        """Construct standard freedesktop autostart desktop entry content."""
        install_path = app.install_path or ""
        escaped_path = install_path.replace('\\', '\\\\').replace('"', '\\"')
        exec_line = f'"{escaped_path}"'
        if app.exec_args and app.exec_args.strip():
            exec_line += f" {app.exec_args.strip()}"
        exec_line += " %U"

        category = app.category or "Utility"
        if not category.endswith(";"):
            category += ";"

        lines = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={app.name}",
            f"Exec={exec_line}",
            f"Icon={app.icon_path or app.name}",
            f"Categories={category}",
            f"Terminal={'true' if app.terminal else 'false'}",
            f"Comment={app.comment or f'Autostart for {app.name}'}",
            "X-GNOME-Autostart-enabled=true",
        ]
        return "\n".join(lines) + "\n"
