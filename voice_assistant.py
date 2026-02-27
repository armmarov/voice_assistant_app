#!/usr/bin/env python3
"""
Voice Assistant — entry point
------------------------------
Flow: Mic → Wake Word (Porcupine) → VAD → ASR → LLM → TTS → Speaker

Run directly:   python voice_assistant.py
Run as service: systemctl start voice_assistant
Compile:        make build

Configuration:
  Copy .env.example → .env and fill in your values.
  All settings can also be set as regular environment variables (env vars
  override .env values).

Required:
  PORCUPINE_ACCESS_KEY  — from https://console.picovoice.ai/
  WAKE_WORD_MODEL_PATH  — path to .ppn file; omit to use built-in "porcupine" keyword
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the current working directory (where the binary / script is run from).
# Using Path.cwd() ensures the compiled binary finds .env relative to where it was
# launched, not relative to the Nuitka temp extraction directory.
# If .env does not exist this is a no-op.
load_dotenv(dotenv_path=Path.cwd() / ".env")

from src import config
from src.daemon import VoiceAssistantDaemon


def _setup_logging() -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(config.LOG_FILE))
    except PermissionError:
        pass  # no write access to log file; stdout only
    logging.basicConfig(level=config.LOG_LEVEL, format=fmt, handlers=handlers)


if __name__ == "__main__":
    _setup_logging()
    VoiceAssistantDaemon().run()
