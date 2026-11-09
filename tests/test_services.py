"""
tests/test_services.py — Unit test suite for Auxiliary Services:
SandboxDetectorService, AutostartManager, BackupManagerService,
IntegrityVerifierService, and HubCatalogService.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.app import App, InstallMode, SandboxType, UpdateSource
from src.models.database import DatabaseManager
from src.services.autostart import AutostartManager
from src.services.backup import BackupError, BackupManagerService
from src.services.hub import HubCatalogError, HubCatalogService
from src.services.installer import InstallerService
from src.services.integrity import IntegrityVerifierService
from src.services.sandbox import SandboxDetectorService
from src.utils.paths import Paths
from tests.conftest import create_mock_appimage


# ---------------------------------------------------------------------------
# 1. SandboxDetectorService Tests
# ---------------------------------------------------------------------------

class TestSandboxDetectorService:
    def test_detect_unsandboxed_app(self, temp_workspace):
        app = App(name="UnsandboxedApp", install_path="/tmp/fake.AppImage")
        detector = SandboxDetectorService()
        is_sb, sb_type = detector.detect(app)
        assert is_sb is False
        assert sb_type is None

    def test_detect_explicit_flags(self):
        detector = SandboxDetectorService()

        app_bwrap = App(name="BwrapApp", sandboxed=True, sandbox_type=SandboxType.BUBBLEWRAP)
        is_sb, sb_type = detector.detect(app_bwrap)
        assert is_sb is True
        assert sb_type == SandboxType.BUBBLEWRAP

        app_firejail = App(name="FirejailApp", sandboxed=True, sandbox_type=SandboxType.FIREJAIL)
        is_sb, sb_type = detector.detect(app_firejail)
        assert is_sb is True
        assert sb_type == SandboxType.FIREJAIL

    def test_detect_desktop_file_exec_prefixes(self, tmp_path):
        detector = SandboxDetectorService()

        # Firejail in desktop file
        desk_fj = tmp_path / "fj.desktop"
        desk_fj.write_text("[Desktop Entry]\nType=Application\nName=FJ\nExec=firejail /path/to/app.AppImage\n")
        app_fj = App(name="FJApp", desktop_entry=str(desk_fj))
        is_sb, sb_type = detector.detect(app_fj)
        assert is_sb is True
        assert sb_type == SandboxType.FIREJAIL

        # Bwrap in desktop file
        desk_bw = tmp_path / "bw.desktop"
        desk_bw.write_text("[Desktop Entry]\nType=Application\nName=BW\nExec=bwrap --ro-bind / / /path/to/app.AppImage\n")
        app_bw = App(name="BWApp", desktop_entry=str(desk_bw))
        is_sb, sb_type = detector.detect(app_bw)
        assert is_sb is True
        assert sb_type == SandboxType.BUBBLEWRAP

    def test_detect_exec_args(self):
        detector = SandboxDetectorService()

        app1 = App(name="ArgApp1", exec_args="--sandbox firejail")
        is_sb, sb_type = detector.detect(app1)
        assert is_sb is True
        assert sb_type == SandboxType.FIREJAIL

        app2 = App(name="ArgApp2", exec_args="bwrap --unshare-all")
        is_sb, sb_type = detector.detect(app2)
        assert is_sb is True
        assert sb_type == SandboxType.BUBBLEWRAP

    def test_detect_firejail_profile_presence(self, tmp_path):
        config_dir = tmp_path / "config"
        fj_dir = config_dir / "firejail"
        fj_dir.mkdir(parents=True)
        (fj_dir / "mycoolapp.profile").write_text("# firejail profile\n")

        detector = SandboxDetectorService(config_dir=config_dir)
        app = App(name="MyCoolApp", filename="MyCoolApp.AppImage")
        is_sb, sb_type = detector.detect(app)
        assert is_sb is True
        assert sb_type == SandboxType.FIREJAIL

    def test_wrap_command(self):
        detector = SandboxDetectorService()
        app = App(name="App", install_path="/opt/apps/App.AppImage")

        cmd_fj = detector.wrap_command(app, SandboxType.FIREJAIL)
        assert cmd_fj[0] == "firejail"
        assert cmd_fj[-1] == "/opt/apps/App.AppImage"

        cmd_bw = detector.wrap_command(app, SandboxType.BUBBLEWRAP)
        assert cmd_bw[0] == "bwrap"
        assert cmd_bw[-1] == "/opt/apps/App.AppImage"


# ---------------------------------------------------------------------------
# 2. AutostartManager Tests
# ---------------------------------------------------------------------------

class TestAutostartManager:
    def test_autostart_enable_and_disable(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        app = App(name="TestApp", install_path=str(temp_workspace["root"] / "TestApp.AppImage"))
        app = db.add_app(app)

        mgr = AutostartManager(db=db)
        autostart_path = temp_workspace["autostart_dir"] / f"{app.name}.desktop"

        assert not autostart_path.exists()
        assert not mgr.is_enabled(app)

        # Enable
        written_path = mgr.enable(app)
        assert written_path.exists()
        assert mgr.is_enabled(app)
        assert app.autostart is True
        # Check DB updated
        reloaded = db.get_app(app.id)
        assert reloaded.autostart is True

        # Disable
        success = mgr.disable(app)
        assert success is True
        assert not autostart_path.exists()
        assert not mgr.is_enabled(app)
        assert app.autostart is False
        reloaded = db.get_app(app.id)
        assert reloaded.autostart is False

    def test_autostart_toggle(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        app = App(name="ToggleApp", install_path=str(temp_workspace["root"] / "ToggleApp.AppImage"))
        app = db.add_app(app)

        mgr = AutostartManager(db=db)
        # 1st toggle: enable
        res = mgr.toggle(app)
        assert res is True
        assert mgr.is_enabled(app)

        # 2nd toggle: disable
        res = mgr.toggle(app)
        assert res is False
        assert not mgr.is_enabled(app)

    def test_autostart_derives_from_desktop_entry(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        desk_path = temp_workspace["root"] / "source.desktop"
        desk_path.write_text("[Desktop Entry]\nType=Application\nName=Derived\nExec=/path/app %U\nIcon=app-icon\n")

        app = App(name="DerivedApp", install_path="/path/app", desktop_entry=str(desk_path))
        mgr = AutostartManager(db=db)
        out = mgr.enable(app)
        content = out.read_text()
        assert "X-GNOME-Autostart-enabled=true" in content
        assert "Icon=app-icon" in content


# ---------------------------------------------------------------------------
# 3. BackupManagerService Tests
# ---------------------------------------------------------------------------

class TestBackupManagerService:
    def test_export_and_import_manifest(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        app1 = db.add_app(App(name="App1", install_path=str(temp_workspace["root"] / "App1.AppImage"), version="1.0.0"))
        app2 = db.add_app(App(name="App2", install_path=str(temp_workspace["root"] / "App2.AppImage"), version="2.0.0"))

        mgr = BackupManagerService(db=db)
        manifest_file = temp_workspace["root"] / "test_backup.json"

        # Export
        mgr.export_manifest(manifest_file)
        assert manifest_file.exists()

        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert data["total_apps"] == 2
        assert len(data["apps"]) == 2
        names = {a["name"] for a in data["apps"]}
        assert "App1" in names and "App2" in names

        # Clear database
        db.delete_app(app1.id)
        db.delete_app(app2.id)
        assert len(db.list_apps()) == 0

        # Import
        restored = mgr.import_manifest(manifest_file)
        assert len(restored) == 2
        assert len(db.list_apps()) == 2
        assert db.get_app_by_name("App1") is not None
        assert db.get_app_by_name("App2") is not None

    def test_import_manifest_errors(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        mgr = BackupManagerService(db=db)

        # Missing file
        with pytest.raises(BackupError):
            mgr.import_manifest(temp_workspace["root"] / "missing.json")

        # Invalid JSON
        bad_json = temp_workspace["root"] / "bad.json"
        bad_json.write_text("{not valid json")
        with pytest.raises(BackupError):
            mgr.import_manifest(bad_json)

    def test_export_and_import_bundle_tar_gz(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        app_file = create_mock_appimage(temp_workspace["root"] / "BundleApp.AppImage", name="BundleApp")
        installer = InstallerService(db=db)
        app = installer.install(app_file)

        mgr = BackupManagerService(db=db)
        bundle_file = temp_workspace["root"] / "bundle.tar.gz"

        # Export bundle
        mgr.export_bundle(bundle_file)
        assert bundle_file.exists()
        assert tarfile.is_tarfile(bundle_file)

        # Purge application
        installer.uninstall(app)
        assert len(db.list_apps()) == 0

        # Restore from bundle
        restored = mgr.import_bundle(bundle_file)
        assert len(restored) >= 1
        assert db.get_app_by_name("BundleApp") is not None


# ---------------------------------------------------------------------------
# 4. IntegrityVerifierService Tests
# ---------------------------------------------------------------------------

class TestIntegrityVerifierService:
    def test_integrity_verification_pass_and_fail(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        app_file = create_mock_appimage(temp_workspace["root"] / "VerifyApp.AppImage", name="VerifyApp")
        installer = InstallerService(db=db)
        app = installer.install(app_file)

        verifier = IntegrityVerifierService(db=db)
        # Should pass on untouched binary
        assert verifier.verify(app) is True

        details = verifier.verify_with_details(app)
        assert details["valid"] is True
        assert details["actual_checksum"] == app.checksum_sha256

        # Corrupt file
        with open(app.install_path, "ab") as f:
            f.write(b"TAMPER_DATA")

        assert verifier.verify(app) is False
        details_fail = verifier.verify_with_details(app)
        assert details_fail["valid"] is False
        assert "Checksum mismatch" in details_fail["error"]

    def test_integrity_missing_file(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        app = App(name="MissingBinaryApp", install_path="/tmp/nonexistent_app.AppImage", checksum_sha256="abc123")
        verifier = IntegrityVerifierService(db=db)
        assert verifier.verify(app) is False
        details = verifier.verify_with_details(app)
        assert details["valid"] is False
        assert "missing" in details["error"].lower()

    def test_verify_all(self, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db=db)
        app1 = installer.install(create_mock_appimage(temp_workspace["root"] / "A1.AppImage", name="A1"))
        app2 = installer.install(create_mock_appimage(temp_workspace["root"] / "A2.AppImage", name="A2"))

        verifier = IntegrityVerifierService(db=db)
        results = verifier.verify_all()
        assert results["A1"] is True
        assert results["A2"] is True


# ---------------------------------------------------------------------------
# 5. HubCatalogService Tests
# ---------------------------------------------------------------------------

class TestHubCatalogService:
    def test_search_and_filter(self, mock_http_server):
        hub = HubCatalogService(feed_url=f"{mock_http_server}/feed.json")

        # Query search
        results = hub.search("Krita")
        assert len(results) >= 1
        assert results[0]["name"] == "Krita"

        # Category search
        cat_results = hub.search(category="AudioVideo")
        assert len(cat_results) >= 1
        assert cat_results[0]["name"] == "VLC"

        # Get by name
        app_item = hub.get_app_by_name("Krita")
        assert app_item is not None
        assert app_item["name"] == "Krita"

        # Nonexistent
        assert hub.get_app_by_name("NonExistentApp") is None

    def test_install_from_hub(self, mock_http_server, temp_workspace):
        db = DatabaseManager(temp_workspace["db_path"])
        installer = InstallerService(db=db)
        hub = HubCatalogService(
            feed_url=f"{mock_http_server}/feed.json",
            cache_dir=temp_workspace["cache_dir"],
            db=db,
            installer=installer,
        )

        app = hub.install_from_hub("Krita")
        assert app is not None
        assert app.name == "Krita"
        assert app.hub_id == "Krita"
        assert Path(app.install_path).exists()
        assert db.get_app_by_name("Krita") is not None
