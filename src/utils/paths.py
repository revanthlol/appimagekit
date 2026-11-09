"""
src/utils/paths.py — Freedesktop.org XDG Base Directory & system path resolution for AppImageKit.

Provides centralized resolution for user/system AppImage storage, desktop entries,
icons, autostart configurations, cache staging, and SQLite registry databases.
Supports dynamic environment overrides and scoped context-manager overrides for test isolation.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple, Union


class InstallMode(str, Enum):
    """Target installation scope for AppImageKit packages."""
    USER = "user"
    SYSTEM = "system"


class Paths:
    """
    Centralized path resolver conforming to Freedesktop.org XDG specifications.
    
    All methods are static to allow simple functional calls while supporting
    contextual test overrides via `Paths.override()` or environment variables.
    """

    _overrides: Dict[str, Path] = {}

    # -----------------------------------------------------------------------
    # Dynamic Override Registry (for testing & isolation)
    # -----------------------------------------------------------------------

    @classmethod
    def set_override(cls, key: str, path: Union[Path, str]) -> None:
        """Register a persistent override for a specific path key."""
        cls._overrides[key] = Path(path).resolve()

    @classmethod
    def clear_overrides(cls) -> None:
        """Clear all active overrides."""
        cls._overrides.clear()

    @classmethod
    @contextmanager
    def override(cls, **kwargs: Union[Path, str]) -> Iterator[None]:
        """
        Context manager to temporarily override one or more paths during testing.
        
        Example:
            with Paths.override(user_appimages_dir=tmp_path / "apps"):
                assert Paths.get_user_appimages_dir() == tmp_path / "apps"
        """
        old_overrides = dict(cls._overrides)
        try:
            for key, val in kwargs.items():
                cls._overrides[key] = Path(val).resolve()
            yield
        finally:
            cls._overrides = old_overrides

    # -----------------------------------------------------------------------
    # Path Accessors
    # -----------------------------------------------------------------------

    @staticmethod
    def get_user_appimages_dir() -> Path:
        """
        Resolve user AppImage storage directory.
        Default: ~/.local/share/appimages
        """
        if "user_appimages_dir" in Paths._overrides:
            return Paths._overrides["user_appimages_dir"]
        if env := os.environ.get("APPIMAGEKIT_APPS_DIR"):
            return Path(env).resolve()
        if xdg_data := os.environ.get("XDG_DATA_HOME"):
            return Path(xdg_data).resolve() / "appimages"
        return Path.home() / ".local" / "share" / "appimages"

    @staticmethod
    def get_system_appimages_dir() -> Path:
        """
        Resolve system-wide AppImage storage directory.
        Default: /opt/appimages
        """
        if "system_appimages_dir" in Paths._overrides:
            return Paths._overrides["system_appimages_dir"]
        if env := (os.environ.get("APPIMAGEKIT_SYSTEM_APPIMAGES_DIR") or os.environ.get("APPIMAGEKIT_SYSTEM_DIR")):
            return Path(env).resolve()
        return Path("/opt/appimages")

    @staticmethod
    def get_database_path() -> Path:
        """
        Resolve AppImageKit SQLite registry database path.
        Default: ~/.local/share/appimagekit/registry.db
        """
        if "database_path" in Paths._overrides:
            return Paths._overrides["database_path"]
        if env := os.environ.get("APPIMAGEKIT_DB_PATH"):
            return Path(env).resolve()
        if xdg_data := os.environ.get("XDG_DATA_HOME"):
            return Path(xdg_data).resolve() / "appimagekit" / "registry.db"
        return Path.home() / ".local" / "share" / "appimagekit" / "registry.db"

    @staticmethod
    def get_user_applications_dir() -> Path:
        """
        Resolve user desktop entries directory.
        Default: ~/.local/share/applications
        """
        if "user_applications_dir" in Paths._overrides:
            return Paths._overrides["user_applications_dir"]
        if env := os.environ.get("APPIMAGEKIT_APPLICATIONS_DIR"):
            return Path(env).resolve()
        if xdg_data := os.environ.get("XDG_DATA_HOME"):
            return Path(xdg_data).resolve() / "applications"
        return Path.home() / ".local" / "share" / "applications"

    @staticmethod
    def get_system_applications_dir() -> Path:
        """
        Resolve system-wide desktop entries directory.
        Default: /usr/share/applications
        """
        if "system_applications_dir" in Paths._overrides:
            return Paths._overrides["system_applications_dir"]
        if env := os.environ.get("APPIMAGEKIT_SYSTEM_APPLICATIONS_DIR"):
            return Path(env).resolve()
        return Path("/usr/share/applications")

    @staticmethod
    def get_icons_dir() -> Path:
        """
        Resolve user hicolor icon theme directory.
        Default: ~/.local/share/icons/hicolor
        """
        if "icons_dir" in Paths._overrides:
            return Paths._overrides["icons_dir"]
        if env := os.environ.get("APPIMAGEKIT_ICONS_DIR"):
            return Path(env).resolve()
        if xdg_data := os.environ.get("XDG_DATA_HOME"):
            return Path(xdg_data).resolve() / "icons" / "hicolor"
        return Path.home() / ".local" / "share" / "icons" / "hicolor"

    @staticmethod
    def get_system_icons_dir() -> Path:
        """
        Resolve system-wide hicolor icon theme directory.
        Default: /usr/share/icons/hicolor
        """
        if "system_icons_dir" in Paths._overrides:
            return Paths._overrides["system_icons_dir"]
        if env := os.environ.get("APPIMAGEKIT_SYSTEM_ICONS_DIR"):
            return Path(env).resolve()
        return Path("/usr/share/icons/hicolor")

    @staticmethod
    def get_autostart_dir() -> Path:
        """
        Resolve user autostart configuration directory.
        Default: ~/.config/autostart
        """
        if "autostart_dir" in Paths._overrides:
            return Paths._overrides["autostart_dir"]
        if env := os.environ.get("APPIMAGEKIT_AUTOSTART_DIR"):
            return Path(env).resolve()
        if xdg_config := os.environ.get("XDG_CONFIG_HOME"):
            return Path(xdg_config).resolve() / "autostart"
        return Path.home() / ".config" / "autostart"

    @staticmethod
    def get_cache_dir() -> Path:
        """
        Resolve application cache and update staging directory.
        Default: ~/.cache/appimagekit
        """
        if "cache_dir" in Paths._overrides:
            return Paths._overrides["cache_dir"]
        if env := os.environ.get("APPIMAGEKIT_CACHE_DIR"):
            return Path(env).resolve()
        if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
            return Path(xdg_cache).resolve() / "appimagekit"
        return Path.home() / ".cache" / "appimagekit"

    @staticmethod
    def get_state_dir() -> Path:
        """
        Resolve application runtime state directory.
        Default: ~/.local/state/appimagekit
        """
        if "state_dir" in Paths._overrides:
            return Paths._overrides["state_dir"]
        if env := os.environ.get("APPIMAGEKIT_STATE_DIR"):
            return Path(env).resolve()
        if xdg_state := os.environ.get("XDG_STATE_HOME"):
            return Path(xdg_state).resolve() / "appimagekit"
        return Path.home() / ".local" / "state" / "appimagekit"

    # -----------------------------------------------------------------------
    # Dual-Mode Storage Resolution & Fallback
    # -----------------------------------------------------------------------

    @staticmethod
    def resolve_install_dir(mode: Union[InstallMode, str] = InstallMode.USER) -> Tuple[Path, InstallMode]:
        """
        Resolves the target installation directory based on requested mode.
        
        If `mode` is SYSTEM, verifies root privileges (os.geteuid() == 0).
        If root privileges are unavailable, automatically falls back to USER mode.
        
        Returns:
            Tuple[Path, InstallMode]: Resolved directory and the active InstallMode.
        """
        mode_val = mode.value if isinstance(mode, InstallMode) else str(mode).lower()
        
        if mode_val == InstallMode.SYSTEM.value:
            is_root = hasattr(os, "geteuid") and os.geteuid() == 0
            if is_root:
                sys_dir = Paths.get_system_appimages_dir()
                return (sys_dir, InstallMode.SYSTEM)
            
            # Privilege fallback to User mode when root privileges are unavailable
            return (Paths.get_user_appimages_dir(), InstallMode.USER)

        return (Paths.get_user_appimages_dir(), InstallMode.USER)

    # -----------------------------------------------------------------------
    # Directory Lifecycle
    # -----------------------------------------------------------------------

    @classmethod
    def ensure_dirs(cls, mode: Union[InstallMode, str] = InstallMode.USER) -> None:
        """
        Create all prerequisite directories for user or system installation mode.
        """
        mode_val = mode.value if isinstance(mode, InstallMode) else str(mode).lower()
        target_dir = cls.get_system_appimages_dir() if mode_val == "system" else cls.get_user_appimages_dir()
        app_dir = cls.get_system_applications_dir() if mode_val == "system" else cls.get_user_applications_dir()
        icons_dir = cls.get_system_icons_dir() if mode_val == "system" else cls.get_icons_dir()

        dirs = [
            target_dir,
            app_dir,
            icons_dir,
            cls.get_database_path().parent,
            cls.get_autostart_dir(),
            cls.get_cache_dir(),
            cls.get_state_dir(),
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
