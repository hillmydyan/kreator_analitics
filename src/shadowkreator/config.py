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

# Default local data directory (PRD Section 8.3: stored locally, no network exposure)
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "shadowkreator.db"
DEFAULT_EXPORT_DIR = DATA_DIR / "exports"


def find_adb_binary(custom_path: Optional[str] = None) -> str:
    """Locate the ADB executable on Windows or macOS."""
    if custom_path and Path(custom_path).is_file():
        return custom_path

    env_adb = os.environ.get("ADB_PATH")
    if env_adb and Path(env_adb).is_file():
        return env_adb

    system = platform.system()
    adb_exe_name = "adb.exe" if system == "Windows" else "adb"
    for bundled_adb in (
        PROJECT_ROOT / "bin" / "platform-tools" / adb_exe_name,
        PROJECT_ROOT / "_internal" / "bin" / "platform-tools" / adb_exe_name,
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
