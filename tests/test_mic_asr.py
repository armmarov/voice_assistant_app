#!/usr/bin/env python3
"""
Mic → ASR Live Test
--------------------
Records from the microphone, sends to ASR, and prints the transcription.

Usage:
    python tests/test_mic_asr.py                  # record 5 seconds from default mic
    python tests/test_mic_asr.py --duration 8     # record 8 seconds
    python tests/test_mic_asr.py --device 1       # use mic device index 1
    python tests/test_mic_asr.py --save           # also save recording to /tmp/mic_test.wav
    python tests/test_mic_asr.py --list-devices   # list available mic devices

Config (via .env or environment variables):
    ASR_BASE_URL     default: http://voice-api.zetrix.com:8005
    MIC_DEVICE_INDEX default: -1 (system default)
"""

import argparse
import base64
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

ASR_BASE_URL     = os.getenv("ASR_BASE_URL",     "http://voice-api.zetrix.com:8005")
ASR_TIMEOUT      = int(os.getenv("ASR_TIMEOUT",  "30"))
MIC_DEVICE_INDEX = int(os.getenv("MIC_DEVICE_INDEX", "-1"))

SAMPLE_RATE  = 16000
CHANNELS     = 1
CHUNK        = 1024  # frames per read

# ─── ANSI colours ─────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):     print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):   print(f"  {RED}✗{RESET} {msg}")
def info(msg):   print(f"  {CYAN}→{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def list_devices() -> None:
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        print(f"\n{BOLD}Available audio input devices:{RESET}")
        found = False
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["maxInputChannels"] > 0:
                marker = f" {GREEN}← default{RESET}" if i == pa.get_default_input_device_info()["index"] else ""
                print(f"  [{i}] {d['name']}  (channels={d['maxInputChannels']}){marker}")
                found = True
        if not found:
            print("  No input devices found.")
        pa.terminate()
    except ImportError:
        print("pyaudio not installed.")
    except Exception as e:
        print(f"Error listing devices: {e}")


def record_wav(duration_s: float, device_index: int) -> bytes:
    """Record from mic and return WAV bytes."""
    try:
        import pyaudio
    except ImportError:
        raise RuntimeError("pyaudio is not installed. Run: pip install pyaudio")

    pa = pyaudio.PyAudio()

    open_kwargs = dict(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )
    if device_index >= 0:
        open_kwargs["input_device_index"] = device_index

    stream = pa.open(**open_kwargs)

    total_chunks = int(SAMPLE_RATE / CHUNK * duration_s)
    frames = []

    for i in range(total_chunks):
        remaining = duration_s - (i * CHUNK / SAMPLE_RATE)
        print(f"\r  {CYAN}Recording …{RESET}  {remaining:.1f}s remaining   ", end="", flush=True)
        frames.append(stream.read(CHUNK, exception_on_overflow=False))

    print(f"\r  {GREEN}Recording done.{RESET}                          ")

    stream.stop_stream()
    stream.close()
    pa.terminate()

    # Pack into WAV
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def send_to_asr(wav_bytes: bytes) -> bool:
    url = f"{ASR_BASE_URL}/asr"
    info(f"Sending to ASR: {url}")
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
        text = data.get("text", "").strip()
        timing = data.get("timing", {})

        ok(f"Response time : {elapsed:.0f} ms")
        if text:
            ok(f"Transcription : {YELLOW}\"{text}\"{RESET}")
        else:
            info(f"Transcription : {YELLOW}(empty — no speech detected){RESET}")

        if timing:
            ok(f"Audio duration: {timing.get('audio_duration_s', '?')} s")
            ok(f"Inference time: {timing.get('infer_ms', '?')} ms")
            ok(f"RTF           : {timing.get('rtf', '?')}")

        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail(f"Request timed out after {ASR_TIMEOUT}s")
    except requests.HTTPError:
        fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        fail(f"{e}")
    return False

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Mic → ASR live test")
    parser.add_argument("--duration",    type=float, default=5.0,          help="Recording duration in seconds (default: 5)")
    parser.add_argument("--device",      type=int,   default=MIC_DEVICE_INDEX, help="Mic device index (-1 = system default)")
    parser.add_argument("--save",        action="store_true",               help="Save recording to /tmp/mic_test.wav")
    parser.add_argument("--list-devices",action="store_true",               help="List available mic devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    print(f"\n{BOLD}=== Mic → ASR Live Test ==={RESET}")
    print(f"ASR URL  : {ASR_BASE_URL}")
    print(f"Device   : {'system default' if args.device < 0 else args.device}")
    print(f"Duration : {args.duration}s")

    # ── Step 1: Record ────────────────────────────────────────────────────────
    header("1. Recording from microphone")
    print(f"\n  {YELLOW}{BOLD}Speak now!{RESET}  Recording for {args.duration}s …\n")

    try:
        wav_bytes = record_wav(args.duration, args.device)
        ok(f"Captured {len(wav_bytes):,} bytes of audio")
    except RuntimeError as e:
        fail(str(e))
        sys.exit(1)
    except Exception as e:
        fail(f"Recording failed: {e}")
        sys.exit(1)

    if args.save:
        path = "/tmp/mic_test.wav"
        with open(path, "wb") as f:
            f.write(wav_bytes)
        ok(f"Saved to {path}")

    # ── Step 2: Transcribe ────────────────────────────────────────────────────
    header("2. Transcribing with ASR")
    result = send_to_asr(wav_bytes)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}=== Summary ==={RESET}")
    if result:
        print(f"  {GREEN}{BOLD}Test passed.{RESET}")
    else:
        print(f"  {RED}{BOLD}Test failed.{RESET}")
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
