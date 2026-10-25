"""AutoTyper: types text into any window as real keystrokes.

Paste your code into the box, press "Pick spot & type", then click wherever
the text should go. Or click into the target yourself and press F9. Or let
Gemini look at the screen and find the box to type in.
Esc stops typing at any time.

Windows only. Standard library only (no pip installs).
"""

import base64
import ctypes
import json
import os
import queue
import re
import struct
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import zlib
from ctypes import wintypes
from tkinter import filedialog, font as tkfont, ttk

APP_NAME = "AutoTyper"
HOTKEY_VK = 0x78  # F9
HOTKEY_LABEL = "F9"
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autotyper_settings.json")


# --------------------------------------------------------------------------
# Win32 plumbing
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = wintypes.LPARAM

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MONITOR_DEFAULTTONEAREST = 2
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
HALFTONE = 4
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MAPVK_VK_TO_VSC = 0

WH_KEYBOARD_LL = 13
HC_ACTION = 0
LLKHF_INJECTED = 0x10
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
SM_SWAPBUTTON = 23

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_A = 0x41
VK_V = 0x56
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# Navigation keys must be flagged "extended", otherwise Windows can treat
# them as numpad keys (and Shift+Home turns into plain Home with NumLock on).
EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetKeyState.argtypes = (ctypes.c_int,)
user32.GetKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.ReleaseDC.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SetStretchBltMode.argtypes = (wintypes.HDC, ctypes.c_int)
gdi32.SetBrushOrgEx.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
gdi32.StretchBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
gdi32.StretchBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT)
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _key_event(vk=0, scan=0, flags=0):
    event = INPUT(type=INPUT_KEYBOARD)
    event.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return event


def _vk_event(vk, up=False):
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    if up:
        flags |= KEYEVENTF_KEYUP
    return _key_event(vk, user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC), flags)


def _unicode_events(ch):
    # Characters outside the BMP (emoji etc.) go out as a UTF-16 surrogate pair.
    data = ch.encode("utf-16-le")
    events = []
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i:i + 2], "little")
        events.append(_key_event(0, unit, KEYEVENTF_UNICODE))
        events.append(_key_event(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return events


def _real_key_events(ch):
    """Physical-style key presses for ch on the current keyboard layout, or None."""
    if len(ch.encode("utf-16-le")) != 2:
        return None
    scan = user32.VkKeyScanW(ch)
    if scan == -1:
        return None
    vk, state = scan & 0xFF, (scan >> 8) & 0xFF
    if state & ~0x07:
        return None
    shift = bool(state & 1)
    if 0x41 <= vk <= 0x5A and user32.GetKeyState(VK_CAPITAL) & 1:
        shift = not shift
    mods = [m for m, on in ((VK_SHIFT, shift), (VK_CONTROL, state & 2), (VK_MENU, state & 4)) if on]
    return ([_vk_event(m) for m in mods] + [_vk_event(vk), _vk_event(vk, up=True)]
            + [_vk_event(m, up=True) for m in reversed(mods)])


def _mouse_event(flags):
    event = INPUT(type=INPUT_MOUSE)
    event.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return event


def _send(events):
    array = (INPUT * len(events))(*events)
    if user32.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
        raise OSError("Windows blocked the keystrokes. If the target app runs as administrator, "
                      "run AutoTyper as administrator too.")


def _is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _window_title(hwnd):
    buf = ctypes.create_unicode_buffer(200)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value or "another window"


# --------------------------------------------------------------------------
# Turning text into a keystroke plan
# --------------------------------------------------------------------------

# Characters after which an editor may insert something right of the cursor
# (a closing bracket or quote, or a closing HTML tag after ">").
AUTO_CLOSE_TRIGGERS = set("([{<>\"'`")
# Characters that can accept an open autocomplete suggestion in some editors.
COMMIT_CHARS = set(".(,;[\t")
WORD_CHAR = re.compile(r"[\w$]")
# Lines editors like to re-indent while you type them (else:, }, end, ...).
REINDENT_LINE = re.compile(
    r"^(?:[)\]}]|</|(?:else|elif|except|finally|catch|case|default|end|elsif|when|"
    r"rescue|ensure|fi|done|esac|then|do|public|private|protected)\b)"
)
# Typed then backspaced to delete a selection that may or may not exist.
# "!" is not a word char, bracket, quote, or autocomplete trigger in common editors.
SCRUB_CHAR = "!"

MODES = ("code", "plain", "paste")


def _text(s):
    return ("text", s)


def _key(vk, *mods, count=1):
    return ("key", vk, mods, count)


def _content_ops(content, dismiss_popups, scrub_right):
    """Type a line's content left to right.

    After every character an editor might auto-close, delete whatever it put to the
    right of the cursor. Nothing auto-inserted is then ever there to "type over", which
    matters because editors disagree on how typing over works (CodeMirror 6 swallows a
    whole closing triple quote on the first quote, for example). Before characters
    that can accept an autocomplete suggestion, close the popup.
    """
    ops, chunk = [], ""
    for j, ch in enumerate(content):
        if dismiss_popups and j and ch in COMMIT_CHARS and WORD_CHAR.match(content[j - 1]):
            if chunk:
                ops.append(_text(chunk))
                chunk = ""
            ops.append(_key(VK_ESCAPE))
        chunk += ch
        if ch in AUTO_CLOSE_TRIGGERS:
            ops.append(_text(chunk))
            chunk = ""
            ops += scrub_right
    if chunk:
        ops.append(_text(chunk))
    return ops


def build_plan(text, mode="code", tab_width=4, tabs_to_spaces=True, dismiss_popups=True,
               start_on_new_line=False):
    """Turn text into a list of ("text", s) / ("key", vk, mods, count) / ("settle",) steps.

    plain: type every character as-is, Enter for newlines.
    code:  same, but undo what code editors do on their own (auto-indent after
           Enter, auto-closed brackets and quotes, re-indenting "else:" or "}",
           autocomplete popups swallowing Enter) so the result matches exactly.
    paste: a single Ctrl+V (the caller puts the text on the clipboard).
    start_on_new_line presses Enter first, for typing below existing text.
    """
    if mode == "paste":
        return ([_key(VK_RETURN)] if start_on_new_line else []) + [_key(VK_V, VK_CONTROL)]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    scrub = [_text(SCRUB_CHAR), _key(VK_BACK)]
    scrub_right = [_key(VK_END, VK_SHIFT)] + scrub
    plan = []
    for i, line in enumerate(text.split("\n")):
        if tabs_to_spaces:
            line = line.expandtabs(tab_width)
        new_line = bool(i or start_on_new_line)
        if mode == "plain":
            if new_line:
                plan.append(_key(VK_RETURN))
            if line:
                plan.append(_text(line))
            continue

        line = line.rstrip()
        content = line.lstrip()
        indent = line[:len(line) - len(content)]
        if new_line:
            plan.append(_key(VK_RETURN))
            # Select whatever indentation the editor inserted and delete it.
            plan.append(_key(VK_HOME, VK_SHIFT))
            plan += scrub
        if indent:
            plan.append(_text(indent))
        plan += _content_ops(content, dismiss_popups, scrub_right)
        if not content:
            continue

        if new_line and REINDENT_LINE.match(content):
            # The editor may have re-indented this line while we typed it. Walk back to the
            # start of the content, select whatever indentation is there now, and retype ours.
            if dismiss_popups:
                plan.append(_key(VK_ESCAPE))
            plan += [_key(VK_LEFT, count=len(content)), _key(VK_HOME, VK_SHIFT)]
            plan += [_text(indent)] if indent else scrub
            plan.append(_key(VK_END))
        if dismiss_popups:
            # Give slow autocomplete a moment to show up, then close it so Enter makes a newline.
            plan += [("settle",), _key(VK_ESCAPE)]
    return plan


def plan_keystrokes(plan):
    total = 0
    for op in plan:
        if op[0] == "text":
            total += len(op[1])
        elif op[0] == "key":
            total += op[3]
    return total


def plan_seconds(plan, delay):
    seconds = 0.0
    for op in plan:
        if op[0] == "text":
            seconds += len(op[1]) * delay
        elif op[0] == "key":
            seconds += op[3] * delay * (2 if op[1] == VK_RETURN else 1)
        elif op[0] == "settle":
            seconds += _settle_time(delay)
    return seconds


def _settle_time(delay):
    return max(2 * delay, 0.03)


# --------------------------------------------------------------------------
# Keyboard watcher (F9 hotkey + Esc to stop) and the typer itself
# --------------------------------------------------------------------------

class KeyboardWatcher(threading.Thread):
    """Low-level keyboard hook. F9 asks to start typing; a real Esc press stops it.

    Keys we inject ourselves are flagged by Windows and ignored here, which is
    what lets the typer press Esc in editors without stopping itself.
    """

    def __init__(self, on_hotkey):
        super().__init__(daemon=True, name="keyboard-watcher")
        self.on_hotkey = on_hotkey
        self.armed = threading.Event()   # set while waiting for a click or typing
        self.escape = threading.Event()  # set when the user presses Esc while armed
        self.ready = threading.Event()
        self.ok = False
        self._thread_id = None
        self._proc = HOOKPROC(self._callback)

    def run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        self.ok = bool(hook)
        self.ready.set()
        if not hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not kb.flags & LLKHF_INJECTED:
                    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    if kb.vkCode == HOTKEY_VK:
                        if down and not self.armed.is_set():
                            self.on_hotkey()
                        return 1  # swallow it so the target app never sees F9
                    if kb.vkCode == VK_ESCAPE and self.armed.is_set():
                        if down:
                            self.escape.set()
                        return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


class Stopped(Exception):
    """Typing was cancelled (Esc, Stop button, focus moved, ...)."""


class Typer:
    def __init__(self, delay=0.012, real_keys=False, watcher=None, own_hwnd=None):
        self.delay = max(0.0, delay)
        self.real_keys = real_keys
        self.watcher = watcher
        self.own_hwnd = own_hwnd
        self.target = None
        self.target_pid = None
        self.cancel = threading.Event()
        self.stop_reason = "Stopped."
        self._esc_quiet_until = 0.0

    def stop(self, reason="Stopped."):
        self.stop_reason = reason
        self.cancel.set()

    def _sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)

    def _check_abort(self):
        if self.cancel.is_set():
            raise Stopped(self.stop_reason)
        if self.watcher is not None and self.watcher.ok:
            if self.watcher.escape.is_set():
                raise Stopped("Stopped with Esc.")
        elif time.monotonic() > self._esc_quiet_until and _is_down(VK_ESCAPE):
            raise Stopped("Stopped with Esc.")
        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)) and 0 <= pt.x <= 1 and 0 <= pt.y <= 1:
            raise Stopped("Stopped: mouse hit the top-left corner.")

    def _check(self):
        self._check_abort()
        if self.target:
            # Popups from the same app (tooltips, autocomplete) are fine; another app is not.
            fg = user32.GetForegroundWindow()
            if fg and fg != self.target and (fg == self.own_hwnd or _window_pid(fg) != self.target_pid):
                raise Stopped(f'Stopped: focus moved to "{_window_title(fg)}".')

    def wait_for_click(self):
        """Block until the user clicks somewhere (the click itself places the cursor)."""
        button = VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        for want_down in (False, True, False):  # release our button, then a full click
            while _is_down(button) != want_down:
                self._check_abort()
                time.sleep(0.01)
        time.sleep(0.3)  # let the click land and the caret settle

    def lock_target(self):
        """Remember the window we are typing into; stop if focus leaves it later."""
        deadline = time.monotonic() + 3
        while any(_is_down(vk) for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)):
            self._check_abort()
            if time.monotonic() > deadline:
                raise Stopped("Stopped: let go of Shift/Ctrl/Alt before typing starts.")
            time.sleep(0.02)
        target = user32.GetForegroundWindow()
        if not target or target == self.own_hwnd:
            raise Stopped("Cancelled: that click landed on AutoTyper itself.")
        self.target = target
        self.target_pid = _window_pid(target)

    def press(self, vk, *mods):
        self._check()
        _send([_vk_event(m) for m in mods] + [_vk_event(vk), _vk_event(vk, up=True)]
              + [_vk_event(m, up=True) for m in reversed(mods)])
        if vk == VK_ESCAPE:
            self._esc_quiet_until = time.monotonic() + 0.15
        self._sleep(self.delay * (2 if vk == VK_RETURN else 1))

    def type_char(self, ch):
        self._check()
        events = _real_key_events(ch) if self.real_keys else None
        _send(events or _unicode_events(ch))
        self._sleep(self.delay)

    def run(self, plan, progress=None):
        total = plan_keystrokes(plan)
        done = 0
        last_report = 0.0

        def tick():
            nonlocal done, last_report
            done += 1
            now = time.monotonic()
            if progress and now - last_report > 0.05:
                last_report = now
                progress(done, total)

        for op in plan:
            if op[0] == "text":
                for ch in op[1]:
                    if ch == "\t":
                        self.press(VK_TAB)
                    else:
                        self.type_char(ch)
                    tick()
            elif op[0] == "key":
                _, vk, mods, count = op
                for _ in range(count):
                    self.press(vk, *mods)
                    tick()
            elif op[0] == "settle":
                self._sleep(_settle_time(self.delay))
        if progress:
            progress(total, total)


# --------------------------------------------------------------------------
# Finding where to type with Gemini
# --------------------------------------------------------------------------

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# An alias Google keeps pointed at the newest Flash model, so it does not break
# when older versions are shut down.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
CAPTURE_MAX_SIDE = 1600
_QUANTIZE = bytes(v & 0xF8 for v in range(256))
AFTER_CLICK = {
    "end": "go to the end and type below",
    "replace": "replace what is there",
    "stay": "type where it clicked",
}
LOCATE_PROMPT = (
    "This is a screenshot of a computer screen. Someone wants to type text into: {hint}. "
    "Find that text input on the screen. If it is a code editor, box only the editable code area: "
    "leave out line numbers, tabs, toolbars, buttons, and console or output panels. "
    "Ignore any window titled AutoTyper.\n"
    "Reply with JSON: found (false if there is no matching place to type), box_2d as "
    "[ymin, xmin, ymax, xmax] scaled to 0-1000, label (a few words naming the input), and "
    "has_text (true if the input already contains text or code)."
)
LOCATE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "found": {"type": "BOOLEAN"},
        "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "label": {"type": "STRING"},
        "has_text": {"type": "BOOLEAN"},
    },
    "required": ["found", "box_2d", "label", "has_text"],
}


class GeminiError(Exception):
    """Gemini could not be asked, or its answer made no sense."""


def _encode_png(width, height, rgb):
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def capture_screen(hwnd=None, max_side=CAPTURE_MAX_SIDE):
    """Screenshot the monitor showing hwnd (or the mouse), scaled down to max_side.

    Returns (png_bytes, (left, top, width, height)) where the rectangle is the
    monitor's area in screen pixels.
    """
    if hwnd:
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    else:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        monitor = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("Could not read the screen size.")
    r = info.rcMonitor
    left, top, width, height = r.left, r.top, r.right - r.left, r.bottom - r.top
    scale = min(1.0, max_side / max(width, height))
    w, h = max(1, round(width * scale)), max(1, round(height * scale))

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    try:
        old = gdi32.SelectObject(mem_dc, bitmap)
        gdi32.SetStretchBltMode(mem_dc, HALFTONE)
        gdi32.SetBrushOrgEx(mem_dc, 0, 0, None)
        copied = gdi32.StretchBlt(mem_dc, 0, 0, w, h, screen_dc, left, top, width, height, SRCCOPY | CAPTUREBLT)
        gdi32.SelectObject(mem_dc, old)  # GetDIBits wants the bitmap deselected
        if not copied:
            raise OSError("Could not capture the screen.")
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                                  biPlanes=1, biBitCount=32, biCompression=0)
        pixels = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem_dc, bitmap, 0, h, pixels, ctypes.byref(header), 0) != h:
            raise OSError("Could not read the screenshot pixels.")
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)

    bgra = pixels.raw
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    # Dropping the 3 lowest bits of each color looks the same but compresses about 2.5x smaller.
    return _encode_png(w, h, bytes(rgb.translate(_QUANTIZE))), (left, top, width, height)


def build_locate_request(png, hint):
    return {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}},
            {"text": LOCATE_PROMPT.format(hint=hint.strip() or "the main text box or code editor")},
        ]}],
        "generationConfig": {"response_mime_type": "application/json", "response_schema": LOCATE_SCHEMA},
    }


def parse_locate_response(data):
    """Pull {"found", "box", "label", "has_text"} out of a generateContent response."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        if not reason and data.get("candidates"):
            reason = data["candidates"][0].get("finishReason")
        raise GeminiError(f"Gemini returned no answer ({reason or 'empty response'}).")
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        answer = json.loads(text)
    except ValueError:
        raise GeminiError(f"Gemini's answer was not valid JSON: {text[:120]!r}") from None
    if isinstance(answer, list):
        answer = answer[0] if answer else {}
    if not isinstance(answer, dict):
        raise GeminiError("Gemini's answer was not in the expected format.")

    found = bool(answer.get("found", True))
    box = answer.get("box_2d")
    if found:
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            raise GeminiError(f"Gemini did not give a usable box: {box!r}")
        ymin, xmin, ymax, xmax = (min(1000, max(0, round(v))) for v in box)
        if ymin >= ymax or xmin >= xmax:
            raise GeminiError(f"Gemini gave an empty box: {box!r}")
        box = (ymin, xmin, ymax, xmax)
    return {"found": found, "box": box, "label": str(answer.get("label") or "text input"),
            "has_text": bool(answer.get("has_text"))}


def box_to_screen(box, rect):
    """Map a 0-1000 [ymin, xmin, ymax, xmax] box onto the captured monitor: (x1, y1, x2, y2)."""
    ymin, xmin, ymax, xmax = box
    left, top, width, height = rect
    return (left + round(xmin * width / 1000), top + round(ymin * height / 1000),
            left + round(xmax * width / 1000), top + round(ymax * height / 1000))


def ask_gemini_where(png, hint, api_key, model=DEFAULT_GEMINI_MODEL, timeout=90):
    model = (model or DEFAULT_GEMINI_MODEL).strip().removeprefix("models/")
    request = urllib.request.Request(
        GEMINI_URL.format(model=urllib.parse.quote(model, safe="")),
        data=json.dumps(build_locate_request(png, hint)).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key.strip()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:  # error body was not the usual JSON
            message = e.reason
        raise GeminiError(f"Gemini error {e.code}: {message}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GeminiError(f"Could not reach Gemini: {getattr(e, 'reason', e)}") from None
    return parse_locate_response(data)


def click_at(x, y):
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    # SendInput presses physical buttons; with buttons swapped, a normal click is the physical right one.
    if user32.GetSystemMetrics(SM_SWAPBUTTON):
        _send([_mouse_event(MOUSEEVENTF_RIGHTDOWN), _mouse_event(MOUSEEVENTF_RIGHTUP)])
    else:
        _send([_mouse_event(MOUSEEVENTF_LEFTDOWN), _mouse_event(MOUSEEVENTF_LEFTUP)])


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

MODE_HINTS = {
    "code": "For coding sites in Chrome (LeetCode, HackerRank, Replit, vscode.dev...) and editors like "
            "VS Code or PyCharm. Also safe in plain text boxes. Undoes auto-indent, auto-closed brackets "
            "and autocomplete so spacing comes out exactly as written. Start on an empty line: code mode "
            "clears anything right of the cursor.",
    "plain": "Types every character exactly as it is. For Notepad, chat boxes, plain text fields, "
             "terminals, or editors with auto-indent turned off.",
    "paste": "Copies the text and presses Ctrl+V once. Fastest and exact, but does nothing "
             "where pasting is blocked.",
}


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _format_seconds(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)

        self.events = queue.Queue()
        self.worker = None
        self.typer = None
        self.watcher = KeyboardWatcher(lambda: self.events.put(("hotkey",)))
        self.watcher.start()
        self.watcher.ready.wait(2)

        saved = load_settings()
        self.mode = tk.StringVar(value=saved.get("mode", "code") if saved.get("mode") in MODES else "code")
        self.delay_ms = tk.DoubleVar(value=saved.get("delay_ms", 12))
        self.tab_width = tk.IntVar(value=saved.get("tab_width", 4))
        self.tabs_to_spaces = tk.BooleanVar(value=saved.get("tabs_to_spaces", True))
        self.dismiss_popups = tk.BooleanVar(value=saved.get("dismiss_popups", True))
        self.real_keys = tk.BooleanVar(value=saved.get("real_keys", False))
        self.gemini_key = tk.StringVar(value=saved.get("gemini_key") or os.environ.get("GEMINI_API_KEY", ""))
        self.gemini_model = tk.StringVar(value=saved.get("gemini_model") or DEFAULT_GEMINI_MODEL)
        self.gemini_hint = tk.StringVar(value=saved.get("gemini_hint", "the code editor"))
        after = saved.get("after_click") if saved.get("after_click") in AFTER_CLICK else "end"
        self.after_click = tk.StringVar(value=AFTER_CLICK[after])
        self.confirm_click = tk.BooleanVar(value=saved.get("confirm_click", True))
        self.overlay = None
        self.notice = None
        self._overlay_job = None
        self.target_confirmed = threading.Event()

        self._build()
        self.editor.insert("1.0", saved.get("text", ""))
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self._refresh()
        self._set_status(self._ready_message())
        self.update_idletasks()
        self.minsize(int(self.winfo_reqwidth() * 0.85), int(self.winfo_reqheight() * 0.7))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._poll)

    # ---- layout ----------------------------------------------------------

    def _build(self):
        style = ttk.Style(self)
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(14, 6))
        style.configure("Hint.TLabel", foreground="#5f6368")

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        # Everything below the text box is packed from the bottom up, so the
        # controls stay visible and only the text box shrinks on small windows.
        self.status = ttk.Label(outer, style="Hint.TLabel", wraplength=640, justify="left")
        self.status.pack(side="bottom", fill="x")

        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(12, 6))
        self.go_button = ttk.Button(actions, text="Pick spot & type", style="Go.TButton",
                                    command=lambda: self.start("pick"))
        self.go_button.pack(side="left")
        self.ai_button = ttk.Button(actions, text="Find with Gemini & type", style="Go.TButton",
                                    command=lambda: self.start("gemini"))
        self.ai_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=1)
        self.progress.pack(side="left", fill="x", expand=True, padx=(8, 0))

        speed = ttk.Frame(outer)
        speed.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Label(speed, text="Delay per key").pack(side="left")
        ttk.Scale(speed, from_=0, to=100, variable=self.delay_ms, length=220,
                  command=lambda _v: self._refresh()).pack(side="left", padx=8)
        self.speed_label = ttk.Label(speed, style="Hint.TLabel")
        self.speed_label.pack(side="left")

        ai = ttk.LabelFrame(outer, text="Find where to type with Gemini", padding=(10, 6))
        ai.pack(side="bottom", fill="x", pady=(10, 0))
        row = ttk.Frame(ai)
        row.pack(fill="x")
        ttk.Label(row, text="Type into").pack(side="left")
        ttk.Entry(row, textvariable=self.gemini_hint).pack(side="left", fill="x", expand=True, padx=(6, 10))
        ttk.Label(row, text="then").pack(side="left")
        ttk.Combobox(row, textvariable=self.after_click, values=list(AFTER_CLICK.values()), state="readonly",
                     width=28).pack(side="left", padx=(6, 0))
        row = ttk.Frame(ai)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="API key").pack(side="left")
        ttk.Entry(row, textvariable=self.gemini_key, show="•", width=26).pack(side="left", padx=(6, 10))
        ttk.Label(row, text="Model").pack(side="left")
        ttk.Entry(row, textvariable=self.gemini_model, width=20).pack(side="left", padx=(6, 10))
        ttk.Checkbutton(row, text="Ask before clicking", variable=self.confirm_click).pack(side="left")
        ttk.Label(ai, style="Hint.TLabel", text="Sends a screenshot of your screen to Google Gemini to find the box "
                  "to type in. Free API key: aistudio.google.com/apikey").pack(anchor="w", pady=(4, 0))

        target = ttk.LabelFrame(outer, text="Typing into", padding=(10, 6))
        target.pack(side="bottom", fill="x")

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="Text to type", font=("Segoe UI Semibold", 10)).pack(side="left")
        self.count_label = ttk.Label(top, style="Hint.TLabel")
        self.count_label.pack(side="left", padx=10)
        ttk.Button(top, text="Clear", command=self._clear).pack(side="right")
        ttk.Button(top, text="Load file...", command=self._load).pack(side="right", padx=(0, 6))

        box = ttk.Frame(outer)
        box.pack(fill="both", expand=True, pady=(6, 10))
        mono = tkfont.Font(family="Consolas", size=10)
        self.editor = tk.Text(box, wrap="none", undo=True, font=mono, relief="solid", borderwidth=1,
                              tabs=(mono.measure(" " * 4),), padx=6, pady=4, width=84, height=11)
        ys = ttk.Scrollbar(box, orient="vertical", command=self.editor.yview)
        xs = ttk.Scrollbar(box, orient="horizontal", command=self.editor.xview)
        self.editor.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.editor.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.editor.bind("<Control-a>", self._select_all)
        self.editor.bind("<Control-A>", self._select_all)
        self.editor.bind("<<Modified>>", self._on_modified)

        radios = ttk.Frame(target)
        radios.pack(fill="x")
        for value, label in (("code", "Code editor"), ("plain", "Plain text box"), ("paste", "Paste (Ctrl+V)")):
            ttk.Radiobutton(radios, text=label, value=value, variable=self.mode,
                            command=self._refresh).pack(side="left", padx=(0, 16))
        self.hint = ttk.Label(target, style="Hint.TLabel", wraplength=640, justify="left")
        self.hint.pack(fill="x", pady=(4, 6))
        for label in (self.hint, self.status):
            label.bind("<Configure>", lambda e: e.widget.configure(wraplength=max(200, e.width - 4)))

        tabs_row = ttk.Frame(target)
        tabs_row.pack(fill="x")
        self.tabs_check = ttk.Checkbutton(tabs_row, text="Turn tabs into spaces, width", variable=self.tabs_to_spaces,
                                          command=self._refresh)
        self.tabs_check.pack(side="left")
        self.tab_spin = ttk.Spinbox(tabs_row, from_=1, to=8, width=3, textvariable=self.tab_width,
                                    command=self._refresh)
        self.tab_spin.pack(side="left", padx=(4, 0))
        self.dismiss_check = ttk.Checkbutton(target, variable=self.dismiss_popups, command=self._refresh,
                                             text="Press Esc to close autocomplete popups (turn off for Jupyter / Colab)")
        self.dismiss_check.pack(anchor="w", pady=(2, 0))
        self.real_check = ttk.Checkbutton(target, variable=self.real_keys,
                                          text="Send real key presses (for VMs, remote desktop, games)")
        self.real_check.pack(anchor="w", pady=(2, 0))

    # ---- helpers ---------------------------------------------------------

    def _hwnd(self):
        try:
            return int(self.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return None

    def _ready_message(self):
        if self.watcher.ok:
            return (f"Ready. Pick a spot, or click into any window and press {HOTKEY_LABEL}. "
                    "Esc stops typing.")
        return ("Ready. (The F9 hotkey and Esc-to-stop could not be set up, so stop by moving "
                "the mouse to the top-left corner of the screen.)")

    def _set_status(self, text):
        self.status.configure(text=text)

    def _select_all(self, _event=None):
        self.editor.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _clear(self):
        self.editor.delete("1.0", "end")

    def _load(self):
        path = filedialog.askopenfilename(title="Load a file to type")
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                data = f.read()
        except OSError as e:
            self._set_status(f"Could not open that file: {e}")
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", data)

    def _on_modified(self, _event=None):
        if self.editor.edit_modified():
            self.editor.edit_modified(False)
            if getattr(self, "_refresh_job", None):
                self.after_cancel(self._refresh_job)
            self._refresh_job = self.after(250, self._refresh)

    def _after_click_key(self):
        label = self.after_click.get()
        return next((key for key, text in AFTER_CLICK.items() if text == label), "end")

    def _typing_options(self):
        try:
            width = max(1, min(8, int(self.tab_width.get())))
        except (tk.TclError, ValueError):
            width = 4
        return dict(mode=self.mode.get(), tab_width=width, tabs_to_spaces=self.tabs_to_spaces.get(),
                    dismiss_popups=self.dismiss_popups.get())

    def _refresh(self):
        self._refresh_job = None
        mode = self.mode.get()
        self.hint.configure(text=MODE_HINTS[mode])
        typing = mode != "paste"
        self.dismiss_check.configure(state="normal" if mode == "code" else "disabled")
        self.tabs_check.configure(state="normal" if typing else "disabled")
        self.tab_spin.configure(state="normal" if typing and self.tabs_to_spaces.get() else "disabled")
        self.real_check.configure(state="normal" if typing else "disabled")

        text = self.editor.get("1.0", "end-1c")
        lines = len(text.splitlines())
        self.count_label.configure(text=f"{lines} lines, {len(text)} characters")
        delay = round(self.delay_ms.get()) / 1000
        if typing and text:
            eta = plan_seconds(build_plan(text, **self._typing_options()), delay)
            self.speed_label.configure(text=f"{delay * 1000:.0f} ms (about {_format_seconds(eta)} for this text)")
        else:
            self.speed_label.configure(text=f"{delay * 1000:.0f} ms")

    # ---- typing ----------------------------------------------------------

    def start(self, how):
        """how: "pick" (type at the next click), "hotkey" (type at the cursor now) or
        "gemini" (find the spot on screen with Gemini, click it, then type)."""
        if self.worker and self.worker.is_alive():
            return
        text = self.editor.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("Nothing to type yet. Paste something into the box first.")
            return
        own = self._hwnd()
        if how == "hotkey":
            fg = user32.GetForegroundWindow()
            if not fg or fg == own:
                self._set_status(f"Click into the window you want to type in, then press {HOTKEY_LABEL}.")
                return
        gemini = None
        if how == "gemini":
            key = self.gemini_key.get().strip()
            if not key:
                self._set_status("Paste a Gemini API key first (free at aistudio.google.com/apikey).")
                return
            gemini = dict(key=key, model=self.gemini_model.get().strip() or DEFAULT_GEMINI_MODEL,
                          hint=self.gemini_hint.get(), after=self._after_click_key(), ask=self.confirm_click.get())

        options = self._typing_options()
        if options["mode"] == "paste":
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()

        self.typer = Typer(delay=round(self.delay_ms.get()) / 1000, real_keys=self.real_keys.get(),
                           watcher=self.watcher, own_hwnd=own)
        self.target_confirmed.clear()
        self.watcher.escape.clear()
        self.watcher.armed.set()
        self.go_button.configure(state="disabled")
        self.ai_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(value=0)
        if how != "hotkey":
            self.iconify()
        self.worker = threading.Thread(target=self._work, args=(self.typer, text, options, how, gemini),
                                       daemon=True)
        self.worker.start()

    def stop(self):
        if self.typer:
            self.typer.stop()

    def _work(self, typer, text, options, how, gemini):
        try:
            below_existing = False
            if how == "pick":
                self.events.put(("status", "Click where the text should go. Esc cancels."))
                typer.wait_for_click()
            elif how == "gemini":
                below_existing = self._find_and_click(typer, gemini)
            typer.lock_target()
            if how == "gemini" and gemini["after"] == "end":
                typer.press(VK_END, VK_CONTROL)
            elif how == "gemini" and gemini["after"] == "replace":
                typer.press(VK_A, VK_CONTROL)
                typer.press(VK_BACK)
            plan = build_plan(text, start_on_new_line=below_existing, **options)
            self.events.put(("status", "Typing... Esc stops it (so does moving the mouse to the "
                                       "top-left corner of the screen)."))
            typer.run(plan, lambda done, total: self.events.put(("progress", done, total)))
            outcome = "Done."
        except (Stopped, GeminiError) as e:
            outcome = str(e)
        except Exception as e:  # report anything unexpected instead of dying silently
            outcome = f"Something went wrong: {e}"
        finally:
            self.watcher.armed.clear()
        self.events.put(("finished", outcome, how != "hotkey"))

    def _find_and_click(self, typer, gemini):
        """Screenshot, ask Gemini where to type, show it, click it.

        Returns True when the text should start on a new line below what is already there.
        """
        time.sleep(0.6)  # let AutoTyper finish minimizing so it is not in the screenshot
        typer._check_abort()
        png, monitor = capture_screen(user32.GetForegroundWindow())
        self.events.put(("notice", f"Asking Gemini where to type ({gemini['model']})...  Esc cancels", monitor))
        spot = ask_gemini_where(png, gemini["hint"], gemini["key"], gemini["model"])
        typer._check_abort()
        if not spot["found"]:
            raise Stopped(f'Gemini could not find "{gemini["hint"]}" on the screen.')
        box = box_to_screen(spot["box"], monitor)
        self.events.put(("confirm", box, monitor, spot["label"], gemini["ask"]))
        while not self.target_confirmed.is_set():
            typer._check_abort()
            time.sleep(0.03)
        time.sleep(0.2)  # let the outline vanish before clicking under it
        click_at((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        time.sleep(0.35)
        return gemini["after"] == "end" and spot["has_text"]

    # ---- on-screen overlays (main thread only) ----------------------------

    OVERLAY_KEY = "#ff00fe"  # painted pixels in this color are see-through
    OVERLAY_RED = "#d93025"

    def _overlay_window(self):
        win = tk.Toplevel(self)
        win.withdraw()  # shown once it is sized and placed
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=self.OVERLAY_KEY)
        win.attributes("-transparentcolor", self.OVERLAY_KEY)
        return win

    def _show_notice(self, text, monitor):
        self._hide_overlays()
        left, top, width, _ = monitor
        win = tk.Toplevel(self)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        tk.Label(win, text=text, bg="#202124", fg="white", font=("Segoe UI", 10), padx=16, pady=8).pack()
        win.update_idletasks()
        win.geometry(f"+{left + (width - win.winfo_reqwidth()) // 2}+{top + 24}")
        win.deiconify()
        self.notice = win

    def _show_target(self, box, monitor, label, ask):
        """Outline the box Gemini picked, with a bar to confirm or cancel."""
        self._hide_overlays()
        x1, y1, x2, y2 = box
        mon_left, mon_top, mon_width, _ = monitor
        win = self._overlay_window()
        bar = tk.Frame(win, bg=self.OVERLAY_RED)
        message = f"Gemini found: {label}." + ("  Type here?" if ask else "  Typing in a moment, Esc cancels.")
        tk.Label(bar, text=message, bg=self.OVERLAY_RED, fg="white",
                 font=("Segoe UI Semibold", 10)).pack(side="left", padx=8, pady=4)
        if ask:
            ttk.Button(bar, text="Cancel (Esc)", command=lambda: self._answer_target(False)).pack(
                side="right", padx=(4, 6), pady=4)
            ttk.Button(bar, text="Type here (Enter)", command=lambda: self._answer_target(True)).pack(
                side="right", pady=4)
            win.bind("<Return>", lambda _e: self._answer_target(True))
            win.bind("<KP_Enter>", lambda _e: self._answer_target(True))
        win.update_idletasks()

        # Put the bar above the box when there is room, otherwise inside its top edge.
        bar_h = bar.winfo_reqheight()
        width = max(x2 - x1, bar.winfo_reqwidth() + 20)
        ox = max(mon_left, min(x1, mon_left + mon_width - width))
        oy = y1 - bar_h if y1 - bar_h >= mon_top else y1
        win.geometry(f"{width}x{y2 - oy}+{ox}+{oy}")
        canvas = tk.Canvas(win, bg=self.OVERLAY_KEY, highlightthickness=0)
        canvas.place(x=0, y=0, relwidth=1, relheight=1)
        canvas.create_rectangle(x1 - ox + 2, y1 - oy + 2, x2 - ox - 2, y2 - oy - 2, outline=self.OVERLAY_RED, width=4)
        bar.place(x=0, y=0, width=width, height=bar_h)
        bar.lift()
        win.deiconify()

        self.overlay = win
        if ask:
            win.after(50, win.focus_force)
            self._overlay_job = self.after(30000, lambda: self._answer_target(False))
        else:
            self._overlay_job = self.after(1200, lambda: self._answer_target(True))

    def _answer_target(self, ok):
        if self.overlay is None:
            return
        self._hide_overlays()
        if ok:
            self.target_confirmed.set()
        elif self.typer:
            self.typer.stop("Cancelled.")

    def _hide_overlays(self):
        if self._overlay_job:
            self.after_cancel(self._overlay_job)
            self._overlay_job = None
        for win in (self.overlay, self.notice):
            if win is not None:
                win.destroy()
        self.overlay = self.notice = None

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "hotkey":
                    self.start("hotkey")
                elif kind == "status":
                    self._set_status(event[1])
                elif kind == "notice":
                    self._show_notice(event[1], event[2])
                elif kind == "confirm":
                    self._show_target(*event[1:])
                elif kind == "progress":
                    done, total = event[1], max(1, event[2])
                    self.progress.configure(value=done / total)
                    self.title(f"{done * 100 // total}% - {APP_NAME}")
                elif kind == "finished":
                    outcome, restore = event[1], event[2]
                    self._hide_overlays()
                    self.title(APP_NAME)
                    self.go_button.configure(state="normal")
                    self.ai_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._set_status(f"{outcome} {self._ready_message()}")
                    if restore:
                        self.deiconify()
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def _on_close(self):
        self.stop()
        key = self.gemini_key.get().strip()
        save_settings(dict(self._typing_options(), delay_ms=round(self.delay_ms.get()),
                           real_keys=self.real_keys.get(), text=self.editor.get("1.0", "end-1c"),
                           gemini_key="" if key == os.environ.get("GEMINI_API_KEY", "") else key,
                           gemini_model=self.gemini_model.get().strip(), gemini_hint=self.gemini_hint.get(),
                           after_click=self._after_click_key(), confirm_click=self.confirm_click.get()))
        self.watcher.stop()
        self.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
