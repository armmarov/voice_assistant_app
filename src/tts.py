import logging
from typing import Optional

import requests

from . import config

log = logging.getLogger(__name__)


class TTSClient:
    """Send text to the TTS service and return WAV bytes."""

    def synthesize(self, text: str) -> Optional[bytes]:
        payload = {
            "target_text": text,
            "voice_type": config.TTS_VOICE,
            "stream": False,
        }
        try:
            resp = requests.post(
                config.TTS_ENDPOINT,
                json=payload,
                timeout=config.TTS_TIMEOUT,
            )
            resp.raise_for_status()
            log.debug("TTS received %d bytes", len(resp.content))
            return resp.content
        except requests.RequestException as exc:
            log.error("TTS request failed: %s", exc)
            return None
