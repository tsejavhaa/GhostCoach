"""
Local window capture for macOS.

Designed for capturing a Screen Sharing / VNC viewer window locally on the iMac.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import tempfile
from dataclasses import dataclass

from PIL import Image

try:
    import Quartz

    HAS_QUARTZ = True
except ImportError:
    HAS_QUARTZ = False

try:
    from AppKit import NSWorkspace

    HAS_NSWORKSPACE = True
except ImportError:
    HAS_NSWORKSPACE = False

SKIP_APPS = {
    "Window Server",
    "Dock",
    "SystemUIServer",
    "Control Center",
    "Notification Center",
    "loginwindow",
    "Finder",
    "Interview Assistant",
}


@dataclass
class WindowInfo:
    window_id: int
    app_name: str
    title: str = ""
    on_screen: bool = False
    pid: int = 0

    def label(self) -> str:
        title_suffix = f" — {self.title}" if self.title else ""
        visibility = "" if self.on_screen else " [other Space]"
        return f"{self.app_name}{title_suffix}{visibility}"


class LocalWindowCapture:
    def __init__(self):
        self.connected = False
        self.target_window: WindowInfo | None = None

    def connect(self) -> tuple[bool, str]:
        if not HAS_QUARTZ:
            return False, "Install pyobjc-framework-Quartz on this Mac."
        self.connected = True
        return True, "Local window capture ready"

    def disconnect(self):
        self.connected = False
        self.target_window = None

    def list_windows(self) -> list[WindowInfo]:
        if not HAS_QUARTZ:
            return []

        results: list[WindowInfo] = []
        pid_to_wid: dict[int, int] = {}
        raw = Quartz.CGWindowListCopyWindowInfo(0, Quartz.kCGNullWindowID) or []
        on_screen_ids = {
            w.get("kCGWindowNumber")
            for w in (
                Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionOnScreenOnly,
                    Quartz.kCGNullWindowID,
                )
                or []
            )
        }

        seen: set[str] = set()
        for window in raw:
            app_name = str(window.get("kCGWindowOwnerName") or "")
            title = str(window.get("kCGWindowName") or "")
            window_id = int(window.get("kCGWindowNumber", 0))
            layer = int(window.get("kCGWindowLayer", 99))
            pid = int(window.get("kCGWindowOwnerPID", 0))

            if not app_name or app_name in SKIP_APPS:
                continue
            if layer > 1 or layer < 0:
                continue

            if pid and window_id:
                pid_to_wid.setdefault(pid, window_id)

            key = f"{app_name}||{title}"
            if key in seen:
                continue
            seen.add(key)

            results.append(
                WindowInfo(
                    window_id=window_id,
                    app_name=app_name,
                    title=title,
                    on_screen=(window_id in on_screen_ids),
                    pid=pid,
                )
            )

        if HAS_NSWORKSPACE:
            known_lower = {w.app_name.lower() for w in results}
            try:
                for app in NSWorkspace.sharedWorkspace().runningApplications():
                    if app.activationPolicy() != 0:
                        continue
                    app_name = str(app.localizedName() or "")
                    pid = int(app.processIdentifier())
                    if not app_name or app_name in SKIP_APPS:
                        continue
                    if app_name.lower() in known_lower:
                        continue
                    results.append(
                        WindowInfo(
                            window_id=pid_to_wid.get(pid, 0),
                            app_name=app_name,
                            title="",
                            on_screen=False,
                            pid=pid,
                        )
                    )
            except Exception:
                pass

        results.sort(key=lambda w: (not w.on_screen, w.app_name.lower(), w.title.lower()))
        return results

    def set_target_window(self, window: WindowInfo | None):
        self.target_window = window

    def capture_screen(self) -> Image.Image | None:
        if not self.connected:
            return None
        if self.target_window and self.target_window.window_id > 0:
            image = self._capture_window(self.target_window.window_id)
            if image is not None:
                return image
        return self._capture_main_display()

    def _capture_window(self, window_id: int) -> Image.Image | None:
        if HAS_QUARTZ:
            try:
                cg_image = Quartz.CGWindowListCreateImage(
                    Quartz.CGRectNull,
                    Quartz.kCGWindowListOptionIncludingWindow,
                    window_id,
                    Quartz.kCGWindowImageBoundsIgnoreFraming,
                )
                image = _cgimage_to_pil(cg_image)
                if image is not None and not _is_blank(image):
                    return image
            except Exception:
                pass

        return _cli_screencapture_window(window_id)

    def _capture_main_display(self) -> Image.Image | None:
        if not HAS_QUARTZ:
            return None
        try:
            cg_image = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
            return _cgimage_to_pil(cg_image)
        except Exception:
            return None


def _cli_screencapture_window(window_id: int) -> Image.Image | None:
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        result = subprocess.run(
            ["screencapture", "-l", str(window_id), "-o", temp_path],
            capture_output=True,
            timeout=6,
        )
        if result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
            return Image.open(temp_path).copy().convert("RGB")
    except Exception:
        return None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return None


def _is_blank(image: Image.Image, threshold: int = 8) -> bool:
    try:
        return int(image.convert("L").getextrema()[1]) < threshold
    except Exception:
        return False


def _cgimage_to_pil(cg_image) -> Image.Image | None:
    if cg_image is None:
        return None
    try:
        width = int(Quartz.CGImageGetWidth(cg_image))
        height = int(Quartz.CGImageGetHeight(cg_image))
        if width <= 0 or height <= 0:
            return None

        bytes_per_row = width * 4
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        bitmap_info = Quartz.kCGImageAlphaNoneSkipFirst | Quartz.kCGBitmapByteOrder32Host
        context = Quartz.CGBitmapContextCreate(
            None,
            width,
            height,
            8,
            bytes_per_row,
            color_space,
            bitmap_info,
        )
        if context is None:
            return None

        Quartz.CGContextDrawImage(context, ((0, 0), (width, height)), cg_image)
        ptr = Quartz.CGBitmapContextGetData(context)
        if ptr is None:
            return None

        raw = bytes((ctypes.c_uint8 * (bytes_per_row * height)).from_address(int(ptr)))
        image = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1)
        return image.convert("RGB")
    except Exception:
        return None
