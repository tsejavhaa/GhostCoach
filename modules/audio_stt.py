"""
Audio + Speech-to-Text Module
Uses sounddevice for mic capture and faster-whisper for transcription.
Optimized for Apple Silicon (M1) with int8 quantization.
"""
import threading
import queue
import numpy as np
from typing import Callable


class AudioSTT:
    SAMPLE_RATE = 16000
    CHUNK_SECONDS = 5      # transcribe every 5 seconds
    SILENCE_THRESHOLD = 0.01

    def __init__(self, model_size: str = "base"):
        self.model = None
        self.model_size = model_size
        self.recording = False
        self._audio_queue: queue.Queue = queue.Queue()
        self._stream = None
        self._worker_thread: threading.Thread | None = None
        self.on_transcription: Callable[[str], None] | None = None
        self._audio_buffer: list[float] = []

    # ── Model loading ──────────────────────────────────────────────
    def load_model(self) -> bool:
        """Load faster-whisper model. Call once before recording."""
        from faster_whisper import WhisperModel
        self.model = WhisperModel(
            self.model_size,
            device="cpu",          # M1 uses CPU; fast enough with int8
            compute_type="int8"
        )
        return True

    # ── Recording control ──────────────────────────────────────────
    def start_recording(
        self,
        callback: Callable[[str], None] | None = None,
        device_index: int | None = None
    ):
        """Start continuous microphone capture + live transcription."""
        import sounddevice as sd

        if callback:
            self.on_transcription = callback

        self.recording = True
        self._audio_buffer = []

        def _audio_cb(indata, frames, time_info, status):
            if self.recording:
                chunk = indata[:, 0].tolist()
                self._audio_buffer.extend(chunk)
                if len(self._audio_buffer) >= self.SAMPLE_RATE * self.CHUNK_SECONDS:
                    segment = np.array(
                        self._audio_buffer[: self.SAMPLE_RATE * self.CHUNK_SECONDS],
                        dtype=np.float32,
                    )
                    self._audio_buffer = self._audio_buffer[self.SAMPLE_RATE * self.CHUNK_SECONDS :]
                    if np.abs(segment).mean() > self.SILENCE_THRESHOLD:
                        self._audio_queue.put(segment)

        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            callback=_audio_cb,
            device=device_index,
        )
        self._stream.start()

        self._worker_thread = threading.Thread(
            target=self._transcription_loop, daemon=True
        )
        self._worker_thread.start()

    def stop_recording(self):
        """Stop recording and transcription."""
        self.recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ── Internal ───────────────────────────────────────────────────
    def _transcription_loop(self):
        while self.recording:
            try:
                audio = self._audio_queue.get(timeout=1.0)
                if self.model and self.on_transcription:
                    segments, _ = self.model.transcribe(
                        audio,
                        language="en",
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                    )
                    text = " ".join(seg.text for seg in segments).strip()
                    if text:
                        self.on_transcription(text)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[STT] Transcription error: {e}")
                # Keep looping — one bad audio chunk shouldn't kill the session

    # ── Utilities ──────────────────────────────────────────────────
    def get_audio_devices(self) -> list[tuple[int, str]]:
        """Return list of (index, name) for available input devices."""
        import sounddevice as sd
        devs = sd.query_devices()
        return [
            (i, d["name"])
            for i, d in enumerate(devs)
            if d["max_input_channels"] > 0
        ]

    def transcribe_file(self, path: str) -> str:
        """Transcribe an audio file directly (utility)."""
        if not self.model:
            return ""
        segments, _ = self.model.transcribe(path, language="en")
        return " ".join(seg.text for seg in segments).strip()