import logging
import threading
from typing import Optional

import requests

from . import config

log = logging.getLogger(__name__)


class LLMClient:
    """
    OpenAI-compatible chat completion client.
    Works with any server that implements POST /v1/chat/completions.
    Maintains full conversation history; call reset() to clear it.
    """

    def __init__(self):
        self._history: list = []
        self._lock = threading.Lock()

    def chat(self, user_text: str) -> Optional[str]:
        with self._lock:
            self._history.append({"role": "user", "content": user_text})
            messages = [
                {"role": "system", "content": config.LLM_SYSTEM_PROMPT}
            ] + self._history

        payload = {
            "model": config.LLM_MODEL,
            "messages": messages,
            "max_tokens": config.LLM_MAX_TOKENS,
            "stream": False,
        }
        headers = {"Authorization": config.LLM_API_KEY}
        url = f"{config.LLM_BASE_URL}/chat/completions"

        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=config.LLM_TIMEOUT,
            )
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            with self._lock:
                self._history.append({"role": "assistant", "content": reply})
            log.debug("LLM reply: %r", reply)
            return reply
        except requests.RequestException as exc:
            log.error("LLM request failed: %s", exc)
            return None
        except (KeyError, IndexError) as exc:
            log.error("LLM unexpected response: %s", exc)
            return None

    def reset(self):
        with self._lock:
            self._history.clear()
        log.info("Conversation history cleared.")
