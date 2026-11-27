"""How much a video costs, and whether the PC has room for one more.

Measured on the development PC (6 x 1080p60, Phase 1): each player uses about 170 MB of graphics memory
and about 200 MB of RAM, whatever mpv's buffer settings. Graphics memory scales roughly with picture
size, so a 4K video is estimated at about 500 MB. These are estimates for a safety margin, not exact.
"""
import ctypes
import os
import winreg
from ctypes import wintypes

RAM_PER_VIDEO_MB = 220
RAM_RESERVE_MB = 768          # leave this much RAM free for Windows and everything else
VRAM_SHARE = 0.8              # use at most this share of the graphics card's memory
INTEGRATED_BELOW_MB = 2048    # less "dedicated" memory than this: integrated graphics, no card limit


def video_cost_mb(w: int = 1920, h: int = 1080) -> float:
    """Estimated graphics memory for one player showing a w x h video (1080p if not known yet)."""
    return 60 + 110 * (w * h) / (1920 * 1080)


def gpu_memory_mb() -> int | None:
    """Dedicated memory of the largest graphics card, from the driver's registry entry (64-bit value,
    unlike Win32_VideoController.AdapterRAM, which stops at 4 GB). None if it cannot be read."""
    if os.environ.get("TESSERAWALL_VRAM_MB"):          # tests: pretend to have a small card
        return int(os.environ["TESSERAWALL_VRAM_MB"])
    best = None
    key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as cls:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        size, _ = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")
                        size = int.from_bytes(size, "little") if isinstance(size, bytes) else int(size)
                        best = max(best or 0, size // 2**20)
                except OSError:
                    continue
    except OSError:
        return None
    if best is not None and best < INTEGRATED_BELOW_MB:
        # Integrated graphics (Intel, AMD APUs) report only a small reserved slice here (often 128-512 MB)
        # and really draw on system RAM, which the RAM check covers. As a card limit it would refuse the
        # very first video.
        return None
    return best


def free_ram_mb() -> int:
    class MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMSTAT(dwLength=ctypes.sizeof(MEMSTAT))
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ullAvailPhys // 2**20)


def room_for_one_more(costs_mb: list[float], vram_mb: int | None, ram_free_mb: int) -> tuple[bool, str]:
    """Can another (1080p-sized, until known) video be opened next to videos costing `costs_mb`?
    The first video is always allowed: refusing it would only leave an empty wall."""
    if not costs_mb:
        return True, ""
    need = sum(costs_mb) + video_cost_mb()
    if vram_mb and need > vram_mb * VRAM_SHARE:
        return False, f"not enough video memory (about {need:.0f} of {vram_mb} MB needed)"
    if ram_free_mb - RAM_PER_VIDEO_MB < RAM_RESERVE_MB:
        return False, f"not enough free memory ({ram_free_mb} MB free)"
    return True, ""
