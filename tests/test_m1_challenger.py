"""
tests/test_m1_challenger.py — Empirical Challenge Suite for Milestone 1.

Adversarial stress harness challenging:
1. SQLite Concurrency: Threaded and multi-process concurrent insertions and queries under WAL mode.
2. Installer Stress: Zero-byte files, huge sparse files, read-only sources, invalid magic headers, symlink loops.
3. Process Launcher Stress: Non-executable auto-healing, nonexistent files, Exec format errors, complex argument quoting.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

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


# =============================================================================
# Helper Fixtures & Generators
# =============================================================================

@pytest.fixture
def challenger_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provides isolated XDG paths and database for challenger testing."""
    root = tmp_path / "challenger_ws"
    apps_dir = root / "appimages"
    system_dir = root / "opt_appimages"
    db_file = root / "registry.db"

    apps_dir.mkdir(parents=True, exist_ok=True)
    system_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("APPIMAGEKIT_APPS_DIR", str(apps_dir))
    monkeypatch.setenv("APPIMAGEKIT_SYSTEM_DIR", str(system_dir))
    monkeypatch.setenv("APPIMAGEKIT_DB_PATH", str(db_file))

    db = DatabaseManager(db_file)
    installer = InstallerService(db=db)

    return {
        "root": root,
        "apps_dir": apps_dir,
        "system_dir": system_dir,
        "db_file": db_file,
        "db": db,
        "installer": installer,
    }


def create_dummy_elf_appimage(path: Path, payload: bytes = b"") -> Path:
    """Creates a mock AppImage file with valid ELF magic and minimum 52 bytes."""
    header = b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 44  # 52 bytes
    content = header + payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def create_logging_executable(path: Path, log_file: Path) -> Path:
    """Creates a python script acting as an executable AppImage that logs argv to log_file."""
    script = f"""#!/bin/sh
exec /usr/bin/python3 -c "
import sys, json
with open('{log_file}', 'w') as f:
    json.dump(sys.argv[1:], f)
" "$@"
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o755)
    return path


# =============================================================================
# Suite 1: SQLite Concurrency Under WAL Mode
# =============================================================================

class TestSQLiteWALConcurrency:
    """Adversarial stress-testing of DatabaseManager under high concurrent write/read load."""

    def test_concurrent_threaded_inserts_and_queries(self, challenger_workspace):
        """
        Challenge: 10 concurrent threads each performing 25 inserts and 50 queries.
        Validates:
        - No 'database is locked' errors under WAL mode.
        - Exactly 250 records successfully committed.
        - Aggregate disk usage matches total inserted size.
        """
        db = challenger_workspace["db"]
        num_threads = 10
        ops_per_thread = 25
        total_records = num_threads * ops_per_thread

        def worker_task(thread_id: int) -> Tuple[int, int]:
            inserted = 0
            queries = 0
            for i in range(ops_per_thread):
                app_name = f"ThreadApp_{thread_id}_{i}"
                app_path = f"/tmp/apps/{app_name}.AppImage"
                app = App(
                    name=app_name,
                    filename=f"{app_name}.AppImage",
                    install_path=app_path,
                    size_bytes=1000 + i,
                    category="Utility",
                    checksum_sha256=hashlib.sha256(app_name.encode()).hexdigest(),
                )
                db.add_app(app)
                inserted += 1

                # Interleave queries
                _ = db.get_app_by_name(app_name)
                _ = db.count_apps()
                queries += 2

            return inserted, queries

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker_task, tid) for tid in range(num_threads)]
            results = [f.result() for f in futures]

        total_inserted = sum(r[0] for r in results)
        assert total_inserted == total_records
        assert db.count_apps() == total_records

        apps = db.list_apps()
        assert len(apps) == total_records

        # Total disk usage calculation accuracy
        expected_size = sum(1000 + i for _ in range(num_threads) for i in range(ops_per_thread))
        assert db.get_total_disk_usage() == expected_size

    @staticmethod
    def _mp_worker(db_path: str, proc_id: int, count: int) -> int:
        """Standalone worker for multiprocessing tests."""
        from src.models.app import App
        from src.models.database import DatabaseManager
        import hashlib

        db = DatabaseManager(db_path)
        success = 0
        for i in range(count):
            app_name = f"ProcApp_{proc_id}_{i}"
            app = App(
                name=app_name,
                filename=f"{app_name}.AppImage",
                install_path=f"/tmp/proc_apps/{app_name}.AppImage",
                size_bytes=500,
                checksum_sha256=hashlib.sha256(app_name.encode()).hexdigest(),
            )
            db.add_app(app)
            success += 1
        return success

    def test_concurrent_multiprocess_inserts(self, challenger_workspace):
        """
        Challenge: 4 separate OS processes inserting concurrently into the same SQLite database file.
        Validates WAL multi-process concurrency and busy timeout recovery.
        """
        db_file = str(challenger_workspace["db_file"])
        num_procs = 4
        inserts_per_proc = 25
        total_expected = num_procs * inserts_per_proc

        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=num_procs) as pool:
            tasks = [pool.apply_async(self._mp_worker, (db_file, pid, inserts_per_proc)) for pid in range(num_procs)]
            results = [t.get(timeout=15) for t in tasks]

        assert sum(results) == total_expected
        db = challenger_workspace["db"]
        assert db.count_apps() == total_expected

    def test_concurrent_readers_during_active_write_transactions(self, challenger_workspace):
        """
        Challenge: In WAL mode, SQLite readers MUST NOT be blocked by an active writing transaction.
        Tests concurrent reader queries while writer holds a connection and executes writes.
        """
        db = challenger_workspace["db"]

        # Pre-populate some records
        for i in range(20):
            db.add_app(App(
                name=f"PreApp_{i}",
                filename=f"PreApp_{i}.AppImage",
                install_path=f"/tmp/pre_{i}.AppImage",
                size_bytes=100,
                checksum_sha256="abc",
            ))

        reader_results = []
        stop_readers = False

        def reader_loop():
            while not stop_readers:
                apps = db.list_apps()
                assert len(apps) >= 20
                reader_results.append(len(apps))
                time.sleep(0.005)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            reader_futures = [executor.submit(reader_loop) for _ in range(3)]

            # Perform slow batch writes
            for i in range(20, 40):
                db.add_app(App(
                    name=f"BatchApp_{i}",
                    filename=f"BatchApp_{i}.AppImage",
                    install_path=f"/tmp/batch_{i}.AppImage",
                    size_bytes=100,
                    checksum_sha256="def",
                ))
                time.sleep(0.01)

            stop_readers = True
            for f in reader_futures:
                f.result()

        assert len(reader_results) > 10, "Readers should have executed multiple reads concurrently without blocking"
        assert db.count_apps() == 40

    def test_concurrent_duplicate_insertion_collision(self, challenger_workspace):
        """
        Challenge: 10 threads attempt to insert an App with the EXACT same install_path simultaneously.
        Validates that exactly 1 insert succeeds and the other 9 raise sqlite3.IntegrityError cleanly.
        """
        import sqlite3

        db = challenger_workspace["db"]
        app = App(
            name="CollisionApp",
            filename="collision.AppImage",
            install_path="/tmp/apps/collision.AppImage",
            size_bytes=1234,
            checksum_sha256="collision123",
        )

        successes = 0
        integrity_errors = 0

        def try_insert():
            nonlocal successes, integrity_errors
            try:
                db.add_app(app)
                successes += 1
            except sqlite3.IntegrityError:
                integrity_errors += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_insert) for _ in range(10)]
            for f in futures:
                f.result()

        assert successes == 1, f"Expected exactly 1 successful insert, got {successes}"
        assert integrity_errors == 9, f"Expected 9 IntegrityErrors, got {integrity_errors}"
        assert db.count_apps() == 1


# =============================================================================
# Suite 2: Installer Stress (Zero-Byte, Huge, Read-Only, Magic Headers)
# =============================================================================

class TestInstallerStress:
    """Adversarial stress-testing of InstallerService edge cases and corrupt files."""

    def test_installer_zero_byte_file(self, challenger_workspace):
        """
        Challenge: Zero-byte source file (touch empty.AppImage).
        Expected: InvalidAppImageError raised, no leftover files in target dir, no DB records.
        """
        installer = challenger_workspace["installer"]
        db = challenger_workspace["db"]
        empty_file = challenger_workspace["root"] / "empty.AppImage"
        empty_file.touch()

        with pytest.raises(InvalidAppImageError, match="empty \\(0 bytes\\)"):
            installer.install(empty_file)

        assert db.count_apps() == 0
        dest_files = list(challenger_workspace["apps_dir"].glob("*"))
        assert len(dest_files) == 0

    def test_installer_huge_sparse_file(self, challenger_workspace):
        """
        Challenge: Large file (2 GiB sparse file with ELF magic header).
        Validates:
        - Chunked SHA256 calculation does not run out of memory.
        - Exact 2 GiB (2147483648 bytes) recorded in DB without integer overflow.
        - Executable permissions properly applied.
        """
        installer = challenger_workspace["installer"]
        db = challenger_workspace["db"]
        huge_file = challenger_workspace["root"] / "huge_app-x86_64.AppImage"

        two_gib = 2 * 1024 * 1024 * 1024  # 2 GiB
        # Create sparse file: write ELF header at start, seek to 2GB-1, write 1 byte
        with open(huge_file, "wb") as f:
            f.write(b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 44)
            f.seek(two_gib - 1)
            f.write(b"\x00")

        assert huge_file.stat().st_size == two_gib

        app = installer.install(huge_file)
        assert app.size_bytes == two_gib
        assert db.get_total_disk_usage() == two_gib

        installed_path = Path(app.install_path)
        assert installed_path.exists()
        assert installed_path.stat().st_size == two_gib
        # Verify 0o755 permission
        assert installed_path.stat().st_mode & 0o755 == 0o755

    def test_installer_read_only_source_file(self, challenger_workspace):
        """
        Challenge: Source file with mode 0o444 (read-only for all).
        Validates:
        - Source can be read and copied.
        - Installed target is upgraded to 0o755 executable (not left as 0o444).
        """
        installer = challenger_workspace["installer"]
        src = create_dummy_elf_appimage(challenger_workspace["root"] / "readonly.AppImage")
        src.chmod(0o444)

        try:
            app = installer.install(src)
            installed_path = Path(app.install_path)
            assert installed_path.exists()
            # Must be executable (0o755)
            assert os.access(installed_path, os.X_OK)
            assert installed_path.stat().st_mode & 0o111 != 0
        finally:
            # Restore write permission so tmp_path teardown works
            src.chmod(0o644)

    def test_installer_unreadable_source_file(self, challenger_workspace):
        """
        Challenge: Source file with mode 0o000 (no permissions).
        Expected: PermissionDeniedError raised.
        """
        installer = challenger_workspace["installer"]
        src = create_dummy_elf_appimage(challenger_workspace["root"] / "unreadable.AppImage")
        src.chmod(0o000)

        try:
            with pytest.raises(PermissionDeniedError):
                installer.install(src)
        finally:
            src.chmod(0o644)

    @pytest.mark.parametrize(
        "header_bytes,expected_error_match",
        [
            (b"\x7fELF" + b"\x00" * 10, "does not appear to be an AppImage"),     # ELF < 52 bytes
            (b"\x7fELF" + b"\x00" * 47, "does not appear to be an AppImage"),     # 51 bytes (1 byte short)
            (b"MZ\x90\x00" + b"\x00" * 60, "does not appear to be an AppImage"),  # Windows PE binary
            (b"\xca\xfe\xba\xbe" + b"\x00" * 60, "does not appear to be an AppImage"),  # Mach-O / Java
            (b"GARBAGE_BINARY_DATA" * 5, "does not appear to be an AppImage"),    # Arbitrary binary
            (b"This is a plain text file\n", "does not appear to be an AppImage"), # Plain text
        ],
    )
    def test_installer_invalid_magic_headers(self, challenger_workspace, header_bytes, expected_error_match):
        """
        Challenge: Files with corrupted, truncated, or foreign binary magic headers.
        Expected: InvalidAppImageError raised.
        """
        installer = challenger_workspace["installer"]
        bad_file = challenger_workspace["root"] / "bad_magic.AppImage"
        bad_file.write_bytes(header_bytes)

        with pytest.raises(InvalidAppImageError, match=expected_error_match):
            installer.install(bad_file)

    def test_installer_broken_symlink_source(self, challenger_workspace):
        """
        Challenge: Source path is a dangling symlink.
        Expected: AppImageNotFoundError.
        """
        installer = challenger_workspace["installer"]
        dangling_link = challenger_workspace["root"] / "dangling.AppImage"
        dangling_link.symlink_to(challenger_workspace["root"] / "does_not_exist.AppImage")

        with pytest.raises(AppImageNotFoundError):
            installer.install(dangling_link)

    def test_installer_symlink_loop(self, challenger_workspace):
        """
        Challenge: Source path is a circular symlink loop (a -> b -> a).
        Expected: Handled with AppImageNotFoundError or InvalidAppImageError, not unhandled recursion.
        """
        installer = challenger_workspace["installer"]
        link_a = challenger_workspace["root"] / "loop_a.AppImage"
        link_b = challenger_workspace["root"] / "loop_b.AppImage"
        link_a.symlink_to(link_b)
        link_b.symlink_to(link_a)

        with pytest.raises((AppImageNotFoundError, InvalidAppImageError, OSError)):
            installer.install(link_a)


# =============================================================================
# Suite 3: Process Launcher Stress (Permissions, Paths, Complex Quoting)
# =============================================================================

class TestProcessLauncherStress:
    """Adversarial stress-testing of InstallerService.launch process execution."""

    def test_launcher_auto_heals_missing_executable_bit(self, challenger_workspace):
        """
        Challenge: Binary on disk had its executable bit cleared (e.g. chmod 0o644).
        Validates: launcher detects missing X_OK and restores 0o755 before launching.
        """
        installer = challenger_workspace["installer"]
        log_file = challenger_workspace["root"] / "heal_test.log"
        script = create_logging_executable(
            challenger_workspace["apps_dir"] / "healable.AppImage",
            log_file
        )
        # Clear executable bit
        script.chmod(0o644)
        assert not os.access(script, os.X_OK)

        app = App(
            name="HealApp",
            filename="healable.AppImage",
            install_path=str(script),
        )

        proc = installer.launch(app, extra_args=["arg1"])
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0

        # Verify bit was restored
        assert os.access(script, os.X_OK)
        # Verify execution succeeded
        assert log_file.exists()
        assert json.loads(log_file.read_text()) == ["arg1"]

    def test_launcher_nonexistent_binary(self, challenger_workspace):
        """
        Challenge: App model points to a binary path that does not exist.
        Expected: AppImageNotFoundError.
        """
        installer = challenger_workspace["installer"]
        app = App(
            name="GhostApp",
            filename="ghost.AppImage",
            install_path=str(challenger_workspace["apps_dir"] / "ghost.AppImage"),
        )

        with pytest.raises(AppImageNotFoundError, match="not found"):
            installer.launch(app)

    def test_launcher_directory_as_binary(self, challenger_workspace):
        """
        Challenge: App model points to a directory path instead of a file.
        Expected: InvalidAppImageError.
        """
        installer = challenger_workspace["installer"]
        dir_as_app = challenger_workspace["apps_dir"] / "dir_app.AppImage"
        dir_as_app.mkdir(parents=True, exist_ok=True)

        app = App(
            name="DirApp",
            filename="dir_app.AppImage",
            install_path=str(dir_as_app),
        )

        with pytest.raises(InvalidAppImageError, match="not a file"):
            installer.launch(app)

    def test_launcher_exec_format_error(self, challenger_workspace):
        """
        Challenge: Binary has 0o755 permissions but is neither a valid ELF nor a valid script.
        Expected: Kernel raises ENOEXEC, wrapped into ExecutionError by launcher.
        """
        installer = challenger_workspace["installer"]
        corrupt_bin = challenger_workspace["apps_dir"] / "corrupt_exec.AppImage"
        corrupt_bin.write_bytes(b"NOT_A_VALID_ELF_AND_NO_SHEBANG_AT_ALL\n" * 10)
        corrupt_bin.chmod(0o755)

        app = App(
            name="CorruptExecApp",
            filename="corrupt_exec.AppImage",
            install_path=str(corrupt_bin),
        )

        with pytest.raises(ExecutionError, match="Failed to launch"):
            installer.launch(app)

    def test_launcher_complex_argument_quoting_and_spaces(self, challenger_workspace):
        """
        Challenge: Test complex arguments containing spaces, double quotes, single quotes,
        escaped characters, and shell control characters (;, &, $, `, |, etc.).
        Validates:
        - Arguments are parsed via shlex without splitting interior spaces.
        - Shell control characters are passed literally without shell injection.
        """
        installer = challenger_workspace["installer"]
        log_file = challenger_workspace["root"] / "complex_args.log"
        script = create_logging_executable(
            challenger_workspace["apps_dir"] / "arg_tester.AppImage",
            log_file
        )

        # Complex exec_args configured on App
        # Note: --flag "space inside" 'single quoted' --empty ""
        app = App(
            name="ArgApp",
            filename="arg_tester.AppImage",
            install_path=str(script),
            exec_args='--flag "space inside" \'single quoted\' --json "{\\"key\\": \\"val\\"}"',
        )

        # Extra args passed at launch time
        extra_args = [
            "arg with spaces",
            "special;$PATH&`id`|cat",
            "--title=\"Quoted Title\"",
        ]

        proc = installer.launch(app, extra_args=extra_args)
        proc.communicate(timeout=5)
        assert proc.returncode == 0

        assert log_file.exists()
        received_args = json.loads(log_file.read_text())

        expected_args = [
            "--flag",
            "space inside",
            "single quoted",
            "--json",
            '{"key": "val"}',
            "arg with spaces",
            "special;$PATH&`id`|cat",
            '--title="Quoted Title"',
        ]
        assert received_args == expected_args

    def test_launcher_unclosed_quotes_in_exec_args(self, challenger_workspace):
        """
        Challenge: App has malformed exec_args with an unclosed quotation mark.
        Question: Does shlex.split raise ValueError directly or should it be handled?
        We record the empirical behavior.
        """
        installer = challenger_workspace["installer"]
        log_file = challenger_workspace["root"] / "unclosed_args.log"
        script = create_logging_executable(
            challenger_workspace["apps_dir"] / "unclosed.AppImage",
            log_file
        )

        app = App(
            name="UnclosedApp",
            filename="unclosed.AppImage",
            install_path=str(script),
            exec_args="--title 'unclosed quotation mark",
        )

        # shlex.split raises ValueError on unclosed quotation
        with pytest.raises(ValueError, match="No closing quotation"):
            installer.launch(app)

    def test_launcher_binary_path_with_spaces_and_special_chars(self, challenger_workspace):
        """
        Challenge: Binary itself resides in a directory with spaces and parentheses.
        e.g., '/path with spaces (v1)/special app.AppImage'
        Validates: Popen does not split on spaces in the binary path itself.
        """
        installer = challenger_workspace["installer"]
        log_file = challenger_workspace["root"] / "spaced_bin.log"
        folder = challenger_workspace["root"] / "path with spaces (v1)"
        folder.mkdir(parents=True, exist_ok=True)
        script = create_logging_executable(
            folder / "special app [v2].AppImage",
            log_file
        )

        app = App(
            name="SpacedApp",
            filename="special app [v2].AppImage",
            install_path=str(script),
        )

        proc = installer.launch(app, extra_args=["ok"])
        proc.communicate(timeout=5)
        assert proc.returncode == 0

        assert log_file.exists()
        assert json.loads(log_file.read_text()) == ["ok"]

    def test_launcher_massive_argument_count(self, challenger_workspace):
        """
        Challenge: Launching with 500 distinct arguments.
        Validates: Argument handling under volume.
        """
        installer = challenger_workspace["installer"]
        log_file = challenger_workspace["root"] / "massive_args.log"
        script = create_logging_executable(
            challenger_workspace["apps_dir"] / "massive.AppImage",
            log_file
        )

        app = App(
            name="MassiveApp",
            filename="massive.AppImage",
            install_path=str(script),
        )

        large_arg_list = [f"--arg-{i}=value_{i}" for i in range(500)]
        proc = installer.launch(app, extra_args=large_arg_list)
        proc.communicate(timeout=5)
        assert proc.returncode == 0

        assert log_file.exists()
        received = json.loads(log_file.read_text())
        assert len(received) == 500
        assert received == large_arg_list

    def test_concurrent_install_same_process_temp_file_collision(self, challenger_workspace):
        """
        Challenge: Concurrently installing multiple different AppImages with the SAME target filename
        in the same process.
        Since tmp_dest uses os.getpid(), all threads in the same process share the same PID.
        We challenge whether this causes a race condition on tmp_dest.
        """
        installer = challenger_workspace["installer"]
        root = challenger_workspace["root"]

        # Create 5 distinct source AppImages that all want to install as 'race.AppImage'
        # in different subdirectories
        sources = []
        for i in range(5):
            d = root / f"src_{i}"
            src = create_dummy_elf_appimage(d / "race.AppImage", payload=f"payload_{i}_{'X'*100000}".encode())
            sources.append(src)

        results = []
        errors = []

        def do_install(src_file):
            try:
                # With reinstall=True to bypass AppAlreadyInstalledError check
                app = installer.install(src_file, reinstall=True)
                results.append(app)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(do_install, src) for src in sources]
            for f in futures:
                f.result()

        # Check what happened
        print(f"Results: {len(results)}, Errors: {len(errors)}")
        for err in errors:
            print(f"Error encountered: {type(err).__name__}: {err}")

    def test_concurrent_cold_install_same_filename_race(self, challenger_workspace):
        """
        Challenge: Two threads concurrently install the same file from scratch (cold DB).
        Validates whether DB unique constraint or file copy temp name causes race failures.
        """
        installer = challenger_workspace["installer"]
        root = challenger_workspace["root"]

        src1 = create_dummy_elf_appimage(root / "dir1" / "app.AppImage", payload=b"one" * 10000)
        src2 = create_dummy_elf_appimage(root / "dir2" / "app.AppImage", payload=b"two" * 10000)

        results = []
        errors = []

        def do_install(src):
            try:
                app = installer.install(src, reinstall=False)
                results.append(app)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(do_install, src1)
            f2 = executor.submit(do_install, src2)
            f1.result()
            f2.result()

        print(f"Cold install results: {len(results)}, Errors: {[type(e).__name__ for e in errors]}")
        # One must succeed, the other must fail cleanly
        assert len(results) == 1
        assert len(errors) == 1

    def test_unicode_and_emoji_app_metadata(self, challenger_workspace):
        """
        Challenge: UTF-8, non-ASCII, and emoji characters in app name, comments, and path.
        e.g., '🚀 AppImage 測試 & Café (v3.0).AppImage'
        Validates database storage, retrieval, case-insensitive lookup, and disk usage.
        """
        db = challenger_workspace["db"]
        installer = challenger_workspace["installer"]

        app_name = "🚀 AppImage 測試 & Café"
        filename = f"{app_name}.AppImage"
        src = create_dummy_elf_appimage(challenger_workspace["root"] / filename)

        app = installer.install(src, category="Graphics")
        assert app.id is not None
        assert app.name == app_name

        fetched = db.get_app_by_name(app_name)
        assert fetched is not None
        assert fetched.id == app.id
        assert fetched.name == app_name

        # Case-insensitive / normalized search
        listed = db.list_apps(query="café")
        assert len(listed) == 1
        assert listed[0].id == app.id

        listed_emoji = db.list_apps(query="🚀")
        assert len(listed_emoji) == 1
        assert listed_emoji[0].id == app.id
