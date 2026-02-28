import logging
from typing import Iterator, Optional

import requests

from . import config

log = logging.getLogger(__name__)


class TTSClient:
    """Send text to the TTS service and return WAV bytes or a PCM stream."""

    def synthesize(self, text: str, timeout: Optional[int] = None) -> Optional[bytes]:
        """Return complete WAV bytes (non-streaming). Used for short ack phrases."""
        payload = {
            "target_text": text,
            "voice_type": config.TTS_VOICE,
            "stream": False,
        }
        try:
            resp = requests.post(
                config.TTS_ENDPOINT,
                json=payload,
                timeout=timeout if timeout is not None else config.TTS_TIMEOUT,
            )
            resp.raise_for_status()
            log.debug("TTS received %d bytes", len(resp.content))
            return resp.content
        except requests.RequestException as exc:
            log.error("TTS request failed: %s", exc)
            return None

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        """Yield raw PCM chunks (44100 Hz, mono, 16-bit) as they arrive.

        Audio starts playing before TTS finishes generating, cutting perceived
        latency from the full generation time down to the first-chunk delay.
        """
        payload = {
            "target_text": text,
            "voice_type": config.TTS_VOICE,
            "stream": True,
        }
        try:
            resp = requests.post(
                config.TTS_ENDPOINT,
                json=payload,
                timeout=(10, config.TTS_TIMEOUT),   # 10s connect; TTS_TIMEOUT read timeout
                stream=True,
            )
            resp.raise_for_status()
            chunks = 0
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    chunks += 1
                    yield chunk
            log.debug("TTS stream complete: %d chunks", chunks)
        except requests.RequestException as exc:
            log.error("TTS stream failed: %s", exc)
