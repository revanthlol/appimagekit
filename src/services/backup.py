"""
src/services/backup.py — Backup and Restore Management for AppImageKit.
Supports exporting and importing application manifests in JSON format,
as well as full portable archive bundles (.tar.gz).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..models.app import App
from ..models.database import DatabaseManager
from ..utils.paths import Paths

logger = logging.getLogger("appimagekit.backup")


class BackupError(Exception):
    """Base exception for backup operations."""
    pass


class BackupManagerService:
    """
    Manages export and restore of AppImageKit applications and registries:
    - JSON manifest export & import.
    - Compressed archive bundle (.tar.gz) creation and extraction.
    """

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        paths: Optional[Paths] = None,
    ) -> None:
        self.db = db if db is not None else DatabaseManager()
        self.paths = paths or Paths()

    def export_manifest(self, destination_path: Union[str, Path]) -> Path:
        """
        Export registered application list to a formatted JSON manifest file.
        Returns the destination Path.
        """
        dest = Path(destination_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        apps = self.db.list_apps()
        manifest: Dict[str, Any] = {
            "format_version": "1.0",
            "appimagekit_version": "0.1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_apps": len(apps),
            "apps": [app.to_dict() for app in apps],
        }

        with open(dest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        logger.info("Exported manifest with %d app(s) to %s", len(apps), dest)
        return dest

    def import_manifest(
        self,
        manifest_path: Union[str, Path],
        overwrite: bool = True,
    ) -> List[App]:
        """
        Import applications from a JSON manifest into the database registry.
        If an app already exists, updates it if overwrite is True.
        Returns list of restored/updated App instances.
        """
        path = Path(manifest_path).resolve()
        if not path.is_file():
            raise BackupError(f"Manifest file not found: '{manifest_path}'")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise BackupError(f"Invalid JSON in manifest '{manifest_path}': {e}") from e

        raw_apps = data.get("apps", [])
        if not isinstance(raw_apps, list):
            raise BackupError(f"Manifest format error: 'apps' key must be a list in '{manifest_path}'")

        restored: List[App] = []
        for item in raw_apps:
            try:
                app_data = dict(item)
                # Lookup if app already exists by path or name
                existing = None
                if app_data.get("install_path"):
                    existing = self.db.get_app_by_path(app_data["install_path"])
                if not existing and app_data.get("name"):
                    existing = self.db.get_app_by_name(app_data["name"])

                if existing:
                    if overwrite:
                        app = App.from_dict(app_data)
                        app.id = existing.id
                        self.db.update_app(app)
                        restored.append(app)
                    else:
                        restored.append(existing)
                else:
                    app = App.from_dict(app_data)
                    app.id = None  # Ensure fresh primary key
                    saved_app = self.db.add_app(app)
                    restored.append(saved_app)
            except Exception as e:
                logger.warning("Failed to import app '%s': %s", item.get("name"), e)

        logger.info("Imported %d app(s) from %s", len(restored), path)
        return restored

    def export_bundle(self, bundle_path: Union[str, Path]) -> Path:
        """
        Export both the JSON manifest and the underlying AppImage binaries
        into a self-contained .tar.gz bundle.
        """
        bundle = Path(bundle_path).resolve()
        bundle.parent.mkdir(parents=True, exist_ok=True)

        apps = self.db.list_apps()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            manifest_file = tmp_path / "manifest.json"
            self.export_manifest(manifest_file)

            with tarfile.open(bundle, "w:gz") as tar:
                tar.add(str(manifest_file), arcname="manifest.json")

                for app in apps:
                    if app.install_path and Path(app.install_path).is_file():
                        arc_name = f"binaries/{Path(app.install_path).name}"
                        tar.add(app.install_path, arcname=arc_name)
                    if app.icon_path and Path(app.icon_path).is_file():
                        arc_name = f"icons/{Path(app.icon_path).name}"
                        tar.add(app.icon_path, arcname=arc_name)

        logger.info("Exported full bundle to %s", bundle)
        return bundle

    def import_bundle(
        self,
        bundle_path: Union[str, Path],
        target_dir: Optional[Path] = None,
    ) -> List[App]:
        """
        Restore applications and binaries from a .tar.gz bundle.
        """
        bundle = Path(bundle_path).resolve()
        if not bundle.is_file():
            raise BackupError(f"Bundle file not found: '{bundle_path}'")

        target = target_dir or self.paths.get_user_appimages_dir()
        target.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with tarfile.open(bundle, "r:gz") as tar:
                tar.extractall(path=tmp_path)

            manifest_file = tmp_path / "manifest.json"
            if not manifest_file.exists():
                raise BackupError("Invalid bundle: missing manifest.json inside archive.")

            # Copy extracted binaries to target directory and make executable
            bin_dir = tmp_path / "binaries"
            if bin_dir.exists():
                for f in bin_dir.iterdir():
                    if f.is_file():
                        dest_f = target / f.name
                        shutil.copy2(f, dest_f)
                        dest_f.chmod(dest_f.stat().st_mode | 0o755)

            # Copy extracted icons if present
            icons_dir = tmp_path / "icons"
            user_icons = self.paths.get_icons_dir()
            if icons_dir.exists() and user_icons:
                user_icons.mkdir(parents=True, exist_ok=True)
                for ic in icons_dir.iterdir():
                    if ic.is_file():
                        dest_ic = user_icons / ic.name
                        shutil.copy2(ic, dest_ic)

            # Import manifest
            return self.import_manifest(manifest_file, overwrite=True)
