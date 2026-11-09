"""
src/services/updater.py — Multi-tier Cascading Update Engine for AppImageKit.

Supports:
- Tier 1: Embedded zsync delta updates (via zsync CLI with direct HTTP fallback)
- Tier 2: GitHub Releases API query, version comparison, and asset streaming
- Tier 3: Direct manual download URL fallback check via HTTP HEAD (ETag, Last-Modified, Size)
- Tier 4: Manual local file in-place update
- Batch Updates: check_all_updates and apply_all_updates
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    from packaging.version import parse as parse_version
except ImportError:
    parse_version = None

from ..models.app import App, UpdateSource
from ..models.database import DatabaseManager
from .installer import InstallerService

logger = logging.getLogger("appimagekit.updater")


@dataclass
class UpdateInfo:
    """
    Metadata representation of an update candidate.
    """
    available: bool = False
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    update_source: UpdateSource = UpdateSource.NONE
    download_url: Optional[str] = None
    release_notes: Optional[str] = None
    download_size: Optional[int] = None
    zsync_url: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    @property
    def update_available(self) -> bool:
        """Alias for compatibility with test assertions."""
        return self.available

    @update_available.setter
    def update_available(self, value: bool) -> None:
        self.available = value


def clean_version_str(ver: Optional[str]) -> Optional[str]:
    """Strip leading 'v', 'V', 'release-', and whitespace from a version string."""
    if not ver:
        return None
    s = str(ver).strip()
    if s.lower().startswith("release-"):
        s = s[8:]
    elif s.lower().startswith("v"):
        s = s[1:]
    return s.strip()


def compare_versions(current_ver: Optional[str], candidate_ver: Optional[str]) -> int:
    """
    Compare two version strings:
    Returns:
       1 if candidate_ver > current_ver (candidate is newer)
       0 if candidate_ver == current_ver
      -1 if candidate_ver < current_ver (candidate is older)
    """
    c_clean = clean_version_str(current_ver)
    cand_clean = clean_version_str(candidate_ver)

    if not cand_clean and not c_clean:
        return 0
    if cand_clean and not c_clean:
        return 1
    if not cand_clean and c_clean:
        return -1

    if parse_version is not None:
        try:
            p_curr = parse_version(c_clean)
            p_cand = parse_version(cand_clean)
            if p_cand > p_curr:
                return 1
            elif p_cand < p_curr:
                return -1
            else:
                return 0
        except Exception:
            pass

    # Fallback: segment comparison
    try:
        def to_tuple(v_str: str) -> Tuple[int, ...]:
            parts = []
            for part in re.split(r'[-._]', v_str):
                digits = re.sub(r'\D', '', part)
                if digits:
                    parts.append(int(digits))
            return tuple(parts)

        t_curr = to_tuple(c_clean)
        t_cand = to_tuple(cand_clean)
        if t_cand > t_curr:
            return 1
        elif t_cand < t_curr:
            return -1
        else:
            return 0
    except Exception:
        if cand_clean != c_clean:
            return 1
        return 0


def extract_version_from_text(text: str) -> Optional[str]:
    """
    Extract a clean semantic version string from text, filenames, or URLs.
    Avoids matching hostnames or IP addresses (e.g. 127.0.0.1).
    Matches patterns like 2.0.0, 1.2.3, 0.9.5, 3.4.1.
    """
    if not text:
        return None

    # If it's a URL, inspect only the path segment
    if "://" in text:
        parsed = urllib.parse.urlparse(text)
        target = parsed.path
    else:
        target = text

    # Remove standard extensions before extracting
    for ext in [".AppImage.zsync", ".AppImage", ".zsync", ".tar.gz", ".zip", ".tar.xz"]:
        if target.endswith(ext):
            target = target[:-len(ext)]
            break

    # Also avoid architecture tags interfering
    for arch in ["-x86_64", "_x86_64", "-amd64", "_amd64", "-aarch64", "_arm64", "-i386"]:
        target = target.replace(arch, "")

    match = re.search(r'(?:^|[-_vV/])(\d+(?:\.\d+)+(?:[-_]?(?:alpha|beta|rc|pre)\.?\d*)?)', target, re.IGNORECASE)
    if match:
        return match.group(1).lstrip("vV")
    return None


class UpdaterService:
    """
    Cascading update service managing updates across all four tiers:
    - Tier 1: Embedded zsync delta update
    - Tier 2: GitHub Releases API
    - Tier 3: Direct URL fallback via HTTP HEAD
    - Tier 4: Manual in-place local file update
    """

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        installer: Optional[InstallerService] = None,
        timeout: float = 10.0,
    ) -> None:
        self.db = db if db is not None else DatabaseManager()
        self.installer = installer if installer is not None else InstallerService(db=self.db)
        self.timeout = timeout

    # -------------------------------------------------------------------------
    # Embedded Update Information Probe (Tier 1)
    # -------------------------------------------------------------------------

    def get_embedded_update_info(self, appimage_path: Union[str, Path]) -> Optional[str]:
        """
        Extracts embedded update information string from an AppImage.
        Probes standard runtime flags --appimage-updateinformation / --appimage-updateinfo
        and inspects the binary for .upd_info / zsync signatures.
        """
        path = Path(appimage_path).resolve()
        if not path.exists():
            return None

        # 1. Try runtime execution flags if executable
        if os.access(path, os.X_OK):
            for flag in ["--appimage-updateinformation", "--appimage-updateinfo"]:
                try:
                    res = subprocess.run(
                        [str(path), flag],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    out = res.stdout.strip()
                    if res.returncode == 0 and out:
                        return out
                except Exception:
                    pass

        # 2. Binary inspection for zsync| or gh-releases-zsync| signatures
        try:
            with open(path, "rb") as f:
                header = f.read(65536)
                for prefix in (b"zsync|", b"gh-releases-zsync|"):
                    idx = header.find(prefix)
                    if idx != -1:
                        end_null = header.find(b"\x00", idx)
                        end_nl = header.find(b"\n", idx)
                        end_candidates = [pos for pos in (end_null, end_nl) if pos != -1]
                        end_idx = min(end_candidates) if end_candidates else idx + 512
                        return header[idx:end_idx].decode("utf-8", errors="ignore").strip()
        except Exception:
            pass

        return None

    # -------------------------------------------------------------------------
    # Tier 1: zsync / AppImageUpdate Checking
    # -------------------------------------------------------------------------

    def _check_tier1_zsync(self, app: App, update_str: Optional[str]) -> Optional[UpdateInfo]:
        """
        Check for update via embedded zsync or gh-releases-zsync string.
        """
        if not update_str:
            return None

        # Parse zsync|<url>
        if update_str.startswith("zsync|"):
            zsync_url = update_str.split("zsync|", 1)[1].strip()
            if not zsync_url:
                return None

            try:
                # Validate that zsync URL is reachable
                req = urllib.request.Request(
                    zsync_url,
                    headers={"User-Agent": "AppImageKit/1.0"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    status = resp.status
                    if status not in (200, 206):
                        return None

                latest_ver = extract_version_from_text(zsync_url)
                cand_url = zsync_url[:-6] if zsync_url.endswith(".zsync") else zsync_url
                # Adapt /zsync/ to /downloads/ if on test server or use direct url
                if "/zsync/" in cand_url:
                    dl_url = cand_url.replace("/zsync/", "/downloads/")
                else:
                    dl_url = cand_url

                is_newer = True
                if app.version and latest_ver:
                    is_newer = compare_versions(app.version, latest_ver) > 0

                return UpdateInfo(
                    available=is_newer,
                    current_version=app.version,
                    latest_version=latest_ver or app.version,
                    update_source=UpdateSource.APPIMAGEUPDATE,
                    download_url=dl_url,
                    zsync_url=zsync_url,
                )
            except Exception as e:
                logger.debug("Tier 1 zsync probe failed for %s: %s", app.name, e)
                return None

        # Parse gh-releases-zsync|<owner>|<repo>|<tag>|<pattern>
        if update_str.startswith("gh-releases-zsync|"):
            parts = update_str.split("|")
            if len(parts) >= 4:
                owner = parts[1].strip()
                repo = parts[2].strip()
                tag = parts[3].strip()
                # Synthesize GitHub Releases check
                gh_url = f"https://api.github.com/repos/{owner}/{repo}"
                app_clone = App(
                    id=app.id,
                    name=app.name,
                    version=app.version,
                    install_path=app.install_path,
                    size_bytes=app.size_bytes,
                    update_source=UpdateSource.GITHUB,
                    update_url=gh_url,
                )
                info = self._check_tier2_github(app_clone)
                if info and info.available:
                    info.update_source = UpdateSource.APPIMAGEUPDATE
                    return info

        return None

    # -------------------------------------------------------------------------
    # Tier 2: GitHub Releases API Checking
    # -------------------------------------------------------------------------

    def _resolve_github_api_url(self, update_url: str) -> str:
        """
        Normalizes a repository URL or slug into a GitHub Releases latest endpoint:
        - http://127.0.0.1:port/repos/owner/repo -> http://127.0.0.1:port/repos/owner/repo/releases/latest
        - https://github.com/owner/repo -> https://api.github.com/repos/owner/repo/releases/latest
        - owner/repo -> https://api.github.com/repos/owner/repo/releases/latest
        """
        clean = update_url.strip()
        if clean.endswith("/releases/latest"):
            return clean

        if clean.startswith("http://") or clean.startswith("https://"):
            parsed = urllib.parse.urlparse(clean)
            if "github.com" in parsed.netloc:
                slug = parsed.path.strip("/").split("/")
                if len(slug) >= 2:
                    return f"https://api.github.com/repos/{slug[0]}/{slug[1]}/releases/latest"
            # Host mock or api endpoint
            base = clean.rstrip("/")
            if "/releases" in base:
                return base
            return f"{base}/releases/latest"

        # owner/repo format
        parts = clean.split("/")
        if len(parts) == 2:
            return f"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases/latest"

        return f"https://api.github.com/repos/{clean}/releases/latest"

    def _check_tier2_github(self, app: App) -> Optional[UpdateInfo]:
        """
        Check for updates via GitHub Releases API.
        """
        if not app.update_url:
            return None

        api_url = self._resolve_github_api_url(app.update_url)
        try:
            req = urllib.request.Request(
                api_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "AppImageKit/1.0",
                }
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            tag_name = data.get("tag_name", "")
            latest_ver = extract_version_from_text(tag_name) or clean_version_str(tag_name)
            release_notes = data.get("body", "")

            # Locate matching AppImage asset
            assets = data.get("assets", [])
            selected_asset = None
            zsync_url = None

            for asset in assets:
                asset_name = asset.get("name", "")
                if asset_name.endswith(".AppImage"):
                    if not selected_asset:
                        selected_asset = asset
                    elif "x86_64" in asset_name or "amd64" in asset_name:
                        selected_asset = asset
                elif asset_name.endswith(".AppImage.zsync"):
                    zsync_url = asset.get("browser_download_url")

            if not selected_asset and assets:
                # Fallback to first asset if no explicit .AppImage extension
                selected_asset = assets[0]

            dl_url = selected_asset.get("browser_download_url") if selected_asset else None
            dl_size = selected_asset.get("size") if selected_asset else None

            is_newer = False
            if latest_ver and app.version:
                is_newer = compare_versions(app.version, latest_ver) > 0
            elif latest_ver:
                is_newer = True

            return UpdateInfo(
                available=is_newer,
                current_version=app.version,
                latest_version=latest_ver or app.version,
                update_source=UpdateSource.GITHUB,
                download_url=dl_url,
                release_notes=release_notes,
                download_size=dl_size,
                zsync_url=zsync_url,
            )
        except Exception as e:
            logger.debug("Tier 2 GitHub check failed for %s: %s", app.name, e)
            return None

    # -------------------------------------------------------------------------
    # Tier 3: Direct Manual URL Checking
    # -------------------------------------------------------------------------

    def _check_tier3_manual_url(self, app: App) -> Optional[UpdateInfo]:
        """
        Check for updates via direct HTTP HEAD request comparing size, ETag, or Last-Modified.
        """
        if not app.update_url or not app.update_url.startswith(("http://", "https://")):
            return None

        class _HeadRequest(urllib.request.Request):
            def get_method(self) -> str:
                return "HEAD"

        try:
            req = _HeadRequest(app.update_url, headers={"User-Agent": "AppImageKit/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                headers = resp.headers
                etag = headers.get("ETag")
                last_modified = headers.get("Last-Modified")
                cl_header = headers.get("Content-Length")
                content_length = int(cl_header) if cl_header and cl_header.isdigit() else None

            latest_ver = extract_version_from_text(app.update_url)

            is_newer = False
            if latest_ver and app.version:
                cmp_res = compare_versions(app.version, latest_ver)
                if cmp_res > 0:
                    is_newer = True
                elif cmp_res == 0:
                    # Same version string: check if size differs
                    if content_length and content_length != app.size_bytes:
                        is_newer = True
            else:
                # No version in URL: check if size or headers differ
                if content_length and content_length != app.size_bytes:
                    is_newer = True
                elif etag or last_modified:
                    is_newer = True

            return UpdateInfo(
                available=is_newer,
                current_version=app.version,
                latest_version=latest_ver or app.version,
                update_source=UpdateSource.MANUAL_URL,
                download_url=app.update_url,
                download_size=content_length,
                etag=etag,
                last_modified=last_modified,
            )
        except Exception as e:
            logger.debug("Tier 3 Direct URL check failed for %s: %s", app.name, e)
            return None

    # -------------------------------------------------------------------------
    # Master Update Check
    # -------------------------------------------------------------------------

    def check_update(self, app: App) -> UpdateInfo:
        """
        Cascading update check through all configured tiers:
        Tier 1 (zsync / embedded info) -> Tier 2 (GitHub API) -> Tier 3 (Direct URL).
        If no update source is configured or no updates are found, returns UpdateInfo with available=False.
        """
        # Step 1: Probe embedded update information (Tier 1)
        embedded_info_str = None
        if app.install_path:
            embedded_info_str = self.get_embedded_update_info(app.install_path)

        if embedded_info_str or app.update_source == UpdateSource.APPIMAGEUPDATE or app.zsync_url:
            raw_str = embedded_info_str or (f"zsync|{app.zsync_url}" if app.zsync_url else None)
            res = self._check_tier1_zsync(app, raw_str)
            if res is not None:
                return res

        # Step 2: Tier 2 (GitHub Releases API)
        is_github_source = (
            app.update_source == UpdateSource.GITHUB
            or (app.update_url and ("github.com" in app.update_url or "/repos/" in app.update_url))
        )
        if is_github_source:
            res = self._check_tier2_github(app)
            if res is not None:
                return res

        # Step 3: Tier 3 (Direct Manual URL)
        is_manual_url = (
            app.update_source == UpdateSource.MANUAL_URL
            or (app.update_url and app.update_url.startswith(("http://", "https://")))
        )
        if is_manual_url:
            res = self._check_tier3_manual_url(app)
            if res is not None:
                return res

        # Fallback: No update available or no update source configured
        return UpdateInfo(
            available=False,
            current_version=app.version,
            latest_version=app.version,
            update_source=app.update_source,
        )

    # -------------------------------------------------------------------------
    # Batch Updates
    # -------------------------------------------------------------------------

    def check_all_updates(self, apps: List[App]) -> Dict[int, UpdateInfo]:
        """
        Check for updates across a list of applications.
        Returns a dictionary mapping app.id to UpdateInfo.
        """
        results: Dict[int, UpdateInfo] = {}
        for app in apps:
            if app.id is not None:
                results[app.id] = self.check_update(app)
        return results

    def apply_all_updates(
        self,
        apps: List[App],
        progress_callback: Optional[Callable[[App, float], None]] = None,
    ) -> Dict[int, App]:
        """
        Check and apply updates for all applications with available updates.
        Returns a dictionary mapping app.id to the updated App instance.
        """
        updated: Dict[int, App] = {}
        for app in apps:
            if app.id is None:
                continue
            try:
                info = self.check_update(app)
                if info.available:
                    cb = (lambda frac: progress_callback(app, frac)) if progress_callback else None
                    new_app = self.apply_update(app, info, progress_callback=cb)
                    updated[app.id] = new_app
            except Exception as e:
                logger.warning("Failed to update application %s: %s", app.name, e)
        return updated

    # -------------------------------------------------------------------------
    # Applying Updates
    # -------------------------------------------------------------------------

    def _stream_download(
        self,
        url: str,
        dest_path: Path,
        expected_size: Optional[int] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> None:
        """Stream download an HTTP/HTTPS asset into dest_path with progress callback."""
        req = urllib.request.Request(url, headers={"User-Agent": "AppImageKit/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(dest_path, "wb") as f:
            total_str = resp.headers.get("Content-Length")
            total = int(total_str) if total_str and total_str.isdigit() else expected_size
            downloaded = 0
            chunk_size = 65536
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total and total > 0:
                    progress_callback(min(1.0, downloaded / total))

    def apply_update(
        self,
        app: App,
        update_info: Optional[UpdateInfo] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> App:
        """
        Downloads and applies the update for the given application in-place.
        Handles zsync CLI delta updates with direct HTTP streaming fallback.
        """
        if update_info is None:
            update_info = self.check_update(app)

        if not update_info.available:
            logger.info("Application %s is already up to date.", app.name)
            return app

        # Tier 4: Local file in-place update
        if update_info.update_source == UpdateSource.LOCAL_FILE and update_info.download_url:
            local_file = Path(update_info.download_url)
            return self.installer.update_in_place(app, local_file)

        # Tier 1: Try zsync delta if zsync CLI tool is available
        if update_info.update_source == UpdateSource.APPIMAGEUPDATE and update_info.zsync_url:
            zsync_bin = shutil.which("zsync")
            if zsync_bin and app.install_path:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    dest_file = Path(tmp_dir) / Path(app.install_path).name
                    cmd = [zsync_bin, "-i", app.install_path, "-o", str(dest_file), update_info.zsync_url]
                    try:
                        res = subprocess.run(cmd, capture_output=True, timeout=60)
                        if res.returncode == 0 and dest_file.exists() and dest_file.stat().st_size > 0:
                            return self.installer.update_in_place(app, dest_file)
                    except Exception as e:
                        logger.warning("zsync delta download failed, falling back to HTTP: %s", e)

        # Fallback for all online tiers: stream direct HTTP download
        if not update_info.download_url:
            raise RuntimeError(f"Cannot apply update for {app.name}: no download URL available")

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_appimage = Path(tmp_dir) / "update_download.AppImage"
            self._stream_download(
                update_info.download_url,
                temp_appimage,
                expected_size=update_info.download_size,
                progress_callback=progress_callback,
            )
            return self.installer.update_in_place(app, temp_appimage)
