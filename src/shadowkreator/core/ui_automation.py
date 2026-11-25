"""
UI Automation Engine (Tahap 2 PoC Android UI & PRD Section 2.4 #4).

Provides three interchangeable drivers:
1. AdbUiDriver: Zero-setup Android UI Hierarchy reader via `adb shell uiautomator dump`
   plus ADB intent/input controls and screenshot capture for OCR fallback.
2. AppiumUiDriver: Appium + UiAutomator2 WebDriver integration (PRD Section 2.3).
3. MockUiDriver: Deterministic simulator matching PRD Section 10 initial test target
   (1 creator, 5 videos, single & multi-product yellow baskets, and missing-link edge case).
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Set, Tuple

from shadowkreator.core.device_manager import AdbCommandResult, CommandRunner, default_command_runner
from shadowkreator.core.extractor import is_within_period, parse_upload_date, parse_views_count

# Common TikTok package names (Global, Asia/Indonesia, & Lite)
TIKTOK_PACKAGES = (
    "com.ss.android.ugc.trill",      # TikTok Asia / Indonesia
    "com.zhiliaoapp.musically",      # TikTok Global
    "com.zhiliaoapp.musically.go",   # TikTok Lite
)

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


@dataclass
class UiNode:
    """Represents a node in the Android Accessibility / UI Hierarchy."""

    text: str = ""
    resource_id: str = ""
    class_name: str = ""
    content_desc: str = ""
    package: str = ""
    clickable: bool = False
    bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def center(self) -> Tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def label(self) -> str:
        """Combined human-readable text or content-desc."""
        return (self.text or self.content_desc or "").strip()


@dataclass
class RawProductItem:
    """Raw product information read from the yellow basket UI before DB grouping."""

    product_name: str
    product_url: Optional[str] = None
    extraction_note: Optional[str] = None


@dataclass
class RawVideoItem:
    """Raw video metadata read from the creator profile/video screen."""

    video_id: Optional[str]
    video_url: str
    raw_upload_date: Optional[str]
    raw_views: Optional[str]
    has_yellow_basket: bool
    products: List[RawProductItem] = field(default_factory=list)
    error_message: Optional[str] = None


@dataclass
class ScreenPoCReport:
    """Result of Stage 2 PoC Android UI screen verification."""

    success: bool
    driver_name: str
    device_serial: str
    screen_unlocked: bool
    tiktok_installed: bool
    tiktok_package: Optional[str]
    login_wall_detected: bool
    ui_node_count: int
    sample_texts: List[str]
    message: str


def parse_bounds(bounds_str: str) -> Tuple[int, int, int, int]:
    """Parse Android `[x1,y1][x2,y2]` bounds string into 4 integers."""
    match = _BOUNDS_RE.search(bounds_str or "")
    if not match:
        return (0, 0, 0, 0)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4)),
    )


def parse_ui_hierarchy_xml(xml_text: str) -> List[UiNode]:
    """Parse Android `uiautomator dump` XML string into a flat list of UiNode objects."""
    if not xml_text or "<hierarchy" not in xml_text:
        return []
    # Strip any leading/trailing non-XML output from adb
    start = xml_text.find("<hierarchy")
    end = xml_text.rfind("</hierarchy>")
    if start != -1 and end != -1:
        xml_text = xml_text[start : end + len("</hierarchy>")]

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    nodes: List[UiNode] = []
    for elem in root.iter():
        if elem.tag != "node":
            continue
        nodes.append(
            UiNode(
                text=elem.attrib.get("text", ""),
                resource_id=elem.attrib.get("resource-id", ""),
                class_name=elem.attrib.get("class", ""),
                content_desc=elem.attrib.get("content-desc", ""),
                package=elem.attrib.get("package", ""),
                clickable=elem.attrib.get("clickable", "false").lower() == "true",
                bounds=parse_bounds(elem.attrib.get("bounds", "")),
            )
        )
    return nodes


def detect_login_or_verification_wall(nodes: Sequence[UiNode]) -> Tuple[bool, str]:
    """
    Detect if TikTok is showing a login prompt or CAPTCHA/verification gate
    (PRD Section 7.2 & behavioral guidelines: never bypass CAPTCHA/login, stop and report).
    """
    joined = " | ".join(n.label.lower() for n in nodes if n.label)
    verification_keywords = (
        "verify you are human",
        "geser untuk menyelesaikan puzzle",
        "drag the puzzle piece",
        "security verification",
        "verifikasi keamanan",
        "captcha",
    )
    for kw in verification_keywords:
        if kw in joined:
            return True, f"Terdeteksi halaman verifikasi/CAPTCHA TikTok ('{kw}'). Proses dihentikan."

    login_keywords = (
        "log in to tiktok",
        "masuk ke tiktok",
        "sign up for tiktok",
        "mendaftar tiktok",
    )
    for kw in login_keywords:
        if kw in joined:
            return True, f"Terdeteksi halaman login TikTok ('{kw}'). Pastikan akun sudah login manual."

    return False, ""


class UiAutomationDriver(Protocol):
    """Protocol implemented by ADB, Appium, and Mock UI drivers."""

    driver_name: str

    def verify_screen_access(self) -> ScreenPoCReport:
        """Run Stage 2 PoC check on device screen, TikTok package, and UI hierarchy."""
        ...

    def open_creator_profile(self, creator_username: str) -> Tuple[bool, str]:
        """Navigate to the target creator's TikTok profile and verify username match."""
        ...

    def collect_creator_videos(
        self, creator_username: str, period_days: int, reference_date: Optional[date] = None
    ) -> List[RawVideoItem]:
        """Discover and read videos & yellow basket products for the creator."""
        ...


class AdbUiDriver:
    """
    Direct ADB UI Hierarchy & Intent driver.
    Reads Android UI Hierarchy via `uiautomator dump` without requiring a separate Node/Appium server.
    """

    driver_name = "ADB UI Hierarchy Driver"

    def __init__(
        self,
        device_serial: str,
        adb_path: str = "adb",
        runner: Optional[CommandRunner] = None,
        open_yellow_basket_links: bool = True,
    ) -> None:
        self.device_serial = device_serial
        self.adb_path = adb_path
        self._runner: CommandRunner = runner or default_command_runner
        self.open_yellow_basket_links: bool = open_yellow_basket_links
        self._on_status: Optional[Callable[[str], None]] = None
        self._should_cancel: Optional[Callable[[], bool]] = None
        self._on_video_collected: Optional[Callable[[RawVideoItem], None]] = None

    def set_progress_hooks(
        self,
        on_status: Optional[Callable[[str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_video_collected: Optional[Callable[[RawVideoItem], None]] = None,
    ) -> None:
        """Register live status, cancellation, and incremental video persistence callbacks."""
        self._on_status = on_status
        self._should_cancel = should_cancel
        self._on_video_collected = on_video_collected

    def _emit_status(self, message: str) -> None:
        if self._on_status:
            self._on_status(message)

    def _adb(self, args: Sequence[str], timeout: int = 15) -> AdbCommandResult:
        return self._runner([self.adb_path, "-s", self.device_serial, *args], timeout)

    def is_screen_unlocked(self) -> bool:
        """Check if Android device screen is awake and unlocked (PRD Section 2.2)."""
        res = self._adb(["shell", "dumpsys", "window"], timeout=8)
        if not res.ok:
            return False
        out = res.stdout
        if "mDreamingLockscreen=true" in out or "isStatusBarKeyguard=true" in out:
            return False
        return True

    def detect_tiktok_package(self) -> Optional[str]:
        """Return installed TikTok package name if present on the device."""
        res = self._adb(["shell", "pm", "list", "packages"], timeout=10)
        if not res.ok:
            return None
        installed = res.stdout
        for pkg in TIKTOK_PACKAGES:
            if f"package:{pkg}" in installed:
                return pkg
        return None

    def dump_ui_nodes(self, pause_on_idle_error: bool = False) -> List[UiNode]:
        """Dump current screen XML hierarchy and parse nodes (auto-pauses video if needed)."""
        if self._runner is default_command_runner:
            self._adb(["shell", "rm", "-f", "/sdcard/shadowkreator_ui.xml"], timeout=5)
            dump_res = self._adb(
                ["shell", "uiautomator", "dump", "--compressed", "/sdcard/shadowkreator_ui.xml"],
                timeout=15,
            )
            if (
                "dumped to" not in dump_res.combined_output.lower()
                and pause_on_idle_error
                and "detailactivity" in self._get_current_focus_window().lower()
            ):
                # Playing TikTok video prevents uiautomator idle state -> tap upper-left video area to pause
                self._adb(["shell", "input", "tap", "180", "350"], timeout=5)
                time.sleep(0.8)
                dump_res = self._adb(
                    ["shell", "uiautomator", "dump", "--compressed", "/sdcard/shadowkreator_ui.xml"],
                    timeout=15,
                )
        else:
            dump_res = self._adb(
                ["shell", "uiautomator", "dump", "/sdcard/shadowkreator_ui.xml"],
                timeout=15,
            )

        if not dump_res.ok and "dumped to" not in dump_res.combined_output.lower():
            return []
        cat_res = self._adb(["shell", "cat", "/sdcard/shadowkreator_ui.xml"], timeout=10)
        if not cat_res.ok:
            return []
        return parse_ui_hierarchy_xml(cat_res.stdout)

    def verify_screen_access(self) -> ScreenPoCReport:
        """Execute Stage 2 PoC screen & UI Hierarchy verification on connected Android device."""
        if (
            self._runner is default_command_runner
            and self.device_serial
            and not self.device_serial.startswith("SIMULATOR")
        ):
            state_res = self._adb(["get-state"], timeout=4)
            if not (state_res.ok and state_res.stdout.strip().lower() == "device"):
                from shadowkreator.core.device_manager import DeviceManager
                from shadowkreator.core.models import DeviceStatus

                dm = DeviceManager(adb_path=self.adb_path)
                dm._run_adb(["reconnect", "offline"], timeout=5)
                devs, _ = dm.scan_wireless_devices(auto_connect=True)
                connected = [
                    d
                    for d in devs
                    if d.connection_status == DeviceStatus.CONNECTED.value
                ]
                if not connected:
                    connected = [
                        d
                        for d in dm.scan_devices()
                        if d.connection_status == DeviceStatus.CONNECTED.value
                    ]
                if connected:
                    self.device_serial = connected[0].device_serial
                else:
                    return ScreenPoCReport(
                        success=False,
                        driver_name=self.driver_name,
                        device_serial=self.device_serial,
                        screen_unlocked=False,
                        tiktok_installed=False,
                        tiktok_package=None,
                        login_wall_detected=False,
                        ui_node_count=0,
                        sample_texts=[],
                        message=(
                            "Koneksi Wireless ADB ke HP terputus atau port Wi-Fi berubah. "
                            "Pastikan Wireless Debugging di HP aktif lalu klik Scan Wireless Debugging (Wi-Fi)."
                        ),
                    )

        unlocked = self.is_screen_unlocked()
        pkg = self.detect_tiktok_package()

        nodes = self.dump_ui_nodes(pause_on_idle_error=False)
        if not nodes and unlocked and pkg and self._runner is default_command_runner:
            # If uiautomator could not get idle state because a TikTok video is currently playing
            # in the foreground, check window focus; if TikTok is focused, pause or accept active window.
            focus_win = self._get_current_focus_window()
            if pkg in focus_win:
                nodes = [
                    UiNode(
                        text=f"TikTok Active ({pkg})",
                        resource_id=f"{pkg}:id/active_window",
                    )
                ]

        login_wall, wall_msg = detect_login_or_verification_wall(nodes)
        samples = [n.label for n in nodes if n.label][:8]

        if not unlocked:
            return ScreenPoCReport(
                success=False,
                driver_name=self.driver_name,
                device_serial=self.device_serial,
                screen_unlocked=False,
                tiktok_installed=pkg is not None,
                tiktok_package=pkg,
                login_wall_detected=login_wall,
                ui_node_count=len(nodes),
                sample_texts=samples,
                message="Layar HP terkunci atau mati. Harap buka kunci layar HP terlebih dahulu.",
            )

        if pkg is None:
            return ScreenPoCReport(
                success=False,
                driver_name=self.driver_name,
                device_serial=self.device_serial,
                screen_unlocked=True,
                tiktok_installed=False,
                tiktok_package=None,
                login_wall_detected=login_wall,
                ui_node_count=len(nodes),
                sample_texts=samples,
                message="Aplikasi TikTok tidak ditemukan di perangkat Android ini.",
            )

        if login_wall:
            return ScreenPoCReport(
                success=False,
                driver_name=self.driver_name,
                device_serial=self.device_serial,
                screen_unlocked=True,
                tiktok_installed=True,
                tiktok_package=pkg,
                login_wall_detected=True,
                ui_node_count=len(nodes),
                sample_texts=samples,
                message=wall_msg,
            )

        if not nodes:
            return ScreenPoCReport(
                success=False,
                driver_name=self.driver_name,
                device_serial=self.device_serial,
                screen_unlocked=True,
                tiktok_installed=True,
                tiktok_package=pkg,
                login_wall_detected=False,
                ui_node_count=0,
                sample_texts=[],
                message="Gagal membaca UI Hierarchy Android (0 node).",
            )

        return ScreenPoCReport(
            success=True,
            driver_name=self.driver_name,
            device_serial=self.device_serial,
            screen_unlocked=True,
            tiktok_installed=True,
            tiktok_package=pkg,
            login_wall_detected=False,
            ui_node_count=len(nodes),
            sample_texts=samples,
            message=f"PoC UI Berhasil: {len(nodes)} elemen UI terbaca, TikTok ({pkg}) siap.",
        )

    def open_creator_profile(self, creator_username: str) -> Tuple[bool, str]:
        """Open creator profile via Android deep link intent and verify handle on screen."""
        clean_handle = creator_username.strip().lstrip("@")
        if not clean_handle:
            return False, "Username kreator kosong"

        url = f"https://www.tiktok.com/@{clean_handle}"
        start_cmd = ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url]
        if self._runner is default_command_runner:
            start_cmd.append("--activity-clear-top")
            pkg = self.detect_tiktok_package()
            if pkg:
                start_cmd.append(pkg)

        res = self._adb(start_cmd, timeout=10)
        if not res.ok:
            return False, f"Gagal membuka profil melalui intent ADB: {res.combined_output}"

        max_polls = 8 if self._runner is default_command_runner else 1
        nodes: List[UiNode] = []

        for poll_idx in range(max_polls):
            if self._runner is default_command_runner:
                time.sleep(2.4 if poll_idx == 0 else 1.6)
                focus_now = self._get_current_focus_window().lower()
                # If a video player (DetailActivity / PDP sheet) is currently open on top of the profile,
                # press Back to dismiss DetailActivity and re-trigger the profile deep link.
                if any(k in focus_now for k in ("detailactivity", "saascenewrapper", "pdp", "ecommerce")):
                    self._adb(["shell", "input", "keyevent", "4"], timeout=5)
                    time.sleep(1.0)
                    focus_now = self._get_current_focus_window().lower()
                    if any(k in focus_now for k in ("detailactivity", "saascenewrapper", "pdp", "ecommerce")):
                        self._adb(["shell", "input", "keyevent", "4"], timeout=5)
                        time.sleep(1.0)
                        focus_now = self._get_current_focus_window().lower()
                    if "userprofile" not in focus_now:
                        self._adb(start_cmd, timeout=10)
                        time.sleep(2.0)
                elif poll_idx >= 1 and "userprofile" not in focus_now:
                    self._adb(start_cmd, timeout=10)
                    time.sleep(2.2)

            # Never tap or micro-swipe on the video grid area while waiting for the profile page to load
            nodes = self.dump_ui_nodes(pause_on_idle_error=False)
            if not nodes and self._runner is default_command_runner:
                time.sleep(0.9)
                nodes = self.dump_ui_nodes(pause_on_idle_error=False)
            if not nodes:
                continue

            login_wall, wall_msg = detect_login_or_verification_wall(nodes)
            if login_wall:
                return False, wall_msg

            joined_labels = " ".join(n.label.lower() for n in nodes if n.label)
            if (
                "couldn't find this account" in joined_labels
                or "akun tidak ditemukan" in joined_labels
            ):
                return False, f"Profil @{clean_handle} tidak ditemukan di TikTok."

            has_profile_grid = any(
                any(
                    k in n.resource_id.lower()
                    for k in ("tt_profile", "view_profile", "tv_play_count")
                )
                for n in nodes
            )
            has_grid_thumbs = any(
                "play_count" in n.resource_id.lower() for n in nodes
            )
            on_profile_window = (
                self._runner is not default_command_runner
                or "userprofile" in self._get_current_focus_window().lower()
            )
            grid_ready = (
                self._runner is not default_command_runner
                or has_grid_thumbs
                or poll_idx == max_polls - 1
            )
            if on_profile_window and grid_ready and (
                f"@{clean_handle.lower()}" in joined_labels
                or clean_handle.lower() in joined_labels
                or has_profile_grid
            ):
                self._last_profile_nodes = nodes
                return True, f"Profil @{clean_handle} berhasil dibuka dan tervalidasi."

        return False, f"Username pada layar tidak cocok dengan @{clean_handle}."

    def extract_current_video_screen(self, creator_username: str) -> RawVideoItem:
        """
        Parse currently visible TikTok video & yellow basket nodes from UI Hierarchy
        (PRD Section 3.3 & 3.4).
        """
        nodes = self.dump_ui_nodes(pause_on_idle_error=True)
        return parse_video_and_products_from_nodes(nodes, creator_username)

    def collect_creator_videos(
        self, creator_username: str, period_days: int, reference_date: Optional[date] = None
    ) -> List[RawVideoItem]:
        """
        Open creator profile, inspect video thumbnails row-by-row on the profile grid (`tv_play_count`),
        tap each video to read its post time (`tv_post_time`) and yellow basket products (`b6g`),
        return to the profile grid via Back key, and scroll down continuously until videos
        fall outside `period_days` (7 or 30 days) or the bottom of the profile is reached.
        Automatically frees leaked TikTok Shop RAM and recovers the exact profile grid row
        if Android `low-mem` destroys `UserProfileHostActivity` in the background.
        """
        self._last_profile_nodes: List[UiNode] = []
        self._profile_grid_recovered: bool = False
        self._session_product_urls: Dict[str, str] = {}
        self._active_visited_row_sigs: Set[Tuple[str, ...]] = set()
        ok, msg = self.open_creator_profile(creator_username)
        if not ok:
            raise RuntimeError(msg)

        ref_dt = reference_date or date.today()
        profile_nodes = getattr(self, "_last_profile_nodes", None) or self.dump_ui_nodes(
            pause_on_idle_error=False
        )
        has_grid = any("play_count" in n.resource_id.lower() for n in profile_nodes)
        if not has_grid and self._runner is default_command_runner:
            self._return_to_profile_grid(creator_username, scroll_idx=0)
            profile_nodes = self.dump_ui_nodes(pause_on_idle_error=False)

        initial_rows = _extract_grid_rows(profile_nodes)
        if not initial_rows:
            # Fallback if already inside a single video screen (e.g. unit test mock)
            item = parse_video_and_products_from_nodes(
                profile_nodes, creator_username, video_index=1
            )
            return [item] if (item.video_url or item.raw_views) else []

        results: List[RawVideoItem] = []
        visited_row_sigs: Set[Tuple[str, ...]] = self._active_visited_row_sigs
        last_anchor_row_sig: Optional[Tuple[str, ...]] = None
        video_counter = 0
        consecutive_out_of_period = 0
        empty_scroll_count = 0
        max_scroll_pages = 30 if self._runner is default_command_runner else 1

        if self._runner is default_command_runner:
            # Keep screen awake while scraping is running so idle timer does not turn screen off
            self._adb(["shell", "svc", "power", "stayon", "true"], timeout=4)

        try:
            for scroll_idx in range(max_scroll_pages):
                if self._should_cancel and self._should_cancel():
                    break
                if self._runner is default_command_runner and not self.is_screen_unlocked():
                    self._emit_status(
                        "Layar HP dimatikan saat scraping berjalan. Menghentikan proses dan menyimpan data yang sudah terkumpul."
                    )
                    break

                if scroll_idx > 0:
                    if self._runner is default_command_runner:
                        if not self._return_to_profile_grid(
                            creator_username,
                            scroll_idx=scroll_idx,
                            target_row_sig=last_anchor_row_sig,
                        ):
                            self._emit_status(
                                "Halaman profil tidak lagi aktif saat akan scroll; menghentikan pemindaian dan menyimpan data yang sudah terkumpul."
                            )
                            break
                    profile_nodes = self.dump_ui_nodes(pause_on_idle_error=False)
                    if not any("play_count" in n.resource_id.lower() for n in profile_nodes) and self._runner is default_command_runner:
                        time.sleep(0.9)
                        profile_nodes = self.dump_ui_nodes(pause_on_idle_error=False)

                grid_rows = _extract_grid_rows(profile_nodes)
                unvisited_rows = _select_unvisited_rows_below_visited(
                    grid_rows, visited_row_sigs
                )

                # If a single scroll swipe only collapsed the profile header without revealing a new row below
                # the last visited row, perform one additional scroll swipe before counting an empty page.
                if (
                    not unvisited_rows
                    and scroll_idx > 0
                    and self._runner is default_command_runner
                ):
                    self._adb(
                        ["shell", "input", "swipe", "360", "1360", "360", "560", "800"],
                        timeout=8,
                    )
                    time.sleep(1.5)
                    profile_nodes = self.dump_ui_nodes(pause_on_idle_error=False)
                    grid_rows = _extract_grid_rows(profile_nodes)
                    unvisited_rows = _select_unvisited_rows_below_visited(
                        grid_rows, visited_row_sigs
                    )

                if not unvisited_rows:
                    empty_scroll_count += 1
                    if empty_scroll_count >= 2:
                        self._emit_status("Sudah mencapai batas bawah daftar video di halaman profil.")
                        break
                else:
                    empty_scroll_count = 0

                for row_idx_in_page in range(len(unvisited_rows)):
                    row_sig = unvisited_rows[row_idx_in_page][0]
                    row_thumbnails = unvisited_rows[row_idx_in_page][1]
                    visited_row_sigs.add(row_sig)
                    last_anchor_row_sig = row_sig

                    col_idx = 0
                    while col_idx < len(row_thumbnails):
                        if self._should_cancel and self._should_cancel():
                            return results
                        if self._runner is default_command_runner and not self.is_screen_unlocked():
                            self._emit_status(
                                "Layar HP dimatikan saat scraping berjalan. Menghentikan proses dan menyimpan data yang sudah terkumpul."
                            )
                            return results

                        if self._runner is default_command_runner:
                            self._profile_grid_recovered = False
                            if not self._return_to_profile_grid(
                                creator_username,
                                scroll_idx=scroll_idx,
                                target_row_sig=row_sig,
                            ):
                                self._emit_status(
                                    f"Gagal memastikan posisi di grid profil sebelum Video #{video_counter + 1}; menghentikan pemindaian agar tidak salah klik."
                                )
                                return results
                            if self._profile_grid_recovered:
                                self._profile_grid_recovered = False
                                if not self._refresh_rows_after_recovery(
                                    row_sig,
                                    row_thumbnails,
                                    unvisited_rows,
                                    row_idx_in_page,
                                    visited_row_sigs,
                                ):
                                    # Target row is no longer on screen; never tap stale coordinates!
                                    break

                        tap_x, tap_y, grid_views = row_thumbnails[col_idx]
                        col_idx += 1

                        video_counter += 1
                        self._emit_status(
                            f"Membuka Video #{video_counter} dari grid profil (views: {grid_views})..."
                        )
                        self._adb(
                            ["shell", "input", "tap", str(tap_x), str(tap_y)], timeout=5
                        )
                        if self._runner is default_command_runner:
                            time.sleep(1.8)
                            focus_after_tap = self._get_current_focus_window().lower()
                            if "detailactivity" not in focus_after_tap:
                                # If still on profile grid (e.g. scroll was settling), retry tap once slightly lower inside thumbnail
                                if "userprofile" in focus_after_tap:
                                    self._adb(
                                        ["shell", "input", "tap", str(tap_x), str(min(1480, tap_y + 25))],
                                        timeout=5,
                                    )
                                    time.sleep(1.6)
                                    focus_after_tap = self._get_current_focus_window().lower()
                                if "detailactivity" not in focus_after_tap:
                                    # Tap did not open DetailActivity -> stay on grid without misclicking
                                    continue

                        video_nodes = self.dump_ui_nodes(pause_on_idle_error=True)
                        still_on_grid = any(
                            "play_count" in n.resource_id.lower()
                            or "tt_profile_full_screen" in n.resource_id.lower()
                            for n in video_nodes
                        )

                        item = parse_video_and_products_from_nodes(
                            video_nodes,
                            creator_username,
                            fallback_views=grid_views,
                            video_index=video_counter,
                        )
                        # Check upload date against target period (7 or 30 days)
                        parsed_dt = parse_upload_date(item.raw_upload_date, reference_date=ref_dt)
                        in_win = True
                        if parsed_dt is not None:
                            in_win, _, _ = is_within_period(
                                parsed_dt, period_days, reference_date=ref_dt
                            )

                        # Reuse already-extracted product URL if this single-product title was already resolved in this job
                        multi_count = _detect_multi_product_count_from_video_nodes(video_nodes)
                        if (
                            in_win
                            and not still_on_grid
                            and multi_count < 2
                            and len(item.products) == 1
                            and not item.products[0].product_url
                        ):
                            cached_url = self._session_product_urls.get(
                                item.products[0].product_name.strip().lower()
                            )
                            if cached_url:
                                item.products[0].product_url = cached_url

                        # PRD 3.4: If video is within period, has a yellow basket, and `open_yellow_basket_links`
                        # is enabled, open the yellow basket to extract the product URL (or multi-product list).
                        if (
                            self.open_yellow_basket_links
                            and in_win
                            and not still_on_grid
                            and (item.has_yellow_basket or item.products)
                            and (
                                not item.products
                                or any(not p.product_url for p in item.products)
                                or multi_count >= 2
                            )
                        ):
                            self._enrich_products_via_yellow_basket(
                                item, video_nodes, video_counter
                            )
                            if len(item.products) == 1 and item.products[0].product_url:
                                p_key = item.products[0].product_name.strip().lower()
                                if p_key:
                                    self._session_product_urls[p_key] = item.products[0].product_url

                        results.append(item)
                        if in_win and self._on_video_collected is not None:
                            self._on_video_collected(item)

                        if parsed_dt is not None:
                            if not in_win:
                                self._emit_status(
                                    f"Video #{video_counter} diunggah {item.raw_upload_date} ({parsed_dt.isoformat()}) — di luar periode {period_days} hari."
                                )
                                # Top 1-3 videos on TikTok profile can be Pinned ("Disematkan");
                                # past index 3, videos are strictly chronological (newest -> oldest).
                                if video_counter > 3:
                                    consecutive_out_of_period += 1
                            else:
                                consecutive_out_of_period = 0
                                prod_names = (
                                    ", ".join(
                                        (
                                            f"{p.product_name} ({p.product_url})"
                                            if p.product_url
                                            else (
                                                f"{p.product_name} (No URL)"
                                                if self.open_yellow_basket_links
                                                else p.product_name
                                            )
                                        )
                                        for p in item.products
                                    )
                                    if item.products
                                    else "Tanpa keranjang kuning"
                                )
                                self._emit_status(
                                    f"Video #{video_counter} ({item.raw_upload_date}, views: {grid_views}) -> {prod_names}"
                                )

                        # Return to profile grid (UserProfileHostActivity) safely, always anchoring to `row_sig`
                        if not still_on_grid:
                            self._profile_grid_recovered = False
                            self._return_to_profile_grid(
                                creator_username,
                                scroll_idx=scroll_idx,
                                target_row_sig=row_sig,
                            )
                            if self._profile_grid_recovered:
                                self._profile_grid_recovered = False
                                if (
                                    not self._refresh_rows_after_recovery(
                                        row_sig,
                                        row_thumbnails,
                                        unvisited_rows,
                                        row_idx_in_page,
                                        visited_row_sigs,
                                    )
                                    and col_idx < len(row_thumbnails)
                                ):
                                    break

                        if consecutive_out_of_period >= 2:
                            self._emit_status(
                                f"Melewati batas periode {period_days} hari (2 video berturut-turut lebih lama dari {period_days} hari). Menghentikan scroll."
                            )
                            return results

                # Scroll down the profile grid by ~2 rows (800px) to reveal next rows
                if scroll_idx + 1 < max_scroll_pages:
                    if self._should_cancel and self._should_cancel():
                        break
                    if self._runner is default_command_runner and not self.is_screen_unlocked():
                        break
                    if self._runner is default_command_runner:
                        self._return_to_profile_grid(
                            creator_username,
                            scroll_idx=scroll_idx,
                            target_row_sig=last_anchor_row_sig,
                        )
                    self._emit_status(
                        f"Scroll ke baris video berikutnya di profil {creator_username} (sudah memeriksa {video_counter} video)..."
                    )
                    self._adb(
                        ["shell", "input", "swipe", "360", "1360", "360", "560", "800"],
                        timeout=8,
                    )
                    if self._runner is default_command_runner:
                        time.sleep(1.5)

            return results
        finally:
            if self._runner is default_command_runner:
                self._adb(["shell", "svc", "power", "stayon", "false"], timeout=4)

    def _refresh_rows_after_recovery(
        self,
        current_row_sig: Tuple[str, ...],
        current_row_thumbnails: List[Tuple[int, int, str]],
        unvisited_rows: List[List[object]],
        current_row_idx: int,
        visited_row_sigs: Set[Tuple[str, ...]],
    ) -> bool:
        """
        After recovering `UserProfileHostActivity` from a `low-mem` `MainActivity` redirect,
        refresh the `(tap_x, tap_y)` coordinates of the remaining thumbnails in `current_row_thumbnails`
        and any subsequent `unvisited_rows` from the freshly dumped profile grid.
        Returns True if `current_row_sig` was matched on the recovered screen.
        """
        fresh_nodes = getattr(self, "_last_profile_nodes", None) or self.dump_ui_nodes(
            pause_on_idle_error=False
        )
        fresh_rows = _extract_grid_rows(fresh_nodes)
        if not fresh_rows:
            return False

        matched_current = False
        for f_sig, f_thumbs in fresh_rows:
            if _row_sigs_match(f_sig, current_row_sig) and len(f_thumbs) == len(current_row_thumbnails):
                current_row_thumbnails[:] = list(f_thumbs)
                visited_row_sigs.add(f_sig)
                matched_current = True
                break

        for next_idx in range(current_row_idx + 1, len(unvisited_rows)):
            next_sig = unvisited_rows[next_idx][0]
            next_thumbs = unvisited_rows[next_idx][1]
            for f_sig, f_thumbs in fresh_rows:
                if _row_sigs_match(f_sig, next_sig) and len(f_thumbs) == len(next_thumbs):
                    unvisited_rows[next_idx][0] = f_sig
                    unvisited_rows[next_idx][1] = list(f_thumbs)
                    break

        return matched_current

    def _recover_profile_grid_at_scroll(
        self,
        creator_username: str,
        scroll_idx: int = 0,
        target_row_sig: Optional[Tuple[str, ...]] = None,
    ) -> bool:
        """
        Free TikTok's leaked 1.0 GB `SeaPdp` RAM (`am force-stop`), re-open `@creator_username`'s
        profile grid (`UserProfileHostActivity`), and scroll step-by-step until `target_row_sig`
        is confirmed visible on screen whenever Android `low-mem` destroyed `UserProfileHostActivity`
        in the background and caused `DetailActivity.finish()` to redirect to `MainActivity`.
        """
        clean_handle = creator_username.strip().lstrip("@")
        self._emit_status(
            f"Memulihkan posisi grid profil @{clean_handle} setelah redirect otomatis TikTok..."
        )
        visited_sigs: Set[Tuple[str, ...]] = getattr(
            self, "_active_visited_row_sigs", set()
        )
        if self._runner is default_command_runner:
            pkg = self.detect_tiktok_package() or "com.ss.android.ugc.trill"
            # Free the ~1.0 GB leaked SeaPdp Lynx/WebView RAM so the next 8+ videos do NOT keep triggering low-mem redirects
            self._adb(["shell", "am", "force-stop", pkg], timeout=5)
            time.sleep(0.9)

        ok, _ = self.open_creator_profile(creator_username)
        if not ok:
            return False

        nodes = getattr(self, "_last_profile_nodes", None) or self.dump_ui_nodes(
            pause_on_idle_error=False
        )
        rows = _extract_grid_rows(nodes)
        _select_unvisited_rows_below_visited(rows, visited_sigs)

        if self._runner is default_command_runner:
            # If target_row_sig is already visible at the top of the profile (e.g. Row 0 or Row 1), do 0 swipes!
            target_found = (
                target_row_sig is not None
                and any(_row_sigs_match(r_sig, target_row_sig) for r_sig, _ in rows)
            )
            max_swipes = max(scroll_idx + 2, 3) if target_row_sig is not None else scroll_idx
            swipe_count = 0
            while not target_found and swipe_count < max_swipes:
                if self._should_cancel and self._should_cancel():
                    return False
                if not self.is_screen_unlocked():
                    return False
                self._adb(
                    ["shell", "input", "swipe", "360", "1360", "360", "560", "800"],
                    timeout=8,
                )
                swipe_count += 1
                time.sleep(1.5)
                nodes = self.dump_ui_nodes(pause_on_idle_error=False)
                rows = _extract_grid_rows(nodes)
                _select_unvisited_rows_below_visited(rows, visited_sigs)
                if target_row_sig is not None and any(
                    _row_sigs_match(r_sig, target_row_sig) for r_sig, _ in rows
                ):
                    target_found = True
                    break

        self._last_profile_nodes = nodes
        self._profile_grid_recovered = True
        return "userprofile" in self._get_current_focus_window().lower()

    def _press_back_and_wait_window_change(
        self, prev_token: str, max_wait_sec: float = 3.2
    ) -> str:
        """
        Press Android Back (`keyevent 4`) once and poll until the focused `Window{<hash> ...}` token
        changes from `prev_token` (or reaches `UserProfileHostActivity`), up to `max_wait_sec`.
        Never returns early on `mCurrentFocus=null` / `mFocusedApp` transition frames (`cur_token == ""`),
        preventing a duplicate Back key from hitting `UserProfileHostActivity` and exiting to Beranda.
        """
        self._adb(["shell", "input", "keyevent", "4"], timeout=5)
        if self._runner is not default_command_runner:
            return ""
        deadline = time.time() + max_wait_sec
        time.sleep(0.65)
        while time.time() < deadline:
            cur_focus = self._get_current_focus_window()
            if "userprofile" in cur_focus.lower():
                return _extract_window_token(cur_focus) or "userprofile"
            cur_token = _extract_window_token(cur_focus)
            if cur_token and cur_token != prev_token:
                return cur_token
            time.sleep(0.35)
        return _extract_window_token(self._get_current_focus_window())

    def _return_to_profile_grid(
        self,
        creator_username: str,
        scroll_idx: int = 0,
        target_row_sig: Optional[Tuple[str, ...]] = None,
    ) -> bool:
        """
        Safely navigate back from `DetailActivity` to `UserProfileHostActivity`.
        If Android's `low-mem` activity killer destroyed `UserProfileHostActivity` in the background
        while `SeaPdp` was open—causing `DetailActivity.finish()` to redirect to `MainActivity`
        (`aweme://main?extra_param_from_deeplink_return=extra_value_from_detail`)—automatically
        frees TikTok's RAM, recovers `@creator_username`'s profile grid, and scrolls to `target_row_sig`.
        """
        if self._runner is not default_command_runner:
            self._adb(["shell", "input", "keyevent", "4"], timeout=5)
            return True

        raw_focus = self._get_current_focus_window()
        focus = raw_focus.lower()
        if "userprofile" in focus:
            return True

        # Wait up to 1.5s if a window transition is currently in flight (mCurrentFocus=null)
        if not _extract_window_token(raw_focus):
            for _ in range(3):
                time.sleep(0.45)
                raw_focus = self._get_current_focus_window()
                focus = raw_focus.lower()
                if "userprofile" in focus:
                    return True
                if _extract_window_token(raw_focus):
                    break

        if not focus and not self.is_screen_unlocked():
            return False

        if any(
            k in focus
            for k in (
                "launcher",
                "nexuslauncher",
                "quickstep",
                "recents",
                "notificationshade",
                "keyguard",
                "statusbar",
            )
        ):
            return False

        if any(
            k in focus
            for k in ("mainactivity", "tiktokhostactivity", "applinkhookactivity")
        ):
            return self._recover_profile_grid_at_scroll(
                creator_username,
                scroll_idx=scroll_idx,
                target_row_sig=target_row_sig,
            )

        # Press Back ONCE to close DetailActivity and wait up to 3.5s for UserProfileHostActivity
        prev_token = _extract_window_token(raw_focus)
        self._adb(["shell", "input", "keyevent", "4"], timeout=5)
        deadline = time.time() + 3.5
        time.sleep(0.85)
        while time.time() < deadline:
            raw_focus = self._get_current_focus_window()
            focus = raw_focus.lower()
            if "userprofile" in focus:
                time.sleep(0.45)
                return True
            if any(
                k in focus
                for k in ("mainactivity", "tiktokhostactivity", "applinkhookactivity")
            ):
                return self._recover_profile_grid_at_scroll(
                    creator_username,
                    scroll_idx=scroll_idx,
                    target_row_sig=target_row_sig,
                )
            cur_token = _extract_window_token(raw_focus)
            # Only if an unclosed dialog inside DetailActivity just closed to a DIFFERENT
            # DetailActivity window token, press Back one more time to close DetailActivity itself.
            if (
                cur_token
                and prev_token
                and cur_token != prev_token
                and "detailactivity" in cur_token
            ):
                prev_token = cur_token
                self._adb(["shell", "input", "keyevent", "4"], timeout=5)
                deadline = time.time() + 3.5
                time.sleep(0.85)
                continue
            time.sleep(0.35)

        final_focus = self._get_current_focus_window().lower()
        if "userprofile" in final_focus:
            return True
        if any(
            k in final_focus
            for k in ("mainactivity", "tiktokhostactivity", "applinkhookactivity")
        ):
            return self._recover_profile_grid_at_scroll(
                creator_username,
                scroll_idx=scroll_idx,
                target_row_sig=target_row_sig,
            )
        return False

    def _get_current_focus_window(self) -> str:
        """
        Return the active Android activity/window name from `dumpsys window displays`.
        Never returns `mCurrentFocus=null`: when an activity/dialog close animation is in flight
        (`mCurrentFocus=null`), waits briefly and retries so transient null focus is never
        mistaken for an unknown open dialog window.
        """
        focused_app_fallback = ""
        max_attempts = 4 if self._runner is default_command_runner else 1
        for attempt in range(max_attempts):
            res = self._adb(["shell", "dumpsys", "window", "displays"], timeout=6)
            if not res.ok:
                return focused_app_fallback
            current_focus = ""
            focused_app = ""
            for line in res.stdout.splitlines():
                stripped = line.strip()
                if "mCurrentFocus=" in stripped:
                    if "mCurrentFocus=null" not in stripped:
                        current_focus = stripped
                        break
                elif "mFocusedApp=" in stripped and "mFocusedApp=null" not in stripped:
                    focused_app = stripped
            if current_focus:
                return current_focus
            if focused_app:
                focused_app_fallback = focused_app
            if attempt + 1 < max_attempts and self._runner is default_command_runner:
                time.sleep(0.35)
        return focused_app_fallback

    def _read_device_clipboard_urls(self) -> List[str]:
        """
        Read any TikTok Shop / Tokopedia product URL copied to the Android 12/13+ clipboard
        using `service call clipboard 4 s16 com.android.shell i32 0` (after granting READ_CLIPBOARD).
        """
        if not getattr(self, "_clipboard_appops_granted", False):
            self._adb(
                ["shell", "appops", "set", "com.android.shell", "READ_CLIPBOARD", "allow"],
                timeout=4,
            )
            self._clipboard_appops_granted = True

        urls: List[str] = []
        res = self._adb(
            ["shell", "service", "call", "clipboard", "4", "s16", "com.android.shell", "i32", "0"],
            timeout=5,
        )
        if res.ok and res.stdout:
            decoded_parcel = _decode_parcel_hex_dump(res.stdout)
            for u in extract_product_urls_from_text(res.stdout + "\n" + decoded_parcel):
                if u not in urls:
                    urls.append(u)
        return urls

    def _enrich_products_via_yellow_basket(
        self,
        item: RawVideoItem,
        video_nodes: Sequence[UiNode],
        video_index: int,
    ) -> None:
        """
        PRD Section 3.4 Product & Product URL Extraction with Multi-Layer Validation:
        1. Snapshots the current `DetailActivity` window token (`Window{<hash> ...}`) and activity stack.
        2. Validates that the yellow basket tap coordinate `(bx, by)` is inside the safe basket area.
        3. After tapping `(bx, by)`, polls up to 3.2s for the bottom-sheet dialog window to open, then distinguishes:
           - **Multi-Product Yellow Basket (2+ products):** Extracts all product titles from the
             Multi-Product List Bottom Sheet into `item.products` and NEVER taps `(484, 506)` or `(92, 1445)`.
           - **Single-Product PDP (`SeaPdp`):** Taps Share `(484, 506)`, polls up to 2.4s for the Share Dialog
             window to open on top of `SeaPdp`, and strictly verifies it before tapping Copy Link `(92, 1445)`.
        4. Guarantees all opened sheets/dialogs (`Share`, `SeaPdp`, `Multi-Product`) are closed back to
           `DetailActivity` (`token_before_basket`) using `_press_back_and_wait_window_change` before returning.
        """
        focus_before_basket = (
            self._get_current_focus_window() if self._runner is default_command_runner else ""
        )
        token_before_basket = _extract_window_token(focus_before_basket)

        top_before = self._adb(["shell", "dumpsys", "activity", "top"], timeout=6)
        top_before_text = top_before.stdout if top_before.ok else ""
        urls_before = set(extract_product_urls_from_text(top_before_text))
        found_urls: List[str] = list(urls_before) if self._runner is not default_command_runner else []

        expected_multi_count = max(
            len(item.products),
            _detect_multi_product_count_from_video_nodes(video_nodes),
        )
        tap_coords = _find_yellow_basket_tap_target(video_nodes)

        if tap_coords is not None and (not found_urls or expected_multi_count >= 2):
            bx, by = tap_coords
            self._emit_status(
                f"Membuka Keranjang Kuning pada Video #{video_index}..."
            )
            opened_dialog_tokens: Set[str] = set()
            token_after_basket = ""
            token_after_share = ""
            try:
                self._adb(["shell", "input", "tap", str(bx), str(by)], timeout=5)
                focus_after_basket = ""
                if self._runner is default_command_runner:
                    deadline = time.time() + 3.5
                    time.sleep(2.2)
                    while time.time() < deadline:
                        focus_after_basket = self._get_current_focus_window()
                        token_after_basket = _extract_window_token(focus_after_basket)
                        if token_after_basket and token_after_basket != token_before_basket:
                            break
                        time.sleep(0.35)

                if (
                    token_after_basket
                    and token_after_basket != token_before_basket
                    and "userprofile" not in token_after_basket
                    and "mainactivity" not in token_after_basket
                ):
                    opened_dialog_tokens.add(token_after_basket)

                top_after = self._adb(["shell", "dumpsys", "activity", "top"], timeout=6)
                top_after_text = top_after.stdout if top_after.ok else ""
                before_lines_set = set(top_before_text.splitlines())
                added_top_lines = "\n".join(
                    line for line in top_after_text.splitlines() if line not in before_lines_set
                )
                inspection_text = added_top_lines if added_top_lines.strip() else top_after_text

                for u in extract_product_urls_from_text(inspection_text):
                    if u not in urls_before and u not in found_urls:
                        found_urls.append(u)

                added_lower = added_top_lines.lower()
                is_multi_product = (
                    expected_multi_count >= 2
                    or any(
                        k in added_lower
                        for k in (
                            "multiproduct",
                            "multi_product",
                            "productlist",
                            "anchorlist",
                            "anchor_list",
                        )
                    )
                )

                basket_dialog_opened = (
                    self._runner is not default_command_runner
                    or bool(opened_dialog_tokens)
                )
                # Note: ProGuard/R8 in TikTok obfuscates SeaPdpFragment class names in `dumpsys activity top`
                # unless a selling-points carousel message is active; so any single-product yellow basket
                # (`expected_multi_count < 2`) that opens a new dialog window is a single-product PDP.
                is_single_pdp = basket_dialog_opened and not is_multi_product

                if not is_single_pdp and basket_dialog_opened:
                    # Multi-Product Yellow Basket (2+ products):
                    # Extract all product names listed in the bottom sheet and DO NOT tap Share/Copy Link coordinates.
                    sheet_nodes = self.dump_ui_nodes(pause_on_idle_error=False)
                    sheet_products = _extract_products_from_multi_basket_sheet(sheet_nodes)
                    if sheet_products:
                        existing_names = {p.product_name.strip().lower() for p in item.products}
                        for sp in sheet_products:
                            key = sp.product_name.strip().lower()
                            if key and key not in existing_names:
                                existing_names.add(key)
                                item.products.append(sp)
                    if len(item.products) >= 2:
                        self._emit_status(
                            f"Keranjang Kuning Video #{video_index} berisi {len(item.products)} produk (multi-produk terdeteksi)."
                        )
                    for prod in item.products:
                        if not prod.product_url and not prod.extraction_note:
                            prod.extraction_note = (
                                "Keranjang kuning multi-produk (link individual dilewati agar tidak salah klik)"
                            )

                elif (
                    is_single_pdp
                    and basket_dialog_opened
                    and not found_urls
                    and self._runner is default_command_runner
                ):
                    # Single-Product PDP (SeaPdp) confirmed open:
                    # Snapshot clipboard before tapping Share -> Copy Link so stale URLs from previous videos are never misassigned.
                    clipboard_before = set(self._read_device_clipboard_urls())

                    self._adb(["shell", "input", "tap", "484", "506"], timeout=5)
                    focus_after_share = ""
                    token_after_share = ""
                    share_deadline = time.time() + 2.6
                    time.sleep(1.4)
                    while time.time() < share_deadline:
                        focus_after_share = self._get_current_focus_window()
                        token_after_share = _extract_window_token(focus_after_share)
                        if (
                            token_after_share
                            and token_after_share != token_after_basket
                            and token_after_share != token_before_basket
                        ):
                            break
                        time.sleep(0.35)

                    if (
                        token_after_share
                        and token_after_share != token_before_basket
                        and "userprofile" not in token_after_share
                        and "mainactivity" not in token_after_share
                    ):
                        opened_dialog_tokens.add(token_after_share)

                    top_share = self._adb(["shell", "dumpsys", "activity", "top"], timeout=6)
                    top_share_text = top_share.stdout if top_share.ok else ""
                    after_lines_set = set(top_after_text.splitlines())
                    added_share_lower = "\n".join(
                        line for line in top_share_text.splitlines() if line not in after_lines_set
                    ).lower()

                    # Strict validation: a brand-new Dialog window must have opened on DetailActivity
                    # (different from both SeaPdp and the base video window) and must not be Price Details / SKU / Photo Viewer.
                    share_dialog_opened = (
                        bool(token_after_share)
                        and token_after_share != token_after_basket
                        and token_after_share != token_before_basket
                        and (
                            "detailactivity" in token_after_share
                            or "share" in token_after_share
                        )
                        and not any(
                            bad in added_share_lower
                            for bad in (
                                "pricedetail",
                                "price_detail",
                                "photoview",
                                "imageviewer",
                                "skupanel",
                                "sku_panel",
                            )
                        )
                    )

                    if share_dialog_opened:
                        self._adb(["shell", "input", "tap", "92", "1445"], timeout=5)
                        # Tapping Copy Link auto-dismisses the Share dialog back to SeaPdp (token_after_basket).
                        # Wait until the Share dialog finishes auto-closing so `finally:` never fires an extra Back key!
                        copy_dismissed_share = False
                        copy_deadline = time.time() + 2.4
                        time.sleep(1.1)
                        while time.time() < copy_deadline:
                            token_after_copy = _extract_window_token(
                                self._get_current_focus_window()
                            )
                            if token_after_copy and token_after_copy != token_after_share:
                                copy_dismissed_share = True
                                break
                            time.sleep(0.3)
                        for u in self._read_device_clipboard_urls():
                            # Accept URL if it is newly copied OR if tapping Copy Link genuinely dismissed the Share dialog
                            if (u not in clipboard_before or copy_dismissed_share) and u not in found_urls:
                                found_urls.append(u)
                    else:
                        self._emit_status(
                            f"Panel Share produk pada Video #{video_index} tidak terbuka; melewati klik Copy Link agar tidak salah klik."
                        )
                        for prod in item.products:
                            if not prod.product_url and not prod.extraction_note:
                                prod.extraction_note = (
                                    "Panel Share produk tidak terbuka; dilewati agar tidak salah klik"
                                )
            finally:
                # Strictly serialized dialog cleanup:
                # 1. Only if a 2nd-level Share/sub-dialog (`token_after_share`) is STILL focused, press Back ONCE and wait for it to close.
                # 2. Only if the 1st-level Yellow Basket sheet (`token_after_basket`) is focused, press Back ONCE and wait for `token_before_basket`.
                if self._runner is default_command_runner and opened_dialog_tokens:
                    cur_token = _extract_window_token(self._get_current_focus_window())
                    if not cur_token:
                        time.sleep(0.45)
                        cur_token = _extract_window_token(self._get_current_focus_window())

                    if (
                        token_after_share
                        and cur_token == token_after_share
                        and token_after_share != token_after_basket
                        and token_after_share != token_before_basket
                    ):
                        cur_token = self._press_back_and_wait_window_change(
                            token_after_share, max_wait_sec=2.6
                        )

                    if (
                        token_after_basket
                        and cur_token == token_after_basket
                        and token_after_basket != token_before_basket
                    ):
                        self._press_back_and_wait_window_change(
                            token_after_basket, max_wait_sec=3.0
                        )
                    pkg = self.detect_tiktok_package() or "com.ss.android.ugc.trill"
                    self._adb(
                        ["shell", "am", "send-trim-memory", pkg, "RUNNING_CRITICAL"],
                        timeout=4,
                    )

        # Assign discovered URLs to the video's products
        if found_urls and item.products:
            if len(item.products) == 1:
                if not item.products[0].product_url:
                    item.products[0].product_url = found_urls[0]
            else:
                # For multi-product videos, only assign URLs when we have distinct URLs matching product positions
                for idx, prod in enumerate(item.products):
                    if not prod.product_url and idx < len(found_urls):
                        prod.product_url = found_urls[idx]


def _extract_window_token(focus_line: str) -> str:
    """
    Extract the stable Android `WindowState` identity `<hex_hash>:<package/activity>` from
    `mCurrentFocus=Window{279c32c u0 com.ss.android.ugc.trill/...}-[Surface(name=*Title#6516)/@0x...]`,
    stripping the mutable Surface number suffix so window identity comparisons across dialog pushes/pops are exact.
    Returns `""` when no concrete `Window{...}` token is present (e.g. `mCurrentFocus=null` or `mFocusedApp=...`).
    """
    if not focus_line:
        return ""
    m = re.search(r"Window\{([0-9a-fA-F]+)\s+u\d+\s+([^\}\s]+)", focus_line)
    if m:
        return f"{m.group(1).lower()}:{m.group(2).lower()}"
    return ""


_MULTI_PRODUCT_BADGE_RE = re.compile(
    r"(?:(?:·|\•|\|)\s*(\d{1,2})\s*$|\((\d{1,2})\)\s*$|\b(\d{1,2})\s*(?:produk|products|items)\b|^\+?(\d{1,2})$)",
    re.IGNORECASE,
)


def _detect_multi_product_count_from_video_nodes(nodes: Sequence[UiNode]) -> int:
    """
    Inspect the yellow basket chip (`:id/feed_multi_tag_layout` and its children) on `DetailActivity`
    to detect if the video tags 2 or more products (e.g. `· 2`, `(3)`, `2 produk`, `+2`).
    Returns the detected product count (>= 2) or 1 if single-product.
    """
    tag_boxes = [
        n.bounds
        for n in nodes
        if "feed_multi_tag_layout" in n.resource_id.lower()
        and n.bounds[2] > n.bounds[0]
        and n.bounds[3] > n.bounds[1]
    ]
    max_count = 1
    for n in nodes:
        res_id = n.resource_id.lower()
        lbl = n.label.strip()
        if not lbl:
            continue
        x1, y1, x2, y2 = n.bounds
        inside_tag = any(
            x1 >= bx1 and y1 >= by1 and x2 <= bx2 and y2 <= by2
            for bx1, by1, bx2, by2 in tag_boxes
        )
        is_basket_node = (
            inside_tag
            or "feed_multi_tag_layout" in res_id
            or res_id.endswith(":id/b_w")
            or res_id.endswith(":id/b6g")
            or "anchor" in res_id
            or "product" in res_id
        )
        if not is_basket_node:
            continue
        for m in _MULTI_PRODUCT_BADGE_RE.finditer(lbl):
            raw_num = next((g for g in m.groups() if g is not None), None)
            if raw_num:
                try:
                    val = int(raw_num)
                    if 2 <= val <= 50:
                        max_count = max(max_count, val)
                except ValueError:
                    pass
    return max_count


def _extract_products_from_multi_basket_sheet(
    sheet_nodes: Sequence[UiNode],
) -> List[RawProductItem]:
    """
    Extract all product titles from a Multi-Product Yellow Basket List Bottom Sheet (2+ products).
    Filters out prices, discount pills, ratings, sold counts, CTA buttons (`Beli`, `Buy`), and background video labels.
    """
    if not sheet_nodes:
        return []

    ignored_exact = {
        "beli",
        "beli sekarang",
        "buy",
        "buy now",
        "add to cart",
        "tambah ke troli",
        "keranjang",
        "yellow basket",
        "product description",
        "deskripsi produk",
        "shop",
        "toko",
        "chat",
        "power shop",
        "official store",
        "mall",
        "cod",
        "gratis ongkir",
        "free shipping",
        "flash sale",
        "find",
        "search this image",
        "search",
        "back",
        "add comment...",
        "find related content",
        "stickers",
        "mention someone",
        "video",
    }
    price_or_metric_re = re.compile(
        r"^(?:"
        r"(?:from\s*|mulai\s*)?rp\s*[\d.,]+.*"
        r"|-\d{1,2}%"
        r"|\d+(?:[.,]\d+)?\s*(?:rb|k|m|jt)?\+?\s*(?:terjual|sold).*"
        r"|\d\.\d(?:\s*\([\d.,]+[kmrb]*\))?"
        r"|(?:produk di video ini|products in this video|lihat\s*\d+\s*produk).*"
        r"|\d+\s*(?:produk|products|items)"
        r")$",
        re.IGNORECASE,
    )
    bg_res_keywords = (
        ":id/desc",
        ":id/title",
        ":id/tv_post_time",
        ":id/eqm",
        ":id/tv_search_sug_word",
        ":id/tv_bar_search",
        ":id/user_avatar",
        ":id/long_press_layout",
    )

    products: List[RawProductItem] = []
    seen_names: Set[str] = set()

    for n in sheet_nodes:
        res_id = n.resource_id.lower()
        raw_lbl = n.label.strip()
        if not raw_lbl:
            continue
        if any(res_id.endswith(k) for k in bg_res_keywords):
            continue

        cleaned = re.sub(r"^produk:\s*", "", raw_lbl, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*(?:·|\•)\s*\d{1,2}\s*$", "", cleaned).strip()
        low = cleaned.lower()
        if (
            not cleaned
            or len(cleaned) < 5
            or low in ignored_exact
            or price_or_metric_re.match(cleaned)
            or low.startswith(("like video", "read or add comments", "add or remove", "share video", "original sound", "follow "))
        ):
            continue

        is_explicit_product_id = any(
            k in res_id
            for k in (
                "product_title",
                "anchor_title",
                "tv_product_name",
                "product_name",
                "goods_name",
                "goods_title",
                "item_title",
                ":id/b_w",
                ":id/b6g",
            )
        ) or raw_lbl.lower().startswith("produk:")

        x1, y1, x2, y2 = n.bounds
        in_sheet_card_region = (
            x2 > x1
            and y2 > y1
            and 260 <= y1 <= 1510
            and len(cleaned) >= 10
            and not any(k in low for k in ("ends in ", "berakhir dalam", "get rp", "diskon rp", "voucher"))
        )

        if is_explicit_product_id or in_sheet_card_region:
            if low not in seen_names:
                seen_names.add(low)
                products.append(RawProductItem(product_name=cleaned))

    return products


def _decode_parcel_hex_dump(parcel_text: str) -> str:
    """Decode 32-bit little-endian hex words from `service call clipboard` Parcel output."""
    if not parcel_text:
        return ""
    raw_bytes = bytearray()
    for m in re.finditer(r"\b([0-9a-fA-F]{8})\b", parcel_text):
        try:
            raw_bytes.extend(bytes.fromhex(m.group(1))[::-1])
        except ValueError:
            continue
    if not raw_bytes:
        return ""
    ascii_part = raw_bytes.decode("latin1", errors="ignore")
    utf16_part = raw_bytes.decode("utf-16le", errors="ignore")
    return ascii_part + "\n" + utf16_part


_DIRECT_PRODUCT_URL_RE = re.compile(
    r"(https?://(?:vt\.tokopedia\.com/[A-Za-z0-9_/-]{4,40}"
    r"|shop\.tokopedia\.com/[A-Za-z0-9_/-]{4,60}"
    r"|shop\.tiktok\.com/view/product/\d{10,25}"
    r"|www\.tiktok\.com/view/product/\d{10,25}"
    r"|vt\.tiktok\.com/[A-Za-z0-9_/-]{5,30}"
    r"|vm\.tiktok\.com/[A-Za-z0-9_/-]{5,30}))",
    re.IGNORECASE,
)

_PRODUCT_ID_PARAM_RE = re.compile(
    r"(?:product_id|productId|pdp_product_id|sku_product_id|/view/product/)"
    r"[\"'=:\s/\[\]\\]+(\d{15,22})",
    re.IGNORECASE,
)


def extract_product_urls_from_text(raw_text: str) -> List[str]:
    """
    Extract TikTok Shop / Tokopedia product URLs or 19-digit `product_id` values from
    clipboard Parcel output, `dumpsys activity`, `logcat`, or UI node labels.
    Automatically URL-decodes nested `aweme://echybrid` schemas up to 3 levels.
    """
    if not raw_text:
        return []

    decoded = raw_text
    for _ in range(3):
        try:
            next_dec = urllib.parse.unquote(decoded)
            if next_dec == decoded:
                break
            decoded = next_dec
        except Exception:
            break

    combined = raw_text + "\n" + decoded
    urls: List[str] = []
    seen: Set[str] = set()

    for m in _DIRECT_PRODUCT_URL_RE.finditer(combined):
        url = m.group(1).strip().rstrip(".,;\"')]>\\x00")
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for m in _PRODUCT_ID_PARAM_RE.finditer(combined):
        pid = m.group(1).strip()
        canonical = f"https://shop.tiktok.com/view/product/{pid}"
        if canonical not in seen:
            seen.add(canonical)
            urls.append(canonical)

    return urls


def _find_yellow_basket_tap_target(
    nodes: Sequence[UiNode],
) -> Optional[Tuple[int, int]]:
    """
    Find the `(x, y)` center of the yellow basket chip on `DetailActivity`,
    validating that the tap coordinate sits inside the safe yellow-basket zone
    (never on the right-side Like/Comment/Share column or bottom comment bar).
    """
    # Priority 1: Exact TikTok yellow basket resource IDs (:id/feed_multi_tag_layout, :id/b_w, :id/b6g)
    for n in nodes:
        res_id = n.resource_id.lower()
        x1, y1, x2, y2 = n.bounds
        if x2 > x1 and y2 > y1 and (
            "feed_multi_tag_layout" in res_id
            or res_id.endswith(":id/b_w")
            or res_id.endswith(":id/b6g")
        ):
            cx, cy = n.center
            if 10 <= cx <= 570 and 650 <= cy <= 1485:
                return (cx, cy)

    # Priority 2: Generic product/anchor/basket nodes
    for n in nodes:
        res_id = n.resource_id.lower()
        lbl = n.label.lower()
        x1, y1, x2, y2 = n.bounds
        if x2 > x1 and y2 > y1 and (
            any(k in res_id for k in ("product_title", "anchor_title", "anchor_shop"))
            or any(k in lbl for k in ("keranjang", "yellow basket", "produk:"))
        ):
            cx, cy = n.center
            if 10 <= cx <= 570 and 650 <= cy <= 1485:
                return (cx, cy)
    return None


def _row_sigs_match(sig_a: Tuple[str, ...], sig_b: Tuple[str, ...]) -> bool:
    """
    Compare two profile-grid row view signatures (e.g. `("1,128", "516", "467")` vs `("1,198", "555", "502")`).
    Tolerates view-count increments (`±18%` or `±60` views per column across a 3-column row) when a profile grid
    is re-loaded from TikTok's server after recovering from an Android `low-mem` activity destruction.
    """
    if sig_a == sig_b:
        return True
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return False
    if len(sig_a) == 1:
        va = parse_views_count(sig_a[0])
        vb = parse_views_count(sig_b[0])
        if va is None or vb is None:
            return False
        return abs(va - vb) <= max(5, int(max(va, vb) * 0.02))

    ratio = 0.18 if len(sig_a) >= 3 else 0.08
    min_tol = 60 if len(sig_a) >= 3 else 25
    exact_matches = 0
    for a, b in zip(sig_a, sig_b):
        if a == b:
            exact_matches += 1
            continue
        va = parse_views_count(a)
        vb = parse_views_count(b)
        if va is None or vb is None:
            return False
        tol = max(min_tol, int(max(va, vb) * ratio))
        if abs(va - vb) > tol:
            return False
    return exact_matches >= 1 or len(sig_a) >= 3


def _is_row_visited(
    sig: Tuple[str, ...], visited_row_sigs: Set[Tuple[str, ...]]
) -> bool:
    """Return True if `sig` matches any row signature already visited in `visited_row_sigs`."""
    if sig in visited_row_sigs:
        return True
    return any(_row_sigs_match(sig, v) for v in visited_row_sigs)


def _select_unvisited_rows_below_visited(
    grid_rows: Sequence[Tuple[Tuple[str, ...], List[Tuple[int, int, str]]]],
    visited_row_sigs: Set[Tuple[str, ...]],
) -> List[List[object]]:
    """
    Enforce the Geometric Top-to-Bottom Invariant on a profile-grid screen dump:
    Because rows on the profile grid are scanned strictly top-to-bottom (`index 0 .. m-1`),
    if `grid_rows[last_visited_idx]` matches any already-visited row in `visited_row_sigs`,
    then every row at or above `last_visited_idx` (`grid_rows[0 .. last_visited_idx]`) has ALREADY
    been visited (and its current signature is added to `visited_row_sigs`), so only rows
    strictly below `last_visited_idx` (`grid_rows[last_visited_idx + 1 :]`) can be unvisited.
    """
    if not grid_rows:
        return []
    last_visited_idx = -1
    for idx, (sig, _) in enumerate(grid_rows):
        if _is_row_visited(sig, visited_row_sigs):
            last_visited_idx = idx

    if last_visited_idx >= 0:
        for idx in range(last_visited_idx + 1):
            visited_row_sigs.add(grid_rows[idx][0])

    candidate_rows = grid_rows[last_visited_idx + 1 :]
    return [
        [sig, list(thumbs)]
        for sig, thumbs in candidate_rows
        if not _is_row_visited(sig, visited_row_sigs)
    ]


def _extract_grid_rows(
    profile_nodes: Sequence[UiNode],
) -> List[Tuple[Tuple[str, ...], List[Tuple[int, int, str]]]]:
    """
    Group visible `:id/tv_play_count` badges on the creator's profile grid into
    horizontal rows sorted top-to-bottom and left-to-right.
    Returns `[(row_signature, [(tap_x, tap_y, views_text), ...]), ...]`.
    """
    raw_badges: List[Tuple[int, int, int, int, str]] = []
    for node in profile_nodes:
        res_id = node.resource_id.lower()
        if ("tv_play_count" in res_id or "play_count" in res_id) and node.label:
            x1, y1, x2, y2 = node.bounds
            # Exclude badges clipped under the sticky profile header/tab bar (y < 375) or bottom edge
            if x2 > x1 and y2 > y1 and 375 <= y1 <= 1510:
                tap_x = (x1 + x2) // 2
                tap_y = max(345, y1 - 110)
                raw_badges.append((y1, x1, tap_x, tap_y, node.label.strip()))

    if not raw_badges:
        return []

    raw_badges.sort(key=lambda b: (b[0], b[1]))
    rows: List[List[Tuple[int, int, int, int, str]]] = []
    for badge in raw_badges:
        y1 = badge[0]
        if not rows or abs(y1 - rows[-1][0][0]) > 45:
            rows.append([badge])
        else:
            rows[-1].append(badge)

    result: List[Tuple[Tuple[str, ...], List[Tuple[int, int, str]]]] = []
    for r in rows:
        r.sort(key=lambda b: b[1])  # sort left-to-right by x1
        sig = tuple(b[4] for b in r)
        thumbs = [(b[2], b[3], b[4]) for b in r]
        result.append((sig, thumbs))
    return result


_PURE_COUNT_LABEL_RE = re.compile(
    r"^(?:(?:lihat\s*)?\d{1,2}\s*(?:produk|products|items)|(?:·|\•|\+|\|)?\s*\d{1,2}|\(\d{1,2}\))$",
    re.IGNORECASE,
)


def _clean_product_title_label(raw_label: str) -> str:
    """Strip `Produk:` prefix and trailing multi-product count badge (`· 2`, `(3)`) from a yellow basket label."""
    cleaned = re.sub(r"^produk:\s*", "", raw_label or "", flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*(?:(?:·|\•)\s*\d{1,2}|\(\d{1,2}\))\s*$", "", cleaned).strip()
    if _PURE_COUNT_LABEL_RE.match(cleaned):
        return ""
    return cleaned


def parse_video_and_products_from_nodes(
    nodes: Sequence[UiNode],
    creator_username: str,
    fallback_views: Optional[str] = None,
    video_index: int = 1,
) -> RawVideoItem:
    """
    Extract video views, upload date, yellow basket indicator, and product items
    from a list of Android UI Hierarchy nodes (PRD Section 3.3 & 3.4).
    Supports real TikTok Indonesia/Global (`com.ss.android.ugc.trill` / `com.zhiliaoapp.musically`)
    resource IDs such as `:id/tv_post_time`, `:id/feed_multi_tag_layout`, `:id/b6g`, and `:id/desc`.
    """
    clean_handle = creator_username.strip().lstrip("@") or "creator"
    views_str: Optional[str] = fallback_views
    upload_date_str: Optional[str] = None
    video_url: str = ""
    video_id: Optional[str] = None
    caption_str: str = ""
    has_basket = False
    products: List[RawProductItem] = []
    seen_product_names = set()

    basket_keywords = (
        "keranjang",
        "yellow basket",
        "anchor_shop",
        "ecommerce",
    )

    date_pattern = re.compile(
        r"^(\d+\s*(?:s|sec|detik|h|d|w|m|hari|jam|minggu|menit)(?:\s*lalu|\s*ago)?|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2})$",
        re.IGNORECASE,
    )
    views_pattern = re.compile(r"^(\d+(?:[.,]\d+)?\s*[KMBkmb]?)\s*(?:views|tayangan)?$")

    for node in nodes:
        label = node.label
        res_id = node.resource_id.lower()
        if not label and not res_id:
            continue

        # Detect video caption (:id/desc)
        if res_id.endswith(":id/desc") and label:
            caption_str = label.strip()

        # Detect video URL / ID if shared or shown in node
        if "tiktok.com/" in label:
            video_url = label.strip()
            vid_match = re.search(r"/video/(\d+)", video_url)
            if vid_match:
                video_id = vid_match.group(1)

        # Detect views
        if views_str is None:
            if "view" in res_id or ("like" not in res_id and "tayangan" in label.lower()):
                m = re.search(r"(\d+(?:[.,]\d+)?\s*[KMBkmb]?)", label)
                if m:
                    views_str = m.group(1).strip()
            elif "view_count" in res_id or "play_count" in res_id:
                m = views_pattern.match(label.strip())
                if m:
                    views_str = m.group(1).strip()

        # Detect upload date (:id/tv_post_time, :id/upload_date, or relative date string)
        if upload_date_str is None:
            cleaned_date = label.strip().lstrip("·").strip()
            if (
                res_id.endswith(":id/tv_post_time")
                or "upload_date" in res_id
                or "post_time" in res_id
                or date_pattern.match(cleaned_date)
            ):
                if cleaned_date and date_pattern.match(cleaned_date):
                    upload_date_str = cleaned_date

        # Detect yellow basket container or product title on TikTok DetailActivity
        if "feed_multi_tag_layout" in res_id or any(
            k in res_id for k in ("shop", "anchor", "cart", "product", "bag", ":id/b6g", ":id/b_w")
        ) or any(k in label.lower() for k in basket_keywords):
            has_basket = True
            if (
                res_id.endswith(":id/b6g")
                or res_id.endswith(":id/b_w")
                or "product_title" in res_id
                or "anchor_title" in res_id
                or label.lower().startswith("produk:")
            ):
                prod_name = _clean_product_title_label(label)
                if prod_name and prod_name.lower() not in seen_product_names:
                    seen_product_names.add(prod_name.lower())
                    products.append(RawProductItem(product_name=prod_name))

        # Detect product URL
        if any(
            k in label
            for k in ("shop.tiktok.com", "vt.tiktok.com", "vt.tokopedia.com", "shop.tokopedia.com")
        ) and products:
            products[-1].product_url = label.strip()

    # Fallback: If `feed_multi_tag_layout` exists on screen (yellow basket chip) and TikTok
    # obfuscated the child TextView ID to something new, find any child text node inside its bounds.
    if has_basket and not products:
        tag_boxes = [
            n.bounds
            for n in nodes
            if "feed_multi_tag_layout" in n.resource_id.lower()
            and n.bounds[2] > n.bounds[0]
            and n.bounds[3] > n.bounds[1]
        ]
        ignore_labels = {"product description", "deskripsi produk", "keranjang", "yellow basket"}
        for bx1, by1, bx2, by2 in tag_boxes:
            for n in nodes:
                lbl = _clean_product_title_label(n.label)
                if not lbl or lbl.lower() in ignore_labels:
                    continue
                x1, y1, x2, y2 = n.bounds
                if x1 >= bx1 and y1 >= by1 and x2 <= bx2 and y2 <= by2:
                    if lbl.lower() not in seen_product_names:
                        seen_product_names.add(lbl.lower())
                        products.append(RawProductItem(product_name=lbl))

    # Ensure every extracted video has a deterministic video_id and video_url
    if not video_id and (views_str or upload_date_str or caption_str or products):
        fingerprint = f"{clean_handle}|{video_index}|{caption_str[:40]}|{views_str or ''}"
        short_hash = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:10]
        video_id = f"{clean_handle}_{video_index}_{short_hash}"

    if not video_url and video_id:
        video_url = f"https://www.tiktok.com/@{clean_handle}/video/{video_id}"

    return RawVideoItem(
        video_id=video_id,
        video_url=video_url,
        raw_upload_date=upload_date_str,
        raw_views=views_str,
        has_yellow_basket=has_basket or len(products) > 0,
        products=products,
    )


class MockUiDriver:
    """
    Deterministic Simulator Driver matching PRD Section 10 Acceptance Test Target:
    - 1 creator account
    - 5 videos (4 within 7/30 days, 1 older than 30 days to verify period filtering)
    - Videos with single yellow-basket product, multiple products in 1 video,
      video without yellow basket, and product with unavailable link.
    """

    driver_name = "Simulator / Mock Android UI Driver"

    def __init__(self, device_serial: str = "SIMULATOR-ANDROID-13") -> None:
        self.device_serial = device_serial

    def verify_screen_access(self) -> ScreenPoCReport:
        return ScreenPoCReport(
            success=True,
            driver_name=self.driver_name,
            device_serial=self.device_serial,
            screen_unlocked=True,
            tiktok_installed=True,
            tiktok_package="com.ss.android.ugc.trill",
            login_wall_detected=False,
            ui_node_count=42,
            sample_texts=[
                "@creator_affiliate",
                "Keranjang Kuning",
                "Serum Wajah Glowing Niacinamide 10%",
                "125.4K tayangan",
            ],
            message="PoC Mode Simulasi siap: Hierarki UI Android & Keranjang Kuning terverifikasi.",
        )

    def open_creator_profile(self, creator_username: str) -> Tuple[bool, str]:
        clean = creator_username.strip().lstrip("@")
        if not clean or clean.lower() == "notfound":
            return False, f"Profil @{clean} tidak ditemukan."
        return True, f"Profil @{clean} berhasil dibuka dan tervalidasi."

    def collect_creator_videos(
        self, creator_username: str, period_days: int, reference_date: Optional[date] = None
    ) -> List[RawVideoItem]:
        ok, msg = self.open_creator_profile(creator_username)
        if not ok:
            raise RuntimeError(msg)

        ref = reference_date or date.today()
        clean = creator_username.strip().lstrip("@")

        d_1 = (ref - timedelta(days=1)).isoformat()
        d_3 = (ref - timedelta(days=3)).isoformat()
        d_5 = (ref - timedelta(days=5)).isoformat()
        d_15 = (ref - timedelta(days=15)).isoformat()
        d_45 = (ref - timedelta(days=45)).isoformat()

        return [
            # Video 1 (1 day ago): Multi-product video (3 products in 1 video, PRD Section 3.6 note)
            RawVideoItem(
                video_id="7410000001",
                video_url=f"https://www.tiktok.com/@{clean}/video/7410000001",
                raw_upload_date=d_1,
                raw_views="250.5K",
                has_yellow_basket=True,
                products=[
                    RawProductItem(
                        product_name="Serum Wajah Glowing Niacinamide 10%",
                        product_url="https://shop.tiktok.com/view/product/17290001",
                    ),
                    RawProductItem(
                        product_name="Moisturizer Ceramide Barrier Gel 50g",
                        product_url="https://shop.tiktok.com/view/product/17290002",
                    ),
                    RawProductItem(
                        product_name="Sunscreen SPF50 PA++++ No Whitecast",
                        product_url=None,  # Link unavailable edge case (PRD 3.4)
                        extraction_note="Tombol salin tautan produk tidak tersedia di UI",
                    ),
                ],
            ),
            # Video 2 (3 days ago): Single product (same Serum #1)
            RawVideoItem(
                video_id="7410000002",
                video_url=f"https://www.tiktok.com/@{clean}/video/7410000002",
                raw_upload_date=d_3,
                raw_views="180K",
                has_yellow_basket=True,
                products=[
                    RawProductItem(
                        product_name="Serum Wajah Glowing Niacinamide 10%",
                        product_url="https://shop.tiktok.com/view/product/17290001",
                    ),
                ],
            ),
            # Video 3 (5 days ago): Video without yellow basket (PRD 3.3 has_product=False)
            RawVideoItem(
                video_id="7410000003",
                video_url=f"https://www.tiktok.com/@{clean}/video/7410000003",
                raw_upload_date=d_5,
                raw_views="45.2K",
                has_yellow_basket=False,
                products=[],
            ),
            # Video 4 (15 days ago): Included in 30-day period, excluded in 7-day period
            RawVideoItem(
                video_id="7410000004",
                video_url=f"https://www.tiktok.com/@{clean}/video/7410000004",
                raw_upload_date=d_15,
                raw_views="320K",
                has_yellow_basket=True,
                products=[
                    RawProductItem(
                        product_name="Moisturizer Ceramide Barrier Gel 50g",
                        product_url="https://shop.tiktok.com/view/product/17290002",
                    ),
                    # Same product name as Serum #1 but DIFFERENT link -> must NOT merge (PRD 3.5)
                    RawProductItem(
                        product_name="Serum Wajah Glowing Niacinamide 10%",
                        product_url="https://shop.tiktok.com/view/product/99999999",
                    ),
                ],
            ),
            # Video 5 (45 days ago): Outside both 7-day and 30-day windows (must be filtered out)
            RawVideoItem(
                video_id="7410000005",
                video_url=f"https://www.tiktok.com/@{clean}/video/7410000005",
                raw_upload_date=d_45,
                raw_views="990K",
                has_yellow_basket=True,
                products=[
                    RawProductItem(
                        product_name="Produk Lama Di Luar Periode",
                        product_url="https://shop.tiktok.com/view/product/17290099",
                    ),
                ],
            ),
        ]
