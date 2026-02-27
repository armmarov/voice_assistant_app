#!/usr/bin/env python3
"""
ASR Service Test
----------------
Tests the ASR (Automatic Speech Recognition) service.

Usage:
    python tests/test_asr.py                        # uses generated sine-wave WAV
    python tests/test_asr.py --audio path/to/file.wav  # uses your own audio file

Config (via .env or environment variables):
    ASR_BASE_URL   default: http://voice-api.zetrix.com:8005
"""

import argparse
import base64
import math
import os
import struct
import sys
import time
import wave
from io import BytesIO

from dotenv import load_dotenv

load_dotenv()

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

ASR_BASE_URL = os.getenv("ASR_BASE_URL", "http://voice-api.zetrix.com:8005")
ASR_TIMEOUT  = int(os.getenv("ASR_TIMEOUT", "30"))

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

def generate_test_wav(duration_s: float = 2.0, freq_hz: float = 440.0) -> bytes:
    """Generate a sine-wave WAV in memory (no mic or file needed)."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_s)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for i in range(num_samples):
            sample = int(32767 * 0.5 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
            wf.writeframes(struct.pack("<h", sample))
    return buf.getvalue()


def load_wav(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

# ─── Tests ────────────────────────────────────────────────────────────────────

def test_health() -> bool:
    header("1. Health Check  GET /health")
    url = f"{ASR_BASE_URL}/health"
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


def test_asr(wav_bytes: bytes, label: str) -> bool:
    header(f"2. Speech Recognition  POST /asr  [{label}]")
    url = f"{ASR_BASE_URL}/asr"
    info(f"URL: {url}")
    info(f"Audio size: {len(wav_bytes):,} bytes")

    b64 = base64.b64encode(wav_bytes).decode()
    try:
        t0 = time.time()
        resp = requests.post(
            url,
            json={"wav_base64": b64},
            timeout=ASR_TIMEOUT,
        )
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()
        ok(f"Status {resp.status_code}  ({elapsed:.0f} ms)")
        ok(f"Transcribed text: {YELLOW}\"{data.get('text', '')}\"{RESET}")
        timing = data.get("timing", {})
        if timing:
            ok(f"Audio duration : {timing.get('audio_duration_s', '?')} s")
            ok(f"Inference time : {timing.get('infer_ms', '?')} ms")
            ok(f"Total time     : {timing.get('total_ms', '?')} ms")
            ok(f"RTF            : {timing.get('rtf', '?')}")
        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail(f"Request timed out after {ASR_TIMEOUT}s")
    except requests.HTTPError as e:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        fail(f"{e}")
    return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ASR service test")
    parser.add_argument("--audio", help="Path to a WAV file to transcribe (optional)")
    args = parser.parse_args()

    print(f"\n{BOLD}=== ASR Service Test ==={RESET}")
    print(f"Base URL : {ASR_BASE_URL}")

    results = []

    results.append(test_health())

    if args.audio:
        wav = load_wav(args.audio)
        label = os.path.basename(args.audio)
    else:
        info("\nNo --audio provided, generating a 2s sine-wave WAV for connectivity test.")
        info("The transcription result will be empty or noise — that is expected.")
        wav = generate_test_wav(duration_s=2.0)
        label = "generated 440Hz sine wave"

    results.append(test_asr(wav, label))

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
