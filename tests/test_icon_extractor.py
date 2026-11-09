"""
tests/test_icon_extractor.py — Comprehensive unit tests for IconExtractorService.
Validates multi-tier extraction (--appimage-extract, unsquashfs -o <offset>, 7z),
format sniffing, symlink resolution, and freedesktop hicolor routing.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models.app import InstallMode
from src.services.icon_extractor import IconExtractorService
from src.utils.paths import Paths
from tests.conftest import create_mock_appimage, create_type2_mock_appimage


def test_detect_format(tmp_path):
    """Verify content sniffing and extension detection for SVG and PNG."""
    svc = IconExtractorService()

    # PNG by magic bytes
    png_file = tmp_path / "test1"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")
    assert svc.detect_format(png_file) == "png"

    # SVG by XML / SVG tags
    svg_file = tmp_path / "test2"
    svg_file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle/></svg>', encoding="utf-8")
    assert svc.detect_format(svg_file) == "svg"

    xml_svg_file = tmp_path / "test3"
    xml_svg_file.write_text('<?xml version="1.0"?><svg><rect/></svg>', encoding="utf-8")
    assert svc.detect_format(xml_svg_file) == "svg"

    # Extension fallbacks
    ext_svg = tmp_path / "icon.svg"
    ext_svg.write_text("not real xml", encoding="utf-8")
    assert svc.detect_format(ext_svg) == "svg"

    ext_png = tmp_path / "icon.png"
    ext_png.write_bytes(b"generic bytes")
    assert svc.detect_format(ext_png) == "png"


def test_extract_svg_diricon_primary(tmp_path):
    """Verify primary extraction of SVG .DirIcon routes to scalable/apps."""
    icons_dir = tmp_path / "icons" / "hicolor"
    appimage = create_mock_appimage(
        tmp_path / "SvgApp.AppImage",
        name="SvgApp",
        icon_svg='<svg><circle fill="blue"/></svg>',
    )

    with Paths.override(icons_dir=icons_dir):
        svc = IconExtractorService()
        icon_path = svc.extract_icon(appimage, app_name="SvgApp")

        assert icon_path is not None
        assert icon_path.exists()
        assert icon_path.suffix == ".svg"
        assert "scalable" in str(icon_path)
        assert icon_path.parent == icons_dir / "scalable" / "apps"
        assert "blue" in icon_path.read_text(encoding="utf-8")


def test_extract_png_diricon_primary(tmp_path):
    """Verify primary extraction of PNG .DirIcon routes to 256x256/apps."""
    icons_dir = tmp_path / "icons" / "hicolor"
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00"

    # Create mock appimage that extracts a PNG .DirIcon
    app_path = tmp_path / "PngApp.AppImage"
    script = f"""#!/bin/bash
if [ "$1" = "--appimage-extract" ]; then
    mkdir -p squashfs-root
    printf '\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR' > squashfs-root/.DirIcon
    exit 0
fi
exit 0
"""
    app_path.write_text(script, encoding="utf-8")
    app_path.chmod(0o755)

    with Paths.override(icons_dir=icons_dir):
        svc = IconExtractorService()
        icon_path = svc.extract_icon(app_path, app_name="PngApp")

        assert icon_path is not None
        assert icon_path.exists()
        assert icon_path.suffix == ".png"
        assert "256x256" in str(icon_path)
        assert icon_path.parent == icons_dir / "256x256" / "apps"


def test_extract_icon_relative_symlink(tmp_path):
    """Verify resolution of relative symlink .DirIcon pointing within squashfs-root."""
    svc = IconExtractorService()
    root = tmp_path / "squashfs-root"
    root.mkdir(parents=True)

    target_icon = root / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "real_icon.svg"
    target_icon.parent.mkdir(parents=True)
    target_icon.write_text("<svg><rect fill='green'/></svg>", encoding="utf-8")

    # Create relative symlink .DirIcon -> usr/share/icons/...
    dir_icon = root / ".DirIcon"
    os.symlink("usr/share/icons/hicolor/scalable/apps/real_icon.svg", dir_icon)

    candidate = svc._find_candidate_icon(root, app_name="RealApp")
    assert candidate is not None
    assert candidate.resolve() == target_icon.resolve()


def test_locate_squashfs_offset_and_unsquashfs_fallback(tmp_path):
    """Verify locate_squashfs_offset and unsquashfs fallback on non-executable Type 2 AppImage."""
    app_path = tmp_path / "Type2App.AppImage"
    create_type2_mock_appimage(app_path, name="Type2App", icon_svg='<svg><polygon fill="yellow"/></svg>')

    svc = IconExtractorService()
    offset = svc.locate_squashfs_offset(app_path)
    assert offset is not None
    assert offset >= 0

    # Make file non-executable to force fallback extraction
    app_path.chmod(0o644)
    icons_dir = tmp_path / "icons" / "hicolor"

    with Paths.override(icons_dir=icons_dir):
        icon_path = svc.extract_icon(app_path, app_name="Type2App")
        assert icon_path is not None
        assert icon_path.exists()
        assert "yellow" in icon_path.read_text(encoding="utf-8")


def test_7z_extraction_fallback(tmp_path):
    """Verify 7z fallback when runtime extraction and unsquashfs fail."""
    app_path = tmp_path / "SevenZipApp.AppImage"
    create_type2_mock_appimage(app_path, name="SevenZipApp", icon_svg='<svg><circle fill="purple"/></svg>')

    svc = IconExtractorService()
    icons_dir = tmp_path / "icons" / "hicolor"

    with Paths.override(icons_dir=icons_dir):
        # Mock runtime and unsquashfs to fail
        with patch.object(svc, "_extract_via_runtime", return_value=False), \
             patch.object(svc, "_extract_via_unsquashfs", return_value=False):
            icon_path = svc.extract_icon(app_path, app_name="SevenZipApp")
            assert icon_path is not None
            assert icon_path.exists()
            assert "purple" in icon_path.read_text(encoding="utf-8")


def test_desktop_icon_key_fallback(tmp_path):
    """Verify fallback when .DirIcon is missing but embedded .desktop defines Icon=."""
    svc = IconExtractorService()
    root = tmp_path / "squashfs-root"
    root.mkdir(parents=True)

    # Embedded desktop with Icon=my_custom_icon
    (root / "myapp.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=MyApp\nIcon=my_custom_icon\n",
        encoding="utf-8",
    )
    # Icon file in root
    icon_file = root / "my_custom_icon.png"
    icon_file.write_bytes(b"\x89PNG\r\n\x1a\n...")

    candidate = svc._find_candidate_icon(root, app_name="MyApp")
    assert candidate is not None
    assert candidate == icon_file


def test_missing_icon_graceful_handling(tmp_path):
    """Verify that an AppImage containing no icons returns None without crashing."""
    app_path = tmp_path / "NoIcon.AppImage"
    script = """#!/bin/bash
if [ "$1" = "--appimage-extract" ]; then
    mkdir -p squashfs-root
    exit 0
fi
exit 0
"""
    app_path.write_text(script, encoding="utf-8")
    app_path.chmod(0o755)

    svc = IconExtractorService()
    icon_path = svc.extract_icon(app_path, app_name="NoIcon")
    assert icon_path is None


def test_extract_metadata_from_embedded_desktop(tmp_path):
    """Verify extract_metadata returns structured metadata from the embedded desktop file."""
    app_path = create_mock_appimage(
        tmp_path / "MetaApp.AppImage",
        name="MetaApp",
        version="3.2.1",
        category="AudioVideo;Audio;",
    )

    svc = IconExtractorService()
    meta = svc.extract_metadata(app_path)

    assert meta["name"] == "MetaApp"
    assert "Audio" in meta["categories"]
    assert meta["version"] == "3.2.1"
    assert meta["terminal"] is False
    assert "MetaApp" in meta["startup_wm_class"]
