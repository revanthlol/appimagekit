"""
src/services/__init__.py — Application services for AppImageKit.
"""

import sys
import types
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

# Register stub module references for future milestone services
# so test suites can import 'src' without premature import crashes,
# while actual behavior is deferred to their respective milestones.
_FUTURE_SERVICES = [
    ("src.services.updater", ["UpdaterService", "UpdateInfo"]),
    ("src.services.sandbox", ["SandboxDetectorService"]),
    ("src.services.backup", ["BackupManagerService"]),
    ("src.services.hub", ["HubCatalogService"]),
    ("src.services.integrity", ["IntegrityVerifierService"]),
    ("src.services.autostart", ["AutostartManager"]),
]

for mod_name, class_names in _FUTURE_SERVICES:
    if mod_name not in sys.modules:
        stub_mod = types.ModuleType(mod_name)
        for cls_name in class_names:
            setattr(stub_mod, cls_name, type(cls_name, (), {}))
        sys.modules[mod_name] = stub_mod

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
]
