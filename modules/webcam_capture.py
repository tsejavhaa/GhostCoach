"""
Webcam Capture Module
Reads frames directly from a local USB/built-in webcam using OpenCV.
"""
import threading
from PIL import Image
import cv2

class WebcamCapture:
    def __init__(self):
        self.connected = False
        self._cam = None
        self._device_idx = 0
        self._lock = threading.Lock()

    def connect(self, device_index: int = 0) -> tuple[bool, str]:
        
        try:
            with self._lock:
                if self._cam:
                    self._cam.release()

                cam = self._open_camera(device_index)
                if not cam.isOpened():
                    self.connected = False
                    return False, f"Could not open camera {device_index}"

                for _ in range(3):
                    cam.read()

                self._cam = cam
                self._device_idx = device_index
                self.connected = True
                return True, f"Camera {device_index} opened"
        except Exception as e:
            self.connected = False
            return False, str(e)

    def _open_camera(self, device_index: int):
        
        backends = [None]
        avfoundation = getattr(cv2, "CAP_AVFOUNDATION", None)
        if avfoundation is not None:
            backends.insert(0, avfoundation)

        for backend in backends:
            cam = (
                cv2.VideoCapture(device_index, backend)
                if backend is not None
                else cv2.VideoCapture(device_index)
            )
            if cam.isOpened():
                cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                return cam
            cam.release()

        return cv2.VideoCapture(device_index)

    def capture_screen(self) -> Image.Image | None:
        if not self.connected or not self._cam:
            return None

        try:
            with self._lock:
                ok, frame = self._cam.read()
                if not ok or frame is None:
                    return None
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb)
        except Exception as e:
            print(f"[Webcam] Capture error: {e}")
            self.connected = False
            return None

    @staticmethod
    def list_cameras(max_check: int = 5) -> list[int]:

        backends = [None]
        avfoundation = getattr(cv2, "CAP_AVFOUNDATION", None)
        if avfoundation is not None:
            backends.insert(0, avfoundation)

        available = []
        for i in range(max_check):
            found = False
            for backend in backends:
                cam = (
                    cv2.VideoCapture(i, backend)
                    if backend is not None
                    else cv2.VideoCapture(i)
                )
                if cam.isOpened():
                    available.append(i)
                    found = True
                cam.release()
                if found:
                    break
        return available

    def disconnect(self):
        with self._lock:
            if self._cam:
                self._cam.release()
                self._cam = None
        self.connected = False
