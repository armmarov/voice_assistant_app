#!/usr/bin/env python3
"""
TTS Service Test
----------------
Tests the TTS (Text-to-Speech) service.

Usage:
    python tests/test_tts.py                         # synthesizes default test phrase
    python tests/test_tts.py --text "Hello robot"    # custom text
    python tests/test_tts.py --voice liudao          # specific voice
    python tests/test_tts.py --play                  # play audio after synthesis (requires pyaudio)
    python tests/test_tts.py --save output.wav       # save audio to file

Config (via .env or environment variables):
    TTS_BASE_URL   default: http://voice-api.zetrix.com:8006
    TTS_VOICE      default: default
"""

import argparse
import os
import sys
import time
import wave
from io import BytesIO

from dotenv import load_dotenv

load_dotenv()

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

TTS_BASE_URL = os.getenv("TTS_BASE_URL", "http://voice-api.zetrix.com:8006")
TTS_VOICE    = os.getenv("TTS_VOICE",    "default")
TTS_TIMEOUT  = int(os.getenv("TTS_TIMEOUT", "60"))

DEFAULT_TEXT = "Hello, I am your voice assistant. This is a test of the text to speech service."

# ─── ANSI colours ─────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"  {CYAN}→{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def wav_duration(wav_bytes: bytes) -> float:
    """Return duration of WAV in seconds."""
    try:
        with wave.open(BytesIO(wav_bytes)) as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def play_wav(wav_bytes: bytes) -> None:
    """Play WAV bytes through the default speaker."""
    try:
        import pyaudio
        import wave
        pa = pyaudio.PyAudio()
        with wave.open(BytesIO(wav_bytes)) as wf:
            stream = pa.open(
                format=pa.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
            )
            data = wf.readframes(1024)
            while data:
                stream.write(data)
                data = wf.readframes(1024)
            stream.stop_stream()
            stream.close()
        pa.terminate()
        ok("Playback complete.")
    except ImportError:
        fail("pyaudio not installed — cannot play audio.")
    except Exception as e:
        fail(f"Playback failed: {e}")

# ─── Tests ────────────────────────────────────────────────────────────────────

def test_health() -> bool:
    header("1. Health Check  GET /health")
    url = f"{TTS_BASE_URL}/health"
    info(f"URL: {url}")
    try:
        t0 = time.time()
        resp = requests.get(url, timeout=10)
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()
        ok(f"Status {resp.status_code}  ({elapsed:.0f} ms)")
        ok(f"Response: {data}")
        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail("Request timed out")
    except Exception as e:
        fail(f"{e}")
    return False


def test_list_voices() -> bool:
    header("2. List Voices  GET /voices")
    url = f"{TTS_BASE_URL}/voices"
    info(f"URL: {url}")
    try:
        t0 = time.time()
        resp = requests.get(url, timeout=10)
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()
        ok(f"Status {resp.status_code}  ({elapsed:.0f} ms)")
        available = [v for v, enabled in data.items() if enabled]
        ok(f"Available voices: {YELLOW}{', '.join(available)}{RESET}")
        unavailable = [v for v, enabled in data.items() if not enabled]
        if unavailable:
            info(f"Unavailable voices: {', '.join(unavailable)}")
        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail("Request timed out")
    except Exception as e:
        fail(f"{e}")
    return False


def test_synthesize(text: str, voice: str, save_path: str = None, play: bool = False) -> bool:
    header(f"3. Speech Synthesis  POST /generate")
    url = f"{TTS_BASE_URL}/generate"
    info(f"URL  : {url}")
    info(f"Text : \"{text}\"")
    info(f"Voice: {voice}")

    payload = {
        "target_text": text,
        "voice_type": voice,
        "stream": False,
    }
    try:
        t0 = time.time()
        resp = requests.post(url, json=payload, timeout=TTS_TIMEOUT)
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        wav_bytes = resp.content
        duration = wav_duration(wav_bytes)

        ok(f"Status       : {resp.status_code}  ({elapsed:.0f} ms)")
        ok(f"Content-Type : {content_type}")
        ok(f"Audio size   : {len(wav_bytes):,} bytes")
        ok(f"Audio duration: {duration:.2f} s")

        if save_path:
            with open(save_path, "wb") as f:
                f.write(wav_bytes)
            ok(f"Saved to: {save_path}")

        if play:
            info("Playing audio …")
            play_wav(wav_bytes)

        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail(f"Request timed out after {TTS_TIMEOUT}s")
    except requests.HTTPError:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        fail(f"{e}")
    return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TTS service test")
    parser.add_argument("--text",  default=DEFAULT_TEXT, help="Text to synthesize")
    parser.add_argument("--voice", default=TTS_VOICE,    help="Voice name (default/liudao/filrty/zhiyu)")
    parser.add_argument("--save",  metavar="FILE",        help="Save output WAV to this file")
    parser.add_argument("--play",  action="store_true",   help="Play audio after synthesis")
    args = parser.parse_args()

    print(f"\n{BOLD}=== TTS Service Test ==={RESET}")
    print(f"Base URL : {TTS_BASE_URL}")
    print(f"Voice    : {args.voice}")

    results = []
    results.append(test_health())
    results.append(test_list_voices())
    results.append(test_synthesize(args.text, args.voice, save_path=args.save, play=args.play))

    # ─── Summary ──────────────────────────────────────────────────────────────
    passed = sum(results)
    total  = len(results)
    print(f"\n{BOLD}=== Summary ==={RESET}")
    if passed == total:
        print(f"  {GREEN}{BOLD}All {total} tests passed.{RESET}")
    else:
        print(f"  {RED}{BOLD}{passed}/{total} tests passed.{RESET}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
