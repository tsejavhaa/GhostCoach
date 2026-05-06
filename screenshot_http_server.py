#!/usr/bin/env python3
"""
Tiny HTTP screenshot server for Computer B (macOS).

Run on the MacBook:
  python3 codex/screenshot_http_server.py --host 0.0.0.0 --port 8765

Then on the iMac app, connect to:
  IP address: 192.168.x.x
  Port: 8765

Important:
  The Python app on Computer B must be allowed under macOS Screen Recording.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from PIL import Image

try:
    import Quartz

    HAS_QUARTZ = True
except ImportError:
    HAS_QUARTZ = False

FRAME_REFRESH_SECONDS = 0.12


def screen_recording_permission_granted() -> bool | None:
    """
    Return whether Screen Recording permission is granted for this process.
    Returns None when the CoreGraphics preflight API is unavailable.
    """
    framework = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        cg = ctypes.CDLL(framework)
        preflight = cg.CGPreflightScreenCaptureAccess
        preflight.restype = ctypes.c_bool
        return bool(preflight())
    except Exception:
        return None


def request_screen_recording_access() -> bool | None:
    """
    Ask macOS to show the Screen Recording permission prompt when possible.
    Returns the API result, or None when unavailable.
    """
    framework = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        cg = ctypes.CDLL(framework)
        request = cg.CGRequestScreenCaptureAccess
        request.restype = ctypes.c_bool
        return bool(request())
    except Exception:
        return None


def capture_png_bytes() -> bytes:
    fd, path = tempfile.mkstemp(prefix="interview_screen_", suffix=".png")
    os.close(fd)
    try:
        subprocess.run(
            ["screencapture", "-x", "-t", "png", path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def capture_backend_name() -> str:
    return "quartz" if HAS_QUARTZ else "screencapture"


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
        return Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1).convert("RGB")
    except Exception as e:
        print(f"[Quartz capture] {e}")
        return None


def capture_screen_image() -> Image.Image:
    if HAS_QUARTZ:
        try:
            display_id = Quartz.CGMainDisplayID()
            cg_image = Quartz.CGDisplayCreateImage(display_id)
            image = _cgimage_to_pil(cg_image)
            if image is not None:
                return image
        except Exception as e:
            print(f"[Quartz capture] Falling back to screencapture: {e}")

    with Image.open(BytesIO(capture_png_bytes())) as img:
        return img.copy()


class FrameCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_image: Image.Image | None = None
        self._latest_error = ""
        self._latest_timestamp = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _worker(self):
        while not self._stop_event.is_set():
            started = time.perf_counter()
            try:
                image = capture_screen_image()
                with self._lock:
                    self._latest_image = image
                    self._latest_error = ""
                    self._latest_timestamp = time.time()
            except Exception as e:
                with self._lock:
                    self._latest_error = str(e)
            elapsed = time.perf_counter() - started
            sleep_for = max(0.01, FRAME_REFRESH_SECONDS - elapsed)
            self._stop_event.wait(sleep_for)

    def snapshot(self) -> tuple[Image.Image | None, float, str]:
        with self._lock:
            image = self._latest_image.copy() if self._latest_image is not None else None
            return image, self._latest_timestamp, self._latest_error

    def frame_age_seconds(self) -> float | None:
        with self._lock:
            if self._latest_timestamp <= 0:
                return None
            return max(0.0, time.time() - self._latest_timestamp)


FRAME_CACHE = FrameCache()


def _parse_positive_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def build_image_bytes(
    *,
    image_format: str = "png",
    quality: int = 85,
    max_width: int = 0,
    max_height: int = 0,
) -> tuple[bytes, str]:
    fmt = (image_format or "png").strip().lower()
    if fmt not in {"png", "jpeg", "jpg"}:
        fmt = "png"

    wants_resize = max_width > 0 or max_height > 0
    wants_jpeg = fmt in {"jpeg", "jpg"}
    image, _timestamp, _error = FRAME_CACHE.snapshot()
    if image is None:
        image = capture_screen_image()
    working = image.convert("RGB") if wants_jpeg else image.copy()

    if wants_resize:
        target_w = max_width or working.width
        target_h = max_height or working.height
        working.thumbnail((target_w, target_h), Image.BILINEAR)

    out = BytesIO()
    if wants_jpeg:
        working.save(out, format="JPEG", quality=quality)
        return out.getvalue(), "image/jpeg"

    working.save(out, format="PNG")
    return out.getvalue(), "image/png"


class ScreenshotHandler(BaseHTTPRequestHandler):
    server_version = "InterviewScreenshotHTTP/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/health":
            permission = screen_recording_permission_granted()
            body = json.dumps(
                {
                    "ok": True,
                    "screen_recording_permission": permission,
                    "capture_backend": capture_backend_name(),
                    "frame_age_seconds": FRAME_CACHE.frame_age_seconds(),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/screenshot":
            permission = screen_recording_permission_granted()
            if permission is False:
                body = (
                    "screen recording permission not granted for Python/Terminal on Computer B.\n"
                    "Open System Settings -> Privacy & Security -> Screen Recording and allow it,\n"
                    "then restart the screenshot server.\n"
                ).encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            image_format = (params.get("format", ["png"])[0] or "png").lower()
            quality = _parse_positive_int(params.get("quality", [None])[0], 85, minimum=40, maximum=95)
            max_width = _parse_positive_int(params.get("max_width", [None])[0], 0, minimum=0, maximum=7680)
            max_height = _parse_positive_int(params.get("max_height", [None])[0], 0, minimum=0, maximum=4320)

            try:
                data, content_type = build_image_bytes(
                    image_format=image_format,
                    quality=quality,
                    max_width=max_width,
                    max_height=max_height,
                )
            except subprocess.CalledProcessError as e:
                err = e.stderr.decode("utf-8", errors="replace").strip() or str(e)
                body = f"screencapture failed: {err}\n".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as e:
                body = f"server error: {e}\n".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        body = b"Not found\n"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args):
        print(f"[HTTP] {self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiny HTTP screenshot server for macOS.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="Bind port, default: 8765")
    parser.add_argument(
        "--request-access",
        action="store_true",
        help="Ask macOS to show the Screen Recording permission prompt before starting.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.request_access:
        result = request_screen_recording_access()
        print(f"Screen Recording access request result: {result}")

    permission = screen_recording_permission_granted()
    FRAME_CACHE.start()
    server = ThreadingHTTPServer((args.host, args.port), ScreenshotHandler)
    print(f"Screenshot server listening on http://{args.host}:{args.port}")
    print("Endpoints:")
    print("  GET /health")
    print("  GET /screenshot")
    print(f"Screen Recording permission granted: {permission}")
    print(f"Capture backend: {capture_backend_name()}")
    print("If screenshots fail, grant Screen Recording permission to Python/Terminal on Computer B.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        FRAME_CACHE.stop()
        server.server_close()


if __name__ == "__main__":
    main()


# Introduce yourelf with bacground and experiences.