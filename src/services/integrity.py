"""
src/services/integrity.py — SHA256 Integrity Verification Service for AppImageKit.
Calculates SHA256 checksums in chunks and verifies on-disk binaries against SQLite database records.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ..models.app import App
from ..models.database import DatabaseManager

logger = logging.getLogger("appimagekit.integrity")


class IntegrityVerifierService:
    """
    Audits on-disk AppImage binaries against recorded SHA256 checksums in the registry.
    Detects byte-level tampering, file truncation, or unauthorized replacements.
    """

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db if db is not None else DatabaseManager()

    @staticmethod
    def calculate_sha256(file_path: Union[str, Path], chunk_size: int = 65536) -> str:
        """
        Calculate SHA256 checksum of a file in chunks for minimal memory usage.
        """
        path = Path(file_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found for checksum calculation: {file_path}")

        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest().lower()

    def _resolve_app(self, app_or_target: Union[App, str, int, Path]) -> Optional[App]:
        """Resolve an App instance from various target types."""
        if isinstance(app_or_target, App):
            return app_or_target
        if isinstance(app_or_target, int):
            return self.db.get_app(app_or_target)
        if isinstance(app_or_target, (str, Path)):
            target_str = str(app_or_target)
            return self.db.get_app_by_name(target_str) or self.db.get_app_by_path(target_str)
        return None

    def verify(self, app_or_target: Union[App, str, int, Path]) -> bool:
        """
        Verify the integrity of an installed AppImage binary.
        Returns True if on-disk SHA256 matches the recorded database checksum, False otherwise.
        """
        app = self._resolve_app(app_or_target)
        if not app:
            logger.warning("Integrity check failed: could not resolve app from %s", app_or_target)
            return False

        if not app.install_path or not app.checksum_sha256:
            logger.warning("App '%s' lacks install_path or recorded checksum.", app.name)
            return False

        binary_path = Path(app.install_path)
        if not binary_path.is_file():
            logger.warning("Installed binary not found at '%s' for app '%s'", binary_path, app.name)
            return False

        try:
            current_sha = self.calculate_sha256(binary_path)
            matches = current_sha == app.checksum_sha256.lower()
            if not matches:
                logger.warning(
                    "Checksum mismatch for '%s': expected %s, got %s",
                    app.name, app.checksum_sha256, current_sha
                )
            return matches
        except Exception as e:
            logger.error("Error computing checksum for '%s': %s", app.name, e)
            return False

    def verify_with_details(self, app_or_target: Union[App, str, int, Path]) -> Dict[str, Any]:
        """
        Perform integrity check and return detailed status report.
        """
        app = self._resolve_app(app_or_target)
        if not app:
            return {
                "name": str(app_or_target),
                "valid": False,
                "error": "Application not found in registry",
                "install_path": None,
                "expected_checksum": None,
                "actual_checksum": None,
            }

        binary_path = Path(app.install_path)
        if not binary_path.exists():
            return {
                "name": app.name,
                "valid": False,
                "error": f"Binary file missing at {app.install_path}",
                "install_path": app.install_path,
                "expected_checksum": app.checksum_sha256,
                "actual_checksum": None,
            }

        try:
            current_sha = self.calculate_sha256(binary_path)
            valid = current_sha == app.checksum_sha256.lower()
            return {
                "name": app.name,
                "valid": valid,
                "error": None if valid else f"Checksum mismatch: expected {app.checksum_sha256}, got {current_sha}",
                "install_path": app.install_path,
                "expected_checksum": app.checksum_sha256,
                "actual_checksum": current_sha,
                "size_bytes": binary_path.stat().st_size,
            }
        except Exception as e:
            return {
                "name": app.name,
                "valid": False,
                "error": f"Failed to compute checksum: {e}",
                "install_path": app.install_path,
                "expected_checksum": app.checksum_sha256,
                "actual_checksum": None,
            }

    def verify_all(self) -> Dict[str, bool]:
        """Verify integrity of all registered applications. Returns {app_name: is_valid}."""
        results: Dict[str, bool] = {}
        for app in self.db.list_apps():
            results[app.name] = self.verify(app)
        return results
