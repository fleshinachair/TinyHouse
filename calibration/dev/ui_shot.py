#!/usr/bin/env python3
"""A screenshot of Serum 2's own window showing a preset, so its panels can be read instead of guessed at.

  ui_shot.py "Preset name" out.png [--wait 8] [--crop x0,y0,x1,y1] [--scale 0.5]

Starts ui_child.py (which opens Serum's editor on the preset), waits for the window, grabs it from the screen and closes it. The window
appears on the screen for a few seconds. --crop (in window pixels) and --scale keep the picture small."""
import sys as _sys, pathlib as _pl; _sys.path[:0] = [_p for _p in (str(_pl.Path(__file__).resolve().parents[2]), str(_pl.Path(__file__).resolve().parents[1])) if _p not in _sys.path]  # the repo root and calibration/ (this script lives in calibration/dev/)
import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

HERE = Path(__file__).resolve().parents[1]
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def image_of(pid):
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(520)
    n = wintypes.DWORD(520)
    ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    ctypes.windll.kernel32.CloseHandle(h)
    return buf.value.lower()


def windows_of(pid):
    """Visible top-level windows of the child. A venv's python.exe starts the real interpreter as another process, so the window may be
    owned by a python process other than the one launched: any python process's window counts."""
    out = []
    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if (p.value == pid or "python" in image_of(p.value)) and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True
    user32.EnumWindows(cb(each), 0)
    return out


def grab_window(hwnd, w, h):
    """The window's own pixels via PrintWindow (PW_RENDERFULLCONTENT), so a window in front of it, or anything else on the screen, is never in the picture."""
    from PIL import Image
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    ok = user32.PrintWindow(hwnd, mdc, 2)

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPels", wintypes.LONG), ("biYPels", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    return img if ok else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset"); ap.add_argument("out")
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--crop", default="")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--click", default="", help="x,y;x,y ... in window pixels, clicked in order before the grab (e.g. a tab: osc 205,78 mix 275,78 fx 343,78 matrix 410,78 global 478,78)")
    a = ap.parse_args()
    child = subprocess.Popen([sys.executable, str(HERE / "ui_child.py"), a.preset])
    try:
        best = None
        t0 = time.time()
        while time.time() - t0 < a.wait + 30:
            time.sleep(1.0)
            ws = [w for w in windows_of(child.pid) if (w[2][2] - w[2][0]) > 400]
            if ws and time.time() - t0 >= a.wait:
                best = max(ws, key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]))
                break
        if not best:
            print("no Serum window appeared"); return 1
        hwnd, title, (l, t, r, b) = best
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(1.0)
        rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
        for xy in [c for c in a.click.split(";") if c]:
            parts = xy.split(",")
            cx, cy = int(parts[0]), int(parts[1])
            user32.SetCursorPos(rect.left + cx, rect.top + cy)
            time.sleep(0.2)
            if len(parts) > 2:                      # x,y,notches: scroll the wheel there (negative is down)
                user32.mouse_event(0x0800, 0, 0, int(parts[2]) * 120 & 0xFFFFFFFF, 0)
            else:
                user32.mouse_event(0x0002, 0, 0, 0, 0); time.sleep(0.05); user32.mouse_event(0x0004, 0, 0, 0, 0)    # left down, up
            time.sleep(1.0)
        img = grab_window(hwnd, rect.right - rect.left, rect.bottom - rect.top)
        if img is None:
            print("PrintWindow failed"); return 1
        if a.crop:
            x0, y0, x1, y1 = (int(v) for v in a.crop.split(","))
            img = img.crop((x0, y0, x1, y1))
        if a.scale != 1.0:
            img = img.resize((int(img.width * a.scale), int(img.height * a.scale)))
        img.save(a.out)
        print(f"window '{title}' {r - l}x{b - t}; saved {a.out} {img.width}x{img.height}")
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
