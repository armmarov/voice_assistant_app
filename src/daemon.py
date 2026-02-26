import io
import logging
import math
import signal
import struct
import subprocess
import threading
import time
import wave

from . import config
from .asr import ASRClient
from .llm import LLMClient
from .tts import TTSClient
from .audio import AudioPlayer
from .capture import MicrophoneCapture

log = logging.getLogger(__name__)


class VoiceAssistantDaemon:
    """
    Orchestrates the full pipeline:
      Mic → Wake Word → VAD → ASR → LLM → TTS → Speaker
    """

    def __init__(self):
        self._asr    = ASRClient()
        self._llm    = LLMClient()
        self._tts    = TTSClient()
        self._player = AudioPlayer()
        self._mic    = MicrophoneCapture(
            on_utterance=self._handle_utterance,
            on_wake_word=self._handle_wake_word,
        )
        self._busy    = threading.Event()
        self._stop    = threading.Event()
        self._aec_active = self._detect_aec()

    @staticmethod
    def _detect_aec() -> bool:
        """Auto-detect whether PulseAudio AEC (module-echo-cancel) is active."""
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "modules"],
                capture_output=True, text=True, timeout=3,
            )
            active = "module-echo-cancel" in result.stdout
            if active:
                log.info("AEC detected: PulseAudio module-echo-cancel is loaded.")
            else:
                log.info("AEC not detected: PulseAudio module-echo-cancel is not loaded.")
            return active
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.info("AEC not detected: pactl not available.")
            return False

    # ── wake word callback (runs in capture thread) ──────────────────────────

    def _handle_wake_word(self):
        if not config.WAKE_WORD_ACK_PHRASE:
            return
        threading.Thread(target=self._play_ack, daemon=True).start()

    def _play_ack(self):
        self._busy.set()
        log.info("Playing wake word acknowledgement …")
        try:
            audio = self._tts.synthesize(config.WAKE_WORD_ACK_PHRASE)
            if not audio:
                log.debug("TTS unavailable — playing beep.")
                audio = self._generate_beep_wav()
            if config.MIC_MUTE_DURING_PLAYBACK:
                self._mic.mute()
            try:
                self._player.play(audio)
            finally:
                if config.MIC_MUTE_DURING_PLAYBACK:
                    self._mic.resume_listening()   # unmute → back to LISTENING, not IDLE
        finally:
            self._busy.clear()

    @staticmethod
    def _generate_beep_wav(freq: int = 880, duration_ms: int = 200, volume: float = 0.5) -> bytes:
        """Generate a simple sine-wave beep as WAV bytes — no TTS backend needed."""
        sample_rate = 44100
        n_samples   = int(sample_rate * duration_ms / 1000)
        samples     = [
            int(volume * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            for i in range(n_samples)
        ]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack(f"{n_samples}h", *samples))
        return buf.getvalue()

    # ── utterance callback (runs in capture thread) ──────────────────────────

    def _handle_utterance(self, wav_bytes: bytes):
        if self._busy.is_set():
            log.debug("Still playing; utterance dropped.")
            return
        threading.Thread(target=self._pipeline, args=(wav_bytes,), daemon=True).start()

    def _pipeline(self, wav_bytes: bytes):
        self._busy.set()
        try:
            # 1. ASR
            log.info("ASR: transcribing …")
            user_text = self._asr.transcribe(wav_bytes)
            if not user_text:
                log.info("ASR: empty result, skipping.")
                return
            log.info("User said: %s", user_text)

            # 2. LLM
            log.info("LLM: generating reply …")
            reply = self._llm.chat(user_text)
            if not reply:
                log.warning("LLM: no reply.")
                return
            log.info("Assistant: %s", reply)

            # 3. TTS
            log.info("TTS: synthesizing …")
            audio = self._tts.synthesize(reply)
            if not audio:
                log.warning("TTS: no audio returned.")
                return

            # 4. Play
            # Mute and AEC are independent — both can be active simultaneously.
            log.info(
                "Playing response … (mute=%s aec=%s)",
                config.MIC_MUTE_DURING_PLAYBACK,
                self._aec_active,
            )
            if config.MIC_MUTE_DURING_PLAYBACK:
                self._mic.mute()
            try:
                self._player.play(audio)
            finally:
                if config.MIC_MUTE_DURING_PLAYBACK:
                    self._mic.unmute()
        finally:
            self._busy.clear()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def run(self):
        log.info("Voice Assistant starting …")
        self._mic.start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal_handler)

        log.info("Listening. Press Ctrl-C to stop.")
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        finally:
            self._shutdown()

    def _signal_handler(self, signum, frame):
        log.info("Received signal %d, shutting down …", signum)
        self._stop.set()

    def _shutdown(self):
        log.info("Shutting down …")
        self._mic.stop()
        self._player.terminate()
        log.info("Voice Assistant stopped.")
