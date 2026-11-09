"""
tests/test_installer.py — Unit tests for InstallerService (Milestone 1).
Hermetic, headless tests covering installation, permissions, hashing, fallback, and launching.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

from src.models.app import App, InstallMode, UpdateSource
from src.models.database import DatabaseManager
from src.services.installer import (
    AppAlreadyInstalledError,
    AppImageNotFoundError,
    ExecutionError,
    InstallerService,
    InvalidAppImageError,
    PermissionDeniedError,
)
from src.utils.paths import Paths


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """Provides an isolated Paths instance with user and system directories in tmp_path."""
    user_data = tmp_path / "user_data"
    user_config = tmp_path / "user_config"
    system_dir = tmp_path / "opt_appimages"

    user_data.mkdir(parents=True, exist_ok=True)
    user_config.mkdir(parents=True, exist_ok=True)
    system_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_DATA_HOME", str(user_data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_config))
    monkeypatch.setenv("APPIMAGEKIT_SYSTEM_DIR", str(system_dir))

    return Paths()


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    """Provides an isolated SQLite DatabaseManager in tmp_path."""
    db_path = tmp_path / "registry.db"
    return DatabaseManager(db_path=db_path)


@pytest.fixture
def installer(db: DatabaseManager, isolated_paths: Paths) -> InstallerService:
    """Provides an InstallerService configured with isolated DB and Paths."""
    return InstallerService(db=db, paths=isolated_paths)


@pytest.fixture
def mock_appimage(tmp_path: Path) -> Path:
    """Creates a fast, executable mock AppImage script."""
    appimage_file = tmp_path / "TestApp-1.0.0-x86_64.AppImage"
    content = """#!/bin/bash
if [ "$1" = "--echo-args" ]; then
    shift
    echo "$@"
    exit 0
fi
echo "Mock AppImage executed successfully"
exit 0
"""
    appimage_file.write_text(content, encoding="utf-8")
    appimage_file.chmod(0o755)
    return appimage_file


# -----------------------------------------------------------------------------
# Source Validation Tests
# -----------------------------------------------------------------------------

def test_validate_source_success(installer: InstallerService, mock_appimage: Path) -> None:
    """1. Valid source AppImage returns resolved path."""
    validated = installer.validate_source(mock_appimage)
    assert validated.resolve() == mock_appimage.resolve()


def test_validate_source_nonexistent(installer: InstallerService, tmp_path: Path) -> None:
    """2. Nonexistent file raises AppImageNotFoundError."""
    non_existent = tmp_path / "DoesNotExist.AppImage"
    with pytest.raises(AppImageNotFoundError):
        installer.validate_source(non_existent)


def test_validate_source_directory(installer: InstallerService, tmp_path: Path) -> None:
    """3. Directory path raises InvalidAppImageError."""
    dir_path = tmp_path / "somedir.AppImage"
    dir_path.mkdir()
    with pytest.raises(InvalidAppImageError):
        installer.validate_source(dir_path)


def test_validate_source_empty_file(installer: InstallerService, tmp_path: Path) -> None:
    """4. Empty (0-byte) file raises InvalidAppImageError."""
    empty_file = tmp_path / "Empty.AppImage"
    empty_file.touch()
    with pytest.raises(InvalidAppImageError):
        installer.validate_source(empty_file)


# -----------------------------------------------------------------------------
# Installation & Target Resolution Tests
# -----------------------------------------------------------------------------

def test_install_user_mode(installer: InstallerService, mock_appimage: Path, isolated_paths: Paths) -> None:
    """5. Installing in user mode copies binary, sets 0o755, calculates hash, and saves to DB."""
    app = installer.install(mock_appimage, mode=InstallMode.USER, category="Development")

    expected_dir = isolated_paths.get_user_appimages_dir()
    expected_path = expected_dir / mock_appimage.name

    assert expected_path.exists()
    assert app.install_path == str(expected_path.resolve())
    assert app.install_mode == InstallMode.USER
    assert app.category == "Development"
    assert app.id is not None

    # Verify permissions (0o755)
    st = expected_path.stat()
    assert st.st_mode & stat.S_IXUSR
    assert st.st_mode & stat.S_IXGRP
    assert st.st_mode & stat.S_IXOTH

    # Verify SHA256 checksum and size
    expected_checksum = hashlib.sha256(mock_appimage.read_bytes()).hexdigest()
    assert app.checksum_sha256 == expected_checksum
    assert app.size_bytes == expected_path.stat().st_size

    # Verify persisted in database
    db_app = installer.db.get_app(app.id)
    assert db_app is not None
    assert db_app.name == app.name
    assert db_app.checksum_sha256 == expected_checksum


def test_install_system_mode_authorized(
    installer: InstallerService, mock_appimage: Path, isolated_paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6. System mode install succeeds when root privileges are available."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    app = installer.install(mock_appimage, mode=InstallMode.SYSTEM)

    expected_dir = isolated_paths.get_system_appimages_dir()
    expected_path = expected_dir / mock_appimage.name

    assert expected_path.exists()
    assert app.install_path == str(expected_path.resolve())
    assert app.install_mode == InstallMode.SYSTEM


def test_install_system_mode_fallback_when_unauthorized(
    installer: InstallerService, mock_appimage: Path, isolated_paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7. System mode install falls back to user mode when unprivileged."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    app = installer.install(mock_appimage, mode=InstallMode.SYSTEM)

    user_dir = isolated_paths.get_user_appimages_dir()
    expected_path = user_dir / mock_appimage.name

    assert expected_path.exists()
    assert app.install_path == str(expected_path.resolve())
    assert app.install_mode == InstallMode.USER


def test_install_duplicate_prevention(installer: InstallerService, mock_appimage: Path) -> None:
    """8. Installing duplicate path without reinstall=True raises AppAlreadyInstalledError."""
    installer.install(mock_appimage)

    with pytest.raises(AppAlreadyInstalledError):
        installer.install(mock_appimage)


def test_install_reinstall_overwrites(installer: InstallerService, mock_appimage: Path) -> None:
    """9. Installing with reinstall=True overwrites existing binary and updates DB."""
    app1 = installer.install(mock_appimage)
    initial_id = app1.id

    # Modify source
    mock_appimage.write_text("#!/bin/bash\necho updated\n", encoding="utf-8")
    mock_appimage.chmod(0o755)

    app2 = installer.install(mock_appimage, reinstall=True)
    assert app2.id == initial_id
    assert app2.checksum_sha256 == hashlib.sha256(mock_appimage.read_bytes()).hexdigest()


# -----------------------------------------------------------------------------
# Checksum Calculation Tests
# -----------------------------------------------------------------------------

def test_calculate_sha256_chunked(tmp_path: Path) -> None:
    """10. Verify chunked SHA256 matches hashlib output for files > 64 KiB."""
    large_file = tmp_path / "large.bin"
    chunk = b"X" * 1024
    with open(large_file, "wb") as f:
        for _ in range(100):  # 100 KiB > 64 KiB
            f.write(chunk)

    expected = hashlib.sha256(large_file.read_bytes()).hexdigest()
    computed = InstallerService.calculate_sha256(large_file, chunk_size=32768)
    assert computed == expected


# -----------------------------------------------------------------------------
# Launching Tests
# -----------------------------------------------------------------------------

def test_launch_success(installer: InstallerService, mock_appimage: Path) -> None:
    """11. Launching installed app spawns process cleanly."""
    app = installer.install(mock_appimage)
    proc = installer.launch(app)

    assert isinstance(proc, subprocess.Popen)
    exit_code = proc.wait(timeout=5)
    assert exit_code == 0


def test_launch_with_arguments(installer: InstallerService, mock_appimage: Path, tmp_path: Path) -> None:
    """12. Launching with exec_args and extra_args passes all arguments to process."""
    arg_log = tmp_path / "args.log"
    script = f"""#!/bin/bash
echo "$@" > "{arg_log}"
exit 0
"""
    mock_appimage.write_text(script, encoding="utf-8")
    mock_appimage.chmod(0o755)

    app = installer.install(mock_appimage, reinstall=True)
    app.exec_args = "--flag1 --opt=value"

    proc = installer.launch(app, extra_args=["extra1", "extra2"])
    proc.wait(timeout=5)

    assert arg_log.exists()
    logged_args = arg_log.read_text().strip()
    assert logged_args == "--flag1 --opt=value extra1 extra2"


def test_launch_auto_heals_missing_exec_bit(installer: InstallerService, mock_appimage: Path) -> None:
    """13. Launching binary that lost executable bit auto-heals permissions."""
    app = installer.install(mock_appimage)
    binary = Path(app.install_path)

    # Strip execute permissions
    binary.chmod(0o644)
    assert not (binary.stat().st_mode & stat.S_IXUSR)

    proc = installer.launch(app)
    exit_code = proc.wait(timeout=5)
    assert exit_code == 0
    assert binary.stat().st_mode & stat.S_IXUSR


def test_launch_missing_binary(installer: InstallerService, mock_appimage: Path) -> None:
    """14. Launching app whose binary was deleted on disk raises AppImageNotFoundError."""
    app = installer.install(mock_appimage)
    Path(app.install_path).unlink()

    with pytest.raises(AppImageNotFoundError):
        installer.launch(app)


def test_uninstall_baseline(installer: InstallerService, mock_appimage: Path) -> None:
    """15. Uninstall removes binary and deletes DB record."""
    app = installer.install(mock_appimage)
    dest_path = Path(app.install_path)
    assert dest_path.exists()
    assert installer.db.get_app(app.id) is not None

    success = installer.uninstall(app)
    assert success is True
    assert not dest_path.exists()
    assert installer.db.get_app(app.id) is None
