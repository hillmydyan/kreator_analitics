"""
Android 12+ Wireless Debugging QR Code Pairing Generator (AOSP / Android Studio format).

How Android 12+ 'Pair device with QR code' works:
1. Desktop generates a random service name `S` (e.g. `studio-a1b2c3d4`) and pairing code `P`
   and encodes `WIFI:T:ADB;S:<service_name>;P:<password>;;` into a QR Code.
2. User opens Developer Options -> Wireless Debugging -> 'Pair device with QR code' on the phone
   and scans the QR code on the desktop screen.
3. The phone starts its pairing server using password `P` and broadcasts `<service_name>._adb-tls-pairing._tcp.local.`
   over mDNS (Wi-Fi).
4. Desktop detects the `_adb-tls-pairing._tcp` service via mDNS, automatically executes
   `adb pair <ip>:<pairing_port> <password>`, and then auto-connects to `<ip>:<connect_port>`.
"""

from __future__ import annotations

import random
import string
from typing import List, Optional, Tuple


def generate_adb_wifi_qr_credentials(
    service_name: Optional[str] = None,
    password: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Generate `(service_name, password, qr_payload)` for Android 12+ Wireless Debugging QR Pairing.
    Payload format required by Android Settings: `WIFI:T:ADB;S:<service_name>;P:<password>;;`
    """
    if not service_name:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        service_name = f"studio-{suffix}"
    if not password:
        password = "".join(random.choices(string.digits, k=6))
    payload = f"WIFI:T:ADB;S:{service_name};P:{password};;"
    return service_name, password, payload


# ----------------------------------------------------------------------
# Pure-Python ISO/IEC 18004 QR Code Matrix Generator (Versions 1..4, ECC L)
# Zero external dependencies; works identically on macOS and Windows (.exe).
# ----------------------------------------------------------------------
# Version parameters for ECC Level L: (version, size, data_codewords, ec_codewords, alignment_centers)
_QR_VERSIONS_L = (
    (1, 21, 19, 7, ()),
    (2, 25, 34, 10, (6, 18)),
    (3, 29, 55, 15, (6, 22)),
    (4, 33, 80, 20, (6, 26)),
)


def _gf_mul(a: int, b: int) -> int:
    """Multiply two bytes in GF(2^8) with primitive polynomial 0x11D."""
    res = 0
    while b > 0:
        if b & 1:
            res ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
        b >>= 1
    return res


def _rs_generator_poly(degree: int) -> List[int]:
    """Compute Reed-Solomon generator polynomial of given degree in GF(2^8)."""
    poly = [1]
    root = 1
    for _ in range(degree):
        new_poly = [0] * (len(poly) + 1)
        for i, coeff in enumerate(poly):
            new_poly[i] ^= coeff
            new_poly[i + 1] ^= _gf_mul(coeff, root)
        poly = new_poly
        root = _gf_mul(root, 2)
    return poly


def _rs_remainder(data: List[int], ec_count: int) -> List[int]:
    """Compute `ec_count` Reed-Solomon error correction bytes for `data`."""
    gen = _rs_generator_poly(ec_count)
    rem = [0] * ec_count
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(ec_count):
            rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


def _bch_format_bits(ec_level_bits: int, mask_id: int) -> int:
    """Compute 15-bit format information word with BCH(15,5) and mask 0x5412."""
    data = (ec_level_bits << 3) | mask_id
    rem = data << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= 0x537 << i
    return ((data << 10) | rem) ^ 0x5412


def encode_qr_matrix(text: str) -> List[List[bool]]:
    """
    Encode an ASCII/UTF-8 string (up to 78 bytes) into a 2D boolean QR Code matrix
    (`True` = black module, `False` = white module).
    """
    raw_bytes = text.encode("utf-8")
    n_bytes = len(raw_bytes)

    chosen = None
    for ver_info in _QR_VERSIONS_L:
        _, _, data_cap, _, _ = ver_info
        # Byte mode overhead: 4-bit mode (0100) + 8-bit length + 4-bit terminator = 2 bytes
        if n_bytes + 2 <= data_cap:
            chosen = ver_info
            break
    if chosen is None:
        raise ValueError(f"QR payload too long ({n_bytes} bytes)")

    _, size, data_cap, ec_count, align_coords = chosen

    # 1. Build bit stream: Mode 0100 (Byte) + 8-bit char count + data bytes + terminator
    bits: List[int] = [0, 1, 0, 0]
    for i in range(7, -1, -1):
        bits.append((n_bytes >> i) & 1)
    for b in raw_bytes:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)

    max_data_bits = data_cap * 8
    term_len = min(4, max_data_bits - len(bits))
    bits.extend([0] * term_len)
    while len(bits) % 8 != 0:
        bits.append(0)

    data_codewords: List[int] = []
    for i in range(0, len(bits), 8):
        val = 0
        for bit in bits[i : i + 8]:
            val = (val << 1) | bit
        data_codewords.append(val)

    pad_bytes = (0xEC, 0x11)
    pad_idx = 0
    while len(data_codewords) < data_cap:
        data_codewords.append(pad_bytes[pad_idx % 2])
        pad_idx += 1

    # 2. Compute Reed-Solomon ECC codewords
    ec_codewords = _rs_remainder(data_codewords, ec_count)
    all_codewords = data_codewords + ec_codewords

    all_bits: List[int] = []
    for cw in all_codewords:
        for i in range(7, -1, -1):
            all_bits.append((cw >> i) & 1)

    # 3. Initialize matrix and function pattern reservation map
    matrix: List[List[bool]] = [[False] * size for _ in range(size)]
    reserved: List[List[bool]] = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, val: bool) -> None:
        if 0 <= r < size and 0 <= c < size:
            matrix[r][c] = val
            reserved[r][c] = True

    # Finder patterns + white separators
    def place_finder(top_r: int, left_c: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top_r + dr, left_c + dc
                if 0 <= r < size and 0 <= c < size:
                    in_outer = 0 <= dr <= 6 and 0 <= dc <= 6
                    on_border = dr in (0, 6) or dc in (0, 6)
                    in_center = 2 <= dr <= 4 and 2 <= dc <= 4
                    set_func(r, c, in_outer and (on_border or in_center))

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    # Alignment patterns
    if align_coords:
        for ar in align_coords:
            for ac in align_coords:
                if (ar == 6 and ac == 6) or (ar == 6 and ac == size - 7) or (ar == size - 7 and ac == 6):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        val = max(abs(dr), abs(dc)) in (0, 2)
                        set_func(ar + dr, ac + dc, val)

    # Timing patterns
    for i in range(8, size - 8):
        set_func(6, i, i % 2 == 0)
        set_func(i, 6, i % 2 == 0)

    # Dark module and reserved format information areas
    set_func(size - 8, 8, True)
    for i in range(9):
        if not reserved[8][i]:
            reserved[8][i] = True
        if not reserved[i][8]:
            reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True

    # 4. Place data bits in upward/downward 2-column zig-zag
    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        row_iter = range(size - 1, -1, -1) if upward else range(size)
        for r in row_iter:
            for dc in (0, -1):
                c = col + dc
                if not reserved[r][c]:
                    val = bool(all_bits[bit_idx]) if bit_idx < len(all_bits) else False
                    bit_idx += 1
                    # Apply Mask Pattern 0: (r + c) % 2 == 0
                    if (r + c) % 2 == 0:
                        val = not val
                    matrix[r][c] = val
        upward = not upward
        col -= 2

    # 5. Write 15-bit format info for ECC Level L (0b01) and Mask 0 (0b000)
    fmt = _bch_format_bits(0b01, 0)
    for i in range(6):
        matrix[8][i] = bool((fmt >> (14 - i)) & 1)
    matrix[8][7] = bool((fmt >> 8) & 1)
    matrix[8][8] = bool((fmt >> 7) & 1)
    matrix[7][8] = bool((fmt >> 6) & 1)
    for i in range(9, 15):
        matrix[14 - i][8] = bool((fmt >> (14 - i)) & 1)

    for i in range(8):
        matrix[8][size - 1 - i] = bool((fmt >> i) & 1)
    for i in range(7):
        matrix[size - 1 - i][8] = bool((fmt >> (14 - i)) & 1)

    return matrix
