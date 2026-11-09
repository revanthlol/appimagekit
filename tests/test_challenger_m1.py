"""
tests/test_challenger_m1.py — Empirical Challenger Test Suite for Milestone 1.

Mandate Coverage:
1. Checksum verification: test chunked SHA256 against system `sha256sum` binary across various file sizes.
2. Path handling & fuzzing: symlinks, unicode filenames, spaces in path names, missing parent dirs.
3. SQL injection resistance in app names and metadata fields (`'; DROP TABLE apps; --`).
4. Click CLI invocation: `appimagekit install <path>` via Click CliRunner and system subprocess.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Generator

import pytest
from click.testing import CliRunner

from src.main import cli
from src.models.app import App, InstallMode, UpdateSource
from src.models.database import DatabaseManager
from src.services.installer import (
    AppAlreadyInstalledError,
    AppImageNotFoundError,
    InstallerError,
    InstallerService,
    InvalidAppImageError,
    PermissionDeniedError,
)
from src.utils.paths import Paths
from tests.conftest import create_mock_appimage


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def challenger_db(tmp_path: Path) -> Generator[DatabaseManager, None, None]:
    """Provides a fresh isolated DatabaseManager for empirical challenge tests."""
    db_file = tmp_path / "challenger_registry.db"
    manager = DatabaseManager(db_file)
    yield manager
    manager.close()


@pytest.fixture
def isolated_paths_challenger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """Provides isolated XDG paths within tmp_path."""
    user_apps = tmp_path / "user_apps"
    user_apps.mkdir(parents=True, exist_ok=True)
    system_apps = tmp_path / "system_apps"
    system_apps.mkdir(parents=True, exist_ok=True)
    user_data = tmp_path / "xdg_data"
    user_data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_DATA_HOME", str(user_data))
    monkeypatch.setenv("APPIMAGEKIT_APPS_DIR", str(user_apps))
    monkeypatch.setenv("APPIMAGEKIT_SYSTEM_DIR", str(system_apps))
    monkeypatch.setenv("APPIMAGEKIT_DB_PATH", str(tmp_path / "env_registry.db"))

    Paths.set_override("user_appimages_dir", user_apps)
    Paths.set_override("system_appimages_dir", system_apps)
    Paths.set_override("database_path", tmp_path / "env_registry.db")

    return Paths()


@pytest.fixture
def challenger_installer(challenger_db: DatabaseManager, isolated_paths_challenger: Paths) -> InstallerService:
    """Provides an InstallerService configured with isolated DB and Paths."""
    return InstallerService(db=challenger_db, paths=isolated_paths_challenger)


def get_system_sha256(file_path: Path) -> str:
    """Oracle function: invokes system sha256sum binary."""
    res = subprocess.run(["sha256sum", str(file_path)], capture_output=True, text=True, check=True)
    return res.stdout.split()[0].strip().lower()


# =============================================================================
# 1. Checksum Verification Oracle vs system `sha256sum` binary
# =============================================================================

class TestChecksumOracleVsSystemSha256sum:
    """
    Stress-test chunked SHA256 against system `sha256sum` across boundary file sizes
    and varied chunk sizes.
    """

    @pytest.mark.parametrize(
        "file_size,description",
        [
            (0, "0-byte empty file"),
            (1, "1-byte minimal file"),
            (13, "13-byte prime length"),
            (512, "512-byte block"),
            (65535, "64 KiB minus 1 byte (under chunk boundary)"),
            (65536, "64 KiB exact (exact 1 chunk)"),
            (65537, "64 KiB plus 1 byte (spills into second chunk)"),
            (131072, "128 KiB (exact 2 chunks)"),
            (1048576, "1 MiB"),
            (10485760, "10 MiB medium file"),
            (52428800, "50 MiB large binary"),
        ],
    )
    def test_chunked_sha256_matches_system_sha256sum(
        self, tmp_path: Path, file_size: int, description: str
    ) -> None:
        """Verify InstallerService.calculate_sha256 matches system sha256sum exactly."""
        test_file = tmp_path / f"test_size_{file_size}.dat"
        if file_size == 0:
            test_file.touch()
        else:
            # Deterministic, non-trivial pattern
            chunk = os.urandom(min(file_size, 65536))
            written = 0
            with open(test_file, "wb") as f:
                while written < file_size:
                    to_write = min(len(chunk), file_size - written)
                    f.write(chunk[:to_write])
                    written += to_write

        system_hash = get_system_sha256(test_file)
        service_hash = InstallerService.calculate_sha256(test_file)

        assert service_hash == system_hash, f"Mismatch for {description}: service={service_hash} vs system={system_hash}"

    @pytest.mark.parametrize("chunk_size", [1, 7, 1024, 32768, 65536, 131072, 1048576])
    def test_chunk_size_invariance(self, tmp_path: Path, chunk_size: int) -> None:
        """Verify that calculate_sha256 produces identical hashes regardless of buffer size."""
        test_file = tmp_path / "buffer_invariance.dat"
        # 250 KiB file (does not align with standard power of 2)
        test_file.write_bytes(os.urandom(256000))

        system_hash = get_system_sha256(test_file)
        service_hash = InstallerService.calculate_sha256(test_file, chunk_size=chunk_size)

        assert service_hash == system_hash

    def test_installer_service_checksum_against_oracle(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """Verify that installed App recorded in DB has SHA256 identical to system sha256sum."""
        app_file = tmp_path / "OracleApp-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="OracleApp", version="1.0.0")

        app = challenger_installer.install(app_file)
        installed_binary = Path(app.install_path)

        system_hash = get_system_sha256(installed_binary)
        assert app.checksum_sha256 == system_hash
        assert challenger_installer.db.get_app(app.id).checksum_sha256 == system_hash


# =============================================================================
# 2. Path Handling & Fuzzing (Symlinks, Unicode, Spaces, Missing Parents)
# =============================================================================

class TestPathHandlingAndFuzzing:
    """Stress-test path handling with symlinks, unicode, spaces, and missing directories."""

    # ---------------- Symlinks ----------------

    def test_install_valid_symlink_source(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """
        Verify installing from a symlink source works correctly and installs the real binary.
        """
        real_file = tmp_path / "RealTarget-1.0.0-x86_64.AppImage"
        create_mock_appimage(real_file, name="RealTarget")

        symlink_file = tmp_path / "SymlinkApp-1.0.0-x86_64.AppImage"
        symlink_file.symlink_to(real_file)

        assert symlink_file.is_symlink()

        app = challenger_installer.install(symlink_file)
        dest_binary = Path(app.install_path)

        assert dest_binary.exists()
        # The destination must be a genuine regular file, not a dangling symlink
        assert not dest_binary.is_symlink()
        assert dest_binary.is_file()
        assert os.access(dest_binary, os.X_OK)

        # Launch must work
        proc = challenger_installer.launch(app)
        assert proc.wait(timeout=5) == 0

    def test_install_broken_symlink_raises_not_found(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """Broken symlink must raise AppImageNotFoundError."""
        broken_symlink = tmp_path / "Broken.AppImage"
        target_nonexistent = tmp_path / "Ghost.AppImage"
        broken_symlink.symlink_to(target_nonexistent)

        with pytest.raises(AppImageNotFoundError):
            challenger_installer.install(broken_symlink)

    def test_install_symlink_loop_raises_not_found(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """Recursive symlink loop must raise AppImageNotFoundError or OSError."""
        link_a = tmp_path / "loop_a.AppImage"
        link_b = tmp_path / "loop_b.AppImage"
        link_a.symlink_to(link_b)
        link_b.symlink_to(link_a)

        with pytest.raises((AppImageNotFoundError, OSError)):
            challenger_installer.install(link_a)

    def test_destination_dir_is_symlink(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """Target storage directory being a symlink resolves correctly."""
        real_dest_dir = tmp_path / "real_storage"
        real_dest_dir.mkdir(parents=True, exist_ok=True)
        symlink_dest_dir = tmp_path / "symlink_storage"
        symlink_dest_dir.symlink_to(real_dest_dir)

        Paths.set_override("user_appimages_dir", symlink_dest_dir)

        app_file = tmp_path / "SymDirApp-1.0.AppImage"
        create_mock_appimage(app_file, name="SymDirApp")

        app = challenger_installer.install(app_file)
        assert Path(app.install_path).exists()
        # Resolved path must point to real storage
        assert real_dest_dir.resolve() in Path(app.install_path).resolve().parents

    # ---------------- Unicode Filenames & Directories ----------------

    @pytest.mark.parametrize(
        "app_filename,expected_name_substr",
        [
            ("测试应用-1.0.0-x86_64.AppImage", "测试应用"),
            ("日本語アプリ-2.0.AppImage", "日本語アプリ"),
            ("Приложение-1.5.0-x86_64.AppImage", "Приложение"),
            ("🚀RocketApp-3.0.AppImage", "🚀RocketApp"),
            ("café_éditeur_v2.0-x86_64.AppImage", "café"),
            ("مرحبا_app-1.0.AppImage", "مرحبا"),
            ("ỨngDụng_TiếngViệt-1.0.AppImage", "ỨngDụng"),
        ],
    )
    def test_unicode_filenames_lifecycle(
        self,
        challenger_installer: InstallerService,
        tmp_path: Path,
        app_filename: str,
        expected_name_substr: str,
    ) -> None:
        """
        Verify full lifecycle (validate, install, DB persist, query, launch, uninstall)
        with non-ASCII, UTF-8 multi-byte filenames and symbols.
        """
        unicode_dir = tmp_path / "📁_unicode_sources" / "приложения"
        unicode_dir.mkdir(parents=True, exist_ok=True)

        app_path = unicode_dir / app_filename
        create_mock_appimage(app_path, name=expected_name_substr, version="1.0.0")

        # 1. Install
        app = challenger_installer.install(app_path)
        assert app.id is not None
        assert Path(app.install_path).exists()
        assert expected_name_substr in app.name

        # 2. Query from database
        db_app = challenger_installer.db.get_app(app.id)
        assert db_app is not None
        assert db_app.name == app.name
        assert db_app.filename == app_filename

        # 3. Query by path
        by_path = challenger_installer.db.get_app_by_path(app.install_path)
        assert by_path is not None
        assert by_path.id == app.id

        # 4. Launch
        proc = challenger_installer.launch(app)
        assert proc.wait(timeout=5) == 0

        # 5. Uninstall
        assert challenger_installer.uninstall(app) is True
        assert not Path(app.install_path).exists()
        assert challenger_installer.db.get_app(app.id) is None

    # ---------------- Spaces in Paths & Arguments ----------------

    def test_spaces_in_source_path_and_destination(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """Verify handling when source, destination, and filename all contain spaces."""
        source_dir = tmp_path / "My Downloaded Apps / Special Collection"
        source_dir.mkdir(parents=True, exist_ok=True)

        app_path = source_dir / "Super Video Editor 2026 Pro-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_path, name="Super Video Editor")

        custom_dest_dir = tmp_path / "Program Files / Local AppImages"
        Paths.set_override("user_appimages_dir", custom_dest_dir)

        app = challenger_installer.install(app_path)
        assert Path(app.install_path).exists()
        assert " " in app.install_path
        assert Path(app.install_path).parent == custom_dest_dir.resolve()

        # Launch
        proc = challenger_installer.launch(app)
        assert proc.wait(timeout=5) == 0

    def test_launch_complex_spaces_and_quotes_arguments(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """
        Verify launching with arguments containing whitespace, single quotes, double quotes,
        and paths with spaces does not suffer shell tokenization / injection issues.
        """
        log_file = tmp_path / "args_capture.txt"
        app_file = tmp_path / "ArgApp-1.0.AppImage"
        script = f"""#!/bin/bash
for arg in "$@"; do
    echo "ARG:$arg" >> "{log_file}"
done
exit 0
"""
        app_file.write_text(script, encoding="utf-8")
        app_file.chmod(0o755)

        app = challenger_installer.install(app_file)
        app.exec_args = '--title "My Complex App" --path \'/space dir/file.txt\' --empty=""'
        log_file.unlink(missing_ok=True)

        proc = challenger_installer.launch(app, extra_args=['--user-name', 'Jane Doe', 'param with spaces'])
        assert proc.wait(timeout=5) == 0

        assert log_file.exists()
        lines = [line.strip() for line in log_file.read_text().splitlines()]

        expected_tokens = [
            'ARG:--title',
            'ARG:My Complex App',
            'ARG:--path',
            'ARG:/space dir/file.txt',
            'ARG:--empty=',
            'ARG:--user-name',
            'ARG:Jane Doe',
            'ARG:param with spaces',
        ]
        assert lines == expected_tokens

    # ---------------- Missing Parent Directories ----------------

    def test_missing_parent_directories_autocreation(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """
        Verify destination path with deeply nested nonexistent directories is auto-created.
        """
        deep_dir = tmp_path / "deep1" / "deep2" / "deep3" / "apps"
        assert not deep_dir.exists()
        Paths.set_override("user_appimages_dir", deep_dir)

        app_file = tmp_path / "DeepDirApp-1.0.AppImage"
        create_mock_appimage(app_file, name="DeepDirApp")

        app = challenger_installer.install(app_file)
        assert deep_dir.exists()
        assert Path(app.install_path).exists()
        assert Path(app.install_path).parent == deep_dir.resolve()

    def test_database_manager_missing_parent_dir(self, tmp_path: Path) -> None:
        """Verify DatabaseManager auto-creates deeply nested directory for registry.db."""
        deep_db_file = tmp_path / "nested1" / "nested2" / "nested3" / "registry.db"
        assert not deep_db_file.parent.exists()

        db = DatabaseManager(deep_db_file)
        assert deep_db_file.parent.exists()
        assert deep_db_file.exists()
        assert db.count_apps() == 0
        db.close()

    def test_special_characters_filename_lifecycle(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """Verify handling of filenames with complex symbols and punctuation."""
        app_file = tmp_path / "App!@#$%^&()[]{};'=+,~-1.0.AppImage"
        create_mock_appimage(app_file, name="SpecialApp")

        app = challenger_installer.install(app_file)
        assert app.id is not None
        assert Path(app.install_path).exists()

        proc = challenger_installer.launch(app)
        assert proc.wait(timeout=5) == 0

        assert challenger_installer.uninstall(app) is True
        assert not Path(app.install_path).exists()

    def test_unclosed_quotes_in_launch_args_raises_value_error(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """
        Adversarial edge case: unclosed quotation in exec_args causes shlex.split to raise ValueError.
        Documents current error boundary behavior.
        """
        app_file = tmp_path / "UnclosedApp-1.0.AppImage"
        create_mock_appimage(app_file, name="UnclosedApp")

        app = challenger_installer.install(app_file)
        app.exec_args = '--title="unclosed quotation'

        with pytest.raises(ValueError, match="No closing quotation"):
            challenger_installer.launch(app)

    def test_readonly_destination_directory_raises_permission_error(
        self, challenger_installer: InstallerService, tmp_path: Path
    ) -> None:
        """
        Adversarial edge case: when destination storage directory is not writable (0o555),
        _copy_binary raises raw builtins.PermissionError.
        """
        ro_dest = tmp_path / "ro_storage"
        ro_dest.mkdir(mode=0o555)

        Paths.set_override("user_appimages_dir", ro_dest)

        app_file = tmp_path / "RoDestApp-1.0.AppImage"
        create_mock_appimage(app_file, name="RoDestApp")

        with pytest.raises(PermissionError):
            challenger_installer.install(app_file)


# =============================================================================
# 3. SQL Injection Resistance
# =============================================================================

class TestSQLInjectionResistance:
    """
    Stress-test DatabaseManager with malicious SQL injection payloads in all metadata fields
    and search queries (`'; DROP TABLE apps; --`).
    """

    SQLI_PAYLOADS = [
        "'; DROP TABLE apps; --",
        "'; DROP TABLE apps;",
        "' OR '1'='1",
        "' OR 1=1 --",
        "admin'--",
        "'; DELETE FROM apps WHERE 1=1; --",
        "'; UPDATE apps SET pinned=1; --",
        "'; ATTACH DATABASE ':memory:' AS injected; --",
        "' UNION SELECT 1, 'injected', 'f', 'p', null, null, null, 0, 'user', 'none', null, null, 'Utility', 0, null, 0, 'hash', '', '', 0, null, 0, null, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, null --",
        'test" OR ""="',
        "test' OR 'a'='a",
        "/* multiline comment */ '; DROP TABLE apps; --",
    ]

    def test_sqli_in_app_add_and_update(self, challenger_db: DatabaseManager) -> None:
        """
        Insert and update apps containing malicious SQL injection strings in every string column.
        The table must remain intact and string values must be preserved verbatim.
        """
        for i, payload in enumerate(self.SQLI_PAYLOADS):
            app = App(
                name=f"MaliciousApp_{i}_{payload}",
                filename=f"app_{i}_{payload}.AppImage",
                install_path=f"/opt/apps/malicious_{i}_{payload}.AppImage",
                desktop_entry=f"/apps/{payload}.desktop",
                icon_path=f"/icons/{payload}.svg",
                version=f"1.0.0_{payload}",
                category=f"Category_{payload}",
                exec_args=f"--flag {payload}",
                comment=f"Comment {payload}",
                startup_wm_class=f"WM_{payload}",
                hub_id=f"hub_{payload}",
                checksum_sha256=f"hash_{i}_{payload}",
            )

            # Insert
            inserted = challenger_db.add_app(app)
            assert inserted.id is not None
            assert inserted.name == app.name
            assert inserted.exec_args == app.exec_args

            # Verify table still exists and record is retrievable
            fetched = challenger_db.get_app(inserted.id)
            assert fetched is not None
            assert fetched.name == app.name
            assert fetched.comment == app.comment

            # Update with another payload
            fetched.comment = f"Updated_{payload}"
            assert challenger_db.update_app(fetched) is True
            re_fetched = challenger_db.get_app(inserted.id)
            assert re_fetched.comment == f"Updated_{payload}"

        # Confirm all apps were inserted safely without table corruption
        assert challenger_db.count_apps() == len(self.SQLI_PAYLOADS)

    @pytest.mark.parametrize("payload", SQLI_PAYLOADS)
    def test_sqli_in_get_by_name(self, challenger_db: DatabaseManager, payload: str) -> None:
        """Querying get_app_by_name with injection payloads must safely return None."""
        res = challenger_db.get_app_by_name(payload)
        assert res is None
        # Table must still exist
        assert challenger_db.count_apps() >= 0

    @pytest.mark.parametrize("payload", SQLI_PAYLOADS)
    def test_sqli_in_get_by_path(self, challenger_db: DatabaseManager, payload: str) -> None:
        """Querying get_app_by_path with injection payloads must safely return None."""
        res = challenger_db.get_app_by_path(payload)
        assert res is None

    @pytest.mark.parametrize("payload", SQLI_PAYLOADS)
    def test_sqli_in_get_by_checksum(self, challenger_db: DatabaseManager, payload: str) -> None:
        """Querying get_app_by_checksum with injection payloads must safely return None."""
        res = challenger_db.get_app_by_checksum(payload)
        assert res is None

    @pytest.mark.parametrize("payload", SQLI_PAYLOADS)
    def test_sqli_in_list_apps_category_and_mode(
        self, challenger_db: DatabaseManager, payload: str
    ) -> None:
        """list_apps with malicious category or mode must not execute injection."""
        # Insert one legitimate app
        challenger_db.add_app(
            App(name="Legit", filename="legit.AppImage", install_path="/legit.AppImage", checksum_sha256="123")
        )

        res_cat = challenger_db.list_apps(category=payload)
        assert isinstance(res_cat, list)
        assert len(res_cat) == 0

        res_mode = challenger_db.list_apps(mode=payload)
        assert isinstance(res_mode, list)
        assert len(res_mode) == 0

        # Legit app still exists
        assert challenger_db.count_apps() == 1

    @pytest.mark.parametrize("payload", SQLI_PAYLOADS)
    def test_sqli_in_list_apps_query(self, challenger_db: DatabaseManager, payload: str) -> None:
        """list_apps query search must be parameterized and immune to SQL injection."""
        challenger_db.add_app(
            App(name="LegitApp", filename="legit.AppImage", install_path="/legit.AppImage", checksum_sha256="456")
        )

        # Search with SQL injection payload
        res = challenger_db.list_apps(query=payload)
        assert isinstance(res, list)
        # Should not return the legit app unless the payload was part of its name (which it isn't)
        assert len(res) == 0

        # Table apps must be fully intact
        with challenger_db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM apps;")
            count = cur.fetchone()[0]
            assert count == 1


# =============================================================================
# 4. Click CLI Invocation (`appimagekit install <path>`)
# =============================================================================

class TestClickCLIInvocation:
    """
    Stress-test CLI invocation via Click CliRunner and via system subprocess.
    """

    def test_clirunner_install_success(
        self, tmp_path: Path, isolated_paths_challenger: Paths
    ) -> None:
        """Verify `appimagekit install <path>` succeeds with exit code 0 via CliRunner."""
        app_file = tmp_path / "CliApp-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="CliApp")

        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(app_file)])

        assert result.exit_code == 0
        assert "Successfully installed" in result.output
        assert "CliApp" in result.output

    def test_clirunner_install_custom_category(
        self, tmp_path: Path, isolated_paths_challenger: Paths
    ) -> None:
        """Verify `--category` option is recognized and stored in DB."""
        app_file = tmp_path / "DevApp-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="DevApp")

        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(app_file), "--category", "Development"])

        assert result.exit_code == 0
        db = DatabaseManager(isolated_paths_challenger.get_database_path())
        app = db.get_app_by_name("DevApp")
        assert app is not None
        assert app.category == "Development"

    def test_clirunner_install_missing_argument(self) -> None:
        """Invoking `install` without path returns exit code 2 (Click usage error)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["install"])
        assert result.exit_code == 2
        assert "Missing argument" in result.output

    def test_clirunner_install_nonexistent_path(self, tmp_path: Path) -> None:
        """Invoking with nonexistent file returns exit code 2 (Click path validation)."""
        nonexistent = tmp_path / "GhostApp.AppImage"
        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(nonexistent)])
        assert result.exit_code == 2
        assert "does not exist" in result.output

    def test_clirunner_install_directory_path(self, tmp_path: Path) -> None:
        """Invoking with directory returns exit code 2 (dir_okay=False)."""
        some_dir = tmp_path / "NotAFile.AppImage"
        some_dir.mkdir()
        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(some_dir)])
        assert result.exit_code == 2
        assert "is a directory" in result.output

    def test_clirunner_install_empty_file_fails_cleanly(self, tmp_path: Path) -> None:
        """Empty file causes installer error and returns exit code 1."""
        empty_file = tmp_path / "Empty.AppImage"
        empty_file.touch()

        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(empty_file)])
        assert result.exit_code == 1
        assert "Error installing" in result.output
        assert "empty" in result.output.lower()

    def test_clirunner_install_duplicate_fails_cleanly(
        self, tmp_path: Path, isolated_paths_challenger: Paths
    ) -> None:
        """Installing duplicate without reinstall flag returns exit code 1."""
        app_file = tmp_path / "DuplicateApp-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="DuplicateApp")

        runner = CliRunner()
        res1 = runner.invoke(cli, ["install", str(app_file)])
        assert res1.exit_code == 0

        res2 = runner.invoke(cli, ["install", str(app_file)])
        assert res2.exit_code == 1
        assert "already installed" in res2.output.lower()

    def test_clirunner_install_path_with_spaces_and_unicode(
        self, tmp_path: Path, isolated_paths_challenger: Paths
    ) -> None:
        """CliRunner handles paths with spaces and unicode cleanly."""
        space_dir = tmp_path / "my folder / 🚀 tools"
        space_dir.mkdir(parents=True, exist_ok=True)
        app_file = space_dir / "Super App-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="Super App")

        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(app_file)])
        assert result.exit_code == 0
        assert "Super App" in result.output

    # ---------------- System Subprocess Invocations ----------------

    def test_subprocess_cli_help(self) -> None:
        """Verify binary invocation `/home/rev/.local/bin/appimagekit --help`."""
        cli_bin = Path("/home/rev/.local/bin/appimagekit")
        if not cli_bin.exists():
            pytest.skip("CLI binary not installed in ~/.local/bin")

        res = subprocess.run([str(cli_bin), "--help"], capture_output=True, text=True)
        assert res.returncode == 0
        assert "AppImageKit" in res.stdout
        assert "install" in res.stdout

    def test_subprocess_cli_install_execution(self, tmp_path: Path) -> None:
        """
        Verify real system execution of `appimagekit install <path>` via subprocess
        using isolated environment variables.
        """
        cli_bin = Path("/home/rev/.local/bin/appimagekit")
        cmd = [str(cli_bin)] if cli_bin.exists() else ["python", "-m", "src.main"]

        app_file = tmp_path / "SubprocApp-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="SubprocApp")

        user_apps = tmp_path / "subproc_user_apps"
        db_path = tmp_path / "subproc_registry.db"
        env = os.environ.copy()
        env["APPIMAGEKIT_APPS_DIR"] = str(user_apps)
        env["APPIMAGEKIT_DB_PATH"] = str(db_path)

        res = subprocess.run(
            [*cmd, "install", str(app_file)],
            env=env,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        assert "Successfully installed" in res.stdout
        assert "SubprocApp" in res.stdout

        # Verify DB in isolated env
        db = DatabaseManager(db_path)
        app = db.get_app_by_name("SubprocApp")
        assert app is not None
        assert Path(app.install_path).exists()
        db.close()

    def test_clirunner_install_sqli_filename(
        self, tmp_path: Path, isolated_paths_challenger: Paths
    ) -> None:
        """
        Verify CLI install gracefully and safely handles AppImage filename with SQL injection.
        `'; DROP TABLE apps; --.AppImage` must be installed without executing SQL.
        """
        app_file = tmp_path / "'; DROP TABLE apps; --.AppImage"
        create_mock_appimage(app_file, name="SQLiApp")

        runner = CliRunner()
        result = runner.invoke(cli, ["install", str(app_file)])

        assert result.exit_code == 0
        assert "Successfully installed" in result.output

        db = DatabaseManager(isolated_paths_challenger.get_database_path())
        assert db.count_apps() == 1
        installed = db.list_apps()[0]
        assert installed.filename == "'; DROP TABLE apps; --.AppImage"
        db.close()

    def test_subprocess_cli_install_sqli_filename(self, tmp_path: Path) -> None:
        """
        Verify real system subprocess execution of `appimagekit install <path>` with SQL injection filename.
        """
        cli_bin = Path("/home/rev/.local/bin/appimagekit")
        cmd = [str(cli_bin)] if cli_bin.exists() else ["python", "-m", "src.main"]

        app_file = tmp_path / "'; DROP TABLE apps; --.AppImage"
        create_mock_appimage(app_file, name="SQLiSubprocApp")

        user_apps = tmp_path / "subproc_sqli_apps"
        db_path = tmp_path / "subproc_sqli_registry.db"
        env = os.environ.copy()
        env["APPIMAGEKIT_APPS_DIR"] = str(user_apps)
        env["APPIMAGEKIT_DB_PATH"] = str(db_path)

        res = subprocess.run(
            [*cmd, "install", str(app_file)],
            env=env,
            capture_output=True,
            text=True,
        )

        assert res.returncode == 0
        assert "Successfully installed" in res.stdout

        db = DatabaseManager(db_path)
        assert db.count_apps() == 1
        db.close()

    def test_clirunner_install_relative_path(
        self, tmp_path: Path, isolated_paths_challenger: Paths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify installing using a relative path works cleanly."""
        monkeypatch.chdir(tmp_path)
        app_file = tmp_path / "RelativeApp-1.0.0-x86_64.AppImage"
        create_mock_appimage(app_file, name="RelativeApp")

        runner = CliRunner()
        result = runner.invoke(cli, ["install", "./RelativeApp-1.0.0-x86_64.AppImage"])

        assert result.exit_code == 0
        assert "Successfully installed" in result.output
        assert "RelativeApp" in result.output

