"""
Screen Capture Module (HTTP screenshot server)
Fetches PNG screenshots from a tiny HTTP server running on Computer B.
"""
import io
import json
import socket
import threading
import urllib.parse
import urllib.error
import urllib.request
from PIL import Image


class VNCCapture:
    def __init__(self):
        self.connected = False
        self.host = ""
        self.username = ""
        self.password = ""
        self.base_url = ""
        self.capture_backend = ""
        self.last_frame_age_seconds: float | None = None
        self._lock = threading.Lock()
        self._timeout_seconds = 6.0

    def connect(self, host: str, username: str = "", password: str = "") -> tuple[bool, str]:
        """
        Connect to the HTTP screenshot server on Computer B.
        host = IP/hostname
        username = port (kept for API compatibility with the old settings flow)
        password is unused
        """
        port = (username or "").strip() or "8765"
        self.base_url = self._build_base_url(host, port)
        self.host = host.strip()
        self.username = port
        self.password = ""

        try:
            request = urllib.request.Request(
                f"{self.base_url}/health",
                headers={"Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as resp:
                if resp.status != 200:
                    self.connected = False
                    return False, f"Server returned HTTP {resp.status}"
                raw_body = resp.read().decode("utf-8", errors="replace").strip()
                if raw_body.startswith("{"):
                    payload = json.loads(raw_body)
                    if not payload.get("ok"):
                        self.connected = False
                        return False, "Health check failed"
                    self.capture_backend = str(payload.get("capture_backend") or "").strip()
                    self.last_frame_age_seconds = payload.get("frame_age_seconds")
                    permission = payload.get("screen_recording_permission")
                    if permission is False:
                        self.connected = False
                        return (
                            False,
                            "Computer B has not granted Screen Recording permission to Python/Terminal.",
                        )
                elif "ok" not in raw_body.lower():
                    self.connected = False
                    snippet = raw_body[:120] if raw_body else "<empty response>"
                    return False, f"Unexpected health response: {snippet}"
        except json.JSONDecodeError:
            self.connected = False
            return False, "Health check returned invalid JSON. Restart the screenshot server on Computer B."
        except urllib.error.HTTPError as e:
            self.connected = False
            return False, f"Server error: HTTP {e.code}"
        except urllib.error.URLError as e:
            self.connected = False
            return False, self._format_url_error(e)
        except Exception as e:
            self.connected = False
            return False, f"Connection error: {e}"

        self.connected = True
        backend_suffix = f" ({self.capture_backend})" if self.capture_backend else ""
        return True, f"Connected → {self.base_url}{backend_suffix}"

    def _build_base_url(self, host: str, port: str) -> str:
        host = (host or "").strip()
        port = (port or "").strip() or "8765"

        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"http://{host}:{port}"

    def _format_url_error(self, error: urllib.error.URLError) -> str:
        reason = getattr(error, "reason", error)
        reason_text = str(reason).strip() or "unknown network error"
        lower_reason = reason_text.lower()

        if isinstance(reason, socket.timeout) or "timed out" in lower_reason:
            return (
                "Could not reach server: timed out. Confirm the screenshot server is running on "
                "Computer B, use Computer B's current LAN IP address, and allow Python/Terminal "
                "through macOS Firewall on port 8765."
            )

        if "connection refused" in lower_reason:
            return (
                "Could not reach server: connection refused. The IP address was reachable, but "
                "nothing is listening on that port. Start screenshot_http_server.py on Computer B "
                "and verify the port is 8765."
            )

        if "nodename nor servname provided" in lower_reason or "name or service not known" in lower_reason:
            return (
                "Could not reach server: host name not found. Check the IP address or full URL for "
                "Computer B."
            )

        return f"Could not reach server: {reason_text}"

    def capture_screen(
        self,
        *,
        max_width: int = 0,
        max_height: int = 0,
        image_format: str = "png",
        quality: int = 85,
    ) -> Image.Image | None:
        """Fetch the current screen from Computer B over HTTP."""
        if not self.connected or not self.base_url:
            return None

        try:
            with self._lock:
                params = {}
                if max_width > 0:
                    params["max_width"] = str(int(max_width))
                if max_height > 0:
                    params["max_height"] = str(int(max_height))
                if image_format:
                    params["format"] = image_format
                if quality:
                    params["quality"] = str(int(quality))
                query = urllib.parse.urlencode(params)
                url = f"{self.base_url}/screenshot"
                if query:
                    url = f"{url}?{query}"
                request = urllib.request.Request(
                    url,
                    headers={"Cache-Control": "no-cache"},
                )
                with urllib.request.urlopen(request, timeout=self._timeout_seconds) as resp:
                    if resp.status != 200:
                        return None
                    data = resp.read()
                return Image.open(io.BytesIO(data)).copy()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = str(e)
            print(f"[HTTP Capture] HTTP {e.code}: {detail}")
            return None
        except urllib.error.URLError as e:
            print(f"[HTTP Capture] URL error: {self._format_url_error(e)}")
            return None
        except Exception as e:
            print(f"[HTTP Capture] Error: {e}")
            return None

    def disconnect(self):
        self.connected = False
        self.base_url = ""
        self.capture_backend = ""
        self.last_frame_age_seconds = None
