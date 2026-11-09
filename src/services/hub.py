"""
src/services/hub.py — AppImage Hub Catalog Browser and Direct Installer.
Fetches feed from appimage.github.io/feed.json, caches locally, provides
case-insensitive searching, category filtering, and direct AppImage downloads.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..models.app import App, InstallMode
from ..models.database import DatabaseManager
from ..utils.paths import Paths

logger = logging.getLogger("appimagekit.hub")


class HubCatalogError(Exception):
    """Exception raised for AppImage Hub operations."""
    pass


class HubCatalogService:
    """
    Consumes the AppImage Hub catalog feed (https://appimage.github.io/feed.json).
    Caches the catalog feed locally and allows searching by query, category, and direct installation.
    """

    DEFAULT_FEED_URL = "https://appimage.github.io/feed.json"
    CACHE_EXPIRY_SECONDS = 86400  # 24 hours

    def __init__(
        self,
        feed_url: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        db: Optional[DatabaseManager] = None,
        installer: Optional[Any] = None,
    ) -> None:
        self.feed_url = feed_url or self.DEFAULT_FEED_URL
        self.cache_dir = cache_dir or Paths.get_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "hub_feed.json"
        self.db = db
        self.installer = installer

    def fetch_feed(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch the AppImage Hub JSON feed with local file caching and offline fallback.
        """
        # Check cache validity
        if not force_refresh and self.cache_file.exists():
            try:
                mtime = self.cache_file.stat().st_mtime
                if (time.time() - mtime) < self.CACHE_EXPIRY_SECONDS:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                logger.warning("Failed to read cache file %s: %s", self.cache_file, e)

        # Download feed from URL
        req = urllib.request.Request(
            self.feed_url,
            headers={"User-Agent": "AppImageKit/0.1.0 (Linux; Freedesktop)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with open(self.cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return data
        except Exception as e:
            logger.warning("Failed to fetch feed from %s: %s", self.feed_url, e)
            if self.cache_file.exists():
                logger.info("Falling back to existing cached feed.")
                try:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            raise HubCatalogError(f"Unable to fetch AppImage Hub feed from '{self.feed_url}': {e}") from e

    def get_items(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Retrieve the list of catalog items."""
        feed = self.fetch_feed(force_refresh=force_refresh)
        return feed.get("items", [])

    def search(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search catalog feed by query keyword (name/description) and/or category.
        """
        items = self.get_items()
        results: List[Dict[str, Any]] = []

        q = query.strip().lower() if query else None
        cat = category.strip().lower() if category else None

        for item in items:
            name = item.get("name", "")
            desc = item.get("description", "")
            categories = [c.lower() for c in item.get("categories", [])]

            if q and not (q in name.lower() or q in desc.lower()):
                continue

            if cat and cat not in categories:
                continue

            results.append(item)

        return results

    def get_app_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a catalog item by name (case-insensitive)."""
        target = name.strip().lower()
        for item in self.get_items():
            if item.get("name", "").strip().lower() == target:
                return item
        return None

    def install_from_hub(
        self,
        item_or_name: Union[Dict[str, Any], str],
        mode: InstallMode = InstallMode.USER,
    ) -> App:
        """
        Download and install an application directly from the Hub feed.
        """
        if isinstance(item_or_name, str):
            item = self.get_app_by_name(item_or_name)
            if not item:
                raise HubCatalogError(f"Application '{item_or_name}' not found in AppImage Hub catalog.")
        else:
            item = item_or_name

        # Locate download link
        download_url: Optional[str] = None
        for link in item.get("links", []):
            ltype = link.get("type", "").lower()
            url = link.get("url", "")
            if ltype in ("download", "appimage") or url.lower().endswith(".appimage"):
                download_url = url
                break

        if not download_url and item.get("links"):
            download_url = item["links"][0].get("url")

        if not download_url:
            raise HubCatalogError(f"No valid download URL available for '{item.get('name')}'.")

        # Download binary into temporary staging cache
        download_dir = self.cache_dir / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)

        parsed_url = urllib.parse.urlparse(download_url)
        filename = Path(parsed_url.path).name or f"{item.get('name', 'App')}.AppImage"
        if not filename.endswith(".AppImage"):
            filename += ".AppImage"

        dest_file = download_dir / filename
        logger.info("Downloading '%s' from %s to %s", item.get("name"), download_url, dest_file)

        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "AppImageKit/0.1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest_file, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
            dest_file.chmod(0o755)
        except Exception as e:
            raise HubCatalogError(f"Failed to download '{item.get('name')}' from '{download_url}': {e}") from e

        # Install using InstallerService
        from .installer import InstallerService
        db = self.db or DatabaseManager()
        installer = self.installer or InstallerService(db=db)

        categories = item.get("categories", ["Utility"])
        primary_category = categories[0] if categories else "Utility"

        app = installer.install(dest_file, mode=mode, category=primary_category)
        app.hub_id = item.get("name")
        db.update_app(app)
        return app
