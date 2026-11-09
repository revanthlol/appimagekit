"""
src/services/icon_extractor.py — Multi-tier AppImage icon and metadata extraction.
Extracts .DirIcon, PNG, SVG, and embedded .desktop files from AppImage packages.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..models.app import InstallMode
from ..utils.paths import Paths

logger = logging.getLogger("appimagekit.icon_extractor")


class IconExtractorService:
    """
    Extracts icons and embedded desktop metadata from AppImage files.

    Supports:
    - Primary: ./app.AppImage --appimage-extract
    - Fallback 1: unsquashfs -o <offset>
    - Fallback 2: 7z extraction
    - Fallback 3: Parsing embedded .desktop for Icon= key
    - Formats: SVG, PNG
    - Placement: Freedesktop hicolor theme scalable/apps or 256x256/apps
    """

    def __init__(self, paths: Optional[Paths] = None) -> None:
        self.paths = paths if paths is not None else Paths()

    def detect_format(self, file_path: Path) -> str:
        """Sniff file content to detect if it is SVG, PNG, or unknown."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(512)
                if header.startswith(b"\x89PNG\r\n\x1a\n"):
                    return "png"
                sample = header.decode("utf-8", errors="ignore").lower()
                if "<svg" in sample or "<?xml" in sample:
                    return "svg"
        except Exception:
            pass

        ext = file_path.suffix.lower()
        if ext in (".svg", ".svgz"):
            return "svg"
        if ext in (".png",):
            return "png"
        return "png"

    def locate_squashfs_offset(self, appimage_path: Path) -> Optional[int]:
        """
        Locates the byte offset of the SquashFS filesystem payload in Type 2 AppImages.
        """
        path = Path(appimage_path)
        # Try --appimage-offset runtime option if file is executable
        try:
            if os.access(path, os.X_OK):
                res = subprocess.run(
                    [str(path), "--appimage-offset"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip().isdigit():
                    return int(res.stdout.strip())
        except Exception:
            pass

        # Scan for SquashFS magic bytes: little-endian 'hsqs' (0x73717368) or big-endian 'sqsh'
        try:
            with open(path, "rb") as f:
                chunk = f.read(4 * 1024 * 1024)
                offset = chunk.find(b"hsqs")
                if offset != -1:
                    return offset
                offset = chunk.find(b"sqsh")
                if offset != -1:
                    return offset
        except Exception:
            pass

        return None

    def _extract_via_runtime(self, appimage_path: Path, temp_dir: Path) -> bool:
        """Attempt extraction using the AppImage runtime flag --appimage-extract."""
        try:
            cmd = [str(appimage_path.resolve()), "--appimage-extract"]
            res = subprocess.run(
                cmd,
                cwd=str(temp_dir),
                capture_output=True,
                text=True,
                timeout=15,
            )
            root = temp_dir / "squashfs-root"
            return res.returncode == 0 and root.exists()
        except Exception as e:
            logger.debug("Runtime extraction failed: %s", e)
            return False

    def _extract_via_unsquashfs(self, appimage_path: Path, temp_dir: Path) -> bool:
        """Attempt extraction using unsquashfs -o <offset>."""
        if not shutil.which("unsquashfs"):
            return False

        offset = self.locate_squashfs_offset(appimage_path)
        if offset is None:
            return False

        dest = temp_dir / "squashfs-root"
        try:
            cmd = ["unsquashfs", "-dest", str(dest), "-o", str(offset), str(appimage_path), ".DirIcon", "*.desktop"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and dest.exists() and any(dest.iterdir()):
                return True
            # Try full unsquashfs if pattern-matched extraction produced nothing
            cmd_full = ["unsquashfs", "-dest", str(dest), "-o", str(offset), str(appimage_path)]
            res_full = subprocess.run(cmd_full, capture_output=True, text=True, timeout=20)
            return res_full.returncode == 0 and dest.exists() and any(dest.iterdir())
        except Exception as e:
            logger.debug("unsquashfs extraction failed: %s", e)
            return False

    def _extract_via_7z(self, appimage_path: Path, temp_dir: Path) -> bool:
        """Attempt extraction using 7z."""
        if not shutil.which("7z"):
            return False

        dest = temp_dir / "squashfs-root"
        try:
            cmd = ["7z", "x", "-y", f"-o{dest}", str(appimage_path), ".DirIcon", "*.desktop"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and dest.exists() and any(dest.iterdir()):
                return True
            cmd_full = ["7z", "x", "-y", f"-o{dest}", str(appimage_path)]
            res_full = subprocess.run(cmd_full, capture_output=True, text=True, timeout=20)
            return res_full.returncode == 0 and dest.exists() and any(dest.iterdir())
        except Exception as e:
            logger.debug("7z extraction failed: %s", e)
            return False

    def _resolve_symlink(self, link_path: Path, root: Path) -> Optional[Path]:
        """Resolve a relative or absolute symlink within the squashfs-root hierarchy."""
        try:
            target_str = os.readlink(link_path)
            if target_str.startswith("/"):
                resolved = root / target_str.lstrip("/")
            else:
                resolved = (link_path.parent / target_str).resolve()
            if resolved.exists() and resolved.is_file():
                return resolved
        except Exception:
            pass
        return None

    def _find_candidate_icon(self, root: Path, app_name: Optional[str] = None) -> Optional[Path]:
        """Locate the best icon candidate inside squashfs-root."""
        # 1. Direct .DirIcon
        dir_icon = root / ".DirIcon"
        if dir_icon.exists():
            if dir_icon.is_symlink():
                resolved = self._resolve_symlink(dir_icon, root)
                if resolved:
                    return resolved
            elif dir_icon.is_file() and dir_icon.stat().st_size > 0:
                return dir_icon

        # 2. Check embedded .desktop Icon= key
        desktop_files = list(root.glob("*.desktop")) + list(root.glob("usr/share/applications/*.desktop"))
        for df in desktop_files:
            try:
                content = df.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    if line.strip().startswith("Icon="):
                        icon_key = line.split("=", 1)[1].strip()
                        # Search direct in root
                        for ext in [".svg", ".png"]:
                            direct = root / f"{icon_key}{ext}"
                            if direct.exists() and direct.is_file():
                                return direct
                        # Search subdirectories
                        matches = list(root.glob(f"**/{icon_key}.*"))
                        for m in matches:
                            if m.is_file() and m.suffix.lower() in [".png", ".svg"]:
                                return m
            except Exception:
                pass

        # 3. Check for app_name.svg or app_name.png
        if app_name:
            clean = re.sub(r'[^a-zA-Z0-9_\-]', '', app_name).lower()
            for ext in [".svg", ".png"]:
                candidate = root / f"{clean}{ext}"
                if candidate.exists() and candidate.is_file():
                    return candidate
            for m in root.glob(f"**/{clean}.*"):
                if m.is_file() and m.suffix.lower() in [".svg", ".png"]:
                    return m

        # 4. Search any root SVG or PNG files
        for ext in ["*.svg", "*.png"]:
            for candidate in sorted(root.glob(ext)):
                if candidate.is_file() and candidate.name != ".DirIcon" and candidate.stat().st_size > 0:
                    return candidate

        # 5. Search usr/share/icons hierarchy
        for ti in root.glob("usr/share/icons/**/apps/*.*"):
            if ti.is_file() and ti.suffix.lower() in [".svg", ".png"]:
                return ti

        # 6. Search usr/share/pixmaps hierarchy
        for ti in root.glob("usr/share/pixmaps/*.*"):
            if ti.is_file() and ti.suffix.lower() in [".svg", ".png"]:
                return ti

        return None

    def extract_icon(
        self,
        appimage_path: Union[str, Path],
        app_name: Optional[str] = None,
        install_mode: Union[InstallMode, str] = InstallMode.USER,
        destination_dir: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Extracts application icon from AppImage and installs it into the hicolor icon hierarchy.
        Returns the resolved Path of the installed icon, or None if extraction failed.
        """
        path = Path(appimage_path).resolve()
        if not path.exists():
            return None

        slug = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', (app_name or path.stem)).lower().strip('_')
        if not slug:
            slug = "app"

        with tempfile.TemporaryDirectory() as tmp_str:
            temp_dir = Path(tmp_str)
            extracted = (
                self._extract_via_runtime(path, temp_dir)
                or self._extract_via_unsquashfs(path, temp_dir)
                or self._extract_via_7z(path, temp_dir)
            )

            root = temp_dir / "squashfs-root"
            if not extracted or not root.exists():
                return None

            candidate = self._find_candidate_icon(root, app_name)
            if not candidate or not candidate.is_file():
                return None

            fmt = self.detect_format(candidate)
            mode_val = install_mode.value if isinstance(install_mode, InstallMode) else str(install_mode).lower()
            base_icons_dir = self.paths.get_system_icons_dir() if mode_val == "system" else self.paths.get_icons_dir()

            if destination_dir:
                target_dir = destination_dir
            elif fmt == "svg":
                target_dir = base_icons_dir / "scalable" / "apps"
            else:
                target_dir = base_icons_dir / "256x256" / "apps"

            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"{slug}.{fmt}"

            try:
                shutil.copy2(candidate, target_path)
            except Exception as e:
                logger.error("Failed to copy icon: %s", e)
                return None

            # Update icon cache if tool is present
            try:
                subprocess.run(
                    ["gtk-update-icon-cache", "-t", "-f", str(base_icons_dir)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass

            return target_path

    def extract_metadata(self, appimage_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Extracts embedded .desktop file and returns a parsed dictionary of metadata:
        name, exec, icon, categories, comment, terminal, startup_wm_class, version.
        """
        path = Path(appimage_path).resolve()
        metadata: Dict[str, Any] = {}
        if not path.exists():
            return metadata

        with tempfile.TemporaryDirectory() as tmp_str:
            temp_dir = Path(tmp_str)
            extracted = (
                self._extract_via_runtime(path, temp_dir)
                or self._extract_via_unsquashfs(path, temp_dir)
                or self._extract_via_7z(path, temp_dir)
            )
            root = temp_dir / "squashfs-root"
            if not extracted or not root.exists():
                return metadata

            desktop_files = list(root.glob("*.desktop")) + list(root.glob("usr/share/applications/*.desktop"))
            if desktop_files:
                df = desktop_files[0]
                try:
                    from .desktop_entry import DesktopEntryService
                    parsed = DesktopEntryService.parse_desktop_file(df)
                    metadata.update(parsed)
                except Exception as e:
                    logger.debug("Failed to parse embedded desktop file: %s", e)

        # Probe --appimage-version if version was not found in desktop file
        if not metadata.get("version"):
            try:
                if os.access(path, os.X_OK):
                    res = subprocess.run(
                        [str(path), "--appimage-version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        metadata["version"] = res.stdout.strip()
            except Exception:
                pass

        return metadata
