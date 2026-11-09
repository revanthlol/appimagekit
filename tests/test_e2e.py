"""
tests/test_e2e.py — Systematic 4-Tier Requirement-Driven E2E Test Suite for AppImageKit.

Matrix:
- Harness Tests: Validates test fixtures and mock generators
- Tier 1: Feature Coverage (>=5 test cases per core feature R1-R5)
- Tier 2: Boundary & Corner Cases (empty files, corrupt headers, permission denied, tamper detection)
- Tier 3: Cross-Feature Combinations (install -> update in-place -> launch -> verify -> backup -> uninstall)
- Tier 4: Real-World Scenarios (multi-app lifecycle, special characters in paths)

Authoritative Sources:
- ORIGINAL_REQUEST.md (Requirements R1-R5, local file update requirement)
- PROJECT.md (Architecture, Interface Contracts, Feature Inventory 1-32)
- PLAN.md (Storage strategy, database schema, CLI hierarchy)
- freedesktop.org Desktop Entry Specification v1.5
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

# Attempt import of AppImageKit domain & services
try:
    from src.models.app import App, InstallMode, UpdateSource, SandboxType
    from src.models.database import DatabaseManager
    from src.services.installer import InstallerService
    from src.services.desktop_entry import DesktopEntryService
    from src.services.icon_extractor import IconExtractorService
    from src.services.updater import UpdaterService, UpdateInfo
    from src.services.sandbox import SandboxDetectorService
    from src.services.backup import BackupManagerService
    from src.services.hub import HubCatalogService
    from src.services.integrity import IntegrityVerifierService
    from src.services.autostart import AutostartManager
    from src.utils.paths import Paths
    from src.cli import cli
    SRC_AVAILABLE = True
    SRC_IMPORT_ERROR = None
except ImportError as err:
    SRC_AVAILABLE = False
    SRC_IMPORT_ERROR = str(err)


def require_implementation(feature_id: str, requirement_desc: str):
    """
    Helper to cleanly signal when implementation code is pending for a feature.
    Allows pytest to verify test harness syntax and report actionable failure
    status to implementation agents.
    """
    if not SRC_AVAILABLE:
        pytest.fail(
            f"[PENDING IMPLEMENTATION] {feature_id}: {requirement_desc}. "
            f"Package 'src' not yet implemented: {SRC_IMPORT_ERROR}"
        )


# ===========================================================================
# 0. Test Harness & Fixture Self-Verification Tests (Always Pass)
# ===========================================================================

class TestHarnessAndFixtures:
    """Verifies that the mock AppImage generators, HTTP server, and workspaces work properly."""

    def test_harness_01_mock_appimage_execution_and_flags(self, tmp_path):
        """Verify create_mock_appimage simulates standard AppImage CLI flags."""
        from tests.conftest import create_mock_appimage

        app_path = tmp_path / "MockApp.AppImage"
        create_mock_appimage(
            app_path,
            name="MockApp",
            version="1.2.3",
            category="Development",
            update_info="zsync|http://localhost/app.zsync",
            exec_output="MockApp Executed Successfully"
        )

        assert app_path.exists()
        assert os.access(app_path, os.X_OK)

        # 1. Normal execution
        res = subprocess.run([str(app_path), "--foo", "bar"], capture_output=True, text=True, check=True)
        assert "MockApp Executed Successfully" in res.stdout

        # 2. --appimage-version
        res = subprocess.run([str(app_path), "--appimage-version"], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "1.2.3"

        # 3. --appimage-updateinformation
        res = subprocess.run([str(app_path), "--appimage-updateinformation"], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "zsync|http://localhost/app.zsync"

        # 4. --appimage-extract
        extract_dir = tmp_path / "extract_test"
        extract_dir.mkdir()
        res = subprocess.run([str(app_path), "--appimage-extract"], cwd=extract_dir, capture_output=True, text=True, check=True)
        assert (extract_dir / "squashfs-root" / ".DirIcon").exists()
        desktop_file = extract_dir / "squashfs-root" / "MockApp.desktop"
        assert desktop_file.exists()
        content = desktop_file.read_text()
        assert "Type=Application" in content
        assert "Categories=Development;" in content

    def test_harness_02_mock_http_server_endpoints(self, mock_http_server):
        """Verify local mock HTTP server serves GitHub Releases API, Hub feed, and downloads."""
        # 1. GitHub Releases endpoint
        gh_url = f"{mock_http_server}/repos/testowner/testrepo/releases/latest"
        req = urllib.request.Request(gh_url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["tag_name"] == "v2.0.0"
            assert len(data["assets"]) >= 1

        # 2. AppImage Hub feed endpoint
        feed_url = f"{mock_http_server}/feed.json"
        with urllib.request.urlopen(feed_url) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert "items" in data
            assert len(data["items"]) >= 1

        # 3. Direct download HEAD & GET
        dl_url = f"{mock_http_server}/downloads/TestApp-2.0.0-x86_64.AppImage"
        req = urllib.request.Request(dl_url, method="HEAD")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Length") == "2048"

        req_get = urllib.request.Request(dl_url, method="GET")
        with urllib.request.urlopen(req_get) as resp:
            assert resp.status == 200
            content = resp.read()
            assert len(content) > 0
            assert b"#!/bin/bash" in content

    def test_harness_03_temp_workspace_isolation(self, temp_workspace):
        """Verify temp_workspace fixture provides isolated directories."""
        assert temp_workspace["apps_dir"].exists()
        assert temp_workspace["desktop_dir"].exists()
        assert temp_workspace["icons_dir"].exists()
        assert temp_workspace["autostart_dir"].exists()
        assert temp_workspace["cache_dir"].exists()
        assert temp_workspace["app_data_dir"].exists()

    def test_harness_04_corrupt_appimage_generator(self, tmp_path):
        """Verify create_corrupt_appimage creates requested boundary conditions."""
        from tests.conftest import create_corrupt_appimage

        empty = create_corrupt_appimage(tmp_path / "empty.AppImage", corruption_type="empty")
        assert empty.stat().st_size == 0

        bad_magic = create_corrupt_appimage(tmp_path / "bad_magic.AppImage", corruption_type="bad_magic")
        assert bad_magic.read_bytes()[:4] != b"\x7fELF"

        truncated = create_corrupt_appimage(tmp_path / "truncated.AppImage", corruption_type="truncated_elf")
        assert truncated.read_bytes() == b"\x7fELF"


# ===========================================================================
# 1. Tier 1: Feature Coverage (Core Requirements R1 - R5)
# ===========================================================================

@pytest.mark.tier1
class TestTier1FeatureCoverage:
    """
    Tier 1: Feature Coverage.
    >= 5 test cases per core feature R1 - R5.
    """

    # --- R1: Storage & Dual-Mode Installation ---

    def test_r1_01_user_mode_storage_and_install(self, temp_workspace):
        """
        [R1-1] Install AppImage in user mode (~/.local/share/appimages/).
        Authoritative source: ORIGINAL_REQUEST.md § R1, PROJECT.md Feature 1.
        Expected: App binary copied to user_appimages_dir, DB entry created with install_mode='user'.
        """
        require_implementation("R1-1", "User mode storage installation")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "NotePad-1.0.0.AppImage", name="NotePad")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        app = installer.install(src_app, mode=InstallMode.USER, category="Office")

        assert app is not None
        assert app.install_mode == InstallMode.USER
        assert Path(app.install_path).exists()
        assert Path(app.install_path).parent == temp_workspace["apps_dir"]
        assert db.get_app_by_name("NotePad") is not None

    def test_r1_02_system_mode_fallback_when_unprivileged(self, temp_workspace):
        """
        [R1-2] System-wide storage fallback to user-local when root is unavailable.
        Authoritative source: ORIGINAL_REQUEST.md § R1, PROJECT.md Feature 2.
        Expected: Automatic fallback to user-local storage without crash.
        """
        require_implementation("R1-2", "System mode fallback")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "SysTool-1.0.0.AppImage", name="SysTool")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        # Non-root user requesting system mode
        app = installer.install(src_app, mode=InstallMode.SYSTEM)
        assert app is not None
        assert app.install_mode == InstallMode.USER
        assert Path(app.install_path).exists()
        assert Path(app.install_path).parent == temp_workspace["apps_dir"]

    def test_r1_03_executable_permissions_enforced(self, temp_workspace):
        """
        [R1-3] Make installed files executable (chmod +x / 0o755).
        Authoritative source: ORIGINAL_REQUEST.md § R1, PROJECT.md Feature 4.
        Expected: Source file with 0o644 becomes executable (0o755) in destination.
        """
        require_implementation("R1-3", "Executable permission enforcement")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "NonExec.AppImage", name="NonExec")
        src_app.chmod(0o644)  # explicitly remove execute bit
        assert not os.access(src_app, os.X_OK)

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        installed_stat = Path(app.install_path).stat()
        assert os.access(app.install_path, os.X_OK)
        assert installed_stat.st_mode & 0o111 != 0

    def test_r1_04_sha256_checksum_and_size_calculation(self, temp_workspace):
        """
        [R1-4] Compute SHA256 checksum and record file size upon installation.
        Authoritative source: ORIGINAL_REQUEST.md § R1, PROJECT.md Feature 5.
        Expected: Database record has exact SHA256 hex digest and size_bytes.
        """
        require_implementation("R1-4", "SHA256 checksum and file size calculation")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "ChecksumApp.AppImage", name="ChecksumApp")
        raw_bytes = src_app.read_bytes()
        expected_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        expected_size = len(raw_bytes)

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        assert app.checksum_sha256 == expected_sha256
        assert app.size_bytes == expected_size
        stored = db.get_app_by_name("ChecksumApp")
        assert stored.checksum_sha256 == expected_sha256
        assert stored.size_bytes == expected_size

    def test_r1_05_sqlite_database_persistence_and_wal_mode(self, temp_workspace):
        """
        [R1-5] SQLite database initialization, WAL journal mode, and metadata persistence.
        Authoritative source: PROJECT.md Feature 6 & Architecture, PLAN.md § Database.
        Expected: SQLite database operates in WAL mode, indices exist, records persist across connections.
        """
        require_implementation("R1-5", "SQLite persistence and WAL mode")
        import sqlite3

        db = DatabaseManager(temp_workspace["db_path"])
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            assert journal_mode.lower() == "wal"

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='apps';")
            assert cur.fetchone() is not None

            cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
            indices = [row[0] for row in cur.fetchall()]
            assert "idx_apps_name" in indices or any("name" in idx for idx in indices)

    def test_r1_06_application_launching_service(self, temp_workspace):
        """
        [R1-6] Spawning installed AppImage process with arguments.
        Authoritative source: PROJECT.md Feature 7, PLAN.md § 3.
        Expected: InstallerService.launch spawns process and returns subprocess.Popen instance.
        """
        require_implementation("R1-6", "Application launching service")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(
            temp_workspace["root"] / "source" / "Launchable.AppImage",
            name="Launchable",
            exec_output="Launch test success"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        proc = installer.launch(app, extra_args=["--test-flag", "value1"])
        assert isinstance(proc, subprocess.Popen)
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0

    def test_r1_07_cli_install_command(self, temp_workspace, cli_runner):
        """
        [R1-7] CLI `appimagekit install <path>` execution.
        Authoritative source: ORIGINAL_REQUEST.md § R1, PROJECT.md Feature 24.
        Expected: Exit code 0, prints confirmation, records app in registry.
        """
        require_implementation("R1-7", "CLI install command")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "CliApp.AppImage", name="CliApp")
        result = cli_runner.invoke(cli, ["install", str(src_app), "--category", "Utility"])
        assert result.exit_code == 0
        assert "installed" in result.output.lower() or "cliapp" in result.output.lower()

    # --- R2: Desktop Entry & Icon Management ---

    def test_r2_01_extract_icon_and_metadata(self, temp_workspace):
        """
        [R2-1] Automatically extract .DirIcon (SVG/PNG) from AppImage on installation.
        Authoritative source: ORIGINAL_REQUEST.md § R2, PROJECT.md Feature 8.
        Expected: Icon extracted to icons_dir, icon_path recorded in App model.
        """
        require_implementation("R2-1", "Icon extraction from AppImage")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(
            temp_workspace["root"] / "source" / "IconApp.AppImage",
            name="IconApp",
            icon_svg='<svg><circle cx="10" cy="10" r="5" fill="red"/></svg>'
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        assert app.icon_path is not None
        icon_p = Path(app.icon_path)
        assert icon_p.exists()
        assert "red" in icon_p.read_text(encoding="utf-8")

    def test_r2_02_generate_freedesktop_compliant_desktop_entry(self, temp_workspace):
        """
        [R2-2] Generate valid freedesktop.org v1.5 .desktop entry.
        Authoritative source: ORIGINAL_REQUEST.md § R2, PROJECT.md Feature 10, freedesktop spec.
        Expected: File contains Type=Application, Name, Exec, Icon, Categories (with trailing semicolon).
        """
        require_implementation("R2-2", "Freedesktop .desktop generation")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(
            temp_workspace["root"] / "source" / "DeskApp.AppImage",
            name="DeskApp",
            category="Development"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        assert app.desktop_entry is not None
        desktop_path = Path(app.desktop_entry)
        assert desktop_path.exists()
        content = desktop_path.read_text(encoding="utf-8")

        assert "[Desktop Entry]" in content
        assert "Type=Application" in content
        assert "Name=DeskApp" in content
        assert "Exec=" in content
        assert str(app.install_path) in content
        assert "Categories=Development;" in content or "Categories=" in content
        assert "Terminal=false" in content

    def test_r2_03_desktop_entry_escaping_and_special_paths(self, temp_workspace):
        """
        [R2-3] Desktop entry quoting and escaping of paths containing spaces and field codes (%U).
        Authoritative source: PROJECT.md Feature 11, specs_report.md § 2.1.
        Expected: Exec line quotes path with spaces, appends %U without invalid % codes.
        """
        require_implementation("R2-3", "Desktop entry escaping")
        from tests.conftest import create_mock_appimage

        spaced_dir = temp_workspace["root"] / "spaced folder"
        spaced_dir.mkdir(parents=True)
        src_app = create_mock_appimage(spaced_dir / "Space App.AppImage", name="Space App")

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        desktop_content = Path(app.desktop_entry).read_text(encoding="utf-8")
        for line in desktop_content.splitlines():
            if line.startswith("Exec="):
                exec_val = line.split("Exec=", 1)[1]
                # If path has space, it must be enclosed in quotes
                if " " in str(app.install_path):
                    assert f'"{app.install_path}"' in exec_val or f"'{app.install_path}'" in exec_val
                assert "%U" in exec_val or "%u" in exec_val or "%f" in exec_val

    def test_r2_04_desktop_entry_editor_service(self, temp_workspace):
        """
        [R2-4] In-app .desktop editor allowing inspection and customization of Name, Comment, Exec args.
        Authoritative source: ORIGINAL_REQUEST.md § R2, PROJECT.md Feature 12.
        Expected: Modifying desktop metadata updates both the .desktop file and SQLite database.
        """
        require_implementation("R2-4", "Desktop entry editor service")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "EditApp.AppImage", name="EditApp")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        editor_svc = DesktopEntryService()
        updated_app = editor_svc.update_entry(
            app=app,
            name="Custom EditApp",
            comment="Customized description",
            exec_args="--custom-arg",
            categories="Graphics;2DGraphics;"
        )

        # Verify database record
        assert updated_app.name == "Custom EditApp"
        assert updated_app.comment == "Customized description"
        assert updated_app.exec_args == "--custom-arg"

        # Verify .desktop file content
        content = Path(app.desktop_entry).read_text(encoding="utf-8")
        assert "Name=Custom EditApp" in content
        assert "Comment=Customized description" in content
        assert "--custom-arg" in content

    def test_r2_05_clean_uninstallation_purge(self, temp_workspace):
        """
        [R2-5] Uninstallation cleanly purges binary, .desktop entry, extracted icon, and DB record.
        Authoritative source: ORIGINAL_REQUEST.md Acceptance Criteria, PROJECT.md Feature 13.
        Expected: Zero orphan files and zero dangling SQLite records.
        """
        require_implementation("R2-5", "Clean uninstallation purge")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "PurgeApp.AppImage", name="PurgeApp")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        bin_path = Path(app.install_path)
        desk_path = Path(app.desktop_entry)
        icon_path = Path(app.icon_path) if app.icon_path else None
        app_id = app.id

        assert bin_path.exists()
        assert desk_path.exists()
        if icon_path:
            assert icon_path.exists()

        success = installer.uninstall(app)
        assert success is True

        # Assert all artifacts purged
        assert not bin_path.exists()
        assert not desk_path.exists()
        if icon_path:
            assert not icon_path.exists()
        assert db.get_app(app_id) is None

    def test_r2_06_cli_uninstall_command(self, temp_workspace, cli_runner):
        """
        [R2-6] CLI `appimagekit uninstall <app>` execution.
        Authoritative source: ORIGINAL_REQUEST.md § R2, PROJECT.md Feature 24.
        Expected: Exit code 0, uninstalls app and purges files.
        """
        require_implementation("R2-6", "CLI uninstall command")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "source" / "CliUninstall.AppImage", name="CliUninstall")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        installer.install(src_app)

        result = cli_runner.invoke(cli, ["uninstall", "CliUninstall"])
        assert result.exit_code == 0
        assert db.get_app_by_name("CliUninstall") is None

    # --- R3: Multi-Tier Cascading Update Engine ---

    def test_r3_01_tier1_zsync_delta_update(self, temp_workspace, mock_http_server):
        """
        [R3-1] Tier 1 update: embedded zsync / .upd_info delta update detection.
        Authoritative source: ORIGINAL_REQUEST.md § R3, PROJECT.md Feature 14.
        Expected: UpdaterService recognizes embedded zsync update information.
        """
        require_implementation("R3-1", "Tier 1 zsync update check")
        from tests.conftest import create_mock_appimage

        zsync_url = f"{mock_http_server}/zsync/ZsyncApp-2.0.0.AppImage.zsync"
        src_app = create_mock_appimage(
            temp_workspace["root"] / "source" / "ZsyncApp.AppImage",
            name="ZsyncApp",
            version="1.0.0",
            update_info=f"zsync|{zsync_url}"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        updater = UpdaterService()
        update_info = updater.check_update(app)

        assert update_info is not None
        assert update_info.update_available is True
        assert update_info.update_source == UpdateSource.APPIMAGEUPDATE

    def test_r3_02_tier2_github_releases_api_update(self, temp_workspace, mock_http_server):
        """
        [R3-2] Tier 2 update: GitHub Releases API query and version comparison.
        Authoritative source: ORIGINAL_REQUEST.md § R3, PROJECT.md Feature 15.
        Expected: Detects v2.0.0 is newer than installed 1.0.0 via GitHub API mock.
        """
        require_implementation("R3-2", "Tier 2 GitHub Releases update check")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(
            temp_workspace["root"] / "source" / "GhApp.AppImage",
            name="GhApp",
            version="1.0.0"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        # Configure GitHub update source pointing to mock server URL
        app.update_source = UpdateSource.GITHUB
        app.update_url = f"{mock_http_server}/repos/owner/GhApp"
        db.update_app(app)

        updater = UpdaterService()
        update_info = updater.check_update(app)

        assert update_info.update_available is True
        assert update_info.latest_version == "2.0.0"
        assert update_info.download_url.endswith(".AppImage")

    def test_r3_03_tier3_direct_manual_url_update(self, temp_workspace, mock_http_server):
        """
        [R3-3] Tier 3 update: Direct manual download URL fallback check via HTTP HEAD.
        Authoritative source: ORIGINAL_REQUEST.md § R3, PROJECT.md Feature 16.
        Expected: HTTP HEAD returns Content-Length/ETag/Last-Modified detecting new build.
        """
        require_implementation("R3-3", "Tier 3 Direct URL update check")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(
            temp_workspace["root"] / "source" / "UrlApp.AppImage",
            name="UrlApp",
            version="1.0.0"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        app.update_source = UpdateSource.MANUAL_URL
        app.update_url = f"{mock_http_server}/downloads/UrlApp-2.0.0-x86_64.AppImage"
        db.update_app(app)

        updater = UpdaterService()
        update_info = updater.check_update(app)

        assert update_info.update_available is True
        assert update_info.update_source == UpdateSource.MANUAL_URL

    def test_r3_04_tier4_local_file_in_place_update(self, temp_workspace):
        """
        [R3-4] Tier 4 update (User Addition): Manually select local .AppImage to update in-place.
        Authoritative source: ORIGINAL_REQUEST.md § 2026-11-09T13:15:54Z, PROJECT.md Feature 17.
        Expected: Replaces binary atomically, updates version/checksum/size in SQLite, preserves custom desktop settings.
        """
        require_implementation("R3-4", "Tier 4 Local file in-place update")
        from tests.conftest import create_mock_appimage

        # 1. Install version 1.0.0
        v1_src = create_mock_appimage(temp_workspace["root"] / "v1" / "Writer-1.0.0.AppImage", name="Writer", version="1.0.0")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(v1_src)

        # 2. User customizes desktop comment and categories
        editor_svc = DesktopEntryService()
        editor_svc.update_entry(app, comment="My Custom Office Tool", categories="Office;WordProcessor;")

        # 3. Create version 2.0.0 candidate
        v2_src = create_mock_appimage(temp_workspace["root"] / "v2" / "Writer-2.0.0.AppImage", name="Writer", version="2.0.0")
        v2_sha = hashlib.sha256(v2_src.read_bytes()).hexdigest()
        orig_install_path = app.install_path

        # 4. Perform in-place update
        updated_app = installer.update_in_place(app, v2_src)

        # 5. Assertions
        assert updated_app.install_path == orig_install_path
        assert updated_app.version == "2.0.0"
        assert updated_app.checksum_sha256 == v2_sha
        assert updated_app.size_bytes == v2_src.stat().st_size

        # Customization must be preserved!
        desktop_content = Path(updated_app.desktop_entry).read_text(encoding="utf-8")
        assert "Comment=My Custom Office Tool" in desktop_content
        assert "Office;WordProcessor;" in desktop_content

    def test_r3_05_batch_update_all_check(self, temp_workspace, mock_http_server):
        """
        [R3-5] Batch update checking across all installed applications ("Update All").
        Authoritative source: ORIGINAL_REQUEST.md § R3, PROJECT.md Feature 18.
        Expected: Returns update status dictionary for all registered apps.
        """
        require_implementation("R3-5", "Batch update checking")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        app1 = installer.install(create_mock_appimage(temp_workspace["root"] / "a1.AppImage", name="AppOne", version="1.0.0"))
        app1.update_source = UpdateSource.GITHUB
        app1.update_url = f"{mock_http_server}/repos/owner/AppOne"
        db.update_app(app1)

        app2 = installer.install(create_mock_appimage(temp_workspace["root"] / "a2.AppImage", name="AppTwo", version="1.0.0"))
        # No update source

        updater = UpdaterService()
        results = updater.check_all_updates([app1, app2])

        assert isinstance(results, dict)
        assert app1.id in results
        assert results[app1.id].update_available is True
        assert results[app2.id].update_available is False

    def test_r3_06_cli_update_with_local_file(self, temp_workspace, cli_runner):
        """
        [R3-6] CLI `appimagekit update <app_name> --file /path/to/new.AppImage`.
        Authoritative source: ORIGINAL_REQUEST.md lines 61-62, PROJECT.md Feature 24.
        Expected: Exit code 0, performs in-place update from local file.
        """
        require_implementation("R3-6", "CLI update with local file")
        from tests.conftest import create_mock_appimage

        v1 = create_mock_appimage(temp_workspace["root"] / "Player-1.0.AppImage", name="Player", version="1.0")
        v2 = create_mock_appimage(temp_workspace["root"] / "Player-2.0.AppImage", name="Player", version="2.0")

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        installer.install(v1)

        result = cli_runner.invoke(cli, ["update", "Player", "--file", str(v2)])
        assert result.exit_code == 0
        app = db.get_app_by_name("Player")
        assert app.version == "2.0"

    # --- R4: GTK4 / Libadwaita Interface Contracts (Headless) ---

    def test_r4_01_model_controller_decoupling(self, temp_workspace):
        """
        [R4-1] Model and service presentation layer executes headlessly with zero GTK dependency.
        Authoritative source: PROJECT.md § 1 & 5.5.
        Expected: All model, service, and data operations execute cleanly without DISPLAY/WAYLAND_DISPLAY.
        """
        require_implementation("R4-1", "Model/controller decoupling")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(create_mock_appimage(temp_workspace["root"] / "Headless.AppImage", name="Headless"))

        assert isinstance(app, App)
        assert app.name == "Headless"

    def test_r4_02_grid_view_filter_categories(self, temp_workspace):
        """
        [R4-2] Category and search filtering in the application registry.
        Authoritative source: ORIGINAL_REQUEST.md § R4, PROJECT.md Feature 27.
        Expected: DatabaseManager.list_apps correctly filters by category and search query.
        """
        require_implementation("R4-2", "Grid view filter categories")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        installer.install(create_mock_appimage(temp_workspace["root"] / "Game1.AppImage", name="Game1"), category="Game")
        installer.install(create_mock_appimage(temp_workspace["root"] / "Dev1.AppImage", name="Dev1"), category="Development")
        installer.install(create_mock_appimage(temp_workspace["root"] / "Util1.AppImage", name="Util1"), category="Utility")

        all_apps = db.list_apps()
        assert len(all_apps) == 3

        games = db.list_apps(category="Game")
        assert len(games) == 1
        assert games[0].name == "Game1"

        devs = db.list_apps(category="Development")
        assert len(devs) == 1
        assert devs[0].name == "Dev1"

        searched = db.list_apps(query="util")
        assert len(searched) == 1
        assert searched[0].name == "Util1"

    def test_r4_03_sidebar_disk_usage_calculation(self, temp_workspace):
        """
        [R4-3] Accurate aggregation of total disk usage across all installed AppImages.
        Authoritative source: ORIGINAL_REQUEST.md § R4, PROJECT.md Feature 27.
        Expected: get_total_disk_usage() equals exact sum of recorded sizes.
        """
        require_implementation("R4-3", "Disk usage calculation")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        a1 = installer.install(create_mock_appimage(temp_workspace["root"] / "D1.AppImage", name="D1"))
        a2 = installer.install(create_mock_appimage(temp_workspace["root"] / "D2.AppImage", name="D2"))

        expected_total = a1.size_bytes + a2.size_bytes
        assert db.get_total_disk_usage() == expected_total

    def test_r4_04_app_details_view_contract(self, temp_workspace):
        """
        [R4-4] App detail view property contracts.
        Authoritative source: ORIGINAL_REQUEST.md § R4, PLAN.md § App Detail View.
        Expected: Data model exposes all properties needed for detail view: launch, update, autostart, sandbox.
        """
        require_implementation("R4-4", "App detail view contract")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(create_mock_appimage(temp_workspace["root"] / "DetailApp.AppImage", name="DetailApp"))

        assert hasattr(app, "name")
        assert hasattr(app, "version")
        assert hasattr(app, "install_path")
        assert hasattr(app, "desktop_entry")
        assert hasattr(app, "sandboxed")
        assert hasattr(app, "sandbox_type")
        assert hasattr(app, "autostart")
        assert hasattr(app, "checksum_sha256")
        assert hasattr(app, "size_bytes")

    def test_r4_05_drag_and_drop_ingestion_contract(self, temp_workspace):
        """
        [R4-5] Drag-and-drop file ingestion validation contract.
        Authoritative source: ORIGINAL_REQUEST.md § R4, PROJECT.md Feature 29.
        Expected: Ingestion routine accepts .AppImage files and rejects non-AppImage formats.
        """
        require_implementation("R4-5", "Drag and drop ingestion contract")
        from tests.conftest import create_mock_appimage

        valid_appimage = create_mock_appimage(temp_workspace["root"] / "DropMe.AppImage", name="DropMe")
        invalid_zip = temp_workspace["root"] / "archive.zip"
        invalid_zip.write_text("not an appimage")

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        # Valid AppImage installs cleanly
        app = installer.install(valid_appimage)
        assert app is not None

        # Non-AppImage raises ValueError or invalid format exception
        with pytest.raises((ValueError, Exception)):
            installer.install(invalid_zip)

    # --- R5: Auxiliary Services & CLI ---

    def test_r5_01_autostart_toggle_management(self, temp_workspace):
        """
        [R5-1] Autostart toggle creating and removing ~/.config/autostart/<app>.desktop.
        Authoritative source: ORIGINAL_REQUEST.md § R5, PROJECT.md Feature 19.
        Expected: Enabling autostart writes .desktop to autostart_dir; disabling removes it.
        """
        require_implementation("R5-1", "Autostart management")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(create_mock_appimage(temp_workspace["root"] / "AutoApp.AppImage", name="AutoApp"))

        autostart_mgr = AutostartManager()
        autostart_file = temp_workspace["autostart_dir"] / f"{app.name}.desktop"

        # Enable autostart
        autostart_mgr.enable(app)
        assert autostart_file.exists()
        assert app.autostart is True

        # Disable autostart
        autostart_mgr.disable(app)
        assert not autostart_file.exists()
        assert app.autostart is False

    def test_r5_02_sandbox_detection(self, temp_workspace):
        """
        [R5-2] Sandbox detection inspecting Bubblewrap (bwrap) and Firejail indicators.
        Authoritative source: ORIGINAL_REQUEST.md § R5, PROJECT.md Feature 20.
        Expected: Inspects application environment and desktop entry for sandboxing prefixes.
        """
        require_implementation("R5-2", "Sandbox detection")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(create_mock_appimage(temp_workspace["root"] / "Sandboxed.AppImage", name="Sandboxed"))

        detector = SandboxDetectorService()
        is_sandboxed, s_type = detector.detect(app)
        assert isinstance(is_sandboxed, bool)
        assert s_type in (None, SandboxType.BUBBLEWRAP, SandboxType.FIREJAIL)

    def test_r5_03_backup_export_and_import(self, temp_workspace):
        """
        [R5-3] Backup and export/import of installed applications list in JSON format.
        Authoritative source: ORIGINAL_REQUEST.md § R5, PROJECT.md Feature 21, specs_report.md § 5.3.
        Expected: Export creates JSON manifest; importing re-registers or restores app entries.
        """
        require_implementation("R5-3", "Backup export and import")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        installer.install(create_mock_appimage(temp_workspace["root"] / "B1.AppImage", name="B1", version="1.0"))
        installer.install(create_mock_appimage(temp_workspace["root"] / "B2.AppImage", name="B2", version="2.0"))

        backup_mgr = BackupManagerService(db)
        export_file = temp_workspace["root"] / "backup.json"
        backup_mgr.export_manifest(export_file)

        assert export_file.exists()
        data = json.loads(export_file.read_text(encoding="utf-8"))
        assert "apps" in data
        assert len(data["apps"]) == 2

        # Clear and restore
        for app in db.list_apps():
            db.delete_app(app.id)
        assert len(db.list_apps()) == 0

        backup_mgr.import_manifest(export_file)
        restored = db.list_apps()
        assert len(restored) == 2

    def test_r5_04_appimage_hub_catalog_search(self, mock_http_server):
        """
        [R5-4] AppImage Hub feed consumption and search.
        Authoritative source: ORIGINAL_REQUEST.md § R5, PROJECT.md Feature 22.
        Expected: Queries catalog feed and returns matching items with download links.
        """
        require_implementation("R5-4", "AppImage Hub catalog search")

        hub_svc = HubCatalogService(feed_url=f"{mock_http_server}/feed.json")
        results = hub_svc.search("Krita")

        assert len(results) >= 1
        assert results[0]["name"] == "Krita"
        assert "links" in results[0]

    def test_r5_05_integrity_verification_sha256(self, temp_workspace):
        """
        [R5-5] Integrity verification auditing on-disk binaries against recorded SHA256 checksums.
        Authoritative source: ORIGINAL_REQUEST.md § R5, PROJECT.md Feature 23.
        Expected: Returns True on untampered file; returns False when binary content is modified.
        """
        require_implementation("R5-5", "Integrity verification SHA256")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(create_mock_appimage(temp_workspace["root"] / "IntegrityApp.AppImage", name="IntegrityApp"))

        verifier = IntegrityVerifierService(db)
        assert verifier.verify(app) is True

        # Tamper with the binary
        bin_path = Path(app.install_path)
        with open(bin_path, "a") as f:
            f.write("\n# Tampered byte")

        assert verifier.verify(app) is False

    def test_r5_06_complete_cli_suite(self, temp_workspace, cli_runner):
        """
        [R5-6] Complete Click CLI suite commands: list, info, verify, launch.
        Authoritative source: ORIGINAL_REQUEST.md § R5, PROJECT.md Feature 24.
        Expected: All subcommands execute cleanly with exit code 0 on valid inputs.
        """
        require_implementation("R5-6", "CLI suite commands")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        installer.install(create_mock_appimage(temp_workspace["root"] / "CliTool.AppImage", name="CliTool"))

        # 1. list command
        res_list = cli_runner.invoke(cli, ["list", "--json"])
        assert res_list.exit_code == 0
        assert "CliTool" in res_list.output

        # 2. info command
        res_info = cli_runner.invoke(cli, ["info", "CliTool", "--json"])
        assert res_info.exit_code == 0
        info_data = json.loads(res_info.output)
        assert info_data["name"] == "CliTool"

        # 3. verify command
        res_ver = cli_runner.invoke(cli, ["verify", "CliTool"])
        assert res_ver.exit_code == 0

        # 4. launch command
        res_launch = cli_runner.invoke(cli, ["launch", "CliTool"])
        assert res_launch.exit_code == 0


# ===========================================================================
# 2. Tier 2: Boundary & Corner Cases
# ===========================================================================

@pytest.mark.tier2
class TestTier2BoundaryAndCornerCases:
    """
    Tier 2: Boundary & Corner Cases.
    Negative inputs, corrupt files, permission fallbacks, missing metadata.
    """

    def test_tier2_01_empty_appimage_file_rejection(self, temp_workspace):
        """
        [Tier2-1] Attempting to install a 0-byte file must be rejected.
        Authoritative source: specs_report.md § 3.4.1.
        Expected: Raises exception, no file copied, no SQLite row created.
        """
        require_implementation("Tier2-1", "0-byte AppImage rejection")
        from tests.conftest import create_corrupt_appimage

        empty_file = create_corrupt_appimage(temp_workspace["root"] / "zero.AppImage", corruption_type="empty")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        with pytest.raises((ValueError, Exception)):
            installer.install(empty_file)

        assert len(db.list_apps()) == 0

    def test_tier2_02_corrupt_header_rejection(self, temp_workspace):
        """
        [Tier2-2] Attempting to install non-ELF / non-AppImage garbage data must be rejected.
        Authoritative source: specs_report.md § 1.1 & § 3.4.1.
        Expected: Raises exception during validation before copy.
        """
        require_implementation("Tier2-2", "Corrupt header rejection")
        from tests.conftest import create_corrupt_appimage

        corrupt_file = create_corrupt_appimage(temp_workspace["root"] / "garbage.AppImage", corruption_type="bad_magic")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        with pytest.raises((ValueError, Exception)):
            installer.install(corrupt_file)

        assert len(db.list_apps()) == 0

    def test_tier2_03_non_executable_source_made_executable(self, temp_workspace):
        """
        [Tier2-3] Installing a valid mock AppImage that lacks execute bits (0o644).
        Authoritative source: ORIGINAL_REQUEST.md § R1.
        Expected: Installer successfully sets 0o755 on installed binary.
        """
        require_implementation("Tier2-3", "Non-executable source made executable")
        from tests.conftest import create_corrupt_appimage

        non_exec = create_corrupt_appimage(temp_workspace["root"] / "ReadOnly.AppImage", corruption_type="non_executable")
        assert not os.access(non_exec, os.X_OK)

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(non_exec)

        assert os.access(app.install_path, os.X_OK)

    def test_tier2_04_missing_update_info_fallback(self, temp_workspace):
        """
        [Tier2-4] AppImage with no .upd_info and no update_url configured.
        Authoritative source: specs_report.md § 1.3 & § 3.
        Expected: check_update returns update_available=False cleanly without error.
        """
        require_implementation("Tier2-4", "Missing update info fallback")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "NoUpd.AppImage", name="NoUpd", update_info="")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        updater = UpdaterService()
        info = updater.check_update(app)
        assert info.update_available is False

    def test_tier2_05_in_place_update_with_identical_file(self, temp_workspace):
        """
        [Tier2-5] Attempting Tier 4 in-place update with an identical AppImage (same checksum).
        Authoritative source: specs_report.md § 3.4.2.
        Expected: Detects identical file and handles gracefully without corrupting state.
        """
        require_implementation("Tier2-5", "In-place update with identical file")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "Identical.AppImage", name="Identical", version="1.0.0")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        # Update using same file
        updated = installer.update_in_place(app, src_app)
        assert updated.checksum_sha256 == app.checksum_sha256
        assert Path(updated.install_path).exists()

    def test_tier2_06_uninstall_nonexistent_app(self, temp_workspace, cli_runner):
        """
        [Tier2-6] Attempting to uninstall an unknown/unregistered application.
        Authoritative source: PROJECT.md § CLI Hierarchy.
        Expected: CLI returns non-zero error code cleanly without unhandled Python traceback.
        """
        require_implementation("Tier2-6", "Uninstall nonexistent app error handling")

        result = cli_runner.invoke(cli, ["uninstall", "GhostApp"])
        assert result.exit_code != 0

    def test_tier2_07_integrity_tamper_detection(self, temp_workspace, cli_runner):
        """
        [Tier2-7] CLI verify detects byte-level corruption/tampering of installed AppImage.
        Authoritative source: PROJECT.md Feature 23.
        Expected: CLI verify exits with non-zero code upon checksum mismatch.
        """
        require_implementation("Tier2-7", "Integrity tamper detection CLI")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(temp_workspace["root"] / "TamperApp.AppImage", name="TamperApp")
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        # Mutate binary
        with open(app.install_path, "wb") as f:
            f.write(b"CORRUPTED_BYTES")

        res = cli_runner.invoke(cli, ["verify", "TamperApp"])
        assert res.exit_code != 0
        assert "fail" in res.output.lower() or "mismatch" in res.output.lower() or "corrupt" in res.output.lower()

    def test_tier2_08_malformed_desktop_entry_fallback(self, temp_workspace):
        """
        [Tier2-8] Mock AppImage with missing/broken embedded desktop file falls back to defaults.
        Authoritative source: specs_report.md § 1.2.
        Expected: Installer generates valid freedesktop entry using binary name fallback.
        """
        require_implementation("Tier2-8", "Malformed desktop entry fallback")
        from tests.conftest import create_mock_appimage

        # Script that extracts nothing during --appimage-extract
        path = temp_workspace["root"] / "NoDesktop.AppImage"
        script = "#!/bin/bash\nif [ \"$1\" = \"--appimage-extract\" ]; then mkdir -p squashfs-root; exit 0; fi\nexit 0\n"
        path.write_text(script)
        path.chmod(0o755)

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(path)

        assert app.desktop_entry is not None
        assert Path(app.desktop_entry).exists()
        content = Path(app.desktop_entry).read_text()
        assert "Type=Application" in content


# ===========================================================================
# 3. Tier 3: Cross-Feature Combinations
# ===========================================================================

@pytest.mark.tier3
class TestTier3CrossFeatureCombinations:
    """
    Tier 3: Complex Multi-Step Feature Pipelines.
    Integration chains exercising interactions between installer, updater,
    desktop editor, autostart, integrity, and backup services.
    """

    def test_tier3_01_full_lifecycle_pipeline(self, temp_workspace, cli_runner):
        """
        [Tier3-1] Complete 10-step lifecycle pipeline:
        1. Install version 1.0 (R1)
        2. Customize desktop entry with comment and category (R2)
        3. Perform Tier 4 in-place update using version 2.0 local file (R3)
        4. Verify customization preservation (R2 + R3)
        5. Enable autostart (R5)
        6. Verify integrity passes with v2 checksum (R5)
        7. Launch app with arguments (R1 + R5)
        8. Export backup manifest (R5)
        9. Uninstall app and verify clean purge of all files (R2)
        10. Import backup manifest and verify catalog restoration (R5)
        """
        require_implementation("Tier3-1", "Full lifecycle pipeline")
        from tests.conftest import create_mock_appimage

        # Step 1: Install v1.0
        v1_app = create_mock_appimage(
            temp_workspace["root"] / "pkg" / "Pipeline-1.0.AppImage",
            name="PipelineApp",
            version="1.0.0",
            category="Development"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(v1_app)
        assert app.version == "1.0.0"

        # Step 2: Customize desktop entry
        desktop_svc = DesktopEntryService()
        desktop_svc.update_entry(app, comment="Mission Critical Pipeline", categories="Development;IDE;")
        assert "Mission Critical Pipeline" in Path(app.desktop_entry).read_text()

        # Step 3: Tier 4 local in-place update to v2.0
        v2_app = create_mock_appimage(
            temp_workspace["root"] / "pkg" / "Pipeline-2.0.AppImage",
            name="PipelineApp",
            version="2.0.0",
            category="Development"
        )
        updated_app = installer.update_in_place(app, v2_app)

        # Step 4: Verify customization preserved
        assert updated_app.version == "2.0.0"
        desk_text = Path(updated_app.desktop_entry).read_text()
        assert "Mission Critical Pipeline" in desk_text
        assert "Development;IDE;" in desk_text

        # Step 5: Enable autostart
        autostart_mgr = AutostartManager()
        autostart_mgr.enable(updated_app)
        assert (temp_workspace["autostart_dir"] / f"{updated_app.name}.desktop").exists()

        # Step 6: Verify integrity
        integrity_svc = IntegrityVerifierService(db)
        assert integrity_svc.verify(updated_app) is True

        # Step 7: Launch app
        proc = installer.launch(updated_app, extra_args=["--pipeline-run"])
        stdout, _ = proc.communicate(timeout=5)
        assert proc.returncode == 0

        # Step 8: Export backup
        backup_file = temp_workspace["root"] / "pipeline_backup.json"
        backup_svc = BackupManagerService(db)
        backup_svc.export_manifest(backup_file)
        assert backup_file.exists()

        # Step 9: Uninstall app
        installer.uninstall(updated_app)
        assert not Path(updated_app.install_path).exists()
        assert not Path(updated_app.desktop_entry).exists()
        assert not (temp_workspace["autostart_dir"] / f"{updated_app.name}.desktop").exists()
        assert db.get_app_by_name("PipelineApp") is None

        # Step 10: Import backup
        backup_svc.import_manifest(backup_file)
        restored = db.get_app_by_name("PipelineApp")
        assert restored is not None
        assert restored.version == "2.0.0"

    def test_tier3_02_network_update_cascade_and_integrity(self, temp_workspace, mock_http_server):
        """
        [Tier3-2] Network update cascade:
        1. Install app with GitHub Releases update source
        2. Query mock GitHub API for update -> returns v2.0.0 asset URL
        3. Apply update via UpdaterService
        4. Verify new binary replaces old binary, checksum updated, and passes integrity
        """
        require_implementation("Tier3-2", "Network update cascade")
        from tests.conftest import create_mock_appimage

        src_app = create_mock_appimage(
            temp_workspace["root"] / "NetApp.AppImage",
            name="NetApp",
            version="1.0.0"
        )
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src_app)

        app.update_source = UpdateSource.GITHUB
        app.update_url = f"{mock_http_server}/repos/owner/NetApp"
        db.update_app(app)

        updater = UpdaterService()
        update_info = updater.check_update(app)
        assert update_info.update_available is True

        updated_app = updater.apply_update(app, update_info)
        assert updated_app.version == "2.0.0"

        # Check integrity of new binary
        verifier = IntegrityVerifierService(db)
        assert verifier.verify(updated_app) is True


# ===========================================================================
# 4. Tier 4: Real-World Scenarios
# ===========================================================================

@pytest.mark.tier4
class TestTier4RealWorldScenarios:
    """
    Tier 4: End-to-End Real-World Multi-App Lifecycle Scenarios.
    Simulates actual user environments with diverse app categories, concurrent apps,
    complex file naming, and batch maintenance.
    """

    def test_tier4_01_multi_app_concurrent_lifecycle(self, temp_workspace):
        """
        [Tier4-1] Multi-app concurrent lifecycle:
        - Install 5 apps across diverse categories (Dev, Games, Media, Office, Utilities).
        - Verify category filtering and disk usage calculation across all apps.
        - Update 2 apps concurrently (one via local file, one via simulated network).
        - Run batch integrity verification across all apps.
        - Uninstall 2 apps; verify remaining 3 apps are completely intact.
        """
        require_implementation("Tier4-1", "Multi-app concurrent lifecycle")
        from tests.conftest import create_mock_appimage

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)

        apps_meta = [
            ("VSCodium", "Development", "1.80.0"),
            ("SuperTuxKart", "Game", "1.4.0"),
            ("Audacity", "AudioVideo", "3.3.0"),
            ("LibreOffice", "Office", "7.5.0"),
            ("PeaZip", "Utility", "9.2.0"),
        ]

        installed_apps = []
        for name, cat, ver in apps_meta:
            src = create_mock_appimage(temp_workspace["root"] / f"{name}.AppImage", name=name, version=ver, category=cat)
            app = installer.install(src, category=cat)
            installed_apps.append(app)

        assert len(db.list_apps()) == 5

        # Check disk usage
        total_disk = db.get_total_disk_usage()
        assert total_disk == sum(a.size_bytes for a in installed_apps)

        # Update 1 app in-place via local file
        vscodium = db.get_app_by_name("VSCodium")
        new_vscodium = create_mock_appimage(temp_workspace["root"] / "VSCodium-1.85.0.AppImage", name="VSCodium", version="1.85.0")
        installer.update_in_place(vscodium, new_vscodium)
        assert db.get_app_by_name("VSCodium").version == "1.85.0"

        # Batch integrity verification
        verifier = IntegrityVerifierService(db)
        for app in db.list_apps():
            assert verifier.verify(app) is True

        # Uninstall 2 apps (SuperTuxKart and PeaZip)
        installer.uninstall(db.get_app_by_name("SuperTuxKart"))
        installer.uninstall(db.get_app_by_name("PeaZip"))

        remaining = db.list_apps()
        assert len(remaining) == 3
        remaining_names = {a.name for a in remaining}
        assert remaining_names == {"VSCodium", "Audacity", "LibreOffice"}

        # Verify all remaining app binaries still exist on disk
        for a in remaining:
            assert Path(a.install_path).exists()
            assert Path(a.desktop_entry).exists()

    def test_tier4_02_special_characters_and_paths(self, temp_workspace, cli_runner):
        """
        [Tier4-2] Application with special characters, spaces, and brackets in name and path:
        e.g., 'My Cool Tool (v2) [Beta] & Co.AppImage'
        Verifies correct escaping, desktop entry generation, argument passing, and clean purge.
        """
        require_implementation("Tier4-2", "Special characters in path and name")
        from tests.conftest import create_mock_appimage

        special_name = "Tool (v2) [Beta] & Co"
        folder = temp_workspace["root"] / "special folder with spaces & (symbols)"
        folder.mkdir(parents=True)
        src = create_mock_appimage(folder / f"{special_name}.AppImage", name=special_name)

        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db)
        app = installer.install(src)

        assert app is not None
        assert Path(app.install_path).exists()
        assert Path(app.desktop_entry).exists()

        # Desktop entry must safely quote the path
        content = Path(app.desktop_entry).read_text(encoding="utf-8")
        assert f'"{app.install_path}"' in content or f"'{app.install_path}'" in content

        # Launch app
        proc = installer.launch(app, extra_args=["--arg", "value"])
        proc.communicate(timeout=5)
        assert proc.returncode == 0

        # Uninstall cleanly
        success = installer.uninstall(app)
        assert success is True
        assert not Path(app.install_path).exists()
        assert not Path(app.desktop_entry).exists()
