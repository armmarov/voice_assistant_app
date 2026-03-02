import os

# ─── ASR Service ──────────────────────────────────────────────────────────────
ASR_BASE_URL = os.getenv("ASR_BASE_URL", "http://3.114.138.123:8005")
ASR_ENDPOINT = f"{ASR_BASE_URL}/asr"

# ─── TTS Service ──────────────────────────────────────────────────────────────
TTS_BASE_URL = os.getenv("TTS_BASE_URL", "http://3.114.138.123:8006")
TTS_ENDPOINT = f"{TTS_BASE_URL}/generate"
TTS_VOICE    = os.getenv("TTS_VOICE", "default")   # liudao, filrty, zhiyu, default

# ─── LLM Service (OpenAI-compatible) ──────────────────────────────────────────
LLM_BASE_URL  = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY   = os.getenv("LLM_API_KEY",  "nokey")
LLM_MODEL     = os.getenv("LLM_MODEL",    "llama3")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "150"))
LLM_SYSTEM_PROMPT = os.getenv(
    "LLM_SYSTEM_PROMPT",
    "You are a helpful voice assistant. Your responses will be spoken aloud via text-to-speech. "
    "Keep answers to 1-3 short sentences. No bullet points, no lists, no markdown, no emojis.",
)

# ─── Echo Cancellation ────────────────────────────────────────────────────────
# MIC_MUTE_DURING_PLAYBACK — mute mic in software during TTS playback.
#   Simple, no extra setup. Prevents self-triggering. No barge-in support.
#
# AEC (Acoustic Echo Cancellation) is auto-detected from the OS at startup.
#   If PulseAudio module-echo-cancel is loaded, AEC is considered active.
#   See README.md for PulseAudio AEC setup instructions.
#
# Both can be active simultaneously for maximum robustness.
MIC_MUTE_DURING_PLAYBACK = os.getenv("MIC_MUTE_DURING_PLAYBACK", "true").lower() == "true"

# ─── Audio Devices ────────────────────────────────────────────────────────────
# PyAudio device indices — run the following to list available devices:
#   python -c "import pyaudio; pa=pyaudio.PyAudio(); [print(i, pa.get_device_info_by_index(i)['name']) for i in range(pa.get_device_count())]"
# Set to -1 to use system default.
MIC_DEVICE_INDEX = int(os.getenv("MIC_DEVICE_INDEX", "-1"))
SPK_DEVICE_INDEX = int(os.getenv("SPK_DEVICE_INDEX", "-1"))

# ─── Audio Capture ────────────────────────────────────────────────────────────
MIC_SAMPLE_RATE   = 16000   # Hz — required by VAD and ASR
MIC_CHANNELS      = 1
MIC_CHUNK_MS      = 30      # ms per VAD frame (10 / 20 / 30 ms only)
MIC_CHUNK_SAMPLES = int(MIC_SAMPLE_RATE * MIC_CHUNK_MS / 1000)  # = 480

VAD_AGGRESSIVENESS = int(os.getenv("VAD_AGGRESSIVENESS", "3"))    # 0-3
VAD_SILENCE_MS     = int(os.getenv("VAD_SILENCE_MS",     "1200")) # stop after N ms silence
VAD_MIN_SPEECH_MS  = int(os.getenv("VAD_MIN_SPEECH_MS",  "2000")) # ignore < N ms utterances

# ─── Audio Playback ───────────────────────────────────────────────────────────
SPK_SAMPLE_RATE = 44100   # TTS output is 44100 Hz mono 16-bit
SPK_CHANNELS    = 1

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE  = os.getenv("LOG_FILE",  "/var/log/voice_assistant.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ─── HTTP timeouts (seconds) ──────────────────────────────────────────────────
ASR_TIMEOUT = int(os.getenv("ASR_TIMEOUT", "30"))
TTS_TIMEOUT = int(os.getenv("TTS_TIMEOUT", "60"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# ─── Wake Word ────────────────────────────────────────────────────────────────
# Engine: "openwakeword" (free, slower) or "porcupine" (fast, requires access key)
WAKE_WORD_ENGINE = os.getenv("WAKE_WORD_ENGINE", "openwakeword")

# --- OpenWakeWord settings ---
# Built-in models: hey_jarvis, alexa, hey_mycroft, hey_rhasspy
# Custom model: set WAKE_WORD_MODEL_PATH to a local .onnx/.tflite file.
WAKE_WORD_MODEL        = os.getenv("WAKE_WORD_MODEL",       "hey_jarvis")
WAKE_WORD_MODEL_PATH   = os.getenv("WAKE_WORD_MODEL_PATH",  "")     # empty → use WAKE_WORD_MODEL
WAKE_WORD_THRESHOLD    = float(os.getenv("WAKE_WORD_THRESHOLD",    "0.5"))

# --- Porcupine settings ---
# Get your access key from https://console.picovoice.ai/
PORCUPINE_ACCESS_KEY      = os.getenv("PORCUPINE_ACCESS_KEY", "")
# Built-in keywords: porcupine, bumblebee, alexa, jarvis, computer, hey google, etc.
# Or set PORCUPINE_KEYWORD_PATH to a custom .ppn file.
PORCUPINE_KEYWORD         = os.getenv("PORCUPINE_KEYWORD",       "jarvis")
PORCUPINE_KEYWORD_PATH    = os.getenv("PORCUPINE_KEYWORD_PATH",  "")
PORCUPINE_SENSITIVITY     = float(os.getenv("PORCUPINE_SENSITIVITY", "0.5"))

# --- Common ---
WAKE_LISTEN_TIMEOUT_MS = int(os.getenv("WAKE_LISTEN_TIMEOUT_MS", "10000"))
CONVERSATION_TIMEOUT_MS = int(os.getenv("CONVERSATION_TIMEOUT_MS", "300000"))  # 5 min
WAKE_WORD_ACK_PHRASE   = os.getenv("WAKE_WORD_ACK_PHRASE", "Yes sir")
