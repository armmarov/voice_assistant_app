import io
import logging
import threading
import wave
from typing import List

import pyaudio

from . import config

log = logging.getLogger(__name__)


def pcm_frames_to_wav(pcm_frames: List[bytes], sample_rate: int, channels: int) -> bytes:
    """Wrap raw 16-bit PCM frames in a WAV container."""
    raw = b"".join(pcm_frames)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return buf.getvalue()


class AudioPlayer:
    """Plays WAV audio bytes through the system speaker (blocking)."""

    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._lock = threading.Lock()

    def play(self, wav_bytes: bytes):
        with self._lock:
            buf = io.BytesIO(wav_bytes)
            try:
                with wave.open(buf, "rb") as wf:
                    open_kwargs = dict(
                        format=self._pa.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True,
                    )
                    if config.SPK_DEVICE_INDEX >= 0:
                        open_kwargs["output_device_index"] = config.SPK_DEVICE_INDEX
                    stream = self._pa.open(**open_kwargs)
                    chunk_size = 1024
                    data = wf.readframes(chunk_size)
                    while data:
                        stream.write(data)
                        data = wf.readframes(chunk_size)
                    stream.stop_stream()
                    stream.close()
            except Exception as exc:
                log.error("Playback error: %s", exc)

    def terminate(self):
        self._pa.terminate()
