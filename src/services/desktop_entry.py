"""
src/services/desktop_entry.py — Freedesktop.org Desktop Entry v1.5 Management.
Generates, parses, updates, and deletes standards-compliant .desktop entries.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..models.app import App, InstallMode
from ..models.database import DatabaseManager
from ..utils.paths import Paths

logger = logging.getLogger("appimagekit.desktop_entry")


class DesktopEntryService:
    """
    Manages Freedesktop.org Desktop Entry v1.5 files for AppImageKit.
    Enforces required keys, quoting, lowercase booleans, and trailing semicolons.
    """

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        paths: Optional[Paths] = None,
    ) -> None:
        self.db = db if db is not None else DatabaseManager()
        self.paths = paths if paths is not None else Paths()

    @staticmethod
    def _sanitize_slug(name: str) -> str:
        """Create a clean filesystem slug from an app name."""
        slug = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name).lower().strip('_')
        return slug if slug else "app"

    @staticmethod
    def format_exec(install_path: str, exec_args: Optional[str] = None) -> str:
        """
        Formats the Exec= line according to Freedesktop v1.5 quoting rules.
        Encloses paths containing spaces in double quotes and appends %U.
        """
        escaped_path = install_path.replace('\\', '\\\\').replace('"', '\\"')
        quoted_bin = f'"{escaped_path}"'

        parts = [quoted_bin]
        if exec_args and exec_args.strip():
            parts.append(exec_args.strip())
        parts.append("%U")
        return " ".join(parts)

    @staticmethod
    def format_categories(categories: Union[str, List[str]]) -> str:
        """
        Formats categories string ensuring it ends with a mandatory trailing semicolon.
        """
        if isinstance(categories, list):
            cats = [c.strip() for c in categories if c.strip()]
            if not cats:
                return "Utility;"
            return ";".join(cats) + ";"
        raw = categories.strip()
        if not raw:
            return "Utility;"
        if not raw.endswith(";"):
            raw += ";"
        return raw

    def generate_entry(
        self,
        app: App,
        destination_dir: Optional[Path] = None,
    ) -> Path:
        """
        Generates and writes a valid Freedesktop v1.5 .desktop entry.
        Returns the Path to the written desktop file.
        """
        if destination_dir:
            target_dir = destination_dir
        else:
            mode_val = app.install_mode.value if isinstance(app.install_mode, InstallMode) else str(app.install_mode).lower()
            target_dir = (
                self.paths.get_system_applications_dir()
                if mode_val == "system"
                else self.paths.get_user_applications_dir()
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        slug = self._sanitize_slug(app.name)
        desktop_filename = f"appimagekit-{slug}.desktop"
        desktop_path = target_dir / desktop_filename

        exec_line = self.format_exec(app.install_path, app.exec_args)
        categories_line = self.format_categories(app.category or "Utility")
        icon_val = app.icon_path or slug
        terminal_val = "true" if app.terminal else "false"

        lines = [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={app.name}",
            f"Exec={exec_line}",
            f"Icon={icon_val}",
            f"Categories={categories_line}",
            f"Terminal={terminal_val}",
        ]

        if app.comment:
            lines.append(f"Comment={app.comment}")

        if app.startup_wm_class:
            lines.append(f"StartupWMClass={app.startup_wm_class}")

        lines.append("StartupNotify=true")

        if app.version:
            lines.append(f"X-AppImage-Version={app.version}")

        content = "\n".join(lines) + "\n"
        desktop_path.write_text(content, encoding="utf-8")

        # Invalidate desktop database
        try:
            subprocess.run(
                ["update-desktop-database", str(target_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

        return desktop_path

    @staticmethod
    def parse_desktop_file(desktop_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Parses a .desktop file into a Python dictionary.
        Handles comments, sections, unescaping, and typed conversions.
        """
        path = Path(desktop_path)
        data: Dict[str, Any] = {
            "name": "",
            "exec": "",
            "icon": "",
            "categories": "Utility;",
            "comment": "",
            "terminal": False,
            "startup_wm_class": None,
            "version": None,
        }
        if not path.exists():
            return data

        in_desktop_entry = False
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_desktop_entry = (stripped == "[Desktop Entry]")
                    continue
                if in_desktop_entry and "=" in stripped:
                    key, val = stripped.split("=", 1)
                    key = key.strip()
                    val = val.strip()

                    # Unescape standard escape codes
                    val = val.replace("\\s", " ").replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")

                    if key == "Name":
                        data["name"] = val
                    elif key == "Exec":
                        data["exec"] = val
                    elif key == "Icon":
                        data["icon"] = val
                    elif key == "Categories":
                        data["categories"] = val
                    elif key == "Comment":
                        data["comment"] = val
                    elif key == "Terminal":
                        data["terminal"] = (val.lower() in ("true", "1"))
                    elif key == "StartupWMClass":
                        data["startup_wm_class"] = val
                    elif key in ("X-AppImage-Version", "Version"):
                        data["version"] = val
        except Exception as e:
            logger.warning("Error parsing desktop file '%s': %s", path, e)

        return data

    def update_entry(
        self,
        app: App,
        name: Optional[str] = None,
        comment: Optional[str] = None,
        exec_args: Optional[str] = None,
        icon: Optional[str] = None,
        categories: Optional[Union[str, List[str]]] = None,
        terminal: Optional[bool] = None,
        startup_wm_class: Optional[str] = None,
    ) -> App:
        """
        Customizes application desktop entry metadata and synchronizes SQLite database.
        """
        if name is not None:
            app.name = name
        if comment is not None:
            app.comment = comment
        if exec_args is not None:
            app.exec_args = exec_args
        if icon is not None:
            app.icon_path = icon
        if categories is not None:
            app.category = self.format_categories(categories)
        if terminal is not None:
            app.terminal = terminal
        if startup_wm_class is not None:
            app.startup_wm_class = startup_wm_class

        old_desktop_path = Path(app.desktop_entry) if app.desktop_entry else None
        dest_dir = old_desktop_path.parent if old_desktop_path else None
        new_desktop_path = self.generate_entry(app, destination_dir=dest_dir)

        # If name change caused a new filename, clean up old file
        if old_desktop_path and old_desktop_path != new_desktop_path and old_desktop_path.exists():
            try:
                old_desktop_path.unlink()
            except OSError:
                pass

        app.desktop_entry = str(new_desktop_path.resolve())
        self.db.update_app(app)
        return app

    def remove_entry(self, desktop_path: Union[str, Path]) -> bool:
        """
        Removes a .desktop file and updates the desktop database.
        """
        p = Path(desktop_path)
        if p.exists():
            try:
                p.unlink()
            except OSError as e:
                logger.warning("Failed to unlink desktop file '%s': %s", p, e)
                return False

        try:
            subprocess.run(
                ["update-desktop-database", str(p.parent)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

        return True

    def validate_entry(self, desktop_path: Union[str, Path]) -> bool:
        """
        Runs desktop-file-validate on the desktop file if the binary is present.
        Returns True if valid (exit code 0), False otherwise.
        """
        p = Path(desktop_path)
        if not p.exists():
            return False

        try:
            res = subprocess.run(
                ["desktop-file-validate", str(p)],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0
        except FileNotFoundError:
            # desktop-file-validate not available on host
            content = p.read_text(encoding="utf-8", errors="ignore")
            return "[Desktop Entry]" in content and "Type=Application" in content and "Exec=" in content
