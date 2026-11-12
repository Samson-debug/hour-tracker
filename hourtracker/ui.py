"""Window helpers shared by the popup and the History / Settings window."""
import ctypes, re, winreg
from pathlib import Path

import webview

HERE = Path(__file__).parent
BG = {False: "#f5f4f1", True: "#161615"}   # must match --bg in base.css


def render(name, theme):
    from .core import render_page
    return render_page(name, theme)


def is_dark(theme):
    if theme in ("light", "dark"):
        return theme == "dark"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def tint_titlebar(window, dark):
    try:
        on = ctypes.c_int(int(dark))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(window.native.Handle.ToInt64()), 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass   # cosmetic only


def grab_focus(window):
    """Windows keeps background apps from taking the keyboard; borrow the active app's right to."""
    try:
        user32, me = ctypes.windll.user32, ctypes.windll.kernel32.GetCurrentThreadId()
        hwnd = ctypes.c_void_p(window.native.Handle.ToInt64())
        other = user32.GetWindowThreadProcessId(ctypes.c_void_p(user32.GetForegroundWindow()), None)
        user32.AttachThreadInput(other, me, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(other, me, False)
    except Exception:
        pass   # the window is still there; one click focuses it


def copy_text(text):
    """Put text on the Windows clipboard."""
    import time
    from ctypes import wintypes
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype, k32.GlobalAlloc.argtypes = wintypes.HGLOBAL, [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype, k32.GlobalLock.argtypes = wintypes.LPVOID, [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.restype, u32.SetClipboardData.argtypes = wintypes.HANDLE, [wintypes.UINT, wintypes.HANDLE]
    data = text.encode("utf-16-le") + b"\0\0"
    handle = k32.GlobalAlloc(0x0002, len(data))   # GMEM_MOVEABLE
    ctypes.memmove(k32.GlobalLock(handle), data, len(data))
    k32.GlobalUnlock(handle)
    for _ in range(20):
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("clipboard busy")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(13, handle):   # CF_UNICODETEXT
            raise OSError("clipboard refused")
    finally:
        u32.CloseClipboard()


def round_corners(window):
    try:
        pref = ctypes.c_int(2)   # Windows 11: rounded corners, also on frameless windows
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(window.native.Handle.ToInt64()), 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def open_window(name, theme, api, width, height, focus=True, title="Hour Tracker", **kw):
    """A centred window showing <name>.html, themed, with `api` callable from the page."""
    dark, screen = is_dark(theme), webview.screens[0]
    width, height = min(width, screen.width - 40), min(height, screen.height - 110)
    window = webview.create_window(
        title, html=render(name, theme), js_api=api, width=width, height=height,
        x=(screen.width - width) // 2, y=max(0, (screen.height - height) // 2 - 20),
        background_color=BG[dark], **kw)
    window.events.before_show += lambda: (tint_titlebar(window, dark), round_corners(window))
    if focus:
        window.events.shown += lambda: grab_focus(window)
    return window
