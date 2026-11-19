"""Windows calls shared by the wall and its windows: the user32 handle and the monitor under the pointer."""
import ctypes
import logging
from ctypes import wintypes


log = logging.getLogger("tesserawall")


user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
GA_ROOT = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def pictures_folder() -> str:
    """The user's Pictures folder, wherever Windows keeps it (it may be redirected, e.g. to OneDrive)."""
    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
    pictures = GUID(0x33E28130, 0x4E1E, 0x4676, (ctypes.c_ubyte * 8)(0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB))
    out = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(pictures), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception as e:
        log.info("Pictures folder lookup failed: %s", e)
    import os
    return os.path.join(os.path.expanduser("~"), "Pictures")


CF_UNICODETEXT, CF_HDROP = 13, 15


def read_clipboard() -> tuple[str, list]:
    """(text, files) on the clipboard: copied text, and files copied in Explorer. Win32 directly rather than
    Tk, so it can run in a worker thread: getting data that the owning app renders on request blocks until
    that app answers, which must not freeze the wall."""
    import time
    k32, shell32 = ctypes.windll.kernel32, ctypes.windll.shell32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    for _ in range(10):                      # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        log.info("clipboard: could not open it")
        return "", []
    text, files = "", []
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            drop = user32.GetClipboardData(CF_HDROP)
            if drop:
                for i in range(shell32.DragQueryFileW(drop, 0xFFFFFFFF, None, 0)):
                    buf = ctypes.create_unicode_buffer(shell32.DragQueryFileW(drop, i, None, 0) + 1)
                    shell32.DragQueryFileW(drop, i, buf, len(buf))
                    files.append(buf.value)
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            ptr = k32.GlobalLock(handle) if handle else None
            if ptr:
                try:
                    text = ctypes.wstring_at(ptr)
                finally:
                    k32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
    return text, files


def monitor_under_cursor() -> tuple[int, int, int, int]:
    """(x, y, width, height) in real pixels of the monitor the pointer is on."""
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromPoint(pt, 2), ctypes.byref(info))   # 2 = nearest monitor
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top
