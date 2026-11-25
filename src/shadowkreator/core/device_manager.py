"""ADB Device Manager implementing PRD Section 3.1 & 4.2 (USB & Wireless Debugging Scanner)."""

from __future__ import annotations

import concurrent.futures
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from shadowkreator.config import MIN_ANDROID_VERSION, find_adb_binary
from shadowkreator.core.database import DatabaseManager, utc_now_iso
from shadowkreator.core.models import ConnectionType, DeviceInfo, DeviceStatus

# Regex for IP:Port or mDNS wireless ADB serials
_WIRELESS_SERIAL_RE = re.compile(
    r"^(\d{1,3}(?:\.\d{1,3}){3}:\d+|adb-[^.\s]+(?:\._adb-tls-connect\._tcp)?\.?)$",
    re.IGNORECASE,
)

# Regex for parsing `adb mdns services` lines:
# e.g. "adb-R58R123456A-xyz123\t_adb-tls-connect._tcp.\t192.168.1.25:41555"
_MDNS_LINE_RE = re.compile(
    r"^(\S+)\s+(_adb-tls-(?:connect|pairing)\._tcp\.?|_adb\._tcp\.?)\s+(\d{1,3}(?:\.\d{1,3}){3}:\d+)\s*$",
    re.IGNORECASE,
)

# Regex for parsing IPv4 addresses from `arp -a` on both Windows and macOS
_ARP_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")

# Prevent flashing cmd.exe console windows when spawning adb.exe from a windowed GUI on Windows
_SUBPROCESS_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    if platform.system() == "Windows"
    else 0
)


@dataclass
class AdbCommandResult:
    """Result of executing an ADB CLI command."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined_output(self) -> str:
        parts = [p.strip() for p in (self.stdout, self.stderr) if p and p.strip()]
        return "\n".join(parts)


@dataclass
class WirelessServiceInfo:
    """Represents a discovered Android Wireless Debugging service on the Wi-Fi network."""

    instance_name: str
    service_type: str
    host_port: str

    @property
    def is_pairing_service(self) -> bool:
        """True if this endpoint is an open Android 12+ Wireless Pairing dialog."""
        return "pairing" in self.service_type.lower()

    @property
    def is_connect_service(self) -> bool:
        """True if this endpoint is an active Android 12+ Wireless Debugging connect port."""
        return "connect" in self.service_type.lower() or "_adb._tcp" in self.service_type.lower()


CommandRunner = Callable[[Sequence[str], int], AdbCommandResult]


def default_command_runner(cmd: Sequence[str], timeout: int = 10) -> AdbCommandResult:
    """Execute a subprocess command safely with timeout and ADB mDNS OpenScreen enabled."""
    try:
        env = os.environ.copy()
        env.setdefault("ADB_MDNS_OPENSCREEN", "1")
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
            creationflags=_SUBPROCESS_FLAGS,
        )
        return AdbCommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except FileNotFoundError:
        return AdbCommandResult(
            returncode=127,
            stdout="",
            stderr=f"ADB executable not found: {cmd[0]}",
        )
    except subprocess.TimeoutExpired:
        return AdbCommandResult(
            returncode=124,
            stdout="",
            stderr=f"ADB command timed out after {timeout}s",
        )
    except Exception as exc:
        return AdbCommandResult(
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def detect_connection_type(serial: str) -> str:
    """Classify an ADB serial as USB or Wireless (PRD Section 3.1)."""
    if _WIRELESS_SERIAL_RE.match(serial.strip()):
        return ConnectionType.WIRELESS.value
    return ConnectionType.USB.value


def map_adb_state_to_status(adb_state: str) -> str:
    """Map raw `adb devices` state token to PRD Section 3.1 DeviceStatus."""
    state = adb_state.strip().lower()
    if state == "device":
        return DeviceStatus.CONNECTED.value
    if state == "unauthorized":
        return DeviceStatus.UNAUTHORIZED.value
    if state == "offline":
        return DeviceStatus.OFFLINE.value
    if state in ("disconnect", "disconnected"):
        return DeviceStatus.DISCONNECTED.value
    if "pair" in state:
        return DeviceStatus.PAIRING_REQUIRED.value
    return DeviceStatus.ERROR.value


def parse_adb_mdns_services(mdns_output: str) -> List[WirelessServiceInfo]:
    """Parse output of `adb mdns services` into WirelessServiceInfo entries."""
    services: List[WirelessServiceInfo] = []
    seen = set()
    for raw_line in (mdns_output or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("list of discovered"):
            continue
        match = _MDNS_LINE_RE.match(line)
        if not match:
            continue
        instance_name, service_type, host_port = (
            match.group(1),
            match.group(2),
            match.group(3),
        )
        key = (service_type.lower(), host_port)
        if key in seen:
            continue
        seen.add(key)
        services.append(
            WirelessServiceInfo(
                instance_name=instance_name,
                service_type=service_type,
                host_port=host_port,
            )
        )
    return services


def _is_tcp_port_open(ip: str, port: int = 5555, timeout_sec: float = 0.25) -> bool:
    """Quick non-intrusive TCP socket check on LAN IP:port for legacy Wireless ADB (:5555)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


_HOST_IP_CACHE: Dict[str, str] = {}


def _run_dns_sd_capture(
    args: Sequence[str],
    wait_sec: float = 0.7,
    stop_pattern: Optional[re.Pattern] = None,
) -> str:
    """
    Run `dns-sd` (macOS / Bonjour CLI) and capture stdout cleanly.
    Uses a PTY on POSIX so `dns-sd` flushes line-by-line immediately and returns
    as soon as `stop_pattern` matches (or after `wait_sec` max).
    """
    dns_sd_bin = "/usr/bin/dns-sd" if Path_exists("/usr/bin/dns-sd") else shutil.which("dns-sd")
    if not dns_sd_bin:
        return ""
    if os.name == "posix":
        import pty
        import select

        master_fd: Optional[int] = None
        proc: Optional[subprocess.Popen] = None
        try:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                [dns_sd_bin, *args],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
            os.close(slave_fd)
            deadline = time.time() + wait_sec
            chunks: List[bytes] = []
            text_so_far = ""
            while time.time() < deadline:
                remaining = max(0.02, min(0.15, deadline - time.time()))
                rlist, _, _ = select.select([master_fd], [], [], remaining)
                if rlist:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    chunks.append(data)
                    text_so_far = b"".join(chunks).decode("utf-8", "ignore")
                    if stop_pattern is not None and stop_pattern.search(text_so_far):
                        break
            return text_so_far
        except Exception:
            return ""
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass

    try:
        proc = subprocess.Popen(
            [dns_sd_bin, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_SUBPROCESS_FLAGS,
        )
        time.sleep(wait_sec)
        proc.terminate()
        out, _ = proc.communicate(timeout=2)
        return out or ""
    except Exception:
        return ""


def Path_exists(p: str) -> bool:
    try:
        return os.path.isfile(p)
    except Exception:
        return False


_BROWSE_ADD_RE = re.compile(r"\sAdd\s+\d+\s+\d+\s+\S+\s+_adb-tls-", re.IGNORECASE)
_REACH_RE = re.compile(r"can be reached at\s+([^\s:]+):(\d+)")
_IPV4_RE = re.compile(r"\sAdd\s+\S+\s+\d+\s+\S+\s+(\d{1,3}(?:\.\d{1,3}){3})\b")


def _discover_via_dns_sd(
    service_types: Sequence[str] = ("_adb-tls-connect._tcp", "_adb-tls-pairing._tcp"),
) -> List[WirelessServiceInfo]:
    """
    Use OS native `dns-sd` (Bonjour mDNSResponder on macOS / Windows Bonjour)
    to discover `_adb-tls-connect._tcp` and `_adb-tls-pairing._tcp` when `adb mdns services`
    returns empty.
    """
    services: List[WirelessServiceInfo] = []
    seen = set()
    for svc_type in service_types:
        browse_out = _run_dns_sd_capture(
            ["-B", svc_type, "local."],
            wait_sec=1.2,
            stop_pattern=_BROWSE_ADD_RE,
        )
        instances: List[str] = []
        for line in browse_out.splitlines():
            if " Add " in line and svc_type in line:
                parts = line.split()
                inst = parts[-1].strip()
                if inst and inst not in instances:
                    instances.append(inst)

        for inst in instances:
            lookup_out = _run_dns_sd_capture(
                ["-L", inst, svc_type, "local."],
                wait_sec=2.0,
                stop_pattern=_REACH_RE,
            )
            m_reach = _REACH_RE.search(lookup_out)
            if not m_reach:
                continue
            target_host, port = m_reach.group(1), m_reach.group(2)

            ip_addr = _HOST_IP_CACHE.get(target_host)
            if not ip_addr:
                ip_out = _run_dns_sd_capture(
                    ["-G", "v4", target_host],
                    wait_sec=2.0,
                    stop_pattern=_IPV4_RE,
                )
                m_ip = _IPV4_RE.search(ip_out) or re.search(
                    r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", ip_out
                )
                if not m_ip:
                    continue
                ip_addr = m_ip.group(1)
                _HOST_IP_CACHE[target_host] = ip_addr

            host_port = f"{ip_addr}:{port}"
            key = (f"{svc_type}.", host_port)
            if key in seen:
                continue
            seen.add(key)
            services.append(
                WirelessServiceInfo(
                    instance_name=inst,
                    service_type=f"{svc_type}.",
                    host_port=host_port,
                )
            )
    return services


class DeviceManager:
    """
    Manages Android device discovery (USB & Wireless Debugging mDNS/LAN scan),
    connection, pairing, and readiness validation via ADB (PRD Section 3.1).
    """

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        adb_path: Optional[str] = None,
        runner: Optional[CommandRunner] = None,
        enable_lan_probe: bool = True,
    ) -> None:
        self.db = db
        self.adb_path = find_adb_binary(adb_path)
        self._runner: CommandRunner = runner or default_command_runner
        self.enable_lan_probe = enable_lan_probe

    def _run_adb(self, args: Sequence[str], timeout: int = 10) -> AdbCommandResult:
        return self._runner([self.adb_path, *args], timeout)

    def is_adb_available(self) -> Tuple[bool, str]:
        """Check whether the ADB binary is installed and reachable."""
        res = self._run_adb(["version"], timeout=5)
        if res.ok:
            first_line = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "ADB OK"
            return True, first_line
        return False, res.combined_output or "ADB tidak ditemukan"

    def scan_devices(self) -> List[DeviceInfo]:
        """
        Scan currently attached USB and Wireless devices via `adb devices -l`
        and enrich authorized devices with model and Android version (PRD Section 3.1).
        """
        res = self._run_adb(["devices", "-l"], timeout=10)
        if not res.ok:
            return []

        devices: List[DeviceInfo] = []
        for raw_line in res.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            serial = parts[0]
            raw_state = parts[1]
            kv_tokens = {}
            for token in parts[2:]:
                if ":" in token:
                    k, v = token.split(":", 1)
                    kv_tokens[k] = v

            status = map_adb_state_to_status(raw_state)
            conn_type = detect_connection_type(serial)
            fallback_model = kv_tokens.get("model", "Unknown Model").replace("_", " ")

            if status == DeviceStatus.CONNECTED.value:
                info = self._inspect_connected_device(serial, conn_type, fallback_model)
            else:
                info = DeviceInfo(
                    device_serial=serial,
                    device_name=fallback_model,
                    model=fallback_model,
                    android_version="Unknown",
                    connection_type=conn_type,
                    connection_status=status,
                    last_connected_at=utc_now_iso(),
                )

            if self.db is not None:
                self.db.upsert_device(info)
            devices.append(info)

        return devices

    def discover_wireless_services(self) -> List[WirelessServiceInfo]:
        """
        Discover Android devices broadcasting Wireless Debugging on the local Wi-Fi network
        using `adb mdns services`, OS native Bonjour (`dns-sd`),
        plus optional ARP neighbor check on TCP port 5555 (PRD Section 3.1).
        """
        res = self._run_adb(["mdns", "services"], timeout=8)
        services = parse_adb_mdns_services(res.stdout if res.ok else "")
        known_keys = {(s.service_type.lower().rstrip("."), s.host_port) for s in services}
        known_host_ports = {s.host_port for s in services}

        if self._runner is default_command_runner:
            for svc in _discover_via_dns_sd():
                key = (svc.service_type.lower().rstrip("."), svc.host_port)
                if key not in known_keys:
                    services.append(svc)
                    known_keys.add(key)
                    known_host_ports.add(svc.host_port)

        if self.enable_lan_probe and self._runner is default_command_runner:
            for ip in self._get_arp_neighbor_ips():
                hp = f"{ip}:5555"
                if hp not in known_host_ports and _is_tcp_port_open(ip, 5555):
                    services.append(
                        WirelessServiceInfo(
                            instance_name=f"android-lan-{ip}",
                            service_type="_adb._tcp.",
                            host_port=hp,
                        )
                    )
                    known_host_ports.add(hp)

        return services

    def discover_pairing_services(self) -> List[WirelessServiceInfo]:
        """
        Fast-path discovery of `_adb-tls-pairing._tcp` services only (used while
        waiting for the phone to scan a Wireless Debugging QR code).
        """
        res = self._run_adb(["mdns", "services"], timeout=4)
        services = [
            s
            for s in parse_adb_mdns_services(res.stdout if res.ok else "")
            if s.is_pairing_service
        ]
        known_host_ports = {s.host_port for s in services}
        if self._runner is default_command_runner:
            for svc in _discover_via_dns_sd(service_types=("_adb-tls-pairing._tcp",)):
                if svc.host_port not in known_host_ports:
                    services.append(svc)
                    known_host_ports.add(svc.host_port)
        return services

    @staticmethod
    def _get_arp_neighbor_ips() -> List[str]:
        """Read local ARP cache IPs so we can probe port 5555 quickly."""
        try:
            proc = subprocess.run(
                ["arp", "-a"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
                creationflags=_SUBPROCESS_FLAGS,
            )
            ips = []
            for m in _ARP_IP_RE.finditer(proc.stdout or ""):
                ip = m.group(1)
                if (
                    not ip.endswith(".255")
                    and not ip.endswith(".1")
                    and not ip.startswith("224.")
                ):
                    ips.append(ip)
            # Check in parallel so ARP scanning takes < 0.3s total
            open_ips: List[str] = []
            if not ips:
                return []
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                future_to_ip = {
                    pool.submit(_is_tcp_port_open, ip, 5555, 0.2): ip for ip in ips[:32]
                }
                for fut in concurrent.futures.as_completed(future_to_ip):
                    if fut.result():
                        open_ips.append(future_to_ip[fut])
            return open_ips
        except Exception:
            return []

    def scan_wireless_devices(
        self, auto_connect: bool = True
    ) -> Tuple[List[DeviceInfo], List[WirelessServiceInfo]]:
        """
        Scan Wi-Fi network for Android devices with Wireless Debugging active (PRD Section 3.1):
        1. Discovers mDNS services (`_adb-tls-connect._tcp` and `_adb-tls-pairing._tcp`)
           and LAN `:5555` endpoints.
        2. Automatically attempts `adb connect` on discovered connect endpoints if `auto_connect=True`.
        3. Records pairing-ready devices with status `Pairing Required` so they appear in the UI table.
        """
        services = self.discover_wireless_services()
        discovered_by_serial: Dict[str, DeviceInfo] = {}

        # First include any wireless devices already known to `adb devices -l`
        for dev in self.scan_devices():
            if dev.connection_type == ConnectionType.WIRELESS.value:
                discovered_by_serial[dev.device_serial] = dev

        for svc in services:
            if svc.is_connect_service:
                if auto_connect and svc.host_port not in discovered_by_serial:
                    _, _, dev = self.connect_wireless_device(svc.host_port)
                    if dev.device_name == dev.device_serial and svc.instance_name:
                        dev.device_name = f"{svc.instance_name} ({svc.host_port})"
                        if self.db is not None:
                            self.db.upsert_device(dev)
                    discovered_by_serial[svc.host_port] = dev
                elif svc.host_port not in discovered_by_serial:
                    dev = DeviceInfo(
                        device_serial=svc.host_port,
                        device_name=f"{svc.instance_name} ({svc.host_port})",
                        model=svc.instance_name,
                        android_version="12+",
                        connection_type=ConnectionType.WIRELESS.value,
                        connection_status=DeviceStatus.DISCONNECTED.value,
                        last_connected_at=utc_now_iso(),
                    )
                    if self.db is not None:
                        self.db.upsert_device(dev)
                    discovered_by_serial[svc.host_port] = dev

            elif svc.is_pairing_service:
                dev = DeviceInfo(
                    device_serial=svc.host_port,
                    device_name=f"{svc.instance_name} [Pairing Port]",
                    model=svc.instance_name,
                    android_version="12+",
                    connection_type=ConnectionType.WIRELESS.value,
                    connection_status=DeviceStatus.PAIRING_REQUIRED.value,
                    last_connected_at=utc_now_iso(),
                )
                if self.db is not None:
                    self.db.upsert_device(dev)
                discovered_by_serial[svc.host_port] = dev

        return list(discovered_by_serial.values()), services

    def _get_prop(self, serial: str, prop: str) -> str:
        res = self._run_adb(["-s", serial, "shell", "getprop", prop], timeout=5)
        return res.stdout.strip() if res.ok else ""

    def _inspect_connected_device(
        self, serial: str, conn_type: str, fallback_model: str
    ) -> DeviceInfo:
        """Query manufacturer, model, marketing name, and Android version via getprop."""
        manufacturer = self._get_prop(serial, "ro.product.manufacturer")
        model = self._get_prop(serial, "ro.product.model") or fallback_model
        market_name = (
            self._get_prop(serial, "ro.product.marketname")
            or self._get_prop(serial, "ro.config.marketing_name")
        )
        android_ver = self._get_prop(serial, "ro.build.version.release") or "Unknown"
        sdk_str = self._get_prop(serial, "ro.build.version.sdk")
        sdk_int = int(sdk_str) if sdk_str.isdigit() else None

        if market_name:
            device_name = market_name
        elif manufacturer and model:
            if model.lower().startswith(manufacturer.lower()):
                device_name = model
            else:
                device_name = f"{manufacturer} {model}"
        else:
            device_name = model or serial

        return DeviceInfo(
            device_serial=serial,
            device_name=device_name.strip(),
            model=model.strip(),
            android_version=android_ver.strip(),
            connection_type=conn_type,
            connection_status=DeviceStatus.CONNECTED.value,
            sdk_int=sdk_int,
            last_connected_at=utc_now_iso(),
        )

    def pair_wireless_device(self, host_port: str, pairing_code: str) -> Tuple[bool, str]:
        """Pair an Android 12+ device using Wireless Debugging pairing code (PRD Section 3.1)."""
        target = host_port.strip()
        code = pairing_code.strip()
        if not target or ":" not in target:
            return False, "Format IP:Port pairing tidak valid (contoh: 192.168.1.10:37123)"
        if not code:
            return False, "Kode pairing tidak boleh kosong"

        res = self._run_adb(["pair", target, code], timeout=15)
        output = res.combined_output
        lowered = output.lower()
        if "operation not permitted" in lowered or "cannot connect to daemon" in lowered:
            self._run_adb(["kill-server"], timeout=5)
            self._run_adb(["start-server"], timeout=8)
            res = self._run_adb(["pair", target, code], timeout=15)
            output = res.combined_output
            lowered = output.lower()
        if res.ok and ("successfully paired" in lowered or "paired to" in lowered):
            return True, output
        if (
            "protocol fault" in lowered
            or "failed to handshake" in lowered
            or "unable to start pairing client" in lowered
        ):
            return (
                False,
                f"Gagal melakukan handshake pairing ke {target}.\n\n"
                "Penyebab paling umum:\n"
                "1. Port yang dimasukkan adalah Port Connect utama, BUKAN Port Pairing.\n"
                "2. Popup 'Pair device with pairing code' di layar HP sudah tertutup.\n\n"
                "Solusi: Di HP buka 'Pair device with pairing code', lalu pastikan angka Port setelah titik dua (:) "
                "sama persis dengan Port yang tampil di dalam popup kode 6 digit tersebut.",
            )
        return False, output or "Gagal melakukan pairing perangkat"

    def connect_wireless_device(self, host_port: str) -> Tuple[bool, str, DeviceInfo]:
        """Connect to a paired Android device over Wireless Debugging (`adb connect ip:port`)."""
        target = host_port.strip()
        if not target or ":" not in target:
            err_dev = DeviceInfo(
                device_serial=target or "invalid",
                device_name="Unknown",
                model="Unknown",
                android_version="Unknown",
                connection_type=ConnectionType.WIRELESS.value,
                connection_status=DeviceStatus.ERROR.value,
            )
            return False, "Format IP:Port tidak valid (contoh: 192.168.1.10:5555)", err_dev

        res = self._run_adb(["connect", target], timeout=15)
        output = res.combined_output
        lowered = output.lower()
        if "operation not permitted" in lowered or "cannot connect to daemon" in lowered:
            self._run_adb(["kill-server"], timeout=5)
            self._run_adb(["start-server"], timeout=8)
            res = self._run_adb(["connect", target], timeout=15)
            output = res.combined_output
            lowered = output.lower()

        if res.ok and ("connected to" in lowered or "already connected" in lowered):
            devices = self.scan_devices()
            for dev in devices:
                if dev.device_serial == target:
                    return True, output, dev
            info = self._inspect_connected_device(
                target, ConnectionType.WIRELESS.value, target
            )
            if self.db is not None:
                self.db.upsert_device(info)
            return True, output, info

        if (
            "failed to connect" in lowered
            or "connection refused" in lowered
            or "protocol fault" in lowered
            or "couldn't read status message" in lowered
        ):
            status = DeviceStatus.PAIRING_REQUIRED.value
            if "protocol fault" in lowered or "couldn't read status message" in lowered:
                output = (
                    f"Perangkat {target} menolak koneksi karena belum di-Pair dengan Mac ini "
                    "(Status: Pairing Required).\n\n"
                    "Cara Pairing (Hanya 1x):\n"
                    "1. Di HP Android, masuk ke Wireless Debugging → ketuk 'Pair device with pairing code'.\n"
                    "2. Perhatikan IP & Port yang tertulis di DALAM popup kode 6 digit "
                    f"(angka port-nya BERBEDA dari port Connect {target}).\n"
                    "3. Masukkan IP:Port dari popup tersebut beserta 6 digit kode ke kolom Pairing di atas, "
                    "lalu klik 'Pair Device'."
                )
        else:
            status = DeviceStatus.ERROR.value

        failed_info = DeviceInfo(
            device_serial=target,
            device_name=target,
            model="Unknown",
            android_version="Unknown",
            connection_type=ConnectionType.WIRELESS.value,
            connection_status=status,
            last_connected_at=utc_now_iso(),
        )
        if self.db is not None:
            self.db.upsert_device(failed_info)
        return False, output or "Gagal terhubung ke perangkat wireless", failed_info

    def disconnect_device(self, serial: str) -> Tuple[bool, str]:
        """Disconnect a wireless device or mark a device as Disconnected (PRD Section 3.1)."""
        target = serial.strip()
        if detect_connection_type(target) == ConnectionType.WIRELESS.value:
            res = self._run_adb(["disconnect", target], timeout=10)
            ok = res.ok
            msg = res.combined_output or f"Disconnected {target}"
        else:
            ok = True
            msg = f"Perangkat USB {target} ditandai Disconnected"

        if self.db is not None:
            existing = [d for d in self.db.list_devices() if d.device_serial == target]
            if existing:
                dev = existing[0]
                dev.connection_status = DeviceStatus.DISCONNECTED.value
                self.db.upsert_device(dev)

        return ok, msg

    def reconnect_device(self, serial: str) -> Tuple[bool, str]:
        """Attempt to reconnect a USB or Wireless device (PRD Section 3.1)."""
        target = serial.strip()
        if detect_connection_type(target) == ConnectionType.WIRELESS.value:
            ok, msg, _ = self.connect_wireless_device(target)
            return ok, msg

        res = self._run_adb(["-s", target, "reconnect"], timeout=10)
        self.scan_devices()
        return res.ok, res.combined_output or f"Reconnect dikirim ke {target}"

    def _prune_stale_wireless_duplicates(self, active_dev: DeviceInfo) -> None:
        """
        Remove stale IP:Port entries for the same physical device that are no longer
        present/connected in `adb devices -l` (when Android 12+ Wireless Debugging
        changes its ephemeral port or DHCP IP).
        """
        if self.db is None or not active_dev or not active_dev.model:
            return
        live_res = self._run_adb(["devices"], timeout=5)
        live_connected_serials = set()
        if live_res.ok:
            for line in live_res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1].lower() == "device":
                    live_connected_serials.add(parts[0])

        all_devs = self.db.list_devices()
        for d in all_devs:
            if d.device_serial == active_dev.device_serial:
                continue
            same_model = (
                d.model.strip().lower() == active_dev.model.strip().lower()
                and active_dev.model.strip().lower() not in ("", "unknown", "unknown model")
            )
            is_dead_router = d.device_serial.endswith(".1:5555")
            not_live = d.device_serial not in live_connected_serials
            if is_dead_router or (
                same_model
                and d.connection_type == ConnectionType.WIRELESS.value
                and (not_live or d.connection_status != DeviceStatus.CONNECTED.value)
            ):
                if ":" in d.device_serial and not_live:
                    self._run_adb(["disconnect", d.device_serial], timeout=4)
                with self.db.connection() as conn:
                    conn.execute(
                        "DELETE FROM devices WHERE device_serial = ?",
                        (d.device_serial,),
                    )

    def ensure_device_ready(
        self, device: Optional[DeviceInfo]
    ) -> Optional[DeviceInfo]:
        """
        Verify that `device` is actively reachable via ADB (`adb -s <serial> get-state == device`).
        If a wireless device changed its ephemeral port or IP (causing the old serial to go
        Offline / not found), automatically re-scan mDNS (`_adb-tls-connect._tcp`) and reconnect.
        """
        if device is None:
            return None
        if (
            device.device_serial.startswith("SIMULATOR")
            or self._runner is not default_command_runner
        ):
            return device

        state_res = self._run_adb(
            ["-s", device.device_serial, "get-state"], timeout=4
        )
        if state_res.ok and state_res.stdout.strip().lower() == "device":
            device.connection_status = DeviceStatus.CONNECTED.value
            if self.db is not None:
                self.db.upsert_device(device)
            self._prune_stale_wireless_duplicates(device)
            return device

        # Device serial is offline or changed port -> auto-reconnect via mDNS / ADB
        self._run_adb(["reconnect", "offline"], timeout=5)
        devices, _ = self.scan_wireless_devices(auto_connect=True)
        connected = [
            d
            for d in devices
            if d.connection_status == DeviceStatus.CONNECTED.value
        ]
        if not connected:
            usb_devs = self.scan_devices()
            connected = [
                d
                for d in usb_devs
                if d.connection_status == DeviceStatus.CONNECTED.value
            ]

        if connected:
            best = connected[0]
            self._prune_stale_wireless_duplicates(best)
            return best

        device.connection_status = DeviceStatus.OFFLINE.value
        if self.db is not None:
            self.db.upsert_device(device)
        return device

    @staticmethod
    def validate_device_for_scraping(device: Optional[DeviceInfo]) -> Tuple[bool, str]:
        """
        Validate PRD Section 2.2, 3.1 & 4.3 rules before starting a scraping job:
        - Device must be present.
        - Status must be Connected (authorized).
        - Android version must be >= 12 (API 31+).
        """
        if device is None:
            return False, "Tidak ada perangkat Android yang dipilih."

        if device.connection_status == DeviceStatus.UNAUTHORIZED.value:
            return (
                False,
                "Perangkat belum diotorisasi (Unauthorized). Silakan setujui prompt USB/Wireless Debugging di layar HP.",
            )

        if device.connection_status != DeviceStatus.CONNECTED.value:
            return (
                False,
                f"Perangkat tidak dalam status Connected (status saat ini: {device.connection_status}).",
            )

        if not device.is_supported_android:
            return (
                False,
                f"Versi Android ({device.android_version}) tidak memenuhi syarat minimum Android {MIN_ANDROID_VERSION}+.",
            )

        return True, "Perangkat siap digunakan untuk scraping."
