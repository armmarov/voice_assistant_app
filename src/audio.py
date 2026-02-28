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

    _CHUNK = 1024

    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._lock = threading.Lock()

    def play(self, wav_bytes: bytes):
        with self._lock:
            buf = io.BytesIO(wav_bytes)
            stream = None
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

                    # Read all PCM data up front so we know the total duration.
                    frames = wf.readframes(wf.getnframes())
                    duration_s = wf.getnframes() / wf.getframerate()

                # Run the blocking write loop in a daemon thread so we can
                # enforce a hard timeout and never hang the pipeline forever.
                def _write_loop():
                    try:
                        offset = 0
                        while offset < len(frames):
                            chunk = frames[offset: offset + self._CHUNK]
                            stream.write(chunk)
                            offset += self._CHUNK
                    except Exception as exc:
                        log.error("Playback write error: %s", exc)

                t = threading.Thread(target=_write_loop, daemon=True)
                t.start()
                t.join(timeout=duration_s + 10)
                if t.is_alive():
                    log.warning("Playback timed out after %.1fs, aborting.", duration_s + 10)

            except Exception as exc:
                log.error("Playback error: %s", exc)
            finally:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass

    def terminate(self):
        self._pa.terminate()
