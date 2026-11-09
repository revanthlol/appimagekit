"""
conftest.py — Universal pytest fixtures and test harness for AppImageKit.

Provides:
- Lightweight executable mock AppImage generator (create_mock_appimage)
- Type 2 binary mock AppImage generator (create_type2_mock_appimage)
- Corrupt and boundary AppImage generator (create_corrupt_appimage)
- Local mock HTTP server fixture for update tiers and AppImage Hub feed
- Clean isolated temporary XDG workspaces fixture
- Click CliRunner fixture
"""

import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, Optional

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# 1. Mock AppImage Generators
# ---------------------------------------------------------------------------

SAMPLE_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="30" fill="#3584e4"/>
  <polygon points="24,18 46,32 24,46" fill="#ffffff"/>
</svg>"""


def create_mock_appimage(
    path: Path,
    name: str = "TestApp",
    version: str = "1.0.0",
    category: str = "Utility",
    icon_svg: Optional[str] = None,
    update_info: Optional[str] = None,
    exec_output: Optional[str] = None,
) -> Path:
    """
    Creates a lightweight, standalone, executable mock AppImage script.
    
    Faithfully simulates AppImage runtime CLI flags without requiring
    FUSE, root permissions, or multi-megabyte binary downloads:
      - --appimage-extract: creates squashfs-root with .DirIcon and desktop entry
      - --appimage-updateinformation / --appimage-updateinfo: emits update string
      - --appimage-version: emits version string
      - --appimage-offset: emits 1024
      - --appimage-help: emits help text
      - normal invocation: runs mock app and prints execution message
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    icon_content = icon_svg if icon_svg is not None else SAMPLE_ICON_SVG
    update_str = update_info if update_info is not None else ""
    normal_output = exec_output if exec_output is not None else f"Running {name} version {version} args: $@"

    script_content = f"""#!/bin/bash
# Mock AppImage runtime for {name}
set -e

if [ "$1" = "--appimage-extract" ]; then
    TARGET_DIR="squashfs-root"
    mkdir -p "$TARGET_DIR"
    cat << 'EOF_ICON' > "$TARGET_DIR/.DirIcon"
{icon_content}
EOF_ICON
    cat << 'EOF_DESKTOP' > "$TARGET_DIR/{name}.desktop"
[Desktop Entry]
Type=Application
Name={name}
Exec={name} %U
Icon=.DirIcon
Categories={category};
Comment=Mock {name} AppImage for testing
Terminal=false
StartupWMClass={name}
EOF_DESKTOP
    cat << 'EOF_APPRUN' > "$TARGET_DIR/AppRun"
#!/bin/bash
echo "{normal_output}"
exit 0
EOF_APPRUN
    chmod +x "$TARGET_DIR/AppRun"
    echo "Extracted squashfs-root for {name}"
    exit 0
elif [ "$1" = "--appimage-updateinformation" ] || [ "$1" = "--appimage-updateinfo" ]; then
    if [ -n "{update_str}" ]; then
        echo "{update_str}"
    fi
    exit 0
elif [ "$1" = "--appimage-version" ]; then
    echo "{version}"
    exit 0
elif [ "$1" = "--appimage-offset" ]; then
    echo "1024"
    exit 0
elif [ "$1" = "--appimage-help" ]; then
    echo "AppImage mock runtime"
    echo "Options: --appimage-extract, --appimage-updateinformation, --appimage-version, --appimage-offset"
    exit 0
fi

# Normal execution
echo "{normal_output}"
exit 0
"""
    path.write_text(script_content, encoding="utf-8")
    path.chmod(0o755)
    return path


def create_type2_mock_appimage(
    path: Path,
    name: str = "BinaryApp",
    version: str = "1.0.0",
    category: str = "Development",
    icon_svg: Optional[str] = None
) -> Path:
    """
    Creates an authentic Type 2 binary AppImage with ELF magic bytes
    and Squashfs payload. If mksquashfs is available, builds a real squashfs.
    Otherwise constructs a binary with ELF header and squashfs superblock magic.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    icon_content = icon_svg if icon_svg is not None else SAMPLE_ICON_SVG

    # 128-byte ELF header with Type 2 magic bytes
    # e_ident[0..3] = \x7fELF
    # e_ident[8..10] = AI\x02 (0x41 0x49 0x02)
    elf_header = bytearray(128)
    elf_header[0:4] = b"\x7fELF"
    elf_header[4] = 2    # 64-bit
    elf_header[5] = 1    # little-endian
    elf_header[6] = 1    # original version
    elf_header[8:11] = b"\x41\x49\x02"

    mksquashfs_bin = shutil.which("mksquashfs")
    if mksquashfs_bin:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "squashfs-root"
            root.mkdir(parents=True, exist_ok=True)
            (root / ".DirIcon").write_text(icon_content, encoding="utf-8")
            (root / f"{name}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName={name}\nExec={name} %U\nIcon=.DirIcon\nCategories={category};\n",
                encoding="utf-8"
            )
            app_run = root / "AppRun"
            app_run.write_text(f"#!/bin/sh\necho 'Running {name} {version}'\nexit 0\n", encoding="utf-8")
            app_run.chmod(0o755)

            sqfs_path = Path(tmpdir) / "app.sqfs"
            cmd = [mksquashfs_bin, str(root), str(sqfs_path), "-noappend", "-all-root"]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            with open(path, "wb") as f:
                f.write(elf_header)
                f.write(sqfs_path.read_bytes())
    else:
        # Fallback: write ELF header + synthetic squashfs superblock (hsqs magic: 0x73717368)
        sqfs_synthetic = bytearray(512)
        sqfs_synthetic[0:4] = b"hsqs"
        with open(path, "wb") as f:
            f.write(elf_header)
            f.write(sqfs_synthetic)

    path.chmod(0o755)
    return path


def create_corrupt_appimage(path: Path, corruption_type: str = "empty") -> Path:
    """
    Creates an invalid or boundary AppImage file for negative testing:
    - 'empty': 0-byte file
    - 'bad_magic': Random bytes without ELF header
    - 'truncated_elf': 4 bytes of \x7fELF only
    - 'non_executable': Valid mock AppImage but chmod 0o644 (not executable)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if corruption_type == "empty":
        path.write_bytes(b"")
        path.chmod(0o755)
    elif corruption_type == "bad_magic":
        path.write_bytes(b"CORRUPT_NOT_AN_APPIMAGE_DATA_1234567890\x00\xff\xaa\x55")
        path.chmod(0o755)
    elif corruption_type == "truncated_elf":
        path.write_bytes(b"\x7fELF")
        path.chmod(0o755)
    elif corruption_type == "non_executable":
        create_mock_appimage(path, name="NonExecApp")
        path.chmod(0o644)
    else:
        raise ValueError(f"Unknown corruption_type: {corruption_type}")

    return path


# ---------------------------------------------------------------------------
# 2. Local Mock HTTP Server for Update Tiers & Hub Feed
# ---------------------------------------------------------------------------

class MockUpdateServerHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP handler serving:
    - GitHub Releases API endpoints (/repos/{owner}/{repo}/releases/latest)
    - Direct download files with Content-Length, Last-Modified, ETag (/downloads/...)
    - zsync delta files and Range requests (/zsync/...)
    - AppImage Hub catalog JSON feed (/feed.json)
    """

    def log_message(self, format, *args):
        # Silence HTTP access logs during test runs
        pass

    def do_HEAD(self):
        if self.path.startswith("/downloads/"):
            # Direct URL check simulation
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "2048")
            self.send_header("Last-Modified", "Mon, 09 Nov 2026 12:00:00 GMT")
            self.send_header("ETag", '"mock-etag-v2.0.0"')
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        # 1. GitHub Releases API endpoint
        if "/releases/latest" in self.path:
            port = self.server.server_port
            parts = [p for p in self.path.split("/") if p]
            repo_name = parts[2] if len(parts) >= 3 else "testapp"

            response_data = {
                "tag_name": "v2.0.0",
                "name": f"{repo_name} Release 2.0.0",
                "published_at": "2026-11-09T12:00:00Z",
                "body": "Bugfixes and new features in v2.0.0",
                "assets": [
                    {
                        "name": f"{repo_name}-2.0.0-x86_64.AppImage",
                        "size": 2048,
                        "browser_download_url": f"http://127.0.0.1:{port}/downloads/{repo_name}-2.0.0-x86_64.AppImage"
                    },
                    {
                        "name": f"{repo_name}-2.0.0-x86_64.AppImage.zsync",
                        "size": 512,
                        "browser_download_url": f"http://127.0.0.1:{port}/zsync/{repo_name}-2.0.0-x86_64.AppImage.zsync"
                    }
                ]
            }
            payload = json.dumps(response_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.github+json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        # 2. AppImage Hub feed endpoint
        elif self.path == "/feed.json":
            port = self.server.server_port
            feed_data = {
                "version": 1,
                "items": [
                    {
                        "name": "Krita",
                        "description": "Digital painting and illustration application",
                        "categories": ["Graphics"],
                        "authors": [{"name": "Krita Foundation"}],
                        "license": "GPL-3.0",
                        "links": [
                            {
                                "type": "Download",
                                "url": f"http://127.0.0.1:{port}/downloads/Krita-5.2.0-x86_64.AppImage"
                            }
                        ]
                    },
                    {
                        "name": "VLC",
                        "description": "Multi-format media player and streaming server",
                        "categories": ["AudioVideo"],
                        "authors": [{"name": "VideoLAN"}],
                        "license": "GPL-2.0",
                        "links": [
                            {
                                "type": "Download",
                                "url": f"http://127.0.0.1:{port}/downloads/VLC-3.0.20-x86_64.AppImage"
                            }
                        ]
                    }
                ]
            }
            payload = json.dumps(feed_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        # 3. Downloads endpoint (serving mock AppImage payload)
        elif self.path.startswith("/downloads/"):
            filename = self.path.split("/")[-1]
            app_name = filename.split("-")[0] if "-" in filename else "DownloadedApp"
            version = "2.0.0"

            # Create an in-memory mock AppImage payload
            mock_script = f"""#!/bin/bash
if [ "$1" = "--appimage-extract" ]; then
    mkdir -p squashfs-root
    cat << 'EOF' > squashfs-root/.DirIcon
{SAMPLE_ICON_SVG}
EOF
    cat << 'EOF' > squashfs-root/{app_name}.desktop
[Desktop Entry]
Type=Application
Name={app_name}
Exec={app_name} %U
Icon=.DirIcon
Categories=Utility;
Comment=Downloaded {app_name} from mock server
Terminal=false
StartupWMClass={app_name}
EOF
    exit 0
elif [ "$1" = "--appimage-version" ]; then
    echo "{version}"
    exit 0
fi
echo "Running {app_name} {version}"
exit 0
"""
            payload = mock_script.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Last-Modified", "Mon, 09 Nov 2026 12:00:00 GMT")
            self.send_header("ETag", '"mock-etag-v2.0.0"')
            self.end_headers()
            self.wfile.write(payload)

        # 4. zsync endpoint supporting byte-range requests
        elif self.path.startswith("/zsync/"):
            content = b"zsync-control-file-mock-header\nLength: 2048\nBlocksize: 1024\n"
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                # Simulated partial content
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes 0-100/{len(content)}")
                self.send_header("Content-Length", "101")
                self.end_headers()
                self.wfile.write(content[:101])
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        else:
            self.send_response(404)
            self.end_headers()


class ThreadedMockServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture(scope="session")
def mock_http_server():
    """
    Session-scoped local HTTP server fixture.
    Runs on an ephemeral port on 127.0.0.1 in a daemon thread.
    Yields the base URL string: 'http://127.0.0.1:<port>'.
    """
    server = ThreadedMockServer(("127.0.0.1", 0), MockUpdateServerHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# 3. Clean Isolated Temporary XDG Workspace Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_workspace(tmp_path, monkeypatch):
    """
    Provides a completely isolated XDG directory structure for test executions:
    - apps_dir: ~/.local/share/appimages
    - system_apps_dir: /opt/appimages (simulated)
    - desktop_dir: ~/.local/share/applications
    - icons_dir: ~/.local/share/icons/hicolor
    - autostart_dir: ~/.config/autostart
    - cache_dir: ~/.cache/appimagekit
    - db_path: ~/.local/share/appimagekit/registry.db

    Configures environment variables and monkeypatches Paths if available.
    """
    share_dir = tmp_path / "share"
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    opt_dir = tmp_path / "opt"

    apps_dir = share_dir / "appimages"
    system_apps_dir = opt_dir / "appimages"
    desktop_dir = share_dir / "applications"
    icons_dir = share_dir / "icons" / "hicolor"
    autostart_dir = config_dir / "autostart"
    appimagekit_data = share_dir / "appimagekit"
    db_path = appimagekit_data / "registry.db"

    for d in [apps_dir, system_apps_dir, desktop_dir, icons_dir, autostart_dir, cache_dir, appimagekit_data]:
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_DATA_HOME", str(share_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))

    # Monkeypatch Paths class in src.utils.paths if src is present
    try:
        from src.utils.paths import Paths
        monkeypatch.setattr(Paths, "get_user_appimages_dir", staticmethod(lambda: apps_dir))
        monkeypatch.setattr(Paths, "get_system_appimages_dir", staticmethod(lambda: system_apps_dir))
        monkeypatch.setattr(Paths, "get_database_path", staticmethod(lambda: db_path))
        monkeypatch.setattr(Paths, "get_user_applications_dir", staticmethod(lambda: desktop_dir))
        monkeypatch.setattr(Paths, "get_system_applications_dir", staticmethod(lambda: desktop_dir))
        monkeypatch.setattr(Paths, "get_icons_dir", staticmethod(lambda: icons_dir))
        monkeypatch.setattr(Paths, "get_autostart_dir", staticmethod(lambda: autostart_dir))
        monkeypatch.setattr(Paths, "get_cache_dir", staticmethod(lambda: cache_dir))
    except (ImportError, AttributeError):
        pass

    workspace = {
        "root": tmp_path,
        "apps_dir": apps_dir,
        "system_apps_dir": system_apps_dir,
        "desktop_dir": desktop_dir,
        "icons_dir": icons_dir,
        "autostart_dir": autostart_dir,
        "cache_dir": cache_dir,
        "app_data_dir": appimagekit_data,
        "db_path": db_path,
    }
    return workspace


# ---------------------------------------------------------------------------
# 4. Click CLI Runner Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_runner():
    """Provides an isolated Click CliRunner for testing CLI commands."""
    return CliRunner()


# ---------------------------------------------------------------------------
# 5. Common Reusable Mock AppImage Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_mock_appimage(tmp_path):
    """Provides a basic mock AppImage file."""
    target = tmp_path / "incoming" / "SampleApp-1.0.0-x86_64.AppImage"
    return create_mock_appimage(target, name="SampleApp", version="1.0.0", category="Utility")


@pytest.fixture
def sample_updatable_appimage(tmp_path):
    """Provides a mock AppImage with embedded update information."""
    target = tmp_path / "incoming" / "UpdatableApp-1.0.0-x86_64.AppImage"
    return create_mock_appimage(
        target,
        name="UpdatableApp",
        version="1.0.0",
        category="Development",
        update_info="gh-releases-zsync|owner|updatableapp|latest|UpdatableApp-*.AppImage.zsync"
    )
