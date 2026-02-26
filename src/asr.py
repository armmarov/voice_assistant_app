import base64
import logging
from typing import Optional

import requests

from . import config

log = logging.getLogger(__name__)


class ASRClient:
    """Send a WAV buffer to the ASR service and return transcribed text."""

    def transcribe(self, wav_bytes: bytes) -> Optional[str]:
        b64 = base64.b64encode(wav_bytes).decode()
        try:
            resp = requests.post(
                config.ASR_ENDPOINT,
                json={"wav_base64": b64},
                timeout=config.ASR_TIMEOUT,
            )
            resp.raise_for_status()
            text = resp.json().get("text", "").strip()
            log.debug("ASR result: %r", text)
            return text or None
        except requests.RequestException as exc:
            log.error("ASR request failed: %s", exc)
            return None
