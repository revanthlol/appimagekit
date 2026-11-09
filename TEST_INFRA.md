# AppImageKit — Test Infrastructure & E2E Test Suite Specification

**Document Version:** 1.0  
**Date:** 2026-11-09  
**Status:** ESTABLISHED & ACTIVE  
**Reference Documents:** `ORIGINAL_REQUEST.md`, `PROJECT.md`, `PLAN.md`, freedesktop.org Desktop Entry v1.5 Specification  

---

## 1. Overview & Test Philosophy

AppImageKit is a native Linux application manager combining low-level binary inspection, filesystem operations, freedesktop desktop integration, multi-tier network updating, and a GTK4/Libadwaita interface with a Click CLI.

Testing native Linux desktop software often poses serious challenges in automated and containerized environments:
1. **FUSE Dependency:** Real Type 2 AppImages require `libfuse2`/`libfuse3` and `/dev/fuse`, which are typically blocked in unprivileged Docker containers or CI runners.
2. **Display Server Dependency:** GTK4/Libadwaita widgets require Wayland or X11 compositor sockets, failing in headless environments unless properly isolated.
3. **Network Reliability:** Real network calls to GitHub or external servers introduce flakiness, rate limiting, and network latency.
4. **Host Contamination:** Tests modifying `~/.local/share/appimages/` or `~/.local/share/applications/` risk modifying the developer's actual desktop environment.

### Core Architectural Solutions:
- **Decoupled Business Logic:** All domain models (`App`), SQLite database operations (`DatabaseManager`), service classes (`InstallerService`, `DesktopEntryService`, `UpdaterService`, etc.), and CLI commands (`cli.py`) have **zero GTK/GObject dependency**. They are 100% testable via standard headless `pytest`.
- **Lightweight Mock AppImages:** Instead of multi-megabyte binary blobs requiring loop devices or FUSE, AppImageKit uses executable shell script mock AppImages that faithfully simulate the standard AppImage runtime flags (`--appimage-extract`, `--appimage-updateinformation`, `--appimage-version`, `--appimage-offset`) in < 2ms without root.
- **Real Squashfs Mock Generation:** For tests explicitly auditing ELF headers or Squashfs payload extraction fallbacks (`unsquashfs`), test utilities assemble lightweight ELF+Squashfs packages using `/usr/bin/mksquashfs`.
- **Hermetic Mock HTTP Server:** A dedicated multi-threaded local HTTP server simulates all update endpoints (GitHub Releases API v3, zsync byte-range requests, direct HTTP HEAD/GET, and AppImage Hub feed).
- **Strict XDG Isolation:** Every test runs inside a pristine temporary directory hierarchy isolating `$XDG_DATA_HOME`, `$XDG_CONFIG_HOME`, `$XDG_CACHE_HOME`, and `$XDG_STATE_HOME`.

---

## 2. Directory Layout

```
appimagekit/
├── TEST_INFRA.md            # This specification document
├── TEST_READY.md            # Certification of test suite readiness
├── pyproject.toml           # Package configuration & pytest settings
└── tests/
    ├── conftest.py          # Universal test fixtures, mock servers & generators
    ├── test_e2e.py          # Systematic 4-tier requirement-driven E2E test suite
    ├── test_database.py     # DatabaseManager unit tests
    ├── test_installer.py    # InstallerService unit tests
    ├── test_desktop_entry.py# DesktopEntryService unit tests
    ├── test_icon_extractor.py# IconExtractorService unit tests
    ├── test_updater.py      # UpdaterService unit tests
    ├── test_services.py     # Auxiliary services (backup, hub, autostart, sandbox)
    ├── test_cli.py          # Click CLI suite tests
    └── test_ui_headless.py  # GTK4 controller & headless UI tests
```

---

## 3. Test Fixture Architecture (`tests/conftest.py`)

### 3.1 Mock AppImage Generators

#### 1. `create_mock_appimage(path, name, version, category, icon_svg, update_info, exec_output)`
Generates an executable bash script conforming to the standard AppImage CLI contract:
- `--appimage-extract [<pattern>]`:
  Extracts `squashfs-root/.DirIcon` (SVG/PNG) and `squashfs-root/<name>.desktop`.
  Supports pattern filtering.
- `--appimage-updateinformation` / `--appimage-updateinfo`:
  Emits the embedded update string (e.g. `zsync|http://...` or `gh-releases-zsync|owner|repo|latest|*.AppImage.zsync`).
- `--appimage-version`:
  Emits the version string (e.g. `1.0.0`).
- `--appimage-offset`:
  Emits `1024`.
- `--appimage-help`:
  Emits runtime help.
- Normal invocation:
  Outputs execution details with arguments and exits with code 0.
- Sets file mode to `0o755` (`chmod +x`).

#### 2. `create_type2_mock_appimage(path, name, version)`
Generates an authentic Type 2 binary AppImage with a 128-byte ELF header containing magic bytes `\x7fELF` at offset 0, `\x41\x49\x02` at offset 8, followed by a valid SquashFS superblock created via `mksquashfs`.

#### 3. `create_corrupt_appimage(path, corruption_type)`
Generates deliberate boundary test files:
- `"empty"`: 0-byte file.
- `"bad_magic"`: Non-ELF random garbage data.
- `"truncated_elf"`: 4-byte `\x7fELF` without AppImage magic bytes.
- `"non_executable"`: Valid mock AppImage script with `0o644` permissions.

### 3.2 Local Mock HTTP Server Fixture (`mock_http_server`)
A background HTTP server running on `127.0.0.1` providing:
- `/repos/{owner}/{repo}/releases/latest`: Returns valid GitHub Releases API JSON.
- `/feed.json`: Returns AppImage Hub catalog JSON feed.
- `/downloads/{filename}`: Serves downloadable mock AppImages with `Content-Length`, `Last-Modified`, and `ETag`. Supports `HEAD` and `GET`.
- `/zsync/{filename}`: Serves `.zsync` control headers and responds to `Range: bytes=start-end` requests.

### 3.3 Isolated XDG Workspace Fixture (`temp_workspace`)
Provides a sandboxed filesystem layout per test:
- `apps_dir`: Isolated user storage (`$tmp_path/appimages`)
- `system_apps_dir`: Isolated system storage (`$tmp_path/system_appimages`)
- `desktop_dir`: Isolated desktop entries (`$tmp_path/applications`)
- `icons_dir`: Isolated hicolor icon theme (`$tmp_path/icons`)
- `autostart_dir`: Isolated autostart entries (`$tmp_path/autostart`)
- `cache_dir`: Isolated cache downloads (`$tmp_path/cache`)
- `db_path`: Isolated SQLite registry (`$tmp_path/registry.db`)
Monkeypatches `src.utils.paths.Paths` to point to these directories for 100% host isolation.

### 3.4 Click CLI Runner Fixture (`cli_runner`)
Provides `click.testing.CliRunner(mix_stderr=False)` configured to run CLI commands within the isolated temporary workspace.

---

## 4. The 4-Tier E2E Test Suite Matrix

The primary E2E test suite (`tests/test_e2e.py`) is organized into 4 systematic tiers:

| Tier | Category | Minimum Cases | Focus & Scope |
|---|---|---|---|
| **Tier 1** | Feature Coverage | >= 25 cases (>=5 per R1–R5) | Full primary behavior (happy path) verification of each requirement R1 through R5. |
| **Tier 2** | Boundary & Corner Cases | >= 8 cases | Empty files, corrupt headers, permission denied fallbacks, missing update info, tamper detection. |
| **Tier 3** | Cross-Feature Combinations | >= 2 complex pipelines | Multi-step integration chains: install -> edit desktop -> in-place update -> launch -> verify -> backup -> uninstall. |
| **Tier 4** | Real-World Scenarios | >= 2 end-to-end stories | Multi-app lifecycles across diverse categories, concurrent disk usage tracking, special characters in app names. |

### Requirement Mapping Table:
- **R1: AppImage Storage & Dual-Mode Installation**
  - User mode storage directory creation & binary copying.
  - Automatic fallback to user mode when system permissions are unavailable.
  - Executable flag (`chmod +x` / `0o755`) enforcement.
  - SHA256 checksum and file size calculation.
  - SQLite registry persistence and WAL mode integrity.
  - CLI `appimagekit install` command verification.
- **R2: Desktop Entry & Icon Management**
  - Icon extraction (`.DirIcon` SVG/PNG) into standard hicolor theme.
  - Freedesktop.org Desktop Entry v1.5 generation (`Type=Application`, valid `Exec=`, `Name=`, `Categories=`).
  - Argument quoting, escaping, and field code (`%U`) handling.
  - In-app desktop entry editor service (customizing Name, Comment, Exec args, Categories).
  - Clean uninstallation purge (removing binary, desktop file, icon, autostart, and DB row).
  - CLI `appimagekit uninstall` command verification.
- **R3: Multi-Tier Cascading Update Engine**
  - Tier 1: Embedded zsync delta update detection & range download.
  - Tier 2: GitHub Releases API querying and asset matching.
  - Tier 3: Direct manual URL checking via HTTP HEAD (`Last-Modified`/`ETag`).
  - Tier 4: User addition — manual in-place local `.AppImage` update (`--file`), atomic binary replacement, metadata update, and preservation of user desktop customizations.
  - Batch update ("Update All") check.
  - CLI `appimagekit update` and `appimagekit update <app> --file <path>`.
- **R4: GTK4 / Libadwaita User Interface Contracts (Headless)**
  - Model/controller decoupling: presentation operations execute headlessly.
  - Grid view category filtering (All, Development, Media, Games, Utility).
  - Sidebar disk usage calculation and summary.
  - App detail view state management (Launch, Update, Autostart, Sandbox).
  - Drag-and-drop file ingestion validation and dispatch contract.
- **R5: Auxiliary Services & CLI**
  - Autostart toggle (`~/.config/autostart/<app>.desktop`).
  - Sandbox detection (Bubblewrap `bwrap` and Firejail inspection).
  - Backup export and import of application list (JSON manifest).
  - AppImage Hub catalog search and installation.
  - Integrity verification service (tamper detection via SHA256).
  - Complete CLI suite (`install`, `list`, `launch`, `update`, `uninstall`, `info`, `verify`, `backup`).

---

## 5. Execution Commands & Verification

### Running the Complete E2E Suite:
```bash
pytest tests/test_e2e.py -v
```

### Running Specific Tiers by Marker:
```bash
pytest tests/test_e2e.py -m tier1 -v
pytest tests/test_e2e.py -m tier2 -v
pytest tests/test_e2e.py -m tier3 -v
pytest tests/test_e2e.py -m tier4 -v
```

### Running Test Harness & Fixture Self-Check:
```bash
pytest tests/test_e2e.py -k "test_harness" -v
```

### Headless GTK4 UI Test Execution (When Running GTK UI Tests):
```bash
xvfb-run -a pytest tests/test_ui_headless.py -v
```

---

## 6. Implementation Handoff & Milestone Progression

This test infrastructure establishes the authoritative contract for implementation milestones M1 through M5.
- Milestone implementers must execute `pytest tests/test_e2e.py` to verify their services satisfy these contracts.
- In Milestone M6, all tests across Tiers 1–4 must achieve 100% pass rate before final delivery.
