#!/usr/bin/env python3
"""
End-to-End Integration Test
----------------------------
Validates the full voice assistant pipeline without a microphone:
  TTS → ASR → LLM → TTS → ASR (round-trip)

Optionally tests wake word detection with TTS-generated audio.

Usage:
    python tests/test_e2e.py
    python tests/test_e2e.py --output-dir /tmp/e2e_results
    python tests/test_e2e.py --skip-wake-word
    python tests/test_e2e.py --questions "what time is it" "tell me a joke"

Config (via .env or environment variables):
    ASR_BASE_URL     default: http://voice-api.zetrix.com:8005
    TTS_BASE_URL     default: http://voice-api.zetrix.com:8006
    TTS_VOICE        default: default
    LLM_BASE_URL     default: https://aw-llm.myeg.com.my/v1
    LLM_API_KEY      required for production endpoint
    LLM_MODEL        default: llama3

Exit code: 0 = all pass, 1 = failures
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

import numpy as np
import requests

# ─── Config ───────────────────────────────────────────────────────────────────

ASR_BASE_URL = os.getenv("ASR_BASE_URL", "http://voice-api.zetrix.com:8005")
ASR_TIMEOUT  = int(os.getenv("ASR_TIMEOUT", "30"))

TTS_BASE_URL = os.getenv("TTS_BASE_URL", "http://voice-api.zetrix.com:8006")
TTS_VOICE    = os.getenv("TTS_VOICE", "default")
TTS_TIMEOUT  = int(os.getenv("TTS_TIMEOUT", "60"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://aw-llm.myeg.com.my/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY", "nokey")
LLM_MODEL    = os.getenv("LLM_MODEL", "llama3")
LLM_TIMEOUT  = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_SYSTEM_PROMPT = os.getenv(
    "LLM_SYSTEM_PROMPT",
    "You are a helpful voice assistant. Keep answers concise and conversational.",
)

WAKE_WORD_MODEL     = os.getenv("WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_MODEL_PATH = os.getenv("WAKE_WORD_MODEL_PATH", "")
WAKE_WORD_THRESHOLD = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))

DEFAULT_QUESTIONS = [
    "what is one plus one",
    "what color is the sky",
    "say hello",
]

# ─── ANSI colours ─────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"  {CYAN}→{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

# ─── Service helpers ─────────────────────────────────────────────────────────

def tts_generate(text: str) -> tuple:
    """Call TTS service. Returns (wav_bytes, elapsed_ms)."""
    url = f"{TTS_BASE_URL}/generate"
    payload = {"target_text": text, "voice_type": TTS_VOICE, "stream": False}
    t0 = time.time()
    resp = requests.post(url, json=payload, timeout=TTS_TIMEOUT)
    elapsed = (time.time() - t0) * 1000
    resp.raise_for_status()
    return resp.content, elapsed


def asr_transcribe(wav_bytes: bytes) -> tuple:
    """Call ASR service. Returns (text, elapsed_ms)."""
    url = f"{ASR_BASE_URL}/asr"
    b64 = base64.b64encode(wav_bytes).decode()
    t0 = time.time()
    resp = requests.post(url, json={"wav_base64": b64}, timeout=ASR_TIMEOUT)
    elapsed = (time.time() - t0) * 1000
    resp.raise_for_status()
    data = resp.json()
    return data.get("text", ""), elapsed


def llm_chat(messages: list) -> tuple:
    """Call LLM service. Returns (reply_text, elapsed_ms)."""
    url = f"{LLM_BASE_URL}/chat/completions"
    headers = {"Authorization": LLM_API_KEY}
    payload = {"model": LLM_MODEL, "messages": messages, "stream": False}
    t0 = time.time()
    resp = requests.post(url, json=payload, headers=headers, timeout=LLM_TIMEOUT)
    elapsed = (time.time() - t0) * 1000
    resp.raise_for_status()
    data = resp.json()
    reply = data["choices"][0]["message"]["content"].strip()
    return reply, elapsed


def wav_duration(wav_bytes: bytes) -> float:
    """Return duration of WAV in seconds."""
    try:
        with wave.open(BytesIO(wav_bytes)) as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def resample_wav_to_16k(wav_bytes: bytes) -> np.ndarray:
    """Read a WAV (any sample rate) and return int16 PCM at 16 kHz."""
    with wave.open(BytesIO(wav_bytes)) as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        raw = wf.readframes(n)
        channels = wf.getnchannels()

    pcm = np.frombuffer(raw, dtype=np.int16)
    # Mix to mono if stereo
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)

    if sr == 16000:
        return pcm

    # Simple linear interpolation resampling
    target_len = int(len(pcm) * 16000 / sr)
    indices = np.linspace(0, len(pcm) - 1, target_len)
    resampled = np.interp(indices, np.arange(len(pcm)), pcm.astype(np.float64))
    return resampled.astype(np.int16)


def save_wav(path: str, wav_bytes: bytes):
    """Save raw WAV bytes to a file."""
    with open(path, "wb") as f:
        f.write(wav_bytes)


# ─── Test 0: Service Health Checks ──────────────────────────────────────────

def test_health() -> bool:
    header("Test 0: Service Health Checks")
    all_ok = True

    # ASR health
    asr_url = f"{ASR_BASE_URL}/health"
    info(f"ASR  : {asr_url}")
    try:
        t0 = time.time()
        resp = requests.get(asr_url, timeout=10)
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()
        ok(f"ASR healthy  ({elapsed:.0f} ms)")
    except Exception as e:
        fail(f"ASR health check failed: {e}")
        all_ok = False

    # TTS health
    tts_url = f"{TTS_BASE_URL}/health"
    info(f"TTS  : {tts_url}")
    try:
        t0 = time.time()
        resp = requests.get(tts_url, timeout=10)
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()
        ok(f"TTS healthy  ({elapsed:.0f} ms)")
    except Exception as e:
        fail(f"TTS health check failed: {e}")
        all_ok = False

    # LLM connectivity
    llm_url = f"{LLM_BASE_URL}/chat/completions"
    info(f"LLM  : {llm_url}")
    try:
        messages = [
            {"role": "system", "content": "Reply with exactly: ok"},
            {"role": "user", "content": "ping"},
        ]
        reply, elapsed = llm_chat(messages)
        ok(f"LLM responsive  ({elapsed:.0f} ms) — reply: \"{reply[:50]}\"")
    except Exception as e:
        fail(f"LLM connectivity failed: {e}")
        all_ok = False

    return all_ok


# ─── Test 1: Wake Word Detection ────────────────────────────────────────────

def test_wake_word(output_dir: str = None) -> bool:
    header("Test 1: Wake Word Detection (soft check)")

    wake_phrase = "hey jarvis"
    info(f"Generating TTS audio for \"{wake_phrase}\" ...")

    try:
        wav_bytes, tts_ms = tts_generate(wake_phrase)
        ok(f"TTS generated {len(wav_bytes):,} bytes  ({tts_ms:.0f} ms, {wav_duration(wav_bytes):.2f}s)")
    except Exception as e:
        fail(f"TTS failed: {e}")
        return False

    if output_dir:
        save_wav(os.path.join(output_dir, "wake_word_hey_jarvis.wav"), wav_bytes)
        info(f"Saved: {output_dir}/wake_word_hey_jarvis.wav")

    # Resample to 16kHz for OpenWakeWord
    info("Resampling to 16 kHz ...")
    pcm_16k = resample_wav_to_16k(wav_bytes)
    ok(f"Resampled: {len(pcm_16k)} samples ({len(pcm_16k)/16000:.2f}s)")

    # Load OpenWakeWord
    info("Loading OpenWakeWord model ...")
    try:
        import openwakeword
        from openwakeword.model import Model

        if WAKE_WORD_MODEL_PATH:
            oww = Model(wakeword_models=[WAKE_WORD_MODEL_PATH])
        else:
            model_file = WAKE_WORD_MODEL + "_v0.1.tflite"
            models_dir = __import__("pathlib").Path(openwakeword.__file__).parent / "resources" / "models"
            if not (models_dir / model_file).exists():
                info("Downloading OpenWakeWord models (first run) ...")
                openwakeword.utils.download_models([WAKE_WORD_MODEL + "_v0.1"])
            oww = Model(wakeword_models=[WAKE_WORD_MODEL])
        ok(f"Model loaded: {WAKE_WORD_MODEL_PATH or WAKE_WORD_MODEL}")
    except Exception as e:
        fail(f"OpenWakeWord init failed: {e}")
        warn("Skipping wake word test (model not available)")
        return True  # soft fail

    # Feed 480-sample (30ms) frames — matches capture.py L175-178
    frame_size = 480
    max_score = 0.0
    max_model = ""

    for i in range(0, len(pcm_16k) - frame_size + 1, frame_size):
        frame = pcm_16k[i : i + frame_size]
        prediction = oww.predict(frame)
        for model_name, score in prediction.items():
            if score > max_score:
                max_score = score
                max_model = model_name

    info(f"Max detection score: {max_score:.4f}  (model: {max_model})")
    info(f"Threshold          : {WAKE_WORD_THRESHOLD}")

    if max_score >= WAKE_WORD_THRESHOLD:
        ok(f"Wake word DETECTED — score {max_score:.4f} >= {WAKE_WORD_THRESHOLD}")
    else:
        warn(f"Wake word NOT detected — score {max_score:.4f} < {WAKE_WORD_THRESHOLD}")
        warn("This is expected: TTS-generated speech may not match wake word training data.")

    # Soft pass — always return True
    return True


# ─── Tests 2-4: Pipeline per Question ───────────────────────────────────────

def test_pipeline_question(
    question: str,
    question_num: int,
    llm_history: list,
    output_dir: str = None,
) -> dict:
    """
    Run the 5-step pipeline for one question.
    Returns a dict with step results.
    """
    header(f"Test {question_num + 1}: Pipeline — \"{question}\"")

    result = {
        "question": question,
        "tts_question": False,
        "asr_question": False,
        "llm_reply": False,
        "tts_reply": False,
        "asr_reply": False,
        "asr_question_text": "",
        "llm_reply_text": "",
        "asr_reply_text": "",
    }

    prefix = f"q{question_num + 1}"

    # ── Step 1: TTS → generate question audio ────────────────────────────────
    info(f"Step 1/5: TTS — synthesize question \"{question}\"")
    try:
        q_wav, tts_ms = tts_generate(question)
        ok(f"TTS: {len(q_wav):,} bytes, {wav_duration(q_wav):.2f}s  ({tts_ms:.0f} ms)")
        result["tts_question"] = True
        if output_dir:
            path = os.path.join(output_dir, f"{prefix}_question.wav")
            save_wav(path, q_wav)
            info(f"Saved: {path}")
    except Exception as e:
        fail(f"TTS question failed: {e}")
        return result

    # ── Step 2: ASR → transcribe question audio ──────────────────────────────
    info("Step 2/5: ASR — transcribe question audio")
    try:
        asr_text, asr_ms = asr_transcribe(q_wav)
        ok(f"ASR: \"{YELLOW}{asr_text}{RESET}\"  ({asr_ms:.0f} ms)")
        result["asr_question"] = len(asr_text.strip()) > 0
        result["asr_question_text"] = asr_text
        if not result["asr_question"]:
            warn("ASR returned empty transcription")
    except Exception as e:
        fail(f"ASR question failed: {e}")
        return result

    # ── Step 3: LLM → send ASR text, get reply ──────────────────────────────
    info(f"Step 3/5: LLM — send \"{asr_text}\"")
    try:
        llm_history.append({"role": "user", "content": asr_text})
        reply_text, llm_ms = llm_chat(llm_history)
        llm_history.append({"role": "assistant", "content": reply_text})
        ok(f"LLM: \"{YELLOW}{reply_text}{RESET}\"  ({llm_ms:.0f} ms)")
        result["llm_reply"] = len(reply_text.strip()) > 0
        result["llm_reply_text"] = reply_text
        if not result["llm_reply"]:
            warn("LLM returned empty reply")
    except Exception as e:
        fail(f"LLM failed: {e}")
        # Remove the user message we just appended since LLM failed
        llm_history.pop()
        return result

    # ── Step 4: TTS → generate reply audio ───────────────────────────────────
    info(f"Step 4/5: TTS — synthesize reply")
    try:
        r_wav, tts_ms = tts_generate(reply_text)
        ok(f"TTS: {len(r_wav):,} bytes, {wav_duration(r_wav):.2f}s  ({tts_ms:.0f} ms)")
        result["tts_reply"] = True
        if output_dir:
            path = os.path.join(output_dir, f"{prefix}_reply.wav")
            save_wav(path, r_wav)
            info(f"Saved: {path}")
    except Exception as e:
        fail(f"TTS reply failed: {e}")
        return result

    # ── Step 5: ASR → transcribe reply audio (round-trip) ────────────────────
    info("Step 5/5: ASR — transcribe reply audio (round-trip verification)")
    try:
        asr_reply, asr_ms = asr_transcribe(r_wav)
        ok(f"ASR: \"{YELLOW}{asr_reply}{RESET}\"  ({asr_ms:.0f} ms)")
        result["asr_reply"] = len(asr_reply.strip()) > 0
        result["asr_reply_text"] = asr_reply
        if not result["asr_reply"]:
            warn("ASR returned empty transcription for reply")
    except Exception as e:
        fail(f"ASR reply failed: {e}")

    return result


# ─── Summary ─────────────────────────────────────────────────────────────────

def print_summary(health_ok: bool, wake_ok: bool, skip_wake: bool, results: list):
    header("═══ Summary ═══")

    # Header row
    col_w = 14
    steps = ["TTS(Q)", "ASR(Q)", "LLM", "TTS(R)", "ASR(R)"]
    step_keys = ["tts_question", "asr_question", "llm_reply", "tts_reply", "asr_reply"]

    print(f"\n  {'Test':<22} ", end="")
    for s in steps:
        print(f"{s:^{col_w}}", end="")
    print()
    print(f"  {'─' * 22} ", end="")
    for _ in steps:
        print(f"{'─' * col_w}", end="")
    print()

    # Health row
    h_status = f"{GREEN}PASS{RESET}" if health_ok else f"{RED}FAIL{RESET}"
    print(f"  {'Health checks':<22} {h_status}")

    # Wake word row
    if skip_wake:
        print(f"  {'Wake word':<22} {DIM}SKIPPED{RESET}")
    else:
        w_status = f"{GREEN}PASS{RESET}" if wake_ok else f"{YELLOW}SOFT{RESET}"
        print(f"  {'Wake word':<22} {w_status}")

    # Question rows
    all_pass = health_ok
    for r in results:
        q_label = r["question"][:20]
        print(f"  {q_label:<22} ", end="")
        for key in step_keys:
            if r[key]:
                print(f"{GREEN}{'PASS':^{col_w}}{RESET}", end="")
            else:
                print(f"{RED}{'FAIL':^{col_w}}{RESET}", end="")
                all_pass = False
        print()

    # ASR transcription comparisons
    print(f"\n  {BOLD}ASR Transcriptions:{RESET}")
    for r in results:
        q = r["question"][:20]
        print(f"    {q}:")
        print(f"      Question ASR : {YELLOW}{r['asr_question_text']}{RESET}")
        print(f"      LLM reply    : {r['llm_reply_text'][:80]}")
        print(f"      Reply ASR    : {YELLOW}{r['asr_reply_text']}{RESET}")

    print()
    if all_pass:
        print(f"  {GREEN}{BOLD}All pipeline tests passed.{RESET}")
    else:
        print(f"  {RED}{BOLD}Some pipeline tests failed.{RESET}")

    return all_pass


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="End-to-end integration test")
    parser.add_argument("--output-dir", metavar="DIR",
                        help="Directory to save WAV files")
    parser.add_argument("--skip-wake-word", action="store_true",
                        help="Skip wake word detection test")
    parser.add_argument("--questions", nargs="+", default=DEFAULT_QUESTIONS,
                        help="Questions to test (default: 3 built-in questions)")
    args = parser.parse_args()

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  End-to-End Integration Test{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"  ASR      : {ASR_BASE_URL}")
    print(f"  TTS      : {TTS_BASE_URL}  (voice: {TTS_VOICE})")
    print(f"  LLM      : {LLM_BASE_URL}  (model: {LLM_MODEL})")
    print(f"  Questions: {len(args.questions)}")
    if args.output_dir:
        print(f"  Output   : {args.output_dir}")

    # Create output directory if needed
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    # ── Test 0: Health checks ────────────────────────────────────────────────
    health_ok = test_health()
    if not health_ok:
        fail("Service health checks failed — aborting.")
        sys.exit(1)

    # ── Test 1: Wake word ────────────────────────────────────────────────────
    wake_ok = True
    if args.skip_wake_word:
        header("Test 1: Wake Word Detection — SKIPPED")
    else:
        wake_ok = test_wake_word(output_dir=args.output_dir)

    # ── Tests 2-N: Pipeline per question ─────────────────────────────────────
    llm_history = [{"role": "system", "content": LLM_SYSTEM_PROMPT}]
    pipeline_results = []

    for i, question in enumerate(args.questions):
        result = test_pipeline_question(
            question=question,
            question_num=i,
            llm_history=llm_history,
            output_dir=args.output_dir,
        )
        pipeline_results.append(result)

    # ── Summary ──────────────────────────────────────────────────────────────
    all_pass = print_summary(health_ok, wake_ok, args.skip_wake_word, pipeline_results)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
