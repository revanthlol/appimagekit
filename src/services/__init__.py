"""
src/services/__init__.py — Application services for AppImageKit.
"""

from .installer import (
    InstallerService,
    InstallerError,
    AppImageNotFoundError,
    InvalidAppImageError,
    AppAlreadyInstalledError,
    PermissionDeniedError,
    ExecutionError,
)
from .desktop_entry import DesktopEntryService
from .icon_extractor import IconExtractorService
from .updater import UpdaterService, UpdateInfo
from .sandbox import SandboxDetectorService
from .autostart import AutostartManager
from .backup import BackupManagerService, BackupError
from .integrity import IntegrityVerifierService
from .hub import HubCatalogService, HubCatalogError

__all__ = [
    "InstallerService",
    "InstallerError",
    "AppImageNotFoundError",
    "InvalidAppImageError",
    "AppAlreadyInstalledError",
    "PermissionDeniedError",
    "ExecutionError",
    "DesktopEntryService",
    "IconExtractorService",
    "UpdaterService",
    "UpdateInfo",
    "SandboxDetectorService",
    "AutostartManager",
    "BackupManagerService",
    "BackupError",
    "IntegrityVerifierService",
    "HubCatalogService",
    "HubCatalogError",
]
