"""Application configuration and constants matching PRD specifications."""

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "TikTok Creator Product Analytics Scraper"
APP_VERSION = "1.0.0"

# Target Android requirements (PRD Section 2.2)
MIN_ANDROID_VERSION = 12
MIN_ANDROID_SDK_INT = 31

# Supported analysis periods (PRD Section 3.2)
SUPPORTED_PERIOD_DAYS = (7, 30)

# Default local data directory (stored locally next to the executable or project root)
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    BUNDLE_ROOT = PROJECT_ROOT
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "shadowkreator.db"
DEFAULT_EXPORT_DIR = DATA_DIR / "exports"


def _extract_onefile_windows_adb(bundle_pt_dir: Path) -> Optional[Path]:
    """
    When running as a single-file (--onefile) .exe on Windows, copy bundled adb.exe
    and its DLLs from the temporary _MEIPASS directory into a persistent LocalAppData
    cache directory so the running ADB daemon never locks _MEIPASS on app exit.
    """
    try:
        base_cache = Path(
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
        target_dir = base_cache / "ShadowKreator" / "platform-tools"
        target_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            src_file = bundle_pt_dir / fname
            dst_file = target_dir / fname
            if src_file.is_file():
                if (
                    not dst_file.is_file()
                    or dst_file.stat().st_size != src_file.stat().st_size
                ):
                    shutil.copy2(src_file, dst_file)
        target_adb = target_dir / "adb.exe"
        if target_adb.is_file():
            return target_adb
    except OSError:
        pass
    fallback = bundle_pt_dir / "adb.exe"
    return fallback if fallback.is_file() else None


def find_adb_binary(custom_path: Optional[str] = None) -> str:
    """Locate the ADB executable on Windows or macOS."""
    if custom_path and Path(custom_path).is_file():
        return custom_path

    env_adb = os.environ.get("ADB_PATH")
    if env_adb and Path(env_adb).is_file():
        return env_adb

    system = platform.system()
    adb_exe_name = "adb.exe" if system == "Windows" else "adb"

    if system == "Windows" and BUNDLE_ROOT != PROJECT_ROOT:
        meipass_pt = BUNDLE_ROOT / "bin" / "platform-tools"
        if (meipass_pt / "adb.exe").is_file():
            extracted = _extract_onefile_windows_adb(meipass_pt)
            if extracted is not None:
                return str(extracted)

    for bundled_adb in (
        PROJECT_ROOT / "bin" / "platform-tools" / adb_exe_name,
        PROJECT_ROOT / "_internal" / "bin" / "platform-tools" / adb_exe_name,
        BUNDLE_ROOT / "bin" / "platform-tools" / adb_exe_name,
    ):
        if bundled_adb.is_file():
            return str(bundled_adb)

    which_adb = shutil.which("adb")
    if which_adb:
        return which_adb

    candidates = []
    home = Path.home()
    if system == "Windows":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        candidates.extend([
            local_app_data / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        ])
    elif system == "Darwin":
        candidates.extend([
            PROJECT_ROOT / "bin" / "platform-tools" / "adb",
            Path("/opt/homebrew/bin/adb"),
            Path("/usr/local/bin/adb"),
            home / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
        ])
    else:
        candidates.extend([
            home / "Android" / "Sdk" / "platform-tools" / "adb",
            Path("/usr/bin/adb"),
        ])

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return "adb"
