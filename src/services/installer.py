"""
AppImageKit — Installer Service (Milestone 1 Core Implementation)
Handles installation, validation, permissions, hashing, launching, and database registration.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ..models.app import App, InstallMode, UpdateSource
from ..models.database import DatabaseManager
from ..utils.paths import Paths

logger = logging.getLogger("appimagekit.installer")


class InstallerError(Exception):
    """Base exception for all installer operations."""
    pass


class AppImageNotFoundError(InstallerError, FileNotFoundError):
    """Raised when the specified source or installed AppImage does not exist."""
    pass


class InvalidAppImageError(InstallerError, ValueError):
    """Raised when the file is not a valid AppImage, is empty, or is a directory."""
    pass


class AppAlreadyInstalledError(InstallerError):
    """Raised when an AppImage is already registered at the destination path."""
    pass


class PermissionDeniedError(InstallerError, PermissionError):
    """Raised when filesystem permissions prevent installation or execution."""
    pass


class ExecutionError(InstallerError, RuntimeError):
    """Raised when spawning the AppImage subprocess fails."""
    pass


class InstallerService:
    """
    Manages the lifecycle of AppImages on the host system:
    - Path validation and heuristic verification
    - Dual-mode target directory resolution (User vs System)
    - Automatic privilege fallback
    - Atomic file installation
    - Executable permissions (0o755)
    - Chunked SHA256 checksum calculation
    - Database registration
    - Detached process execution
    """

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        paths: Optional[Paths] = None,
        desktop_service: Optional[Any] = None,
        icon_service: Optional[Any] = None,
        integrity_service: Optional[Any] = None,
        sandbox_service: Optional[Any] = None,
        autostart_service: Optional[Any] = None,
    ) -> None:
        self.db = db if db is not None else DatabaseManager()
        self.paths = paths if paths is not None else Paths()

        if icon_service is not None:
            self.icon_service = icon_service
        else:
            from .icon_extractor import IconExtractorService
            self.icon_service = IconExtractorService(paths=self.paths)

        if desktop_service is not None:
            self.desktop_service = desktop_service
        else:
            from .desktop_entry import DesktopEntryService
            self.desktop_service = DesktopEntryService(db=self.db, paths=self.paths)

        self.integrity_service = integrity_service
        self.sandbox_service = sandbox_service
        self.autostart_service = autostart_service

    # -------------------------------------------------------------------------
    # Validation & Inspection
    # -------------------------------------------------------------------------

    def validate_source(self, appimage_path: Path) -> Path:
        """
        Validate that the source file exists, is readable, non-empty, and appears
        to be a valid AppImage or executable package.
        """
        path = Path(appimage_path).resolve()
        if not path.exists():
            raise AppImageNotFoundError(f"Source file does not exist: {appimage_path}")
        if not path.is_file():
            raise InvalidAppImageError(f"Source path is not a file: {appimage_path}")
        if not os.access(path, os.R_OK):
            raise PermissionDeniedError(f"Source file is not readable: {appimage_path}")
        if path.stat().st_size == 0:
            raise InvalidAppImageError(f"Source file is empty (0 bytes): {appimage_path}")

        if not self._is_appimage_heuristic(path):
            raise InvalidAppImageError(f"File does not appear to be an AppImage: {path.name}")

        return path

    def _is_appimage_heuristic(self, path: Path) -> bool:
        """
        Heuristic check:
        1. Checks magic bytes for ELF header (\x7fELF) and minimal header size.
        2. Allows shell script mocks (#!/bin/ or #! /bin/) for testing.
        Rejects files that fail both checks even if they have .AppImage extension.
        """
        try:
            with open(path, "rb") as f:
                header = f.read(64)
                if header.startswith(b"\x7fELF"):
                    # Standard ELF headers are at least 52 bytes (32-bit) or 64 bytes (64-bit)
                    return len(header) >= 52
                if header.startswith(b"#!/bin/") or header.startswith(b"#! /bin/"):
                    return True
        except OSError:
            pass

        return False

    # -------------------------------------------------------------------------
    # Destination & Permission Resolution
    # -------------------------------------------------------------------------

    def resolve_destination(self, mode: Union[InstallMode, str], filename: str) -> Tuple[Path, InstallMode]:
        """
        Resolves destination path and effective install mode.
        If System mode is requested but the target directory is not writable,
        automatically falls back to User mode with a warning.
        """
        dest_dir, effective_mode = self.paths.resolve_install_dir(mode)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            logger.warning("Could not create system directory '%s': %s. Falling back to User mode.", dest_dir, e)
            dest_dir = self.paths.get_user_appimages_dir()
            dest_dir.mkdir(parents=True, exist_ok=True)
            effective_mode = InstallMode.USER

        return (dest_dir / filename, effective_mode)

    # -------------------------------------------------------------------------
    # Binary Placement & Permissions
    # -------------------------------------------------------------------------

    def _copy_binary(self, source_path: Path, dest_path: Path) -> None:
        """Atomically copy the binary to destination using a temporary file."""
        if source_path.resolve() == dest_path.resolve():
            return

        tmp_dest = dest_path.with_name(f".{dest_path.name}.tmp.{os.getpid()}")
        try:
            shutil.copy2(source_path, tmp_dest)
            os.replace(tmp_dest, dest_path)
        except Exception:
            if tmp_dest.exists():
                try:
                    tmp_dest.unlink()
                except OSError:
                    pass
            raise

    def _make_executable(self, path: Path) -> None:
        """Ensure file has 0o755 executable permissions."""
        try:
            current_mode = path.stat().st_mode
            path.chmod(current_mode | 0o755)
        except OSError as e:
            raise PermissionDeniedError(f"Failed to set executable mode 0o755 on '{path}': {e}") from e

    # -------------------------------------------------------------------------
    # Checksum & Size
    # -------------------------------------------------------------------------

    @staticmethod
    def calculate_sha256(file_path: Path, chunk_size: int = 65536) -> str:
        """Compute SHA256 checksum in chunks for minimal memory consumption."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _derive_app_name(self, path: Path) -> str:
        """Derive a friendly display name from the AppImage filename."""
        stem = path.stem
        for tag in ["-x86_64", "_x86_64", "-amd64", "_amd64", "-i386", "-aarch64", "_arm64", ".linux"]:
            if tag in stem.lower():
                idx = stem.lower().index(tag)
                stem = stem[:idx]
        name = re.sub(r'[-_]v?\d+(\.\d+)*.*$', '', stem)
        clean = name.replace("-", " ").replace("_", " ").strip()
        return clean if clean else path.stem

    # -------------------------------------------------------------------------
    # Core Installation
    # -------------------------------------------------------------------------

    def install(
        self,
        appimage_path: Path,
        mode: Union[InstallMode, str] = InstallMode.USER,
        category: Optional[str] = None,
        update_source: Optional[UpdateSource] = None,
        update_url: Optional[str] = None,
        reinstall: bool = False,
    ) -> App:
        """
        Installs an AppImage into the appropriate storage directory:
        1. Validates source file.
        2. Resolves destination path and handles privilege fallback.
        3. Checks for duplicate registration.
        4. Copies binary safely to destination.
        5. Sets executable permission (0o755).
        6. Computes SHA256 checksum and measures file size.
        7. Records metadata in SQLite database.
        Returns the created App instance.
        """
        source = self.validate_source(appimage_path)
        dest_path, effective_mode = self.resolve_destination(mode, source.name)

        existing_app = self.db.get_app_by_path(str(dest_path.resolve()))
        if existing_app and not reinstall:
            raise AppAlreadyInstalledError(
                f"Application already installed at '{dest_path}' (App ID: {existing_app.id})"
            )

        self._copy_binary(source, dest_path)
        self._make_executable(dest_path)

        checksum = self.calculate_sha256(dest_path)
        size_bytes = dest_path.stat().st_size
        # Extract embedded metadata if available
        metadata: Dict[str, Any] = {}
        if self.icon_service:
            try:
                metadata = self.icon_service.extract_metadata(dest_path)
            except Exception as e:
                logger.warning("Metadata extraction failed: %s", e)

        app_name = metadata.get("name") or self._derive_app_name(dest_path)

        if category:
            app_category = category
        elif metadata.get("categories"):
            app_category = metadata["categories"]
        else:
            app_category = "Utility"

        app_comment = metadata.get("comment", "") or ""
        app_terminal = bool(metadata.get("terminal", False))
        app_wm_class = metadata.get("startup_wm_class")
        app_version = metadata.get("version")

        # Extract application icon
        icon_path_str: Optional[str] = None
        if self.icon_service:
            try:
                icon_path = self.icon_service.extract_icon(
                    dest_path,
                    app_name=app_name,
                    install_mode=effective_mode,
                )
                if icon_path:
                    icon_path_str = str(icon_path.resolve())
            except Exception as e:
                logger.warning("Icon extraction failed: %s", e)

        app = App(
            id=existing_app.id if (existing_app and reinstall) else None,
            name=app_name,
            filename=dest_path.name,
            install_path=str(dest_path.resolve()),
            desktop_entry=None,
            icon_path=icon_path_str,
            version=app_version,
            size_bytes=size_bytes,
            install_mode=effective_mode,
            update_source=update_source or UpdateSource.NONE,
            update_url=update_url,
            zsync_url=None,
            category=app_category,
            sandboxed=False,
            sandbox_type=None,
            autostart=False,
            checksum_sha256=checksum,
            exec_args="",
            comment=app_comment,
            terminal=app_terminal,
            startup_wm_class=app_wm_class,
            pinned=False,
            hub_id=None,
        )

        # Generate freedesktop .desktop entry
        if self.desktop_service:
            try:
                desktop_path = self.desktop_service.generate_entry(app)
                app.desktop_entry = str(desktop_path.resolve())
            except Exception as e:
                logger.warning("Desktop entry generation failed: %s", e)

        if existing_app and reinstall:
            self.db.update_app(app)
            return app
        else:
            return self.db.add_app(app)

    # -------------------------------------------------------------------------
    # Launching
    # -------------------------------------------------------------------------

    def launch(self, app: App, extra_args: Optional[List[str]] = None) -> subprocess.Popen:
        """
        Executes an installed AppImage safely:
        1. Verifies binary exists and is executable (auto-heals missing execute bit).
        2. Safely tokenizes exec_args via shlex.split to prevent injection.
        3. Spawns process asynchronously with start_new_session=True.
        """
        binary_path = Path(app.install_path)
        if not binary_path.exists():
            raise AppImageNotFoundError(f"Installed AppImage binary not found: '{app.install_path}'")
        if not binary_path.is_file():
            raise InvalidAppImageError(f"Target path is not a file: '{app.install_path}'")

        if not os.access(binary_path, os.X_OK):
            logger.info("Restoring missing executable bit on '%s'", binary_path)
            self._make_executable(binary_path)

        cmd = [str(binary_path.resolve())]
        if app.exec_args:
            cmd.extend(shlex.split(app.exec_args))
        if extra_args:
            cmd.extend(extra_args)

        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(Path.home()),
                env=os.environ.copy(),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return process
        except OSError as e:
            raise ExecutionError(f"Failed to launch '{app.name}': {e}") from e

    # -------------------------------------------------------------------------
    # Auxiliary Methods
    # -------------------------------------------------------------------------

    def uninstall(self, app: App) -> bool:
        """
        Uninstalls an application and cleanly purges all associated files:
        1. AppImage binary file and portable directories (.home, .config)
        2. Freedesktop .desktop entry
        3. Extracted icon file
        4. Autostart entry if present
        5. Desktop and icon system caches
        6. SQLite registry record
        """
        # 1. Purge binary and portable directories
        if app.install_path:
            binary = Path(app.install_path)
            if binary.exists():
                try:
                    binary.unlink()
                except OSError as e:
                    logger.warning("Could not delete binary '%s': %s", binary, e)

            for suffix in [".home", ".config"]:
                portable_dir = Path(f"{app.install_path}{suffix}")
                if portable_dir.exists() and portable_dir.is_dir():
                    try:
                        shutil.rmtree(portable_dir)
                    except OSError as e:
                        logger.warning("Could not delete portable directory '%s': %s", portable_dir, e)

        # 2. Purge desktop entry
        if app.desktop_entry:
            desk_path = Path(app.desktop_entry)
            if desk_path.exists():
                try:
                    desk_path.unlink()
                except OSError as e:
                    logger.warning("Could not delete desktop entry '%s': %s", desk_path, e)

        # 3. Purge icon
        if app.icon_path:
            icon_path = Path(app.icon_path)
            if icon_path.exists():
                try:
                    icon_path.unlink()
                except OSError as e:
                    logger.warning("Could not delete icon '%s': %s", icon_path, e)

        # 4. Purge autostart entry
        if self.autostart_service:
            try:
                self.autostart_service.disable(app)
            except Exception:
                pass
        else:
            try:
                autostart_file = self.paths.get_autostart_dir() / f"{app.name}.desktop"
                if autostart_file.exists():
                    autostart_file.unlink()
            except Exception:
                pass

        # 5. Invalidate caches
        try:
            mode_val = app.install_mode.value if isinstance(app.install_mode, InstallMode) else str(app.install_mode).lower()
            app_dir = (
                self.paths.get_system_applications_dir()
                if mode_val == "system"
                else self.paths.get_user_applications_dir()
            )
            subprocess.run(["update-desktop-database", str(app_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass

        try:
            mode_val = app.install_mode.value if isinstance(app.install_mode, InstallMode) else str(app.install_mode).lower()
            icon_dir = (
                self.paths.get_system_icons_dir()
                if mode_val == "system"
                else self.paths.get_icons_dir()
            )
            subprocess.run(["gtk-update-icon-cache", "-t", "-f", str(icon_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass

        # 6. Delete database record
        if app.id is not None:
            return self.db.delete_app(app.id)
        return True

    def update_in_place(self, app: App, new_appimage_path: Path) -> App:
        """
        Tier 4 in-place local update (M3 scope).
        Atomically replaces target binary, updates checksum and file size,
        refreshes icon and desktop entry while preserving user customizations.
        """
        valid_source = self.validate_source(new_appimage_path)
        dest_path = Path(app.install_path)

        # 1. Atomically copy new binary to destination
        self._copy_binary(valid_source, dest_path)
        self._make_executable(dest_path)

        # 2. Update checksum and size
        app.checksum_sha256 = self.calculate_sha256(dest_path)
        app.size_bytes = dest_path.stat().st_size

        # 3. Extract new metadata (version, etc.)
        if self.icon_service:
            try:
                metadata = self.icon_service.extract_metadata(dest_path)
                if metadata.get("version"):
                    app.version = metadata["version"]
            except Exception as e:
                logger.warning("Metadata extraction during update failed: %s", e)

        if not app.version:
            ver_match = re.search(r'[-_vV](\d+(?:\.\d+)+(?:[-.][0-9a-zA-Z]+)?)', valid_source.name)
            if ver_match:
                app.version = ver_match.group(1)

        # 4. Refresh icon if available
        if self.icon_service:
            try:
                new_icon = self.icon_service.extract_icon(
                    dest_path,
                    app_name=app.name,
                    install_mode=app.install_mode,
                )
                if new_icon:
                    app.icon_path = str(new_icon.resolve())
            except Exception as e:
                logger.warning("Icon extraction during update failed: %s", e)

        # 5. Preserve user customizations and refresh desktop entry
        if self.desktop_service and app.desktop_entry:
            desk_path = Path(app.desktop_entry)
            if desk_path.exists():
                try:
                    parsed_desk = self.desktop_service.parse_desktop_file(desk_path)
                    if parsed_desk.get("comment"):
                        app.comment = parsed_desk["comment"]
                    if parsed_desk.get("categories"):
                        app.category = parsed_desk["categories"]
                    if parsed_desk.get("startup_wm_class"):
                        app.startup_wm_class = parsed_desk["startup_wm_class"]
                except Exception as e:
                    logger.warning("Parsing existing desktop file failed: %s", e)
            try:
                new_desk = self.desktop_service.generate_entry(
                    app, destination_dir=desk_path.parent if desk_path else None
                )
                app.desktop_entry = str(new_desk.resolve())
            except Exception as e:
                logger.warning("Regenerating desktop entry failed: %s", e)

        # 6. Update database record with timestamp
        from datetime import datetime, timezone
        app.updated_at = datetime.now(timezone.utc).isoformat()
        self.db.update_app(app)
        return app
