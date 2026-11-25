"""PyInstaller packaging script for Windows & macOS desktop builds."""

import os
import plistlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

WINDOWS_PLATFORM_TOOLS_URL = (
    "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
)


def _ensure_windows_adb(root: Path) -> None:
    """Download official Windows Android Platform-Tools (adb.exe) when building on Windows."""
    win_adb = root / "bin" / "platform-tools" / "adb.exe"
    if win_adb.is_file():
        return
    print("Downloading official Windows Android Platform-Tools (adb.exe)...")
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    zip_path = bin_dir / "platform-tools-windows.zip"
    urllib.request.urlretrieve(WINDOWS_PLATFORM_TOOLS_URL, str(zip_path))
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(str(bin_dir))
    try:
        zip_path.unlink()
    except OSError:
        pass
    print(f"Extracted Windows platform-tools to {bin_dir / 'platform-tools'}")


def build() -> int:
    root = Path(__file__).resolve().parent
    os.environ.setdefault(
        "PYINSTALLER_CONFIG_DIR", str(root / "build" / ".pyinstaller_cache")
    )
    if sys.platform == "win32":
        _ensure_windows_adb(root)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "TikTokCreatorAnalytics",
        "--paths",
        str(root / "src"),
    ]

    # On Windows, build a single self-contained portable TikTokCreatorAnalytics.exe (--onefile)
    # with adb.exe + DLLs embedded directly inside the single .exe file.
    if sys.platform == "win32":
        cmd.append("--onefile")
        pt_dir = root / "bin" / "platform-tools"
        for fname in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            fpath = pt_dir / fname
            if fpath.is_file():
                cmd.extend(["--add-binary", f"{fpath};bin/platform-tools"])

    cmd.append(str(root / "run_app.py"))
    print("Running:", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        return rc

    # Bundle local platform-tools (adb / adb.exe) into dist folder if present
    src_pt = root / "bin" / "platform-tools"
    dist_dir = root / "dist" / "TikTokCreatorAnalytics"
    if src_pt.is_dir() and dist_dir.is_dir():
        dst_pt = dist_dir / "bin" / "platform-tools"
        if dst_pt.exists():
            shutil.rmtree(dst_pt)
        shutil.copytree(src_pt, dst_pt)
        print(f"Bundled platform-tools into {dst_pt}")

    # Bundle tutorial poster & docs into dist folder
    src_docs = root / "docs"
    if src_docs.is_dir() and dist_dir.is_dir():
        dst_docs = dist_dir / "docs"
        dst_docs.mkdir(parents=True, exist_ok=True)
        for doc_name in (
            "Poster_Tutorial_ShadowKreator.png",
            "Poster_Tutorial_ShadowKreator.pdf",
        ):
            src_doc = src_docs / doc_name
            if src_doc.is_file():
                shutil.copy2(src_doc, dst_docs / doc_name)

    # Ensure data & exports folder exists inside dist
    if dist_dir.is_dir():
        (dist_dir / "data" / "exports").mkdir(parents=True, exist_ok=True)

    # On macOS 15+/26+, inject Local Network & Bonjour keys into Info.plist
    info_plist = root / "dist" / "TikTokCreatorAnalytics.app" / "Contents" / "Info.plist"
    if sys.platform == "darwin" and info_plist.is_file():
        with info_plist.open("rb") as fp:
            plist_data = plistlib.load(fp)
        plist_data["NSLocalNetworkUsageDescription"] = (
            "Aplikasi ini membutuhkan akses jaringan lokal (Wi-Fi) untuk menemukan dan terhubung "
            "ke perangkat Android melalui ADB Wireless Debugging."
        )
        plist_data["NSBonjourServices"] = [
            "_adb-tls-connect._tcp",
            "_adb-tls-pairing._tcp",
            "_adb._tcp",
        ]
        with info_plist.open("wb") as fp:
            plistlib.dump(plist_data, fp)
        subprocess.call(
            ["codesign", "--force", "--deep", "--sign", "-", str(info_plist.parents[1])]
        )
        print("Updated macOS Info.plist with NSLocalNetworkUsageDescription & NSBonjourServices")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
