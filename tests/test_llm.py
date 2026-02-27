#!/usr/bin/env python3
"""
LLM Service Test
----------------
Tests the LLM (Language Model) service via OpenAI-compatible /v1/chat/completions.

Usage:
    python tests/test_llm.py                              # single-turn test
    python tests/test_llm.py --prompt "What is 2+2?"     # custom prompt
    python tests/test_llm.py --multi-turn                 # multi-turn conversation test

Config (via .env or environment variables):
    LLM_BASE_URL   default: https://aw-llm.myeg.com.my/v1
    LLM_API_KEY    required for production endpoint
    LLM_MODEL      default: llama3
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://aw-llm.myeg.com.my/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY",  "nokey")
LLM_MODEL    = os.getenv("LLM_MODEL",    "llama3")
LLM_TIMEOUT  = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_SYSTEM_PROMPT = os.getenv(
    "LLM_SYSTEM_PROMPT",
    "You are a helpful voice assistant. Keep answers concise and conversational.",
)

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

def chat(messages: list) -> dict:
    """Send messages and return the full response dict."""
    url = f"{LLM_BASE_URL}/chat/completions"
    headers = {"Authorization": LLM_API_KEY}
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
    }
    t0 = time.time()
    resp = requests.post(url, json=payload, headers=headers, timeout=LLM_TIMEOUT)
    elapsed = (time.time() - t0) * 1000
    resp.raise_for_status()
    return resp.json(), elapsed

# ─── Tests ────────────────────────────────────────────────────────────────────

def test_single_turn(prompt: str) -> bool:
    header(f"1. Single-turn Chat  POST /chat/completions")
    url = f"{LLM_BASE_URL}/chat/completions"
    info(f"URL    : {url}")
    info(f"Model  : {LLM_MODEL}")
    info(f"Prompt : \"{prompt}\"")

    messages = [
        {"role": "system",  "content": LLM_SYSTEM_PROMPT},
        {"role": "user",    "content": prompt},
    ]
    try:
        data, elapsed = chat(messages)
        reply = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        ok(f"Response time  : {elapsed:.0f} ms")
        ok(f"Reply          : {YELLOW}\"{reply}\"{RESET}")
        if usage:
            ok(f"Prompt tokens  : {usage.get('prompt_tokens', '?')}")
            ok(f"Completion tokens: {usage.get('completion_tokens', '?')}")
            ok(f"Total tokens   : {usage.get('total_tokens', '?')}")
        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail(f"Request timed out after {LLM_TIMEOUT}s")
    except requests.HTTPError as e:
        fail(f"HTTP error: {e}")
        try: fail(f"Response body: {e.response.text[:300]}")
        except: pass
    except (KeyError, IndexError) as e:
        fail(f"Unexpected response format: {e}")
    except Exception as e:
        fail(f"{e}")
    return False


def test_multi_turn() -> bool:
    header("2. Multi-turn Conversation")
    url = f"{LLM_BASE_URL}/chat/completions"
    info(f"URL   : {url}")
    info(f"Model : {LLM_MODEL}")

    history = [{"role": "system", "content": LLM_SYSTEM_PROMPT}]
    turns = [
        "My name is Robot. What is your name?",
        "What did I just tell you my name is?",
    ]

    try:
        for i, user_msg in enumerate(turns, 1):
            info(f"Turn {i} — User: \"{user_msg}\"")
            history.append({"role": "user", "content": user_msg})
            data, elapsed = chat(history)
            reply = data["choices"][0]["message"]["content"].strip()
            history.append({"role": "assistant", "content": reply})
            ok(f"Turn {i} — Reply ({elapsed:.0f} ms): {YELLOW}\"{reply}\"{RESET}")

        # Verify the model remembered the name from turn 1
        last_reply = history[-1]["content"].lower()
        if "robot" in last_reply:
            ok("Memory check passed — model remembered the name 'Robot'.")
        else:
            info(f"Memory check: 'Robot' not found in last reply (may still be correct).")

        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
    except requests.Timeout:
        fail(f"Request timed out after {LLM_TIMEOUT}s")
    except requests.HTTPError as e:
        fail(f"HTTP error: {e}")
        try: fail(f"Response body: {e.response.text[:300]}")
        except: pass
    except Exception as e:
        fail(f"{e}")
    return False


def test_models_endpoint() -> bool:
    header("3. Models List  GET /models")
    url = f"{LLM_BASE_URL}/models"
    info(f"URL: {url}")
    headers = {"Authorization": LLM_API_KEY}
    try:
        t0 = time.time()
        resp = requests.get(url, headers=headers, timeout=10)
        elapsed = (time.time() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("id", "?") for m in data.get("data", [])]
        ok(f"Status {resp.status_code}  ({elapsed:.0f} ms)")
        ok(f"Available models: {YELLOW}{', '.join(models) if models else 'none listed'}{RESET}")
        return True
    except requests.ConnectionError:
        fail(f"Cannot connect to {url}")
        return False
    except requests.HTTPError:
        # Some endpoints don't implement /models — not a fatal failure
        info(f"HTTP {resp.status_code} — /models endpoint not available (non-fatal).")
        return True
    except Exception as e:
        info(f"/models endpoint not available: {e} (non-fatal).")
        return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM service test")
    parser.add_argument("--prompt",     default="Say hello and introduce yourself in one sentence.",
                        help="Custom prompt for the single-turn test")
    parser.add_argument("--multi-turn", action="store_true",
                        help="Run multi-turn conversation test")
    args = parser.parse_args()

    print(f"\n{BOLD}=== LLM Service Test ==={RESET}")
    print(f"Base URL : {LLM_BASE_URL}")
    print(f"Model    : {LLM_MODEL}")
    print(f"API Key  : {'(set)' if LLM_API_KEY and LLM_API_KEY != 'nokey' else YELLOW + '(not set — using nokey)' + RESET}")

    results = []
    results.append(test_models_endpoint())
    results.append(test_single_turn(args.prompt))

    if args.multi_turn:
        results.append(test_multi_turn())

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
