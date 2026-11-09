"""
tests/test_updater.py — Unit test suite for AppImageKit Multi-Tier Updater Service.

Tests:
1. UpdateInfo dataclass and property contracts
2. Version comparison logic and extraction helpers
3. Embedded update information inspection
4. Tier 1: zsync delta updates and direct HTTP fallback
5. Tier 2: GitHub Releases API query, version checks, and streaming download
6. Tier 3: Direct Manual URL checks via HTTP HEAD and size/ETag comparison
7. Tier 4: Manual in-place local file replacement with customization preservation
8. Batch updates (check_all_updates and apply_all_updates)
9. CLI update commands and options
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.main import cli
from src.models.app import App, InstallMode, UpdateSource
from src.models.database import DatabaseManager
from src.services.desktop_entry import DesktopEntryService
from src.services.installer import InstallerService, InvalidAppImageError, AppImageNotFoundError
from src.services.updater import (
    UpdaterService,
    UpdateInfo,
    clean_version_str,
    compare_versions,
    extract_version_from_text,
)
from tests.conftest import create_mock_appimage, create_corrupt_appimage


# ===========================================================================
# 1. UpdateInfo Dataclass Tests
# ===========================================================================

def test_update_info_defaults_and_properties():
    """Verify UpdateInfo dataclass fields and compatibility properties."""
    info = UpdateInfo()
    assert info.available is False
    assert info.update_available is False
    assert info.current_version is None
    assert info.latest_version is None
    assert info.update_source == UpdateSource.NONE
    assert info.download_url is None
    assert info.download_size is None
    assert info.release_notes is None

    # Test property setter
    info.update_available = True
    assert info.available is True
    assert info.update_available is True


def test_update_info_custom_initialization():
    """Verify UpdateInfo with populated fields."""
    info = UpdateInfo(
        available=True,
        current_version="1.0.0",
        latest_version="2.1.0",
        update_source=UpdateSource.GITHUB,
        download_url="http://example.com/app.AppImage",
        release_notes="Fixed critical issues",
        download_size=4096,
        zsync_url="http://example.com/app.AppImage.zsync",
        etag='"etag123"',
        last_modified="Mon, 09 Nov 2026 12:00:00 GMT",
    )
    assert info.available is True
    assert info.latest_version == "2.1.0"
    assert info.update_source == UpdateSource.GITHUB
    assert info.download_size == 4096
    assert info.etag == '"etag123"'


# ===========================================================================
# 2. Version Comparison & Extraction Tests
# ===========================================================================

@pytest.mark.parametrize(
    "v1, v2, expected",
    [
        ("1.0.0", "2.0.0", 1),       # v2 is newer
        ("1.2.0", "1.10.0", 1),      # v2 is newer
        ("2.0.0", "1.0.0", -1),      # v1 is newer
        ("1.5.0", "1.5.0", 0),       # equal
        ("v1.0.0", "1.0.0", 0),      # prefix stripped
        ("release-2.0", "2.0", 0),   # release prefix stripped
        ("1.0", "1.0.1", 1),         # point release
        (None, "1.0.0", 1),          # candidate has version, current is None
        ("1.0.0", None, -1),         # current has version, candidate is None
        (None, None, 0),             # both None
    ],
)
def test_compare_versions(v1, v2, expected):
    """Verify version comparison handles semantic, prefixed, and edge cases."""
    assert compare_versions(v1, v2) == expected


@pytest.mark.parametrize(
    "input_text, expected_ver",
    [
        ("MyGreatApp-1.2.3-x86_64.AppImage", "1.2.3"),
        ("ZsyncApp-2.0.0.AppImage.zsync", "2.0.0"),
        ("https://github.com/org/repo/releases/download/v3.4.1/tool.AppImage", "3.4.1"),
        ("app_name_0.9.5.AppImage", "0.9.5"),
        ("no_version_here", None),
    ],
)
def test_extract_version_from_text(input_text, expected_ver):
    """Verify regex semantic version extraction from strings and URLs."""
    assert extract_version_from_text(input_text) == expected_ver


def test_clean_version_str():
    """Verify stripping of prefixes and whitespace."""
    assert clean_version_str("  v2.5.0  ") == "2.5.0"
    assert clean_version_str("release-1.0.4") == "1.0.4"
    assert clean_version_str("V3.0") == "3.0"
    assert clean_version_str(None) is None


# ===========================================================================
# 3. Embedded Update Info Inspection Tests
# ===========================================================================

def test_get_embedded_update_info_from_mock_appimage(temp_workspace):
    """Verify embedded update info is read via --appimage-updateinformation flag."""
    app_path = create_mock_appimage(
        temp_workspace["root"] / "TestUpdateInfo.AppImage",
        name="TestApp",
        update_info="zsync|http://localhost/app.zsync",
    )
    updater = UpdaterService()
    info_str = updater.get_embedded_update_info(app_path)
    assert info_str == "zsync|http://localhost/app.zsync"


def test_get_embedded_update_info_missing_or_corrupt(temp_workspace):
    """Verify graceful handling when update information is absent."""
    app_path = create_mock_appimage(
        temp_workspace["root"] / "NoInfo.AppImage",
        name="NoInfo",
        update_info="",
    )
    updater = UpdaterService()
    info_str = updater.get_embedded_update_info(app_path)
    assert info_str is None or info_str == ""


def test_get_embedded_update_info_nonexistent_file():
    """Verify non-existent file returns None without raising."""
    updater = UpdaterService()
    assert updater.get_embedded_update_info(Path("/nonexistent/app.AppImage")) is None


# ===========================================================================
# 4. Tier 1: zsync / AppImageUpdate Tests
# ===========================================================================

def test_tier1_zsync_update_detection_and_apply(temp_workspace, mock_http_server):
    """Verify Tier 1 update detection and application via direct HTTP fallback."""
    zsync_url = f"{mock_http_server}/zsync/ZsyncApp-2.0.0.AppImage.zsync"
    v1_app = create_mock_appimage(
        temp_workspace["root"] / "ZsyncApp-1.0.0.AppImage",
        name="ZsyncApp",
        version="1.0.0",
        update_info=f"zsync|{zsync_url}",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(v1_app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)

    assert info.available is True
    assert info.update_source == UpdateSource.APPIMAGEUPDATE
    assert info.latest_version == "2.0.0"

    # Track progress callback
    progress_records = []
    def on_progress(p: float):
        progress_records.append(p)

    updated_app = updater.apply_update(app, info, progress_callback=on_progress)
    assert updated_app.version == "2.0.0"
    assert len(progress_records) > 0
    assert progress_records[-1] == 1.0


def test_tier1_zsync_already_up_to_date(temp_workspace, mock_http_server):
    """Verify Tier 1 correctly reports available=False when already at latest version."""
    zsync_url = f"{mock_http_server}/zsync/ZsyncApp-2.0.0.AppImage.zsync"
    v2_app = create_mock_appimage(
        temp_workspace["root"] / "ZsyncApp-2.0.0.AppImage",
        name="ZsyncApp",
        version="2.0.0",
        update_info=f"zsync|{zsync_url}",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(v2_app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)
    assert info.available is False


def test_tier1_zsync_unreachable_server(temp_workspace):
    """Verify graceful fallback when zsync URL fails to connect."""
    bad_url = "http://127.0.0.1:59999/nonexistent.zsync"
    src_app = create_mock_appimage(
        temp_workspace["root"] / "BadZsync.AppImage",
        name="BadZsync",
        version="1.0.0",
        update_info=f"zsync|{bad_url}",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(src_app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)
    assert info.available is False


# ===========================================================================
# 5. Tier 2: GitHub Releases API Tests
# ===========================================================================

def test_tier2_github_releases_url_resolution():
    """Verify URL normalization from various user-provided formats."""
    updater = UpdaterService()
    assert (
        updater._resolve_github_api_url("owner/repo")
        == "https://api.github.com/repos/owner/repo/releases/latest"
    )
    assert (
        updater._resolve_github_api_url("https://github.com/owner/repo")
        == "https://api.github.com/repos/owner/repo/releases/latest"
    )
    assert (
        updater._resolve_github_api_url("http://127.0.0.1:8080/repos/owner/repo")
        == "http://127.0.0.1:8080/repos/owner/repo/releases/latest"
    )


def test_tier2_github_update_check_and_apply(temp_workspace, mock_http_server):
    """Verify GitHub Releases API checking, asset selection, and update application."""
    src_app = create_mock_appimage(
        temp_workspace["root"] / "GhApp-1.0.AppImage",
        name="GhApp",
        version="1.0.0",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(src_app)

    app.update_source = UpdateSource.GITHUB
    app.update_url = f"{mock_http_server}/repos/owner/GhApp"
    db.update_app(app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)

    assert info.available is True
    assert info.latest_version == "2.0.0"
    assert info.update_source == UpdateSource.GITHUB
    assert info.download_url.endswith(".AppImage")
    assert info.download_size == 2048
    assert "Bugfixes" in (info.release_notes or "")

    # Apply update
    updated_app = updater.apply_update(app, info)
    assert updated_app.version == "2.0.0"
    assert db.get_app(app.id).version == "2.0.0"


def test_tier2_github_already_up_to_date(temp_workspace, mock_http_server):
    """Verify GitHub Releases check returns available=False if installed >= latest."""
    src_app = create_mock_appimage(
        temp_workspace["root"] / "GhApp-2.0.AppImage",
        name="GhApp",
        version="2.0.0",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(src_app)

    app.update_source = UpdateSource.GITHUB
    app.update_url = f"{mock_http_server}/repos/owner/GhApp"
    db.update_app(app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)
    assert info.available is False


# ===========================================================================
# 6. Tier 3: Direct Manual URL Tests
# ===========================================================================

def test_tier3_manual_url_check_and_apply(temp_workspace, mock_http_server):
    """Verify HTTP HEAD check against direct URL and stream download."""
    src_app = create_mock_appimage(
        temp_workspace["root"] / "UrlApp-1.0.0.AppImage",
        name="UrlApp",
        version="1.0.0",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(src_app)

    app.update_source = UpdateSource.MANUAL_URL
    app.update_url = f"{mock_http_server}/downloads/UrlApp-2.0.0-x86_64.AppImage"
    db.update_app(app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)

    assert info.available is True
    assert info.update_source == UpdateSource.MANUAL_URL
    assert info.latest_version == "2.0.0"
    assert info.download_size == 2048
    assert info.etag == '"mock-etag-v2.0.0"'

    updated_app = updater.apply_update(app, info)
    assert updated_app.version == "2.0.0"


def test_tier3_manual_url_unreachable(temp_workspace):
    """Verify Tier 3 handles network failure cleanly."""
    src_app = create_mock_appimage(
        temp_workspace["root"] / "UrlFail.AppImage",
        name="UrlFail",
        version="1.0.0",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(src_app)

    app.update_source = UpdateSource.MANUAL_URL
    app.update_url = "http://127.0.0.1:59999/nonexistent.AppImage"
    db.update_app(app)

    updater = UpdaterService(db=db, installer=installer)
    info = updater.check_update(app)
    assert info.available is False


# ===========================================================================
# 7. Tier 4: Manual Local File In-Place Update Tests
# ===========================================================================

def test_tier4_in_place_update_replaces_binary_and_preserves_customizations(temp_workspace):
    """Verify in-place update atomic replacement and customization preservation."""
    v1_src = create_mock_appimage(
        temp_workspace["root"] / "v1" / "Editor-1.0.AppImage",
        name="Editor",
        version="1.0.0",
    )
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(v1_src)

    # User customize desktop entry
    desktop_svc = DesktopEntryService(db=db)
    desktop_svc.update_entry(
        app,
        comment="Custom User Comment For Editor",
        categories="Development;TextEditor;",
        terminal=True,
    )

    # Prepare v2 AppImage candidate
    v2_src = create_mock_appimage(
        temp_workspace["root"] / "v2" / "Editor-2.0.AppImage",
        name="Editor",
        version="2.0.0",
    )
    v2_sha = hashlib.sha256(v2_src.read_bytes()).hexdigest()
    orig_install_path = app.install_path

    # Apply in-place update
    updated = installer.update_in_place(app, v2_src)

    assert updated.install_path == orig_install_path
    assert updated.version == "2.0.0"
    assert updated.checksum_sha256 == v2_sha
    assert updated.size_bytes == v2_src.stat().st_size
    assert os.access(updated.install_path, os.X_OK)

    # Verify customizations were preserved in .desktop file
    desktop_text = Path(updated.desktop_entry).read_text(encoding="utf-8")
    assert "Comment=Custom User Comment For Editor" in desktop_text
    assert "Development;TextEditor;" in desktop_text
    assert "Terminal=true" in desktop_text
    assert "X-AppImage-Version=2.0.0" in desktop_text


def test_tier4_in_place_update_with_invalid_file(temp_workspace):
    """Verify invalid source file raises exception without corrupting existing installation."""
    v1_src = create_mock_appimage(temp_workspace["root"] / "Valid.AppImage", name="Valid")
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(v1_src)

    # Empty file
    empty_src = create_corrupt_appimage(temp_workspace["root"] / "empty.AppImage", "empty")
    with pytest.raises(InvalidAppImageError):
        installer.update_in_place(app, empty_src)

    # Non-existent file
    with pytest.raises(AppImageNotFoundError):
        installer.update_in_place(app, Path("/nonexistent/candidate.AppImage"))

    # Original installation remains intact
    assert Path(app.install_path).exists()
    assert db.get_app(app.id) is not None


# ===========================================================================
# 8. Batch Updates Tests
# ===========================================================================

def test_batch_check_and_apply_all_updates(temp_workspace, mock_http_server):
    """Verify check_all_updates and apply_all_updates across multiple apps."""
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    updater = UpdaterService(db=db, installer=installer)

    # App 1: GitHub with available update
    a1 = installer.install(
        create_mock_appimage(temp_workspace["root"] / "App1.AppImage", name="App1", version="1.0.0")
    )
    a1.update_source = UpdateSource.GITHUB
    a1.update_url = f"{mock_http_server}/repos/owner/App1"
    db.update_app(a1)

    # App 2: No update source configured
    a2 = installer.install(
        create_mock_appimage(temp_workspace["root"] / "App2.AppImage", name="App2", version="1.0.0")
    )

    apps = [a1, a2]
    check_results = updater.check_all_updates(apps)
    assert a1.id in check_results
    assert a2.id in check_results
    assert check_results[a1.id].available is True
    assert check_results[a2.id].available is False

    # Apply batch update
    updated_dict = updater.apply_all_updates(apps)
    assert a1.id in updated_dict
    assert a2.id not in updated_dict
    assert updated_dict[a1.id].version == "2.0.0"


# ===========================================================================
# 9. CLI Update Subcommand Tests
# ===========================================================================

def test_cli_update_in_place_local_file(temp_workspace, cli_runner):
    """Verify CLI `appimagekit update <name> --file <path>`."""
    v1 = create_mock_appimage(temp_workspace["root"] / "CliApp-1.0.AppImage", name="CliApp", version="1.0")
    v2 = create_mock_appimage(temp_workspace["root"] / "CliApp-2.0.AppImage", name="CliApp", version="2.0")

    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    installer.install(v1)

    res = cli_runner.invoke(cli, ["update", "CliApp", "--file", str(v2)])
    assert res.exit_code == 0
    assert "Successfully updated CliApp in-place" in res.output

    app = db.get_app_by_name("CliApp")
    assert app.version == "2.0"


def test_cli_update_check_only(temp_workspace, cli_runner, mock_http_server):
    """Verify CLI `appimagekit update <name> --check-only`."""
    src = create_mock_appimage(temp_workspace["root"] / "CheckApp-1.0.AppImage", name="CheckApp", version="1.0.0")
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    app = installer.install(src)
    app.update_source = UpdateSource.GITHUB
    app.update_url = f"{mock_http_server}/repos/owner/CheckApp"
    db.update_app(app)

    res = cli_runner.invoke(cli, ["update", "CheckApp", "--check-only"])
    assert res.exit_code == 0
    assert "Update available for CheckApp: 1.0.0 -> 2.0.0" in res.output
    # Ensure binary was NOT modified during check-only
    assert db.get_app_by_name("CheckApp").version == "1.0.0"


def test_cli_update_all_check_only(temp_workspace, cli_runner, mock_http_server):
    """Verify CLI `appimagekit update --all --check-only`."""
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db)
    a1 = installer.install(create_mock_appimage(temp_workspace["root"] / "B1.AppImage", name="B1", version="1.0.0"))
    a1.update_source = UpdateSource.GITHUB
    a1.update_url = f"{mock_http_server}/repos/owner/B1"
    db.update_app(a1)

    res = cli_runner.invoke(cli, ["update", "--all", "--check-only"])
    assert res.exit_code == 0
    assert "1 update(s) available" in res.output


def test_cli_update_errors(temp_workspace, cli_runner):
    """Verify CLI update error paths: missing args, nonexistent app, --all with --file."""
    # 1. No args
    res = cli_runner.invoke(cli, ["update"])
    assert res.exit_code != 0

    # 2. --all with --file
    tmp_file = temp_workspace["root"] / "tmp.AppImage"
    tmp_file.touch()
    res = cli_runner.invoke(cli, ["update", "--all", "--file", str(tmp_file)])
    assert res.exit_code != 0
    assert "--file cannot be combined with --all" in res.output

    # 3. Nonexistent app
    res = cli_runner.invoke(cli, ["update", "DoesNotExistApp"])
    assert res.exit_code != 0
    assert "not found in registry" in res.output
